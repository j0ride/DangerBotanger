import unittest

import httpx

from dangerbot.queue import RequestQueue
from dangerbot.service import Policy, SongRequests, User
from dangerbot.spotify import SearchSuggestion, Spotify
from test_bot import config
from test_improvements import Auth, item


class MatchingNormalizationTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_punctuation_in_request_matches_compact_title_in_both_orders(self):
        cases = [
            ('artist a-ha', 'Aha'),
            ('a-ha artist', 'Aha'),
            ('artist a‐ha', 'Aha'),
            ('artist a‑ha!', 'Aha'),
            ("artist can't stop", 'Cant Stop'),
            ('can’t stop artist', 'Cant Stop'),
            ("artist don't stop", 'Dont Stop'),
            ('artist dont stop', "Don't Stop"),
            ('artist AC/DC', 'ACDC'),
            ('artist acdc', 'AC/DC'),
        ]
        for query, title in cases:
            with self.subTest(query=query, title=title):
                self.assertEqual((await self.search(query, [item(title, 'Artist')])).id, 'correct')

    async def test_short_title_fragments_still_suggest_first_result(self):
        cases = [
            ('artist song', 'Wrong Song', 'Artist'),
            ('song artist', 'Song For Someone Else', 'Artist'),
            ('song', 'Wrong Song', 'Artist'),
            ('cute anymore ilit', 'NOT CUTE ANYMORE', 'ILLIT'),
            ('artist love', 'No Love', 'Artist'),
        ]
        for query, title, artist in cases:
            with self.subTest(query=query, title=title):
                result = await self.search(query, [item(title, artist, 'first')])
                self.assertIsInstance(result, SearchSuggestion)
                self.assertEqual(result.track.id, 'first')

    async def test_sufficient_title_coverage_and_exact_result_still_match(self):
        self.assertEqual((await self.search('artist i see you again',
                         [item('Will I See You Again', 'Artist')])).id, 'correct')
        self.assertEqual((await self.search('artist song', [item('Wrong Song', 'Artist', 'wrong'),
                         item('Song', 'Artist')])).id, 'correct')

    async def test_normalization_keeps_spaces_numbers_artist_and_version_checks(self):
        cases = [
            ('artist a ha', 'Aha', 'Artist'),
            ('artist 3-4', '34', 'Artist'),
            ('artist 34', '3-4', 'Artist'),
            ('other a-ha', 'Aha', 'Artist'),
            ('artist a-ha live', 'Aha', 'Artist'),
            ('artist a-ha', 'Aha - Live', 'Artist'),
            ('artist a-ha fakeword', 'Aha', 'Artist'),
            ('artist a-ha aha', 'Aha', 'Artist'),
        ]
        for query, title, artist in cases:
            with self.subTest(query=query, title=title):
                self.assertIsInstance(await self.search(query, [item(title, artist)]), SearchSuggestion)
        self.assertEqual((await self.search('artist a-ha live',
                         [item('Aha - Live', 'Artist')])).id, 'correct')

    async def test_normalized_request_reaches_queue_without_confirmation(self):
        def transport(request):
            if request.url.path.endswith('/search'):
                return httpx.Response(200, json={'tracks': {'items': [item('Cant Stop Now', 'Artist', 'wrong'),
                                                                       item('Cant Stop', 'Artist')]}})
            if request.url.path.endswith('/queue'):
                return httpx.Response(200, json={'queue': []})
            return httpx.Response(204)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            queue = RequestQueue(':memory:')
            self.addCleanup(queue.close)
            service = SongRequests(Spotify(client, Auth()), queue, Policy(config()))
            self.assertIn('adicionada', await service.handle(User('viewer'), "!sr artist can't stop"))
            self.assertEqual(queue.next()['uri'], 'spotify:track:correct')
            self.assertEqual(service.suggestions, {})

    async def test_title_fragment_does_not_enqueue_or_consume_cooldown(self):
        def transport(request):
            if request.url.path.endswith('/search'):
                return httpx.Response(200, json={'tracks': {'items': [item('Wrong Song', 'Artist')]}})
            if request.url.path.endswith('/queue') and request.method == 'GET':
                return httpx.Response(200, json={'queue': []})
            if request.url.path.endswith('/player') and request.method == 'GET':
                return httpx.Response(204)
            self.fail('An uncertain search must not enqueue.')
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            queue = RequestQueue(':memory:')
            self.addCleanup(queue.close)
            policy = Policy(config())
            service = SongRequests(Spotify(client, Auth()), queue, policy)
            self.assertIn('!sim', await service.handle(User('viewer'), '!sr artist song'))
            self.assertEqual(queue.count(), 0)
            self.assertEqual(policy.users, {})
            self.assertEqual(policy.global_until, 0)
