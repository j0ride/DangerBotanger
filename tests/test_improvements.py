import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx

from dangerbot.config import Config
from dangerbot.i18n import MESSAGES
from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import Spotify, SpotifyError
from test_bot import TRACK, config


class Auth:
    async def access_token(self, force=False):
        return "token"


def item(name, artist, identifier="correct"):
    return {"id": identifier, "uri": "spotify:track:" + identifier, "name": name,
            "artists": [{"id": "artist1", "name": artist}], "duration_ms": 180000, "explicit": False}


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_feedback_requests_select_matching_track_instead_of_first_result(self):
        cases = [
            ("snuff Slipknot", item("Duality", "Slipknot", "wrong"), item("Snuff", "Slipknot")),
            ("pitty na sua estantwe", item("Apocalipse", "Natkym", "wrong"), item("Na Sua Estante", "Pitty")),
            ("brand new channel", item("CRANK", "Slayyyter", "wrong"), item("Brand New Channel", "Slayyyter")),
            ("brand new channel slayyter", item("Bad News", "Kanye West", "wrong"), item("Brand New Channel", "Slayyyter")),
            ("three sacred souls will i see you again?", item("Crazy", "Seal", "wrong"),
             item("Will I See You Again?", "Thee Sacred Souls")),
            ("PÍTTY - NA SUA ESTANTE!", item("Apocalipse", "Natkym", "wrong"), item("Na Sua Estante", "Pitty")),
        ]
        for query, wrong, correct in cases:
            with self.subTest(query=query):
                calls = []
                def transport(request):
                    calls.append(request)
                    return httpx.Response(200, json={"tracks": {"items": [wrong, correct]}})
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    track = await Spotify(client, Auth()).search(query)
                self.assertEqual(track.id, "correct")
                self.assertEqual(calls[0].url.params["q"], query)
                self.assertEqual(calls[0].url.params["limit"], "10")

    async def test_unrelated_results_do_not_enter_queue_or_consume_cooldown(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={"tracks": {"items": [item("Duality", "Slipknot")]}}))) as client:
            queue = RequestQueue(":memory:")
            self.addCleanup(queue.close)
            policy = Policy(config())
            service = SongRequests(Spotify(client, Auth()), queue, policy)
            for language in ["br", "en"]:
                service.language = language
                reply = await service.handle(User("viewer"), "!sr snuff Slipknot")
                self.assertIn("!sr", reply)
                self.assertEqual(queue.count(), 0)
                self.assertEqual(policy.users, {})
                self.assertEqual(policy.global_until, 0)

    async def test_exact_title_preferred_and_wrong_artist_rejected(self):
        results = [item("Snuff - Live", "Slipknot", "live"), item("Snuff", "Slipknot")]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={"tracks": {"items": results}}))) as client:
            spotify = Spotify(client, Auth())
            self.assertEqual((await spotify.search("snuff Slipknot")).id, "correct")
            self.assertIsNone(await spotify.search("snuff Pitty"))
            self.assertIsNone(await spotify.search("Slipknot"))

    async def test_malformed_and_unplayable_results(self):
        for payload in [{}, {"tracks": {"items": None}}, {"tracks": {"items": [{}]}}]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json=payload))) as client:
                with self.assertRaises(SpotifyError):
                    await Spotify(client, Auth()).search("song")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={"tracks": {"items": [{**item("Song", "Artist"), "is_playable": False}]}}))) as client:
            self.assertIsNone(await Spotify(client, Auth()).search("song"))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = RequestQueue(":memory:")
        self.addCleanup(self.queue.close)
        self.spotify = AsyncMock()
        self.spotify.playback.return_value = {"item": {"name": "Song", "artists": [{"name": "Artist"}]},
                                              "is_playing": True}
        self.service = SongRequests(self.spotify, self.queue, Policy(config()))
        self.mod = User("mod", frozenset({"moderator"}))

    async def test_aliases_return_same_playback_response_in_both_languages(self):
        for language in ["br", "en"]:
            self.service.language = language
            expected = await self.service.handle(User("viewer"), "!np")
            for command in ["!song", "!currentsong", "!SONG"]:
                self.assertEqual(await self.service.handle(User("viewer"), command), expected)
                self.assertIn("!np", await self.service.handle(User("viewer"), command + " extra"))

    async def test_help_lists_commands_and_fits_chat_limit(self):
        for language in ["br", "en"]:
            self.service.language = language
            reply = await self.service.handle(User("viewer"), "!help")
            for command in ["!help", "!sr", "!np", "!song", "!currentsong", "!queue", "!skip", "!volume", "!setlang"]:
                self.assertIn(command, reply)
            self.assertLessEqual(len(reply), 350)

    async def test_volume_permission_limits_and_success(self):
        for language in ["br", "en"]:
            self.service.language = language
            reply = await self.service.handle(User("viewer"), "!volume 30")
            self.assertEqual(reply, MESSAGES["volume_permission"][language])
            self.spotify.set_volume.assert_not_awaited()
            for argument in ["", "-1", "61", "100", "30.5", "+30", "３０", "30 40", "9" * 100]:
                self.assertEqual(await self.service.handle(self.mod, "!volume " + argument),
                                 MESSAGES["volume_usage"][language])
            self.spotify.set_volume.assert_not_awaited()
        for user in [self.mod, User("owner", frozenset({"broadcaster"}))]:
            for volume in [0, 30, 60]:
                self.assertIn(str(volume) + "%", await self.service.handle(user, f"!volume {volume}"))
                self.spotify.set_volume.assert_awaited_with(volume)

    async def test_volume_api_errors_do_not_confirm_success(self):
        self.spotify.set_volume.side_effect = SpotifyError("Spotify limitou as requisições.", retry_after=42)
        reply = await self.service.handle(self.mod, "!volume 30")
        self.assertIn("limitou", reply)
        self.spotify.set_volume.assert_awaited_once_with(30)

    async def test_volume_http_endpoint_and_device(self):
        for device in ["", "device1"]:
            calls = []
            def transport(request):
                calls.append(request)
                return httpx.Response(204)
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                spotify = Spotify(client, Auth(), device)
                for volume in [-1, 61, True, "30"]:
                    with self.assertRaises(ValueError):
                        await spotify.set_volume(volume)
                self.assertEqual(calls, [])
                await spotify.set_volume(30)
            self.assertEqual(calls[0].method, "PUT")
            self.assertEqual(calls[0].url.path, "/v1/me/player/volume")
            self.assertEqual(dict(calls[0].url.params),
                             {"volume_percent": "30", **({"device_id": device} if device else {})})

    async def test_default_cooldowns_allow_new_requests_after_five_seconds(self):
        with patch("dangerbot.config.load_dotenv"), patch.dict("os.environ", {
                "TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "bot"}, clear=True):
            settings = Config.load()
        self.assertEqual((settings.user_cooldown, settings.global_cooldown), (5, 5))
        now = [100]
        policy = Policy(settings, clock=lambda: now[0])
        self.service.policy = policy
        self.spotify.queue_snapshot.return_value = {"queue": []}
        self.spotify.playback.return_value = None
        self.spotify.search.side_effect = [TRACK, replace(TRACK, id="second", uri="spotify:track:second")]
        viewer = User("viewer")
        self.assertIn("adicionada", await self.service.handle(viewer, "!sr first"))
        now[0] = 104
        self.assertIn("Aguarde 1s", await self.service.handle(viewer, "!sr second"))
        self.assertIn("Aguarde 1s", await self.service.handle(User("other"), "!sr second"))
        now[0] = 105
        self.assertIn("adicionada", await self.service.handle(viewer, "!sr second"))
