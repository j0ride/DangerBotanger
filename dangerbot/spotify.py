from dataclasses import dataclass
import httpx
from .oauth import OAuth


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
        return self.name + " — " + ", ".join(self.artists)


class Spotify:
    def __init__(self, client, oauth: OAuth, device_id=""):
        self.client, self.oauth, self.device_id = client, oauth, device_id

    async def request(self, method, path, **kwargs):
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
                return response.json() if response.content else None
            if response.status_code == 429:
                raise SpotifyError("Spotify limitou as requisições.",
                                   retry_after=int(response.headers.get("Retry-After", "30")))
            messages = {401: "Autorize o Spotify novamente.", 403: "Spotify recusou acesso: verifique Premium e permissões.",
                        404: "Abra o Spotify e inicie a reprodução em um dispositivo."}
            raise SpotifyError(messages.get(response.status_code, "Spotify indisponível."),
                               uncertain=method == "POST" and response.status_code >= 500)

    async def search(self, query):
        data = await self.request("GET", "search", params={"q": query, "type": "track", "limit": 1})
        items = data["tracks"]["items"]
        if not items:
            return None
        item = items[0]
        return Track(item["id"], item["uri"], item["name"],
                     tuple(a["name"] for a in item["artists"]),
                     tuple(a["id"] for a in item["artists"]), item["duration_ms"], item["explicit"])

    async def enqueue(self, uri):
        params = {"uri": uri}
        if self.device_id:
            params["device_id"] = self.device_id
        await self.request("POST", "me/player/queue", params=params)
