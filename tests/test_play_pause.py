import unittest
from unittest.mock import AsyncMock

import httpx

from dangerbot.i18n import MESSAGES
from dangerbot.oauth import OAuthError
from dangerbot.queue import RequestQueue
from dangerbot.service import COMMANDS, Policy, SongRequests, User
from dangerbot.spotify import Spotify, SpotifyError
from test_bot import config


class Auth:
    async def access_token(self, force=False):
        return "token"


class PlaybackControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = RequestQueue(":memory:")
        self.addCleanup(self.queue.close)
        self.spotify = AsyncMock()
        self.service = SongRequests(self.spotify, self.queue, Policy(config()))

    async def test_viewers_denied_but_moderators_and_owner_can_control_playback(self):
        for language in ["br", "en"]:
            self.service.language = language
            for command, method in [("!play", self.spotify.play), ("!pause", self.spotify.pause)]:
                method.reset_mock()
                self.assertEqual(await self.service.handle(User("viewer"), command),
                                 MESSAGES["playback_permission"][language])
                method.assert_not_awaited()
                for role in ["moderator", "broadcaster"]:
                    reply = await self.service.handle(User("controller", frozenset({role})), command.upper())
                    self.assertEqual(reply, MESSAGES["play_success" if command == "!play" else "pause_success"][language])
                self.assertEqual(method.await_count, 2)
        self.assertEqual(self.queue.count(), 0)
        self.assertEqual(self.service.policy.users, {})

    async def test_arguments_rejected_and_help_includes_controls_within_chat_limit(self):
        owner = User("owner", frozenset({"broadcaster"}))
        for command in ["!play", "!pause"]:
            self.assertIn(command, COMMANDS)
            self.assertIn("Uso:", await self.service.handle(owner, command + " song"))
        self.spotify.play.assert_not_awaited()
        self.spotify.pause.assert_not_awaited()
        for language in ["br", "en"]:
            self.service.language = language
            reply = await self.service.handle(User("viewer"), "!help")
            self.assertIn("!play", reply)
            self.assertIn("!pause", reply)
            self.assertLessEqual(len(reply), 350)

    async def test_api_errors_never_confirm_playback_control(self):
        owner = User("owner", frozenset({"broadcaster"}))
        for command, method in [("!play", self.spotify.play), ("!pause", self.spotify.pause)]:
            for error, expected in [
                (SpotifyError("Abra o Spotify e inicie a reprodução em um dispositivo."), "Abra o Spotify"),
                (SpotifyError("Spotify limitou as requisições.", retry_after=30), "limitou"),
                (OAuthError("Autorize novamente."), "Autorize novamente")]:
                method.reset_mock()
                method.side_effect = error
                self.assertIn(expected, await self.service.handle(owner, command))
                method.assert_awaited_once_with()

    async def test_http_endpoints_device_and_bodyless_success(self):
        for action in ["play", "pause"]:
            for device in ["", "device1"]:
                calls = []
                def transport(request):
                    calls.append(request)
                    return httpx.Response(204)
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    await getattr(Spotify(client, Auth(), device), action)()
                self.assertEqual(len(calls), 1)
                request = calls[0]
                self.assertEqual(request.method, "PUT")
                self.assertEqual(request.url.path, "/v1/me/player/" + action)
                self.assertEqual(dict(request.url.params), {"device_id": device} if device else {})
                self.assertEqual(request.content, b"")
