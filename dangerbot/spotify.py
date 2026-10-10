from dataclasses import dataclass
from json import JSONDecodeError
from difflib import SequenceMatcher
import re
import unicodedata
import httpx
from .oauth import OAuth
from .i18n import translate


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

    async def request(self, method, path, *, expect_json=True, allow_empty=False, **kwargs):
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
            messages = {401: "Autorize o Spotify novamente.", 403: "Spotify recusou acesso: verifique Premium e permissões.",
                        404: "Abra o Spotify e inicie a reprodução em um dispositivo."}
            raise SpotifyError(messages.get(response.status_code, "Spotify indisponível."),
                               uncertain=method == "POST" and response.status_code >= 500)

    async def search(self, query):
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
                track = Track(item["id"], item["uri"], item["name"],
                              tuple(a["name"] for a in item["artists"]),
                              tuple(a["id"] for a in item["artists"]), item["duration_ms"], item["explicit"])
                candidates.append((self.match_score(query, track), track))
        except (KeyError, TypeError, AttributeError):
            raise SpotifyError("Spotify retornou uma resposta inválida. Tente novamente.") from None
        if not candidates:
            return None
        score, track = max(candidates, key=lambda candidate: candidate[0])
        return track if score >= 0.90 else SearchSuggestion(candidates[0][1])

    @staticmethod
    def words(text):
        normalized = unicodedata.normalize("NFKD", text.casefold())
        return re.findall(r"[^\W_]+", "".join(c for c in normalized if not unicodedata.combining(c)))

    @staticmethod
    def phrase_score(query, text):
        """Match consecutive words; short words must match exactly, others tolerate small typos."""
        if not query or len(query) > len(text):
            return 0
        best = 0
        for start in range(len(text) - len(query) + 1):
            ratios = [1.0 if left == right else
                      (SequenceMatcher(None, left, right).ratio() if min(len(left), len(right)) > 3 else 0)
                      for left, right in zip(query, text[start:start + len(query)])]
            if min(ratios) >= 0.80:
                # Prefer full titles over excerpts or versions with extra words.
                best = max(best, sum(ratios) / len(ratios) * 0.95 + 0.05 * len(query) / len(text))
        return best

    @classmethod
    def match_score(cls, query, track):
        words, title = cls.words(query), cls.words(track.name)
        best = cls.phrase_score(words, title)
        # Accept either song + artist or artist + song, requiring both parts to match.
        for split in range(1, len(words)):
            for song, artist in ((words[:split], words[split:]), (words[split:], words[:split])):
                title_score = cls.phrase_score(song, title)
                artist_score = max((cls.phrase_score(artist, cls.words(name)) for name in track.artists), default=0)
                if min(title_score, artist_score) >= 0.85:
                    best = max(best, 0.75 * title_score + 0.25 * artist_score)
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
