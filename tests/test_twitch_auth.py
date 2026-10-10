import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx

from dangerbot.oauth import OAuth, OAuthError, authorize
from dangerbot.twitch import Twitch


class TwitchAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_app_and_scope_mismatch_never_replace_saved_tokens(self):
        cases = [
            ({"login": "streamer", "client_id": "client", "scopes": ["chat:read", "chat:edit"]}, "@streamer"),
            ({"login": "bot", "client_id": "other", "scopes": ["chat:read", "chat:edit"]}, "outro aplicativo"),
            ({"login": "bot", "client_id": "client", "scopes": ["chat:read"]}, "chat:edit"),
        ]
        for identity, expected in cases:
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {"TWITCH_CLIENT_ID": "client", "TWITCH_CLIENT_SECRET": "secret"}):
                path = Path(directory) / "twitch-tokens.json"
                path.write_text(json.dumps({"access_token": "previous", "refresh_token": "previous-refresh"}))
                original = path.read_bytes()
                def transport(request):
                    if request.method == "POST":
                        return httpx.Response(200, json={"access_token": "candidate", "refresh_token": "candidate-refresh"})
                    self.assertEqual(request.headers["Authorization"], "OAuth candidate")
                    return httpx.Response(200, json=identity)
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    oauth = OAuth("twitch", client, Path(directory))
                    with self.assertRaisesRegex(OAuthError, expected) as error:
                        await oauth.exchange({"grant_type": "authorization_code", "code": "example"}, expected_login="bot")
                    self.assertNotIn("candidate", str(error.exception))
                    self.assertEqual(oauth.tokens["access_token"], "previous")
                    self.assertEqual(path.read_bytes(), original)

    async def test_correct_bot_identity_is_saved(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "TWITCH_CLIENT_ID": "client", "TWITCH_CLIENT_SECRET": "secret"}):
            def transport(request):
                if request.method == "POST":
                    return httpx.Response(200, json={"access_token": "candidate", "refresh_token": "refresh"})
                return httpx.Response(200, json={"login": "bot", "client_id": "client", "scopes": ["chat:read", "chat:edit"]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                oauth = OAuth("twitch", client, Path(directory))
                await oauth.exchange({"grant_type": "authorization_code", "code": "example"}, expected_login="bot")
            self.assertEqual(json.loads(oauth.path.read_text())["access_token"], "candidate")

    async def test_browser_flow_forces_consent_and_validates_expected_bot(self):
        oauth = MagicMock(client_id="client")
        oauth.exchange = AsyncMock()
        messages = []
        def server_factory(address, callback):
            server = MagicMock()
            server.__enter__.return_value = server
            def receive():
                handler = callback.__new__(callback)
                handler.path = "/callback?state=test-state&code=example"
                handler.send_response = MagicMock()
                handler.end_headers = MagicMock()
                handler.wfile = io.BytesIO()
                handler.do_GET()
            server.handle_request.side_effect = receive
            return server
        with patch.dict(os.environ, {"TWITCH_BOT_NAME": "bot", "TWITCH_REDIRECT_URI": "http://127.0.0.1:8888/callback"}), \
                patch("dangerbot.oauth.load_dotenv"), patch("dangerbot.oauth.OAuth", return_value=oauth), \
                patch("dangerbot.oauth.HTTPServer", side_effect=server_factory), \
                patch("dangerbot.oauth.secrets.token_urlsafe", return_value="test-state"), \
                patch("dangerbot.oauth.webbrowser.open", return_value=True) as browser:
            await authorize("twitch", announce=messages.append, show_url=False)
        query = parse_qs(urlparse(browser.call_args.args[0]).query)
        self.assertEqual(query["force_verify"], ["true"])
        self.assertTrue(any("@bot" in message for message in messages))
        oauth.exchange.assert_awaited_once_with({"grant_type": "authorization_code", "code": "example",
                                                "redirect_uri": "http://127.0.0.1:8888/callback"}, expected_login="bot")

    async def test_old_wrong_token_reports_actual_and_expected_login_at_start(self):
        identity = {"login": "streamer", "client_id": "client", "scopes": ["chat:read", "chat:edit"]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=identity))) as client:
            oauth = SimpleNamespace(client=client, client_id="client", access_token=AsyncMock(return_value="token"))
            twitch = Twitch(SimpleNamespace(bot_name="bot"), oauth, None)
            with self.assertRaisesRegex(OAuthError, "@streamer.*@bot"):
                await twitch.validate()
