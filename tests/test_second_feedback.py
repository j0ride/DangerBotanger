import unittest
from unittest.mock import AsyncMock

import httpx

from dangerbot.queue import RequestQueue
from dangerbot.service import COMMANDS, Policy, SongRequests, User
from dangerbot.spotify import SearchSuggestion, Spotify
from test_bot import TRACK, config
from test_improvements import Auth, item


class FeedbackSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_reported_requests_are_accepted_without_confirmation(self):
        cases = [
            ('purplekiss 7heaven', '7HEAVEN', ['PURPLE KISS']),
            ('chungha california dream', 'California Dream', ['CHUNG HA']),
            ('rescene heart droo', 'Heart Drop', ['RESCENE']),
            ('soyeon, seulgi, sinb e chungha wow thing', 'Wow Thing', ['SEULGI', 'CHUNG HA', 'SinB', 'SOYEON']),
            ('wow thing chungha e sinb, soyeon, seulgi', 'Wow Thing', ['SEULGI', 'CHUNG HA', 'SinB', 'SOYEON']),
        ]
        for query, title, artists in cases:
            correct = item(title, artists[0])
            correct['artists'] = [{'id': 'artist' + str(i), 'name': name} for i, name in enumerate(artists)]
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
                        reply = await service.handle(User('viewer'), '!sr ' + query)
                        self.assertIn('adicionada', reply)
                        self.assertEqual(queue.next()['uri'], 'spotify:track:correct')
                        self.assertEqual(service.suggestions, {})
                    finally:
                        queue.close()

    async def test_unrelated_matches_missing_artists_and_short_word_typos_stay_uncertain(self):
        cases = [
            ('rescene heart droo', item('Heart Drop', 'Other Artist')),
            ('soyeon seulgi sinb e chungha wow thing', item('Wow Thing', 'SEULGI')),
            ('artist cat', item('Car', 'Artist')),
            ('artist completely different song', item('Song', 'Artist')),
        ]
        for query, candidate in cases:
            with self.subTest(query=query):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                        200, json={'tracks': {'items': [candidate]}}))) as client:
                    self.assertIsInstance(await Spotify(client, Auth()).search(query), SearchSuggestion)

    def test_typo_tolerance_is_limited_to_one_edit_or_adjacent_transposition(self):
        for left, right in [('droo', 'drop'), ('dorp', 'drop'), ('estantwe', 'estante'), ('snuf', 'snuff')]:
            self.assertGreaterEqual(Spotify.word_score(left, right), 0.9)
        for left, right in [('cat', 'car'), ('snuff', 'stuffed'), ('artist', 'arxistt'), ('', 'song')]:
            self.assertEqual(Spotify.word_score(left, right), 0)


class FeedbackCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = [100]
        self.queue = RequestQueue(':memory:')
        self.addCleanup(self.queue.close)
        self.spotify = AsyncMock()
        self.spotify.search.return_value = SearchSuggestion(TRACK)
        self.spotify.queue_snapshot.return_value = {'queue': []}
        self.spotify.playback.return_value = None
        self.service = SongRequests(self.spotify, self.queue, Policy(config(), clock=lambda: self.now[0]))
        self.viewer = User('viewer')

    async def test_sim_confirms_only_its_requester_without_new_search(self):
        reply = await self.service.handle(self.viewer, '!sr description')
        self.assertIn('!sim', reply)
        self.assertEqual(await self.service.handle(User('other'), '!sim'), 'Não te perguntei nada')
        self.spotify.search.reset_mock()
        self.assertIn('adicionada', await self.service.handle(User('VIEWER'), '!sim'))
        self.spotify.search.assert_not_awaited()
        self.assertEqual(self.queue.count(), 1)
        self.assertEqual(await self.service.handle(self.viewer, '!sim'), 'Não te perguntei nada')

    async def test_sim_without_question_expiration_and_new_request(self):
        self.assertEqual(await self.service.handle(self.viewer, '!sim'), 'Não te perguntei nada')
        await self.service.handle(self.viewer, '!sr description')
        self.now[0] = 160
        self.assertEqual(await self.service.handle(self.viewer, '!sim'), 'Não te perguntei nada')
        await self.service.handle(self.viewer, '!sr description')
        self.spotify.search.return_value = None
        await self.service.handle(self.viewer, '!sr missing song')
        self.assertEqual(await self.service.handle(self.viewer, '!sim'), 'Não te perguntei nada')
        self.service.language = 'en'
        self.assertEqual(await self.service.handle(self.viewer, '!sim'), "I didn't ask you anything")

    async def test_sim_rechecks_rules_and_rejects_arguments(self):
        await self.service.handle(self.viewer, '!sr description')
        self.assertIn('Uso: !sim', await self.service.handle(self.viewer, '!sim extra'))
        self.service.policy.config.blocked_tracks = frozenset({TRACK.id})
        self.assertIn('bloqueada', await self.service.handle(self.viewer, '!sim'))
        self.assertEqual(self.queue.count(), 0)
        self.assertEqual(self.service.policy.users, {})
        self.service.policy.config.blocked_tracks = frozenset()
        self.service.policy.global_until = 105
        self.assertIn('Aguarde 5s', await self.service.handle(self.viewer, '!sim'))
        self.service.policy.global_until = 0
        self.spotify.queue_snapshot.return_value = {'queue': [{'uri': TRACK.uri}]}
        self.assertIn('já está', await self.service.handle(self.viewer, '!sim'))

    async def test_fila_alias_pagination_and_help_are_available_in_both_languages(self):
        self.assertTrue({'!fila', '!sim'} <= COMMANDS)
        self.spotify.playback_queue.return_value = [item('Song ' + str(i), 'Artist') for i in range(8)]
        for language in ['br', 'en']:
            self.service.language = language
            for argument in ['', ' 2', ' 0', ' nonsense']:
                self.assertEqual(await self.service.handle(self.viewer, '!fila' + argument),
                                 await self.service.handle(self.viewer, '!queue' + argument))
            reply = await self.service.handle(self.viewer, '!help')
            self.assertIn('!sim', reply)
            self.assertIn('!fila', reply)
            self.assertLessEqual(len(reply), 350)
