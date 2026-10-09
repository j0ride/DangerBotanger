import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from dangerbot.oauth import OAuth
from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import Spotify, SpotifyError, Track
from dangerbot.twitch import parse_message


def config(**overrides):
    return SimpleNamespace(**{
        "user_cooldown": 30, "global_cooldown": 5, "permission": "everyone",
        "blocked_users": frozenset(), "blocked_tracks": frozenset(),
        "blocked_artists": frozenset(), "allow_explicit": True,
        "max_duration": 600, "max_pending": 30, "max_user_requests": 10, **overrides})


TRACK = Track("track1", "spotify:track:track1", "Song", ("Artist",), ("artist1",), 180000, False)


class FakeSpotify:
    def __init__(self, error=None):
        self.error = error
        self.sent = []
        self.skips = 0
        self.playback_data = None
        self.spotify_queue = []

    async def search(self, query):
        return TRACK

    async def enqueue(self, uri):
        if self.error:
            raise self.error
        self.sent.append(uri)

    async def skip(self):
        if self.error:
            raise self.error
        self.skips += 1

    async def playback(self):
        if self.error:
            raise self.error
        return self.playback_data

    async def playback_queue(self):
        if self.error:
            raise self.error
        return self.spotify_queue

    async def queue_snapshot(self):
        if self.error:
            raise self.error
        current = self.playback_data.get("item") if self.playback_data else None
        return {"queue": self.spotify_queue, "currently_playing": current}


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = RequestQueue(":memory:")

    async def asyncTearDown(self):
        self.queue.close()

    async def test_np_reports_requester_in_both_languages_and_when_paused(self):
        self.sent_requests("viewer", 1)
        spotify = FakeSpotify()
        spotify.playback_data = {"item": {"uri": "spotify:track:sent0", "name": "Song",
                                          "artists": [{"name": "Artist"}]},
                                 "progress_ms": 0, "is_playing": True}
        service = SongRequests(spotify, self.queue, Policy(config()))
        self.assertIn("Pedida por @viewer.", await service.handle(User("other"), "!np"))
        self.assertIn("Pedida por @viewer.", await service.handle(User("other"), "!np"))
        spotify.playback_data["is_playing"] = False
        await service.handle(User("owner", frozenset({"broadcaster"})), "!setlang en")
        reply = await service.handle(User("other"), "!np")
        self.assertIn("Spotify paused", reply)
        self.assertIn("Requested by @viewer.", reply)

    async def test_np_does_not_credit_autoplay_or_completed_history(self):
        request_id = self.queue.add("viewer", TRACK)
        self.queue.update(request_id, "completed")
        spotify = FakeSpotify()
        spotify.playback_data = {"item": {"uri": TRACK.uri, "name": "Song"},
                                 "progress_ms": 0, "is_playing": True}
        service = SongRequests(spotify, self.queue, Policy(config()))
        self.assertNotIn("Pedida por", await service.handle(User("other"), "!np"))

    async def test_current_song_future_request_is_not_assigned_to_autoplay(self):
        import time
        request_id = self.queue.add("viewer", TRACK)
        self.queue.update(request_id, "sent")
        requester = self.queue.track_playback(
            {"item": {"uri": TRACK.uri}, "progress_ms": 120000}, now=time.time())
        self.assertIsNone(requester)
        self.assertEqual(self.queue.get(request_id)["status"], "sent")

    async def test_same_song_requests_are_consumed_once_per_playback(self):
        import time
        first = self.queue.add("first", TRACK)
        second = self.queue.add("second", TRACK)
        self.queue.update(first, "sent")
        self.queue.update(second, "sent")
        now = time.time() + 20
        item = {"uri": TRACK.uri}
        self.assertEqual(self.queue.track_playback({"item": item, "progress_ms": 0}, now=now), "first")
        self.assertEqual(self.queue.track_playback({"item": item, "progress_ms": 10000}, now=now + 10), "first")
        self.queue.user_slots("first", [{"uri": TRACK.uri}], current_item=item)
        self.assertEqual(self.queue.get(second)["status"], "sent")
        self.assertEqual(self.queue.track_playback({"item": item, "progress_ms": 0}, now=now + 180), "second")
        self.assertEqual(self.queue.get(first)["status"], "completed")

    def sent_requests(self, user, count):
        from dataclasses import replace
        items = []
        for index in range(count):
            track = replace(TRACK, id=f"sent{index}", uri=f"spotify:track:sent{index}")
            request_id = self.queue.add(user, track)
            self.queue.update(request_id, "sent")
            items.append({"uri": track.uri})
        return items

    async def test_ten_spotify_requests_block_viewer_and_broadcaster(self):
        for user in [User("viewer"), User("viewer", frozenset({"broadcaster"}))]:
            self.queue.db.execute("DELETE FROM requests")
            spotify = FakeSpotify()
            spotify.spotify_queue = self.sent_requests(user.name, 10)
            service = SongRequests(spotify, self.queue, Policy(config()))
            self.assertEqual(self.queue.count(), 0)
            self.assertIn("10 pedidos", await service.handle(user, "!sr song"))
            self.assertEqual(self.queue.count(), 0)
            # A confirmed departure opens one slot.
            spotify.spotify_queue.pop(0)
            self.assertIn("adicionada", await service.handle(user, "!sr song"))
            self.assertEqual(self.queue.count(), 1)

    async def test_broadcaster_bypasses_user_and_global_request_cooldowns(self):
        from dataclasses import replace
        now = [100]
        spotify = FakeSpotify()
        policy = Policy(config(), clock=lambda: now[0])
        service = SongRequests(spotify, self.queue, policy)
        async def unique_search(query):
            return replace(TRACK, id=query, uri="spotify:track:" + query)
        spotify.search = unique_search
        self.assertIn("adicionada", await service.handle(User("viewer"), "!sr viewer-song"))
        owner = User("owner", frozenset({"broadcaster"}))
        for index in range(10):
            self.assertIn("adicionada", await service.handle(owner, f"!sr owner-song-{index}"))
        self.assertIn("10 pedidos", await service.handle(owner, "!sr eleventh"))
        self.assertEqual(policy.global_until, 105)
        self.assertNotIn("owner", policy.users)
        self.assertIn("Aguarde", await service.handle(User("mod", frozenset({"moderator"})), "!sr mod-song"))
        now[0] = 129
        self.assertIn("Aguarde 1s", await service.handle(User("viewer"), "!sr next"))
        now[0] = 130
        self.assertIn("adicionada", await service.handle(User("viewer"), "!sr next"))

    async def test_user_slots_do_not_count_other_users_or_manual_songs(self):
        items = self.sent_requests("other", 10)
        items.append({"uri": "spotify:track:manual"})
        self.assertEqual(self.queue.user_slots("viewer", items), 0)
        self.assertEqual(self.queue.user_slots("OTHER", items), 10)

    async def test_currently_playing_is_not_counted_as_waiting(self):
        items = self.sent_requests("viewer", 10)
        current = items.pop(0)
        self.assertEqual(self.queue.user_slots("viewer", items, current_item=current), 9)

    async def test_delayed_visibility_and_uncertain_delivery_keep_reservations(self):
        import time
        request_id = self.queue.add("viewer", TRACK)
        self.queue.update(request_id, "sent")
        self.assertEqual(self.queue.user_slots("viewer", []), 1)
        self.assertEqual(self.queue.user_slots("viewer", [], now=time.time() + 61), 0)
        request_id = self.queue.add("viewer", TRACK)
        self.queue.update(request_id, "uncertain")
        self.assertEqual(self.queue.user_slots("viewer", []), 1)
        self.assertEqual(self.queue.user_slots("viewer", [{"uri": TRACK.uri}]), 1)
        self.assertEqual(self.queue.user_slots("viewer", []), 0)

    async def test_long_snapshot_does_not_release_missing_tail_requests(self):
        import time
        self.sent_requests("viewer", 10)
        unrelated = [{"uri": f"spotify:track:manual{i}"} for i in range(20)]
        self.assertEqual(self.queue.user_slots("viewer", unrelated, now=time.time() + 61), 10)

    async def test_legacy_requests_do_not_fill_broadcaster_limit_with_autoplay(self):
        self.sent_requests("owner", 10)
        self.queue.db.execute("UPDATE requests SET sent_at=NULL, observed=0")
        self.queue.db.execute("UPDATE requests SET status='uncertain' WHERE id=1")
        spotify = FakeSpotify()
        spotify.spotify_queue = [{"uri": f"spotify:track:autoplay{i}"} for i in range(20)]
        service = SongRequests(spotify, self.queue, Policy(config()))
        owner = User("owner", frozenset({"broadcaster"}))
        self.assertIn("adicionada", await service.handle(owner, "!sr song"))
        self.assertEqual(self.queue.db.execute(
            "SELECT COUNT(*) FROM requests WHERE status='completed'").fetchone()[0], 10)

    async def test_legacy_requests_still_in_spotify_queue_still_count(self):
        items = self.sent_requests("owner", 10)
        self.queue.db.execute("UPDATE requests SET sent_at=NULL, observed=0")
        spotify = FakeSpotify()
        spotify.spotify_queue = items + [{"uri": f"spotify:track:autoplay{i}"} for i in range(20)]
        service = SongRequests(spotify, self.queue, Policy(config()))
        self.assertIn("10 pedidos", await service.handle(
            User("owner", frozenset({"broadcaster"})), "!sr song"))

    async def test_queue_lookup_failure_does_not_accept_request(self):
        from unittest.mock import AsyncMock
        spotify = FakeSpotify()
        spotify.queue_snapshot = AsyncMock(side_effect=SpotifyError("Spotify indisponível."))
        service = SongRequests(spotify, self.queue, Policy(config()))
        self.assertIn("indisponível", await service.handle(User("viewer"), "!sr song"))
        self.assertEqual(self.queue.count(), 0)
        self.assertEqual(service.policy.users, {})

    async def test_setlang_permissions_invalid_input_and_switch_back(self):
        service = SongRequests(FakeSpotify(), self.queue, Policy(config()))
        owner = User("owner", frozenset({"broadcaster"}))
        self.assertIn("Somente", await service.handle(User("viewer"), "!setlang en"))
        self.assertEqual(service.language, "br")
        for argument in ["", "pt", "fr", "en br"]:
            self.assertIn("Uso:", await service.handle(owner, "!setlang " + argument))
        self.assertIn("English", await service.handle(owner, "!setlang EN"))
        self.assertEqual(await service.handle(User("viewer"), "!sr"), "Usage: !sr <song and artist>")
        self.assertIn("Only moderators", await service.handle(User("viewer"), "!setlang br"))
        self.assertEqual(service.language, "en")
        self.assertIn("português", await service.handle(
            User("mod", frozenset({"moderator"})), "!setlang br"))
        self.assertIn("Uso:", await service.handle(User("viewer"), "!sr"))

    async def test_english_playback_queue_skip_request_and_policy_errors(self):
        service = SongRequests(FakeSpotify(), self.queue, Policy(config(), clock=lambda: 100))
        owner = User("owner", frozenset({"broadcaster"}))
        await service.handle(owner, "!setlang en")
        viewer = User("viewer")
        self.assertIn("Nothing", await service.handle(viewer, "!np"))
        self.assertIn("queue is empty", await service.handle(viewer, "!queue"))
        self.assertIn("Only moderators", await service.handle(viewer, "!skip"))
        self.assertIn("skipped", await service.handle(owner, "!skip"))
        self.assertIn("Wait 5s", await service.handle(owner, "!skip"))
        service.spotify.playback_data = {"item": {"name": "Música original", "artists": [{"name": "Artista"}]},
                                        "is_playing": True}
        self.assertIn("Now playing: Música original - Artista", await service.handle(viewer, "!np"))
        self.assertEqual("Song - Artist added to the queue.", await service.handle(viewer, "!sr song"))
        self.assertIn("Wait 30s", await service.handle(viewer, "!sr song"))
        service.policy.config.blocked_users = frozenset({"blocked"})
        self.assertIn("blocked", await service.handle(User("blocked"), "!sr song"))

    async def test_english_spotify_and_oauth_errors(self):
        from dangerbot.oauth import OAuthError
        for error, expected in [
            (SpotifyError("Abra o Spotify e inicie a reprodução em um dispositivo."), "start playback"),
            (SpotifyError("Spotify limitou as requisições."), "rate limit"),
            (OAuthError("Execute: python main.py auth spotify"), "Authorization failed")]:
            service = SongRequests(FakeSpotify(error), self.queue, Policy(config()))
            await service.handle(User("owner", frozenset({"broadcaster"})), "!setlang en")
            self.assertIn(expected, await service.handle(User("viewer"), "!np"))

    async def test_failed_language_save_keeps_previous_language(self):
        import sqlite3
        from unittest.mock import patch
        service = SongRequests(FakeSpotify(), self.queue, Policy(config()))
        with patch.object(self.queue, "set_language", side_effect=sqlite3.OperationalError("locked")):
            reply = await service.handle(User("mod", frozenset({"moderator"})), "!setlang en")
        self.assertIn("Não foi possível salvar", reply)
        self.assertEqual(service.language, "br")
        self.assertEqual(self.queue.language(), "br")

    async def test_skip_permissions_and_cooldown(self):
        spotify = FakeSpotify()
        service = SongRequests(spotify, self.queue, Policy(config(), clock=lambda: 100))
        self.assertIn("Somente", await service.handle(User("viewer"), "!skip"))
        self.assertEqual(spotify.skips, 0)
        moderator = User("mod", frozenset({"moderator"}))
        self.assertIn("pulada", await service.handle(moderator, "!skip"))
        self.assertIn("Aguarde", await service.handle(moderator, "!skip"))
        self.assertEqual(spotify.skips, 1)

    async def test_skip_errors_do_not_confirm_or_retry(self):
        for error, expected in [(SpotifyError("Sem dispositivo"), "Sem dispositivo"),
                                (SpotifyError("timeout", uncertain=True), "confirmar")]:
            service = SongRequests(FakeSpotify(error), self.queue, Policy(config()))
            reply = await service.handle(User("owner", frozenset({"broadcaster"})), "!skip")
            self.assertIn(expected, reply)

    async def test_queue_reads_spotify_instead_of_local_outbox_and_paginates(self):
        service = SongRequests(FakeSpotify(), self.queue, Policy(config()))
        self.queue.add("viewer", TRACK)
        self.assertIn("vazia", await service.handle(User("viewer"), "!queue"))
        service.spotify.spotify_queue = [
            {"name": f"Spotify Song {index}", "artists": [{"name": "Artist"}]}
            for index in range(1, 8)]
        first = await service.handle(User("viewer"), "!queue")
        second = await service.handle(User("viewer"), "!queue 2")
        self.assertIn("1. Spotify Song 1", first)
        self.assertNotIn("Spotify Song 6", first)
        self.assertIn("6. Spotify Song 6", second)
        self.assertIn("2/2", second)
        self.assertIn("Página inválida", await service.handle(User("viewer"), "!queue 3"))
        for argument in ["0", "-1", "abc", "9" * 100]:
            self.assertIn("Uso:", await service.handle(User("viewer"), "!queue " + argument))

    async def test_np_handles_playing_paused_episode_and_no_playback(self):
        service = SongRequests(FakeSpotify(), self.queue, Policy(config()))
        self.assertIn("Nenhuma", await service.handle(User("viewer"), "!np"))
        service.spotify.playback_data = {"item": {"name": "Song", "artists": [{"name": "Artist"}]},
                                         "is_playing": True}
        self.assertIn("Tocando agora: Song - Artist", await service.handle(User("viewer"), "!np"))
        service.spotify.playback_data["is_playing"] = False
        self.assertIn("pausado", await service.handle(User("viewer"), "!np"))
        service.spotify.playback_data["item"] = {"name": "Episode", "show": {"name": "Podcast"}}
        self.assertIn("Episode - Podcast", await service.handle(User("viewer"), "!np"))
        service.spotify.playback_data = {"item": None}
        self.assertIn("Nenhuma", await service.handle(User("viewer"), "!np"))

    async def test_spotify_read_errors_are_returned_to_chat(self):
        service = SongRequests(FakeSpotify(SpotifyError("Spotify indisponível")),
                               self.queue, Policy(config()))
        for command in ["!np", "!queue"]:
            self.assertEqual(await service.handle(User("viewer"), command), "Spotify indisponível")

    async def test_playback_and_queue_endpoints_and_empty_playback(self):
        class Auth:
            async def access_token(self, force=False):
                return "token"
        calls = []
        responses = iter([httpx.Response(204),
                          httpx.Response(200, json={"queue": [{"name": "Remote song"}]}),
                          httpx.Response(200, json={"unexpected": []})])
        def transport(request):
            calls.append(request)
            return next(responses)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            spotify = Spotify(client, Auth())
            self.assertIsNone(await spotify.playback())
            self.assertEqual(await spotify.playback_queue(), [{"name": "Remote song"}])
            with self.assertRaises(SpotifyError):
                await spotify.playback_queue()
        self.assertEqual(calls[0].url.path, "/v1/me/player")
        self.assertEqual(calls[1].url.path, "/v1/me/player/queue")
        self.assertTrue(all(call.method == "GET" for call in calls))

    async def test_spotify_skip_uses_device_and_accepts_non_json_success(self):
        class Auth:
            async def access_token(self, force=False):
                return "token"
        for device in ["", "device1"]:
            calls = []
            def transport(request):
                calls.append(request)
                return httpx.Response(200, content=b"OK")
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                await Spotify(client, Auth(), device).skip()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].method, "POST")
            self.assertEqual(calls[0].url.path, "/v1/me/player/next")
            self.assertEqual(dict(calls[0].url.params), {"device_id": device} if device else {})

    async def test_request_dispatch_and_cooldown(self):
        spotify = FakeSpotify()
        service = SongRequests(spotify, self.queue, Policy(config(), clock=lambda: 100))
        self.assertEqual("Song - Artist adicionada à fila.", await service.handle(User("viewer"), "!sr song"))
        self.assertIn("Aguarde", await service.handle(User("viewer"), "!sr song"))
        await service.dispatch_once()
        self.assertEqual(spotify.sent, [TRACK.uri])
        self.assertEqual(self.queue.count(), 0)
        self.assertEqual(self.queue.db.execute("SELECT status FROM requests").fetchone()[0], "sent")

    async def test_permissions_and_blacklist_do_not_accept_request(self):
        for settings, user in [(config(permission="moderator"), User("viewer")),
                               (config(blocked_artists=frozenset({"artist1"})), User("viewer"))]:
            service = SongRequests(FakeSpotify(), self.queue, Policy(settings))
            await service.handle(user, "!sr song")
            self.assertEqual(self.queue.count(), 0)

    async def test_duplicate_and_full_queue(self):
        service = SongRequests(FakeSpotify(), self.queue,
                               Policy(config(user_cooldown=0, global_cooldown=0)))
        await service.handle(User("a"), "!sr song")
        self.assertIn("já", await service.handle(User("b"), "!sr song"))
        service.policy.config.max_pending = 1
        self.assertIn("cheia", await service.handle(User("b"), "!sr song"))

    async def test_failed_delivery_keeps_pending_and_uncertain_is_not_retried(self):
        for error, expected in [(SpotifyError("offline"), "pending"),
                                (SpotifyError("timeout", uncertain=True), "uncertain")]:
            self.queue.db.execute("DELETE FROM requests")
            self.queue.add("viewer", TRACK)
            service = SongRequests(FakeSpotify(error), self.queue, Policy(config()))
            await service.dispatch_once()
            self.assertEqual(self.queue.db.execute("SELECT status FROM requests").fetchone()[0], expected)

    async def test_oauth_refresh_preserves_refresh_token_and_persists(self):
        calls = []
        def transport(request):
            calls.append(request)
            return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {
                "SPOTIFY_CLIENT_ID": "test-client", "SPOTIFY_CLIENT_SECRET": "test-secret"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                oauth = OAuth("spotify", client, Path(directory))
                oauth.tokens = {"refresh_token": "old-refresh", "expires_at": 0}
                results = await asyncio.gather(oauth.access_token(), oauth.access_token())
                self.assertEqual(results, ["new-access", "new-access"])
                self.assertEqual(len(calls), 1)
                self.assertEqual(OAuth("spotify", client, Path(directory)).tokens["refresh_token"], "old-refresh")

    async def test_spotify_401_refresh_and_rate_limit(self):
        class Auth:
            def __init__(self):
                self.forced = []
            async def access_token(self, force=False):
                self.forced.append(force)
                return "token"
        auth = Auth()
        responses = iter([httpx.Response(401), httpx.Response(200, json={"tracks": {"items": []}}),
                          httpx.Response(429, headers={"Retry-After": "42"})])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(responses))) as client:
            spotify = Spotify(client, auth)
            self.assertIsNone(await spotify.search("song"))
            self.assertEqual(auth.forced, [False, True])
            with self.assertRaises(SpotifyError) as error:
                await spotify.enqueue(TRACK.uri)
            self.assertEqual(error.exception.retry_after, 42)

    async def test_queue_success_does_not_require_json_and_is_not_resent(self):
        class Auth:
            async def access_token(self, force=False):
                return "token"
        for status, body in [(204, b""), (200, b""), (200, b" "),
                             (200, b"OK"), (204, b"Added to queue")]:
            with self.subTest(status=status, body=body):
                calls = []
                def transport(request):
                    calls.append(request)
                    return httpx.Response(status, content=body)
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    self.queue.db.execute("DELETE FROM requests")
                    request_id = self.queue.add("viewer", TRACK)
                    service = SongRequests(Spotify(client, Auth()), self.queue, Policy(config()))
                    await service.dispatch_once()
                    await service.dispatch_once()
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0].method, "POST")
                    self.assertEqual(calls[0].url.params["uri"], TRACK.uri)
                    status_row = self.queue.db.execute(
                        "SELECT status FROM requests WHERE id=?", (request_id,)).fetchone()
                    self.assertEqual(status_row[0], "sent")

    async def test_invalid_search_response_returns_error_without_stopping_bot(self):
        class Auth:
            async def access_token(self, force=False):
                return "token"
        for body in [b"", b" ", b"<html>unavailable</html>"]:
            with self.subTest(body=body):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                        lambda _: httpx.Response(200, content=body))) as client:
                    service = SongRequests(Spotify(client, Auth()), self.queue, Policy(config()))
                    reply = await service.handle(User("viewer"), "!sr song")
                    self.assertIn("resposta inválida", reply)
                    self.assertEqual(self.queue.count(), 0)


class PersistenceTests(unittest.TestCase):
    def test_playback_requester_survives_restart(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite3"
            queue = RequestQueue(path)
            request_id = queue.add("viewer", TRACK)
            queue.update(request_id, "sent")
            playback = {"item": {"uri": TRACK.uri}, "progress_ms": 0}
            self.assertEqual(queue.track_playback(playback), "viewer")
            queue.close()
            queue = RequestQueue(path)
            self.assertEqual(queue.track_playback(playback), "viewer")
            self.assertEqual(queue.get(request_id)["status"], "playing")
            queue.close()
    def test_spotify_request_ownership_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite3"
            queue = RequestQueue(path)
            request_id = queue.add("viewer", TRACK)
            queue.update(request_id, "sent")
            queue.close()
            queue = RequestQueue(path)
            self.assertEqual(queue.user_slots("viewer", [{"uri": TRACK.uri}]), 1)
            self.assertEqual(queue.user_slots("other", [{"uri": TRACK.uri}]), 0)
            queue.close()
    def test_language_survives_restart_without_losing_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite3"
            queue = RequestQueue(path)
            request_id = queue.add("viewer", TRACK)
            queue.set_language("en")
            queue.close()
            queue = RequestQueue(path)
            service = SongRequests(FakeSpotify(), queue, Policy(config()))
            self.assertEqual(service.language, "en")
            self.assertEqual(queue.get(request_id)["status"], "pending")
            queue.close()

    def test_restart_quarantines_inflight_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite3"
            queue = RequestQueue(path)
            request_id = queue.add("viewer", TRACK)
            queue.update(request_id, "sending")
            queue.close()
            queue = RequestQueue(path)
            self.assertIsNone(queue.next())
            self.assertEqual(queue.db.execute("SELECT status FROM requests").fetchone()[0], "uncertain")
            queue.close()

    def test_twitch_roles_and_message(self):
        result = parse_message("@badges=broadcaster/1;mod=0 :j0ride!j@host PRIVMSG #j0ride :!sr Song")
        self.assertEqual(result[2], "!sr Song")
        self.assertIn("broadcaster", result[1].roles)
        self.assertIsNone(parse_message("PING :tmi.twitch.tv"))
