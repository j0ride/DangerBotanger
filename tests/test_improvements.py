import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx

from dangerbot.config import Config
from dangerbot.i18n import MESSAGES
from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import SearchSuggestion, Spotify, SpotifyError
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

    async def test_unrelated_results_require_confirmation_without_consuming_cooldown(self):
        def transport(request):
            if request.url.path.endswith("/search"):
                return httpx.Response(200, json={"tracks": {"items": [item("Duality", "Slipknot")]}})
            if request.url.path.endswith("/queue"):
                return httpx.Response(200, json={"queue": []})
            return httpx.Response(204)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            queue = RequestQueue(":memory:")
            self.addCleanup(queue.close)
            policy = Policy(config())
            service = SongRequests(Spotify(client, Auth()), queue, policy)
            for language in ["br", "en"]:
                service.language = language
                reply = await service.handle(User("viewer"), "!sr snuff Slipknot")
                self.assertIn("!sr", reply)
                self.assertIn("Duality", reply)
                self.assertEqual(queue.count(), 0)
                self.assertEqual(policy.users, {})
                self.assertEqual(policy.global_until, 0)

    async def test_exact_title_preferred_and_weak_matches_need_confirmation(self):
        results = [item("Snuff - Live", "Slipknot", "live"), item("Snuff", "Slipknot")]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={"tracks": {"items": results}}))) as client:
            spotify = Spotify(client, Auth())
            self.assertEqual((await spotify.search("snuff Slipknot")).id, "correct")
            self.assertIsInstance(await spotify.search("snuff Pitty"), SearchSuggestion)
            self.assertIsInstance(await spotify.search("Slipknot"), SearchSuggestion)

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

    def prepare_suggestion(self):
        self.now = [100]
        self.service.policy.clock = lambda: self.now[0]
        self.suggested_track = replace(TRACK, name="Sadness and Sorrow", artists=("Example Performer",))
        self.spotify.search.return_value = SearchSuggestion(self.suggested_track)
        self.spotify.queue_snapshot.return_value = {"queue": []}
        self.spotify.playback.return_value = None

    async def test_informal_request_can_confirm_original_suggestion_without_new_search(self):
        self.prepare_suggestion()
        for language, confirmation in [("br", "confirmar"), ("en", "confirm")]:
            with self.subTest(language=language):
                self.queue.db.execute("DELETE FROM requests")
                self.service.language = language
                self.now[0] += 30
                viewer = User("viewer")
                reply = await self.service.handle(viewer, "!sr musica triste do naruto")
                self.assertIn("Sadness and Sorrow", reply)
                self.assertIn("!sr " + confirmation, reply)
                self.assertEqual(self.queue.count(), 0)
                self.assertLessEqual(len(reply), 350)
                self.spotify.search.reset_mock()
                reply = await self.service.handle(viewer, "!sr " + confirmation)
                self.assertIn("Sadness and Sorrow", reply)
                self.assertEqual(self.queue.count(), 1)
                self.spotify.search.assert_not_awaited()
                self.assertEqual(self.service.suggestions, {})
                self.assertEqual(self.service.policy.users["viewer"], self.now[0] + 30)

    async def test_suggestion_belongs_to_requester_and_expires_at_sixty_seconds(self):
        self.prepare_suggestion()
        viewer = User("viewer")
        await self.service.handle(viewer, "!sr musica triste do naruto")
        self.assertIn("sugestão ativa", await self.service.handle(User("other"), "!sr confirmar"))
        self.now[0] = 160
        self.assertIn("expirado", await self.service.handle(viewer, "!sr confirmar"))
        self.assertEqual(self.queue.count(), 0)

    async def test_new_request_replaces_or_clears_previous_suggestion(self):
        self.prepare_suggestion()
        viewer = User("viewer")
        await self.service.handle(viewer, "!sr first description")
        replacement = replace(TRACK, id="second", uri="spotify:track:second", name="Other Song")
        self.spotify.search.return_value = SearchSuggestion(replacement)
        self.assertIn("Other Song", await self.service.handle(viewer, "!sr second description"))
        self.spotify.search.return_value = None
        await self.service.handle(viewer, "!sr missing song")
        self.assertIn("sugestão ativa", await self.service.handle(viewer, "!sr confirmar"))
        self.assertEqual(self.queue.count(), 0)

    async def test_confirmation_rechecks_blacklist_and_duplicates(self):
        self.prepare_suggestion()
        viewer = User("viewer")
        await self.service.handle(viewer, "!sr description")
        self.service.policy.config.blocked_tracks = frozenset({TRACK.id})
        self.assertIn("bloqueada", await self.service.handle(viewer, "!sr confirmar"))
        self.assertEqual(self.queue.count(), 0)
        self.service.policy.config.blocked_tracks = frozenset()
        self.spotify.queue_snapshot.return_value = {"queue": [{"uri": TRACK.uri}]}
        self.assertIn("já está", await self.service.handle(viewer, "!sr confirmar"))
        self.assertEqual(self.queue.count(), 0)

    async def test_confirmation_rechecks_permissions_cooldown_and_queue_limits(self):
        self.prepare_suggestion()
        viewer = User("viewer")
        await self.service.handle(viewer, "!sr description")
        self.service.policy.config.permission = "moderator"
        self.assertIn("permissão", await self.service.handle(viewer, "!sr confirmar"))
        self.service.policy.config.permission = "everyone"
        self.service.policy.global_until = 105
        self.assertIn("Aguarde 5s", await self.service.handle(viewer, "!sr confirmar"))
        self.now[0] = 105
        self.service.policy.config.max_user_requests = 1
        self.queue.add(viewer.name, replace(TRACK, id="another", uri="spotify:track:another"))
        self.assertIn("1 pedidos", await self.service.handle(viewer, "!sr confirmar"))
        self.assertEqual(self.queue.count(), 1)

    async def test_blocked_suggestion_is_not_offered(self):
        self.prepare_suggestion()
        self.service.policy.config.blocked_artists = frozenset({"artist1"})
        self.assertIn("bloqueado", await self.service.handle(User("viewer"), "!sr description"))
        self.assertEqual(self.service.suggestions, {})
        self.assertEqual(self.queue.count(), 0)

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
