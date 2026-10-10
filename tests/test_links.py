import unittest
import httpx

from dangerbot.i18n import MessageError
from dangerbot.links import clean_video_title, parse_link, spotify_short_track, youtube_title
from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import Spotify, SpotifyError
from test_bot import config
from test_improvements import Auth, item


VIDEO = "dQw4w9WgXcQ"
ID = "4uLU6hMCjMI75M1A2tKUQC"
LINK = "https://open.spotify.com/track/" + ID


class LinkParsingTests(unittest.TestCase):
    def test_youtube_formats_keep_only_video_id(self):
        for link in [f"https://www.youtube.com/watch?v={VIDEO}&list=ignored&t=30",
                     f"https://youtu.be/{VIDEO}?si=ignored", f"youtu.be/{VIDEO}",
                     f"https://music.youtube.com/watch?v={VIDEO}",
                     *[f"https://youtube.com/{kind}/{VIDEO}" for kind in ["shorts", "live", "embed"]]]:
            with self.subTest(link=link):
                self.assertEqual(parse_link(link), ("youtube", VIDEO))

    def test_spotify_formats_and_text(self):
        for link in [LINK, LINK + "?si=ignored", LINK.replace("/track/", "/intl-pt/track/"),
                     "spotify:track:" + ID]:
            self.assertEqual(parse_link(link), ("spotify", ID))
        self.assertIsNone(parse_link("Slipknot - Snuff"))
        self.assertEqual(parse_link("https://spotify.link/abc123?si=ignored"),
                         ("spotify_short", "https://spotify.link/abc123"))

    def test_invalid_links_are_rejected(self):
        for link in ["https://youtube.com/playlist?list=abc", "https://youtu.be/invalid",
                     "https://youtube.com/watch?v=" + VIDEO + "/extra", LINK + "/extra",
                     LINK.replace("track", "album"), LINK.replace("track", "playlist"),
                     "spotify:album:" + ID, "https://example.com/track/" + ID,
                     "https://open.spotify.com.evil.com/track/" + ID,
                     "https://open.spotify.com@127.0.0.1/track/" + ID,
                     "https://open.spotify.com:123/track/" + ID, "file:///etc/passwd"]:
            with self.subTest(link=link), self.assertRaises(MessageError):
                parse_link(link)

    def test_cleaning_preserves_musical_versions(self):
        for suffix in ["(Official Music Video)", "[Official Audio]", "- Lyrics", "(Vídeo Oficial)"]:
            self.assertEqual(clean_video_title("Slipknot - Snuff " + suffix), "Slipknot - Snuff")
        for suffix in ["(Live)", "(Remix)", "(Cover)", "(Acoustic)"]:
            self.assertEqual(clean_video_title("Artist - Song " + suffix), "Artist - Song " + suffix)
        self.assertEqual(clean_video_title("Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)"),
                         "Rick Astley - Never Gonna Give You Up")


class LinkRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = RequestQueue(":memory:")
        self.addCleanup(self.queue.close)
        self.calls = []
        self.payload = item("Snuff", "Slipknot", ID)
        self.youtube_payload = {"title": "Slipknot - Snuff (Official Music Video)"}
        self.track_status = 200
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.transport))
        self.addAsyncCleanup(self.client.aclose)
        self.spotify = Spotify(self.client, Auth())
        self.policy = Policy(config())
        self.service = SongRequests(self.spotify, self.queue, self.policy)
        self.viewer = User("viewer")

    def transport(self, request):
        self.calls.append(request)
        if request.url.host == "www.youtube.com":
            self.assertNotIn("authorization", request.headers)
            self.assertEqual(request.url.params["url"], "https://www.youtube.com/watch?v=" + VIDEO)
            return httpx.Response(200, json=self.youtube_payload)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"tracks": {"items": [self.payload]}})
        if "/tracks/" in request.url.path:
            return httpx.Response(self.track_status, json=self.payload)
        if request.url.path.endswith("/queue"):
            return httpx.Response(200, json={"queue": []})
        return httpx.Response(204)

    async def test_youtube_title_uses_current_search_and_enqueues_spotify_track(self):
        reply = await self.service.handle(self.viewer, "!sr https://youtu.be/" + VIDEO)
        self.assertIn("adicionada", reply)
        search = next(r for r in self.calls if r.url.path.endswith("/search"))
        self.assertEqual(search.url.params["q"], "Slipknot Snuff")
        self.assertEqual(self.queue.next()["uri"], "spotify:track:" + ID)
        self.assertIn("viewer", self.policy.users)

    async def test_uncertain_youtube_match_still_needs_confirmation(self):
        self.youtube_payload = {"title": "musica triste do naruto"}
        reply = await self.service.handle(self.viewer, "!sr https://youtu.be/" + VIDEO)
        self.assertIn("!sr confirmar", reply)
        self.assertEqual(self.queue.count(), 0)
        self.assertEqual(self.policy.users, {})
        self.assertIn("adicionada", await self.service.handle(self.viewer, "!sr confirmar"))
        self.assertEqual(self.queue.count(), 1)

    async def test_spotify_links_select_exact_track_without_search(self):
        reply = await self.service.handle(self.viewer, "!sr " + LINK + "?si=" + "a" * 250)
        self.assertIn("adicionada", reply)
        self.assertFalse(any(r.url.path.endswith("/search") for r in self.calls))
        self.assertEqual(self.calls[0].url.path, "/v1/tracks/" + ID)
        self.assertEqual(self.queue.next()["uri"], "spotify:track:" + ID)
        self.assertIn("Aguarde", await self.service.handle(self.viewer, "!sr " + LINK))
        self.assertEqual(len(self.calls), 3)

    async def test_track_policy_duplicate_and_user_limits_apply_to_links(self):
        cases = [(config(blocked_artists=frozenset({"slipknot"})), "bloqueado"),
                 (config(blocked_tracks=frozenset({ID.casefold()})), "bloqueada"),
                 (config(max_duration=1), "duração"), (config(max_user_requests=0), "pedidos")]
        for settings, expected in cases:
            self.service.policy = Policy(settings)
            reply = await self.service.handle(self.viewer, "!sr " + LINK)
            self.assertIn(expected, reply)
            self.assertEqual(self.queue.count(), 0)
            self.assertEqual(self.service.policy.users, {})
        self.service.policy = Policy(config())
        self.queue.add("other", await self.spotify.track(ID))
        self.assertIn("já está", await self.service.handle(self.viewer, "!sr " + LINK))

    async def test_invalid_links_and_blocked_users_make_no_requests(self):
        self.assertIn("Playlists", await self.service.handle(self.viewer, "!sr " + LINK.replace("track", "album")))
        self.policy.config.blocked_users = frozenset({"viewer"})
        self.assertIn("bloqueado", await self.service.handle(self.viewer, "!sr https://youtu.be/" + VIDEO))
        self.assertEqual(self.calls, [])

    async def test_missing_unplayable_and_malformed_spotify_track(self):
        self.track_status = 404
        for language, expected in [("br", "não foi encontrada"), ("en", "not found")]:
            self.service.language = language
            self.assertIn(expected, await self.service.handle(self.viewer, "!sr " + LINK))
        self.track_status = 200
        self.payload["is_playable"] = False
        self.assertIn("unavailable", await self.service.handle(self.viewer, "!sr " + LINK))
        self.payload = {"duration_ms": "bad"}
        with self.assertRaises(SpotifyError):
            await self.spotify.track(ID)
        self.assertEqual(self.queue.count(), 0)
        self.assertEqual(self.policy.users, {})

    async def test_youtube_unavailable_responses_are_actionable(self):
        for response in [httpx.Response(404), httpx.Response(500), httpx.Response(200, content="bad"),
                         httpx.Response(200, json={}), httpx.Response(200, json={"title": 42})]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
                with self.assertRaises(MessageError) as caught:
                    await youtube_title(client, VIDEO)
                self.assertEqual(caught.exception.key, "sr_youtube_unavailable")
        def fail(request):
            raise httpx.ConnectError("offline", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(MessageError):
                await youtube_title(client, VIDEO)

    async def test_short_spotify_link_resolves_only_recognized_hosts(self):
        for target, accepted in [(LINK, True), ("http://127.0.0.1/secrets", False),
                                 ("https://example.com/", False), ("https://youtu.be/" + VIDEO, False),
                                 (LINK.replace("track", "playlist"), False), ("https://spotify.link/abc", False)]:
            calls = []
            def redirect(request):
                calls.append(request)
                self.assertEqual(request.url.host, "spotify.link")
                self.assertNotIn("authorization", request.headers)
                return httpx.Response(302, headers={"location": target})
            async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
                if accepted:
                    self.assertEqual(await spotify_short_track(client, "https://spotify.link/abc"), ID)
                else:
                    with self.assertRaises(MessageError):
                        await spotify_short_track(client, "https://spotify.link/abc")
            self.assertLessEqual(len(calls), 4)
