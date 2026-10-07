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
        "user_cooldown": 60, "global_cooldown": 5, "permission": "everyone",
        "blocked_users": frozenset(), "blocked_tracks": frozenset(),
        "blocked_artists": frozenset(), "allow_explicit": True,
        "max_duration": 600, "max_pending": 30, **overrides})


TRACK = Track("track1", "spotify:track:track1", "Song", ("Artist",), ("artist1",), 180000, False)


class FakeSpotify:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def search(self, query):
        return TRACK

    async def enqueue(self, uri):
        if self.error:
            raise self.error
        self.sent.append(uri)


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = RequestQueue(":memory:")

    async def asyncTearDown(self):
        self.queue.close()

    async def test_request_dispatch_and_cooldown(self):
        spotify = FakeSpotify()
        service = SongRequests(spotify, self.queue, Policy(config(), clock=lambda: 100))
        self.assertIn("recebido", await service.handle(User("viewer"), "!sr song"))
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
