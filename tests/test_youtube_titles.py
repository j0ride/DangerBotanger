import unittest

import httpx

from dangerbot.music_titles import clean_video_title, parse_music_title
from dangerbot.spotify import SearchSuggestion, Spotify
from test_improvements import Auth, item


class TitleNormalizationTests(unittest.TestCase):
    def test_common_video_labels_and_repeated_annotations(self):
        cases = [
            ('Pitty - Na Sua Estante (Vídeo Clipe Oficial) [1080p]', 'Pitty - Na Sua Estante'),
            ('Slipknot - Snuff [Official Video HD]', 'Slipknot - Snuff'),
            ('Slipknot - Snuff (Official Video) (Lyrics) HQ', 'Slipknot - Snuff'),
            ('Artist - Song (Visualizer)', 'Artist - Song'),
            ('Artist - Song 【Official MV】 [4K 60FPS]', 'Artist - Song'),
            ('Artist - Song [M/V]', 'Artist - Song'),
            ('Artist - Song (Lyric Video)', 'Artist - Song'),
            ('Artist - Song (Letra/Tradução)', 'Artist - Song'),
            ('Artist - Song [Legendado em Português]', 'Artist - Song'),
            ('Artist - Song (Official Audio) **NEW**', 'Artist - Song'),
            ('Artist - Song (with lyrics) - HD', 'Artist - Song'),
            ('Artist - Song [Video Oficial]', 'Artist - Song'),
            ('Artist - Song (Official Music Video, 4K)', 'Artist - Song'),
            ('Artist\u200b – Song &amp; Me (Official Video)', 'Artist – Song & Me'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(clean_video_title(source), expected)

    def test_real_titles_and_version_information_are_preserved(self):
        for source in ['Artist - Lyrics', 'LSD - Audio', 'Artist - HD',
                       'Artist - Song (Live at Wembley)', 'Artist - Song [Remix]',
                       'Artist - Song (Acoustic Version)', 'Artist - Song (Cover)',
                       'Artist - Song (Slowed + Reverb)', 'Artist - Song (2009 Remaster)',
                       'Artist - Song [Anime OST]', 'Rush - Moving Pictures (Full Album)',
                       'Artist - 1979', 'Radiohead - No Surprises']:
            with self.subTest(source=source):
                self.assertEqual(clean_video_title(source), source)

    def test_artist_title_separators_and_quotes(self):
        for source in ['Jay-Z - Song', 'Jay-Z — Song', 'Jay-Z | Song', 'Jay-Z : Song',
                       'Jay-Z / Song', 'Jay-Z /// Song', 'Jay-Z _ Song',
                       'Jay-Z "Song"', 'Jay-Z “Song”', 'Song by Jay-Z', 'Song por Jay-Z']:
            with self.subTest(source=source):
                parsed = parse_music_title(source)
                self.assertEqual((parsed.artist, parsed.title), ('Jay-Z', 'Song'))
        self.assertEqual(parse_music_title('AC/DC - Back In Black').artist, 'AC/DC')
        self.assertIsNone(parse_music_title('musica triste do naruto'))


class YouTubeMatchingTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, title, candidates):
        calls = []
        def transport(request):
            calls.append(request)
            return httpx.Response(200, json={'tracks': {'items': candidates}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await Spotify(client, Auth()).search_youtube(title)
        self.assertEqual(len(calls), 1)
        return result, calls[0].url.params['q']

    async def test_common_music_titles_match_without_confirmation(self):
        cases = [
            ('Pitty - Na Sua Estante (Vídeo Clipe Oficial) [1080p]', 'Na Sua Estante', 'Pitty'),
            ('Slipknot "Snuff" [Official Video HD]', 'Snuff', 'Slipknot'),
            ('Snuff - Slipknot (Official Video)', 'Snuff', 'Slipknot'),
            ('AC/DC - Back In Black (Official Video)', 'Back In Black', 'AC/DC'),
            ('Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)',
             'Never Gonna Give You Up', 'Rick Astley'),
            ('PÍTTY — NA SUA ESTANTE! (Official Audio)', 'Na Sua Estante', 'Pitty'),
            ('Lady Gaga - Die With A Smile feat. Bruno Mars (Official Video)',
             'Die With A Smile', 'Lady Gaga'),
            ('Lady Gaga feat. Bruno Mars - Die With A Smile (Official Video)',
             'Die With A Smile', 'Lady Gaga'),
            ('Lady Gaga & Bruno Mars - Die With A Smile (Official Video)',
             'Die With A Smile', 'Lady Gaga'),
        ]
        for title, song, artist in cases:
            correct = item(song, artist)
            if 'Bruno Mars' in title:
                correct['artists'].append({'id': 'guest', 'name': 'Bruno Mars'})
            with self.subTest(title=title):
                result, query = await self.search(title, [item('Other Song', 'Other Artist', 'wrong'), correct])
                self.assertEqual(result.id, 'correct')
                self.assertNotIn('Official', query)

    async def test_featured_artists_in_spotify_title_are_also_matched(self):
        result, _ = await self.search('Artist - Song (feat. Guest) [Official Video]',
                                     [item('Song (feat. Guest)', 'Artist')])
        self.assertEqual(result.id, 'correct')

    async def test_wrong_artists_missing_guests_and_different_versions_need_confirmation(self):
        cases = [
            ('Queen - Somebody To Love', item('Somebody To Love', 'Queen Tribute Band')),
            ('Lady Gaga - Die With A Smile feat. Bruno Mars', item('Die With A Smile', 'Lady Gaga')),
            ('Artist - Song (Live)', item('Song', 'Artist')),
            ('Artist - Song', item('Song (Live)', 'Artist')),
            ('Artist - Song (Remix)', item('Song', 'Artist')),
            ('Artist - Song (Cover)', item('Song', 'Artist')),
            ('Artist - Song', item('Song About Something Else', 'Artist')),
            ('Artist - Song', item('Song', 'Other Artist')),
            ('musica triste do naruto', item('Sadness and Sorrow', 'Performer')),
        ]
        for title, candidate in cases:
            with self.subTest(title=title, candidate=candidate['name']):
                result, _ = await self.search(title, [candidate])
                self.assertIsInstance(result, SearchSuggestion)

    async def test_correct_version_is_preferred_over_wrong_first_result(self):
        result, _ = await self.search('Artist - Song (Live) [Official Video]',
                                     [item('Song', 'Artist', 'studio'), item('Song (Live)', 'Artist')])
        self.assertEqual(result.id, 'correct')

    async def test_ordinary_text_search_keeps_existing_matching(self):
        correct = item('Na Sua Estante', 'Pitty')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, json={'tracks': {'items': [correct]}}))) as client:
            self.assertEqual((await Spotify(client, Auth()).search('pitty na sua estantwe')).id, 'correct')
