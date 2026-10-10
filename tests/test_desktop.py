import asyncio
import io
import logging
import os
from pathlib import Path
from queue import Queue
import ssl
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from dotenv import dotenv_values

from dangerbot.desktop_worker import TaskRunner
from dangerbot.gui import Desktop, GuiLogHandler
from dangerbot.settings import FIELDS, apply_settings, ensure_certificate, load_settings, save_settings, validate_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env"
        self.values = load_settings(self.path)

    def test_every_example_setting_is_available_in_desktop(self):
        example = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")
        self.assertEqual(set(example), {field.key for field in FIELDS})
        self.assertEqual(self.values["TWITCH_CHANNEL"], "")

    def test_save_preserves_comments_unknown_keys_and_literal_secrets(self):
        self.path.write_text("# Preserve this comment\nEXTRA_KEY=keep\nUSER_COOLDOWN=30\n", encoding="utf-8")
        self.values["TWITCH_CLIENT_SECRET"] = "secret ' quote $ and ${HOME} # suffix"
        save_settings(self.path, self.values)
        stored = dotenv_values(self.path, interpolate=False)
        self.assertEqual(stored["TWITCH_CLIENT_SECRET"], self.values["TWITCH_CLIENT_SECRET"])
        self.assertEqual(stored["EXTRA_KEY"], "keep")
        self.assertEqual(stored["USER_COOLDOWN"], "5")
        self.assertIn("# Preserve this comment", self.path.read_text(encoding="utf-8"))
        self.assertFalse(list(self.path.parent.glob(".dangerbot-settings-*")))

    def test_invalid_settings_do_not_overwrite_file(self):
        self.path.write_text("USER_COOLDOWN=5\n", encoding="utf-8")
        original = self.path.read_bytes()
        for key, value in [("USER_COOLDOWN", "nan"), ("GLOBAL_COOLDOWN", "inf"),
                           ("GLOBAL_COOLDOWN", "-1"), ("MAX_USER_REQUESTS", "0"),
                           ("MAX_PENDING_REQUESTS", "1.5"), ("ALLOW_EXPLICIT", "maybe"),
                           ("TWITCH_CHANNEL", "https://twitch.tv/person"),
                           ("SPOTIFY_REDIRECT_URI", "http://example.com:8888/callback"),
                           ("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:99999/callback"),
                           ("TWITCH_CLIENT_SECRET", "hidden\nINJECTED=1")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                save_settings(self.path, {**self.values, key: value})
            self.assertEqual(self.path.read_bytes(), original)

    def test_partial_setup_can_save_but_authentication_requires_provider_credentials(self):
        save_settings(self.path, self.values)
        with self.assertRaises(ValueError):
            validate_settings(self.values, provider="spotify")
        self.values.update(SPOTIFY_CLIENT_ID="id", SPOTIFY_CLIENT_SECRET="secret")
        validate_settings(self.values, provider="spotify")
        with self.assertRaises(ValueError):
            validate_settings(self.values, require_accounts=True)
        self.values.update(TWITCH_CHANNEL="#Channel", TWITCH_BOT_NAME="BOT",
                           TWITCH_CLIENT_ID="id", TWITCH_CLIENT_SECRET="secret")
        normalized = validate_settings(self.values, require_accounts=True)
        self.assertEqual(normalized["TWITCH_CHANNEL"], "channel")
        self.assertEqual(normalized["TWITCH_BOT_NAME"], "bot")

    def test_saved_values_override_old_environment_without_gui_restart(self):
        with patch.dict(os.environ, {"USER_COOLDOWN": "30", "SPOTIFY_CLIENT_SECRET": "old"}):
            self.values["SPOTIFY_CLIENT_SECRET"] = "new"
            apply_settings(self.values)
            self.assertEqual(os.environ["USER_COOLDOWN"], "5")
            self.assertEqual(os.environ["SPOTIFY_CLIENT_SECRET"], "new")

    def test_certificate_is_usable_local_and_existing_files_are_preserved(self):
        from cryptography import x509
        from ipaddress import ip_address
        cert, key = self.path.parent / "certificate.pem", self.path.parent / "key.pem"
        self.assertTrue(ensure_certificate(cert, key))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        parsed = x509.load_pem_x509_certificate(cert.read_bytes())
        names = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn("localhost", names.get_values_for_type(x509.DNSName))
        self.assertIn(ip_address("127.0.0.1"), names.get_values_for_type(x509.IPAddress))
        original = (cert.read_bytes(), key.read_bytes())
        self.assertFalse(ensure_certificate(cert, key))
        self.assertEqual((cert.read_bytes(), key.read_bytes()), original)
        key.unlink()
        with self.assertRaises(ValueError):
            ensure_certificate(cert, key)
        self.assertEqual(cert.read_bytes(), original[0])
        self.assertFalse(key.exists())


class WorkerTests(unittest.TestCase):
    def test_cancel_runs_cleanup_and_rejects_second_operation(self):
        events = Queue()
        runner = TaskRunner(events)
        started, cleaned = threading.Event(), threading.Event()
        async def work():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        runner.start(work)
        self.assertTrue(started.wait(3))
        with self.assertRaises(RuntimeError):
            runner.start(work)
        runner.stop()
        runner.thread.join(3)
        self.assertFalse(runner.active)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(events.get(timeout=1), ("finished", "cancelled"))

    def test_unexpected_errors_do_not_expose_secrets(self):
        events = Queue()
        runner = TaskRunner(events)
        async def work():
            raise RuntimeError("sensitive-value")
        runner.start(work)
        runner.thread.join(3)
        kind, message = events.get(timeout=1)
        self.assertEqual(kind, "error")
        self.assertNotIn("sensitive-value", message)
        self.assertEqual(events.get(timeout=1), ("finished", "error"))

    def test_log_handler_redacts_client_secrets(self):
        events = Queue()
        handler = GuiLogHandler(events, lambda: ["hidden-secret"])
        handler.emit(logging.LogRecord("test", logging.INFO, "", 0, "Failure: hidden-secret", (), None))
        kind, message = events.get(timeout=1)
        self.assertEqual(kind, "log")
        self.assertNotIn("hidden-secret", message)
        self.assertIn("[oculto]", message)


class DesktopOAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_callback_saves_authorization_without_exposing_url_in_gui_messages(self):
        from dangerbot.oauth import authorize
        messages = []
        oauth = MagicMock(client_id="example-id")
        oauth.exchange = AsyncMock()

        def server_factory(address, callback):
            server = MagicMock()
            server.__enter__.return_value = server
            def receive_callback():
                handler = callback.__new__(callback)
                handler.path = "/callback?state=test-state&code=private-code"
                handler.send_response = MagicMock()
                handler.end_headers = MagicMock()
                handler.wfile = io.BytesIO()
                handler.do_GET()
            server.handle_request.side_effect = receive_callback
            return server

        with patch.dict(os.environ, {"SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback"}), \
                patch("dangerbot.oauth.load_dotenv"), patch("dangerbot.oauth.OAuth", return_value=oauth), \
                patch("dangerbot.oauth.HTTPServer", side_effect=server_factory), \
                patch("dangerbot.oauth.secrets.token_urlsafe", return_value="test-state"), \
                patch("dangerbot.oauth.webbrowser.open", return_value=True) as browser:
            await authorize("spotify", announce=messages.append, show_url=False)
        browser.assert_called_once()
        oauth.exchange.assert_awaited_once_with({"grant_type": "authorization_code", "code": "private-code",
                                                "redirect_uri": "http://127.0.0.1:8888/callback"})
        self.assertNotIn("test-state", " ".join(messages))
        self.assertNotIn("private-code", " ".join(messages))
        self.assertTrue(any("salvo" in message for message in messages))

    async def test_no_default_browser_reports_actionable_error(self):
        from dangerbot.oauth import authorize, OAuthError
        with patch.dict(os.environ, {"SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback"}), \
                patch("dangerbot.oauth.load_dotenv"), \
                patch("dangerbot.oauth.OAuth", return_value=MagicMock(client_id="example-id")), \
                patch("dangerbot.oauth.HTTPServer"), patch("dangerbot.oauth.webbrowser.open", return_value=False):
            with self.assertRaisesRegex(OAuthError, "navegador padrão"):
                await authorize("spotify", announce=lambda _: None, show_url=False)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Desktop display not available: {error}")
        self.root.withdraw()
        self.desktop = Desktop(self.root, self.directory.name)
        self.addCleanup(self.desktop.dispose)

    def test_edit_save_reload_and_masked_secrets(self):
        self.desktop.variables["TWITCH_CHANNEL"].set("streamer")
        self.desktop.variables["SPOTIFY_CLIENT_SECRET"].set("hidden-secret")
        self.assertTrue(self.desktop.dirty)
        self.assertTrue(all(entry.cget("show") for entry in self.desktop.secret_entries))
        with patch.dict(os.environ):
            self.assertTrue(self.desktop.save())
        self.assertFalse(self.desktop.dirty)
        saved = load_settings(Path(self.directory.name) / ".env")
        self.assertEqual(saved["TWITCH_CHANNEL"], "streamer")
        self.assertEqual(saved["SPOTIFY_CLIENT_SECRET"], "hidden-secret")
        self.assertNotIn("hidden-secret", self.desktop.output.get("1.0", "end"))

    def test_failed_validation_and_active_operation_cannot_write_settings(self):
        self.desktop.variables["MAX_USER_REQUESTS"].set("0")
        with patch("dangerbot.gui.messagebox.showerror") as error:
            self.assertFalse(self.desktop.save())
        error.assert_called_once()
        self.assertFalse((Path(self.directory.name) / ".env").exists())
        with patch.object(TaskRunner, "active", new_callable=PropertyMock, return_value=True):
            self.assertFalse(self.desktop.save())

    def test_authentication_button_uses_saved_credentials_and_prepares_https(self):
        self.desktop.variables["TWITCH_BOT_NAME"].set("examplebot")
        self.desktop.variables["TWITCH_CLIENT_ID"].set("example-id")
        self.desktop.variables["TWITCH_CLIENT_SECRET"].set("example-secret")
        with patch.dict(os.environ), patch("dangerbot.oauth.authorize", new_callable=AsyncMock) as authorize, \
                patch("dangerbot.gui.ensure_certificate") as certificate:
            self.desktop.authenticate("twitch")
            self.desktop.runner.thread.join(3)
            self.assertFalse(self.desktop.runner.active)
            self.desktop._poll()
            self.assertEqual(os.environ["TWITCH_CLIENT_ID"], "example-id")
        authorize.assert_awaited_once()
        self.assertEqual(authorize.await_args.args, ("twitch",))
        self.assertFalse(authorize.await_args.kwargs["show_url"])
        certificate.assert_called_once()
        self.assertNotIn("example-secret", self.desktop.output.get("1.0", "end"))
        self.assertIsNone(self.desktop.operation)

    def test_start_requires_saved_authorizations_before_connecting(self):
        for key, value in {"TWITCH_CHANNEL": "streamer", "TWITCH_BOT_NAME": "bot",
                           "TWITCH_CLIENT_ID": "example", "TWITCH_CLIENT_SECRET": "example-secret",
                           "SPOTIFY_CLIENT_ID": "example", "SPOTIFY_CLIENT_SECRET": "example-secret"}.items():
            self.desktop.variables[key].set(value)
        with patch.dict(os.environ), patch("dangerbot.gui.messagebox.showinfo") as prompt, \
                patch("dangerbot.app.run", new_callable=AsyncMock) as run:
            self.desktop.start_bot()
        prompt.assert_called_once()
        run.assert_not_awaited()

    def test_settings_disabled_while_running_and_choices_remain_readonly_after_stop(self):
        from tkinter import ttk
        self.desktop._set_busy(True)
        self.assertTrue(all(str(control.cget("state")) == "disabled" for control in self.desktop.controls))
        self.desktop._set_busy(False)
        for control in self.desktop.controls:
            self.assertEqual(str(control.cget("state")), "readonly" if isinstance(control, ttk.Combobox) else "normal")
