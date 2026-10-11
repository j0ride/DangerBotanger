import unittest

import httpx

from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import SearchSuggestion, Spotify
from test_bot import config
from test_improvements import Auth, item


JAPANESE_TITLE = 'セツナハナビ (Setsuna Hanabi)'
TXT = 'TOMORROW X TOGETHER'


class SecondRequestMatchingTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, query, candidates, *, youtube=False):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={'tracks': {'items': candidates}}))) as client:
            spotify = Spotify(client, Auth())
            return await (spotify.search_youtube(query) if youtube else spotify.search(query))

    async def test_feedback_examples_enter_queue_with_wrong_first_result(self):
        cases = [
            ('hitgs - aha', item('A-HA!', 'HITGS')),
            ('txt setsuna hanabi', item(JAPANESE_TITLE, TXT)),
        ]
        for query, correct in cases:
            def transport(request):
                if request.url.path.endswith('/search'):
                    return httpx.Response(200, json={'tracks': {'items': [item('Wrong Song', 'Other Artist', 'wrong'), correct]}})
                if request.url.path.endswith('/queue'):
                    return httpx.Response(200, json={'queue': []})
                return httpx.Response(204)
            with self.subTest(query=query):
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    queue = RequestQueue(':memory:')
                    self.addCleanup(queue.close)
                    service = SongRequests(Spotify(client, Auth()), queue, Policy(config()))
                    self.assertIn('adicionada', await service.handle(User('viewer'), '!sr ' + query))
                    self.assertEqual(queue.next()['uri'], 'spotify:track:correct')
                    self.assertEqual(service.suggestions, {})

    async def test_alternative_spellings_and_initialisms_match_in_either_order(self):
        cases = [
            ('aha hitgs', 'A-HA!', 'HITGS'),
            ('aha', 'A-HA!', 'HITGS'),
            ('a-ha hitgs', 'A-HA!', 'HITGS'),
            ('hitgs aha', 'A‑HA!', 'HITGS'),
            ('setsuna hanabi', JAPANESE_TITLE, TXT),
            ('setsuna hanabi txt', JAPANESE_TITLE, TXT),
            ('setsuna txt hanabi', JAPANESE_TITLE, TXT),
            ('txt セツナハナビ', JAPANESE_TITLE, TXT),
            ('txt setsuna hanabi', 'Setsuna Hanabi (セツナハナビ)', TXT),
            ('txt setsuna hanabi', 'セツナハナビ [Setsuna Hanabi]', TXT),
            ('ratm killing in the name', 'Killing In The Name', 'Rage Against The Machine'),
            ('bmth throne', 'Throne', 'Bring Me The Horizon'),
            ('artist dont stop', "Don't Stop", 'Artist'),
        ]
        for query, title, artist in cases:
            with self.subTest(query=query, title=title):
                self.assertEqual((await self.search(query, [item(title, artist)])).id, 'correct')

    async def test_wrong_artists_unknown_aliases_missing_terms_and_versions_need_confirmation(self):
        cases = [
            ('hitgs aha', item('A-HA!', 'Other Artist')),
            ('txt setsuna hanabi', item('Setsuna Hanabi', 'Other Artist')),
            ('txd setsuna hanabi', item(JAPANESE_TITLE, TXT)),
            ('ratn killing in the name', item('Killing In The Name', 'Rage Against The Machine')),
            ('ts cruel summer', item('Cruel Summer', 'Taylor Swift')),
            ('txt setsuna hanabi fakeword', item(JAPANESE_TITLE, TXT)),
            ('txt setsuna hanabi', item(JAPANESE_TITLE + ' - Live', TXT)),
            ('txt setsuna hanabi live', item(JAPANESE_TITLE, TXT)),
            ('txt setsuna hanabi', item('セツナハナビ (Setsuna Hanabi Remix)', TXT)),
            ('artist a ha', item('Aha', 'Artist')),
            ('artist 34', item('3-4', 'Artist')),
        ]
        for query, candidate in cases:
            with self.subTest(query=query, title=candidate['name']):
                self.assertIsInstance(await self.search(query, [candidate]), SearchSuggestion)

    async def test_explicitly_requested_version_matches_and_original_beats_live(self):
        live = item(JAPANESE_TITLE + ' - Live', TXT, 'live')
        studio = item(JAPANESE_TITLE, TXT)
        self.assertEqual((await self.search('txt setsuna hanabi', [live, studio])).id, 'correct')
        self.assertEqual((await self.search('txt setsuna hanabi live', [studio, live])).id, 'live')

    async def test_youtube_uses_the_same_title_spellings_and_artist_aliases(self):
        cases = [('HITGS - AHA (Official Music Video)', item('A-HA!', 'HITGS')),
                 ('TXT - Setsuna Hanabi (Official Audio)', item(JAPANESE_TITLE, TXT))]
        for query, candidate in cases:
            with self.subTest(query=query):
                self.assertEqual((await self.search(query, [candidate], youtube=True)).id, 'correct')

    def test_latin_subtitles_and_unknown_annotations_are_not_discarded(self):
        self.assertEqual(Spotify.title_words('Song (Different Song)'), [['song', 'different', 'song']])
        self.assertEqual(Spotify.title_words('セツナハナビ (Remix)'), [Spotify.words('セツナハナビ (Remix)')])
