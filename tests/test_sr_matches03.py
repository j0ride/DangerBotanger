import unittest

import httpx

from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import SearchSuggestion, Spotify
from test_bot import config
from test_improvements import Auth, item


CASES = [
    ('classy teardrop', 'Tear Drop', 'CLASSy', 97.75),
    ('meovv ddi do ri', 'DDI RO RI', 'MEOVV', 96.20),
    ('kiikii 404 new era', '404 (New Era)', 'KiiiKiii', 99.00),
]


class ThirdRequestMatchingTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, query, candidates):
        calls = []
        def transport(request):
            calls.append(request)
            return httpx.Response(200, json={'tracks': {'items': candidates}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await Spotify(client, Auth()).search(query)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.host, 'api.spotify.com')
        self.assertEqual(calls[0].url.params['q'], query)
        return result

    async def test_feedback_examples_enter_queue_with_wrong_first_result(self):
        for query, title, artist, score in CASES:
            correct = item(title, artist)
            def transport(request):
                if request.url.path.endswith('/search'):
                    return httpx.Response(200, json={'tracks': {'items': [item('Unrelated Title', artist, 'wrong'), correct]}})
                if request.url.path.endswith('/queue'):
                    return httpx.Response(200, json={'queue': []})
                return httpx.Response(204)
            with self.subTest(query=query):
                self.assertAlmostEqual(Spotify.match_score(query, Spotify.parse_track(correct)) * 100, score)
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    queue = RequestQueue(':memory:')
                    try:
                        service = SongRequests(Spotify(client, Auth()), queue, Policy(config()))
                        self.assertIn('adicionada', await service.handle(User('viewer'), '!sr ' + query))
                        self.assertEqual(queue.next()['uri'], 'spotify:track:correct')
                        self.assertEqual(service.suggestions, {})
                    finally:
                        queue.close()

    async def test_spacing_and_contextual_typo_work_in_both_orders_and_with_artist_in_middle(self):
        cases = [
            ('teardrop classy', 'Tear Drop', 'CLASSy'),
            ('classy tear drop', 'Teardrop', 'CLASSy'),
            ('teardrop', 'Tear Drop', 'CLASSy'),
            ('tear classy drop', 'Teardrop', 'CLASSy'),
            ('classy teardrop live', 'Tear Drop - Live', 'CLASSy'),
            ('ddi do ri meovv', 'DDI RO RI', 'MEOVV'),
            ('ddi meovv do ri', 'DDI RO RI', 'MEOVV'),
        ]
        for query, title, artist in cases:
            with self.subTest(query=query):
                self.assertEqual((await self.search(query, [item(title, artist)])).id, 'correct')

    async def test_spacing_never_drops_words_or_merges_short_words_and_numbers(self):
        cases = [
            ('classy teardrop', 'Tear Drop Again', 'CLASSy'),
            ('classy teardrop', 'Tear Drop', 'Other Artist'),
            ('classy teardrop', 'Tear Drop - Live', 'CLASSy'),
            ('classy teardrop fakeword', 'Tear Drop', 'CLASSy'),
            ('classy teardrop teardrop', 'Tear Drop', 'CLASSy'),
            ('artist no where', 'Nowhere', 'Artist'),
            ('artist not here', 'Nothere', 'Artist'),
            ('artist a ha', 'Aha', 'Artist'),
            ('artist 40 4 new era', '404 (New Era)', 'Artist'),
        ]
        for query, title, artist in cases:
            with self.subTest(query=query):
                self.assertIsInstance(await self.search(query, [item(title, artist)]), SearchSuggestion)

    async def test_short_typo_requires_full_title_exact_anchors_and_strong_artist(self):
        cases = [
            ('ddi do ri', 'DDI RO RI', 'MEOVV'),
            ('meov ddi do ri', 'DDI RO RI', 'MEOVV'),
            ('other ddi do ri', 'DDI RO RI', 'MEOVV'),
            ('meovv ddo do ri', 'DDI RO RI', 'MEOVV'),
            ('meovv ddi do ra', 'DDI RO RI', 'MEOVV'),
            ('meovv ddi do ri', 'DDI RO RI Again', 'MEOVV'),
            ('meovv ddi do ri', 'DDI RO RI - Remix', 'MEOVV'),
            ('meovv do ri', 'RO RI', 'MEOVV'),
            ('meovv do', 'RO', 'MEOVV'),
            ('meovv i do now care', 'I Do Not Care', 'MEOVV'),
            ('meovv 403 new era', '404 (New Era)', 'MEOVV'),
            ('meovv ddi do ri', 'DDI RI', 'MEOVV'),
            ('txd ddi do ri', 'DDI RO RI', 'TOMORROW X TOGETHER'),
        ]
        for query, title, artist in cases:
            with self.subTest(query=query):
                self.assertIsInstance(await self.search(query, [item(title, artist)]), SearchSuggestion)

    async def test_exact_spacing_and_spelling_win_over_approximate_first_result(self):
        cases = [
            ('classy teardrop', [item('Tear Drop', 'CLASSy', 'approximate'), item('Teardrop', 'CLASSy')]),
            ('meovv ddi do ri', [item('DDI RO RI', 'MEOVV', 'approximate'), item('DDI DO RI', 'MEOVV')]),
        ]
        for query, candidates in cases:
            with self.subTest(query=query):
                self.assertEqual((await self.search(query, candidates)).id, 'correct')

    async def test_two_plausible_short_typos_require_confirmation_but_duplicate_releases_do_not(self):
        candidates = [item('DDI RO RI', 'MEOVV', 'first'), item('DDI PO RI', 'MEOVV', 'second')]
        result = await self.search('meovv ddi do ri', candidates)
        self.assertIsInstance(result, SearchSuggestion)
        self.assertEqual(result.track.id, 'first')
        duplicates = [item('DDI RO RI', 'MEOVV', 'first'), item('ddi ro ri', 'MEOVV', 'second')]
        self.assertEqual((await self.search('meovv ddi do ri', duplicates)).id, 'first')
