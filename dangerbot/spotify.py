from dataclasses import dataclass
from json import JSONDecodeError
import re
import unicodedata
import httpx
from .oauth import OAuth
from .i18n import MessageError, translate
from .music_titles import COLLABORATION, MusicTitle, clean_video_title, parse_music_title, song_and_guests, version_words


class SpotifyError(Exception):
    def __init__(self, message, retry_after=0, uncertain=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.uncertain = uncertain


@dataclass(frozen=True)
class Track:
    id: str
    uri: str
    name: str
    artists: tuple[str, ...]
    artist_ids: tuple[str, ...]
    duration_ms: int
    explicit: bool

    @property
    def label(self):
        return self.name + " - " + ", ".join(self.artists)


@dataclass(frozen=True)
class SearchSuggestion:
    """Spotify's first playable result, awaiting explicit viewer confirmation."""
    track: Track


class Spotify:
    def __init__(self, client, oauth: OAuth, device_id=""):
        self.client, self.oauth, self.device_id = client, oauth, device_id

    async def request(self, method, path, *, expect_json=True, allow_empty=False, not_found_key=None, **kwargs):
        for attempt in range(2):
            token = await self.oauth.access_token(force=attempt == 1)
            try:
                response = await self.client.request(method, "https://api.spotify.com/v1/" + path,
                    headers={"Authorization": "Bearer " + token}, **kwargs)
            except httpx.HTTPError:
                raise SpotifyError("Falha de rede no Spotify.", uncertain=method == "POST") from None
            if response.status_code == 401 and attempt == 0:
                continue
            if response.is_success:
                if not expect_json:
                    return None
                if allow_empty and response.status_code == 204:
                    return None
                try:
                    return response.json()
                except (JSONDecodeError, UnicodeDecodeError):
                    raise SpotifyError("Spotify retornou uma resposta inválida. Tente novamente.",
                                       uncertain=method == "POST") from None
            if response.status_code == 429:
                raise SpotifyError("Spotify limitou as requisições.",
                                   retry_after=int(response.headers.get("Retry-After", "30")))
            if response.status_code == 404 and not_found_key:
                raise MessageError(not_found_key)
            messages = {401: "Autorize o Spotify novamente.", 403: "Spotify recusou acesso: verifique Premium e permissões.",
                        404: "Abra o Spotify e inicie a reprodução em um dispositivo."}
            raise SpotifyError(messages.get(response.status_code, "Spotify indisponível."),
                               uncertain=method == "POST" and response.status_code >= 500)

    async def search_candidates(self, query):
        data = await self.request("GET", "search", params={"q": query, "type": "track", "limit": 10})
        if not isinstance(data, dict) or not isinstance(data.get("tracks"), dict):
            raise SpotifyError("Spotify retornou uma resposta inválida. Tente novamente.")
        items = data["tracks"].get("items")
        if not isinstance(items, list):
            raise SpotifyError("Spotify retornou uma resposta inválida. Tente novamente.")
        candidates = []
        try:
            for item in items:
                if item.get("is_playable") is False or item.get("is_local"):
                    continue
                track = self.parse_track(item)
                candidates.append(track)
        except (KeyError, TypeError, AttributeError):
            raise SpotifyError("Spotify retornou uma resposta inválida. Tente novamente.") from None
        return candidates

    async def search(self, query):
        candidates = await self.search_candidates(query)
        if not candidates:
            return None
        score, track = max(((self.match_score(query, track), track) for track in candidates),
                           key=lambda candidate: candidate[0])
        return track if score >= 0.90 else SearchSuggestion(candidates[0])

    async def search_youtube(self, video_title):
        query = clean_video_title(video_title)
        parsed = parse_music_title(query)
        if parsed is None:
            return await self.search(query)
        # Keep both artist and title in the search, including featured guests.
        song, guests = song_and_guests(parsed.title)
        artist = COLLABORATION.sub(" & ", parsed.artist)
        query = artist + " " + song + (" " + guests if guests else "")
        candidates = await self.search_candidates(query)
        if not candidates:
            return None
        reversed_title = MusicTitle(parsed.title, parsed.artist)
        scored = [(max(self.youtube_match_score(parsed, track),
                       self.youtube_match_score(reversed_title, track)), track) for track in candidates]
        score, track = max(scored, key=lambda candidate: candidate[0])
        return track if score >= 0.90 else SearchSuggestion(candidates[0])

    @classmethod
    def artist_score(cls, artist, names):
        artist = COLLABORATION.sub(" & ", artist)
        words = cls.words(artist)
        name_words = [cls.words(name) for name in names if name]
        compact_names = ["".join(words) for words in name_words]
        compact_names.extend("".join(words[1:]) for words in name_words
                             if len(words) > 1 and words[0] == "the")
        # Initialisms must match exactly; never guess a typo in a short alias.
        # Use the returned artist's full name, rather than a global alias lookup.
        initialisms = {"".join(word[0] for word in words) for words in name_words if len(words) >= 3}
        direct = max(float("".join(words) in initialisms),
                     max((cls.word_score("".join(words), name) for name in compact_names), default=0))
        # Cover every requested artist, in any order. Spaces within a name may differ.
        words = [word for index, word in enumerate(words)
                 if not (0 < index < len(words) - 1 and word in {"e", "and", "feat", "ft", "featuring", "x"})]
        if not words:
            return direct
        scores = {0: 1.0}
        chunk_limit = max((len(cls.words(name)) for name in names), default=1) + 1
        for start in range(len(words)):
            if start not in scores:
                continue
            for end in range(start + 1, min(len(words), start + chunk_limit) + 1):
                chunk = "".join(words[start:end])
                score = max(float(chunk in initialisms),
                            max((cls.word_score(chunk, name) for name in compact_names), default=0))
                if score:
                    scores[end] = max(scores.get(end, 0), min(scores[start], score))
        return max(direct, scores.get(len(words), 0))

    @classmethod
    def youtube_match_score(cls, parsed, track):
        song, guests = song_and_guests(parsed.title)
        candidate, candidate_guests = song_and_guests(track.name)
        if version_words(song) != version_words(candidate):
            return 0
        # Punctuation and printed romanizations are alternative title spellings.
        title_score = max((cls.phrase_score(title_words, candidate_words)
                           for title_words in cls.title_words(song)
                           for candidate_words in cls.title_words(candidate)
                           if title_words and candidate_words
                           and min(len(title_words), len(candidate_words)) / max(len(title_words), len(candidate_words)) >= 0.8), default=0)
        artist_score = cls.artist_score(parsed.artist, track.artists)
        guest_score = cls.artist_score(guests, (*track.artists, candidate_guests)) if guests else 1
        return min(title_score, artist_score, guest_score)

    @staticmethod
    def parse_track(item):
        try:
            track = Track(item["id"], item["uri"], item["name"],
                          tuple(a["name"] for a in item["artists"]),
                          tuple(a["id"] for a in item["artists"]), item["duration_ms"], item["explicit"])
            if (not all(isinstance(value, str) and value for value in
                        (track.id, track.uri, track.name, *track.artists, *track.artist_ids))
                    or not track.artists or not isinstance(track.duration_ms, int)
                    or isinstance(track.duration_ms, bool) or track.duration_ms < 0
                    or not isinstance(track.explicit, bool)):
                raise ValueError()
            return track
        except (KeyError, TypeError, AttributeError, ValueError):
            raise SpotifyError("Spotify retornou uma resposta inválida. Tente novamente.") from None

    async def track(self, identifier):
        data = await self.request("GET", "tracks/" + identifier, not_found_key="sr_track_unavailable")
        track = self.parse_track(data)
        if data.get("is_playable") is False or data.get("is_local"):
            raise MessageError("sr_track_unavailable")
        return track

    @staticmethod
    def words(text):
        normalized = unicodedata.normalize("NFKD", text.casefold())
        normalized = re.sub(r"\bi['’]m\b", "im", normalized)
        normalized = re.sub(r"(?<=[^\W\d_])\$|\$(?=[^\W\d_])", "s", normalized)
        return re.findall(r"[^\W_]+", "".join(c for c in normalized if not unicodedata.combining(c)))

    @classmethod
    def word_variants(cls, text):
        # Join punctuation within a name: A-HA -> AHA, AC/DC -> ACDC.
        # Spaces and numeric separators remain significant.
        joined = re.sub(r"(?<=[^\W\d_])[-‐‑–—/.'’](?=[^\W\d_])", "", text)
        return [list(words) for words in dict.fromkeys(tuple(cls.words(variant))
                                                      for variant in (text, joined))]

    @classmethod
    def title_words(cls, text):
        """Keep the title and alternatives explicitly supported by its spelling."""
        variants = [text]
        bilingual = re.fullmatch(r"([^()\[\]]+?)\s*(?:\(([^()]*)\)|\[([^\[\]]*)\])(.*)", text)
        if bilingual:
            original, parenthesized, bracketed, suffix = bilingual.groups()
            alternative = parenthesized if parenthesized is not None else bracketed
            def latin_only(value):
                letters = [char for char in value if char.isalpha()]
                return bool(letters) and all("LATIN" in unicodedata.name(char, "") for char in letters)
            def non_latin_only(value):
                letters = [char for char in value if char.isalpha()]
                return bool(letters) and all("LATIN" not in unicodedata.name(char, "") for char in letters)
            if (version_words(original) == version_words(alternative)
                    and not COLLABORATION.search(original + " " + alternative)
                    and ((non_latin_only(original) and latin_only(alternative))
                         or (latin_only(original) and non_latin_only(alternative)))):
                # Preserve both spellings and every suffix, including live/remix.
                variants.extend((original + " " + suffix, alternative + " " + suffix))
        return [list(words) for words in dict.fromkeys(tuple(words) for variant in variants
                                                      for words in cls.word_variants(variant))]

    @staticmethod
    def word_score(left, right):
        if left == right:
            return 1.0
        # Elongated spelling (slayyyyyyer) should not count as several typos.
        if min(len(left), len(right)) >= 4:
            left, right = (re.sub(r"([^\W\d_])\1{2,}", r"\1\1", word) for word in (left, right))
            if left == right:
                return 0.96
        if min(len(left), len(right)) < 4 or abs(len(left) - len(right)) > 1:
            return 0
        if len(left) == len(right):
            mismatches = [index for index, (a, b) in enumerate(zip(left, right)) if a != b]
            if len(mismatches) == 1:
                return 0.92
            if (len(mismatches) == 2 and mismatches[1] == mismatches[0] + 1
                    and left[mismatches[0]] == right[mismatches[1]]
                    and left[mismatches[1]] == right[mismatches[0]]):
                return 0.92
        else:
            short, long = sorted((left, right), key=len)
            index = next((i for i, (a, b) in enumerate(zip(short, long)) if a != b), len(short))
            if short[index:] == long[index + 1:]:
                return 0.92
        return 0

    @classmethod
    def phrase_score(cls, query, text):
        """Match consecutive words; short words must match exactly, others tolerate small typos."""
        # A short excerpt alone must not give an almost perfect full-title score.
        # Apply the same title coverage requirement as unordered term matching.
        if not query or len(query) > len(text) or len(query) / len(text) < 0.8:
            return 0
        if version_words(" ".join(query)) != version_words(" ".join(text)):
            return 0
        best = 0
        for start in range(len(text) - len(query) + 1):
            ratios = [cls.word_score(left, right)
                      for left, right in zip(query, text[start:start + len(query)])]
            if min(ratios) >= 0.80:
                # Prefer full titles over excerpts or versions with extra words.
                best = max(best, sum(ratios) / len(ratios) * 0.95 + 0.05 * len(query) / len(text))
        return best

    @classmethod
    def term_score(cls, query, text):
        """Match every query term to a distinct title term, regardless of order.

        Bipartite augmenting paths prevent one result word from satisfying several
        requested words. Unlike a partial token-set ratio, missing terms reject.
        """
        if not query or len(query) > len(text) or len(query) / len(text) < 0.8:
            return 0
        if version_words(" ".join(query)) != version_words(" ".join(text)):
            return 0
        edges = [sorted((index for index, word in enumerate(text) if cls.word_score(term, word)),
                        key=lambda index: cls.word_score(term, text[index]), reverse=True)
                 for term in query]
        matches = {}
        def assign(index, seen):
            for target in edges[index]:
                if target in seen:
                    continue
                seen.add(target)
                if target not in matches or assign(matches[target], seen):
                    matches[target] = index
                    return True
            return False
        for index in sorted(range(len(query)), key=lambda index: len(edges[index])):
            if not assign(index, set()):
                return 0
        average = sum(cls.word_score(query[index], text[target]) for target, index in matches.items()) / len(query)
        score = 0.95 * average + 0.05 * len(query) / len(text)
        # Prefer the original word order when several tracks contain the same terms.
        ordered = any(all(cls.word_score(left, right) for left, right in zip(query, text[start:]))
                      for start in range(len(text) - len(query) + 1))
        return score if ordered else score * 0.98

    @classmethod
    def coverage_score(cls, words, track, title):
        best = cls.term_score(words, title)
        # Artist terms may be at the beginning, end or between title terms.
        artist_span_limit = sum(len(cls.words(name)) for name in track.artists) + len(track.artists) + 1
        for start in range(len(words)):
            for end in range(start + 1, min(len(words), start + artist_span_limit) + 1):
                song = words[:start] + words[end:]
                if not song:
                    continue
                artist_score = cls.artist_score(" ".join(words[start:end]), track.artists)
                if artist_score < 0.85:
                    continue
                title_score = cls.term_score(song, title)
                if title_score >= 0.85:
                    best = max(best, 0.75 * title_score + 0.25 * artist_score)
        return best

    @classmethod
    def match_score(cls, query, track):
        titles = cls.title_words(track.name)
        return max((cls.match_title_score(words, title, track)
                    for words in cls.word_variants(query)
                    for title in titles), default=0)

    @classmethod
    def match_title_score(cls, words, title, track):
        best = cls.phrase_score(words, title)
        # Accept either song + artist or artist + song, requiring both parts to match.
        for split in range(1, len(words)):
            for song, artist in ((words[:split], words[split:]), (words[split:], words[:split])):
                title_score = cls.phrase_score(song, title)
                artist_score = cls.artist_score(" ".join(artist), track.artists)
                if min(title_score, artist_score) >= 0.85:
                    best = max(best, 0.75 * title_score + 0.25 * artist_score)
        if best < 1:
            best = max(best, cls.coverage_score(words, track, title))
        # A conversational "I'm" may precede a remembered title. Remove it only
        # when at least two title words AND the named artist still match.
        if len(words) >= 4 and words[0] == "im" and "im" not in title:
            trimmed = words[1:]
            for split in range(2, len(trimmed)):
                title_score = cls.term_score(trimmed[:split], title)
                artist_score = cls.artist_score(" ".join(trimmed[split:]), track.artists)
                if min(title_score, artist_score) >= 0.85:
                    best = max(best, 0.97 * (0.75 * title_score + 0.25 * artist_score))
        return best

    async def set_volume(self, volume):
        if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 60:
            raise ValueError("Volume deve estar entre 0 e 60.")
        params = {"volume_percent": volume}
        if self.device_id:
            params["device_id"] = self.device_id
        await self.request("PUT", "me/player/volume", expect_json=False, params=params)

    async def enqueue(self, uri):
        params = {"uri": uri}
        if self.device_id:
            params["device_id"] = self.device_id
        await self.request("POST", "me/player/queue", expect_json=False, params=params)

    async def skip(self):
        params = {"device_id": self.device_id} if self.device_id else {}
        await self.request("POST", "me/player/next", expect_json=False, params=params)

    async def play(self):
        params = {"device_id": self.device_id} if self.device_id else {}
        await self.request("PUT", "me/player/play", expect_json=False, params=params)

    async def pause(self):
        params = {"device_id": self.device_id} if self.device_id else {}
        await self.request("PUT", "me/player/pause", expect_json=False, params=params)

    @staticmethod
    def item_label(item, language="br"):
        if not isinstance(item, dict):
            raise SpotifyError("Spotify retornou um item inválido.")
        name = item.get("name") or translate(language, "item_unavailable")
        artists = item.get("artists") or []
        names = [artist.get("name", "") for artist in artists if isinstance(artist, dict)]
        if not names and isinstance(item.get("show"), dict):
            names = [item["show"].get("name", "")]
        return str(name) + (" - " + ", ".join(filter(None, names)) if any(names) else "")

    async def playback(self):
        # user-read-playback-state already belongs to our initial OAuth scopes.
        data = await self.request("GET", "me/player", allow_empty=True,
                                  params={"additional_types": "track,episode"})
        if data is not None and not isinstance(data, dict):
            raise SpotifyError("Spotify retornou uma reprodução inválida.")
        return data

    async def playback_queue(self):
        return (await self.queue_snapshot())["queue"]

    async def queue_snapshot(self):
        data = await self.request("GET", "me/player/queue")
        if not isinstance(data, dict) or not isinstance(data.get("queue"), list):
            raise SpotifyError("Spotify retornou uma fila inválida.")
        return data
