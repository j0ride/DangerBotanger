import unittest

import httpx

from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import SearchSuggestion, Spotify
from test_bot import config
from test_improvements import Auth, item


class RequestTermMatchingTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, query, candidates):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={'tracks': {'items': candidates}}))) as client:
            return await Spotify(client, Auth()).search(query)

    async def test_examples_from_sr_matches_and_interleaved_terms_enter_queue(self):
        cases = [
            ('im not cute anymore ilit', 'NOT CUTE ANYMORE', 'ILLIT'),
            ("I'm not cute anymore ilit", 'NOT CUTE ANYMORE', 'ILLIT'),
            ('slayyyyyyer brand new chanels', 'brand new chanel$', 'Slayyyter'),
            ('breath prodigy', 'Breathe', 'The Prodigy'),
            ('cute ilit anymore not', 'NOT CUTE ANYMORE', 'ILLIT'),
            ('brand slayyyter chanel$ new', 'brand new chanel$', 'Slayyyter'),
        ]
        for query, title, artist in cases:
            correct = item(title, artist)
            def transport(request):
                if request.url.path.endswith('/search'):
                    return httpx.Response(200, json={'tracks': {'items': [item('Wrong Song', 'Other Artist', 'wrong'), correct]}})
                if request.url.path.endswith('/queue'):
                    return httpx.Response(200, json={'queue': []})
                return httpx.Response(204)
            with self.subTest(query=query):
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                    queue = RequestQueue(':memory:')
                    try:
                        service = SongRequests(Spotify(client, Auth()), queue, Policy(config()))
                        self.assertIn('adicionada', await service.handle(User('viewer'), '!sr ' + query))
                        self.assertEqual(queue.next()['uri'], 'spotify:track:correct')
                        self.assertEqual(service.suggestions, {})
                    finally:
                        queue.close()

    async def test_missing_terms_negation_wrong_artist_and_artist_only_still_need_confirmation(self):
        cases = [
            ('not cute anymore ilit', item('CUTE ANYMORE', 'ILLIT')),
            ('cute ilit anymore fakeword', item('CUTE ANYMORE', 'ILLIT')),
            ('brand new chanels slayyyyyyer', item('brand new chanel$', 'Other Artist')),
            ('breath prodigy', item('Breathe', 'Other Artist')),
            ('im not cute anymore fakeartist', item('NOT CUTE ANYMORE', 'ILLIT')),
            ('prodigy', item('Breathe', 'The Prodigy')),
            ('cute cute ilit', item('CUTE', 'ILLIT')),
            ('artist fakeword', item('Artist', 'Fakeword Tribute Band')),
        ]
        for query, candidate in cases:
            with self.subTest(query=query):
                self.assertIsInstance(await self.search(query, [candidate]), SearchSuggestion)

    async def test_original_order_and_exact_spelling_win_over_approximate_first_result(self):
        cases = [
            ('artist dog eats man', [item('Man Eats Dog', 'Artist', 'wrong'), item('Dog Eats Man', 'Artist')]),
            ('slayyyter brand new chanels', [item('brand new channels', 'Slayyyter', 'wrong'), item('brand new chanel$', 'Slayyyter')]),
        ]
        for query, candidates in cases:
            with self.subTest(query=query):
                self.assertEqual((await self.search(query, candidates)).id, 'correct')

    def test_coverage_uses_distinct_terms_and_preserves_styled_spellings(self):
        self.assertEqual(Spotify.term_score(['bye', 'bye', 'bye'], ['bye', 'bye']), 0)
        self.assertEqual(Spotify.term_score(['not', 'cute'], ['cute']), 0)
        self.assertGreaterEqual(Spotify.term_score(['anymore', 'cute', 'not'], ['not', 'cute', 'anymore']), 0.9)
        self.assertEqual(Spotify.words('brand new chanel$'), ['brand', 'new', 'chanels'])
        self.assertEqual(Spotify.words('Ke$ha'), ['kesha'])
        self.assertEqual(Spotify.words('$100'), ['100'])
