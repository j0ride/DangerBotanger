"""Resolve public song links without fetching arbitrary viewer-provided URLs."""
import re
from urllib.parse import parse_qs, urlsplit

import httpx

from .i18n import MessageError


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
TRACK_ID = re.compile(r"[A-Za-z0-9]{22}")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def is_link(query):
    return bool(re.match(r"(?i)^(?:[a-z][a-z0-9+.-]*://|spotify:|(?:www\.)?(?:youtube\.com|youtu\.be|open\.spotify\.com|spotify\.link)(?:/|$))", query))


def parse_link(query):
    """Return (provider, ID); text requests return None."""
    if not is_link(query):
        return None
    if query.startswith("spotify:"):
        match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", query)
        if not match:
            raise MessageError("sr_link_invalid")
        return "spotify", match[1]
    try:
        url = urlsplit(query if "://" in query else "https://" + query)
        if (url.scheme not in {"http", "https"} or url.username or url.password
                or url.port is not None or any(c.isspace() for c in query)):
            raise ValueError()
        host = url.hostname
        parts = url.path.strip("/").split("/")
        if host in YOUTUBE_HOSTS:
            if parts == ["watch"]:
                identifier = parse_qs(url.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                identifier = parts[1]
            else:
                identifier = ""
            if VIDEO_ID.fullmatch(identifier):
                return "youtube", identifier
        elif host in {"youtu.be", "www.youtu.be"}:
            if len(parts) == 1 and VIDEO_ID.fullmatch(parts[0]):
                return "youtube", parts[0]
        elif host == "open.spotify.com":
            if parts and re.fullmatch(r"intl-[a-zA-Z-]+", parts[0]):
                parts = parts[1:]
            if len(parts) == 2 and parts[0] == "track" and TRACK_ID.fullmatch(parts[1]):
                return "spotify", parts[1]
        elif host == "spotify.link" and len(parts) == 1 and re.fullmatch(r"[A-Za-z0-9_-]+", parts[0]):
            return "spotify_short", "https://spotify.link/" + parts[0]
    except ValueError:
        pass
    raise MessageError("sr_link_invalid")


def clean_video_title(title):
    # Preserve meaningful versions (live, remix, cover, acoustic).
    noise = r"(?:official\s+(?:music\s+)?(?:video|audio|lyric(?:s|\s+video)?)|(?:vídeo|video|áudio|audio)\s+oficial|lyrics?|lyric\s+video|hd|hq|4k(?:\s+remaster)?)"
    title = re.sub(r"[\[(]\s*" + noise + r"\s*[\])]", "", title, flags=re.I)
    title = re.sub(r"\s*(?:[-|–—:]\s*)?\b" + noise + r"\s*$", "", title, flags=re.I)
    return " ".join(title.split()).strip(" -|–—:")


async def youtube_title(client, identifier):
    try:
        response = await client.get("https://www.youtube.com/oembed", params={
            "url": "https://www.youtube.com/watch?v=" + identifier, "format": "json"
        }, timeout=10, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        title = data.get("title") if isinstance(data, dict) else None
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError()
        title = clean_video_title(title)
        if not title:
            raise ValueError()
        return title
    except (httpx.HTTPError, ValueError):
        raise MessageError("sr_youtube_unavailable") from None


async def spotify_short_track(client, url):
    try:
        # Only follow redirects to explicitly recognized Spotify hosts.
        for _ in range(4):
            response = await client.get(url, timeout=10, follow_redirects=False)
            if not response.is_redirect or "location" not in response.headers:
                break
            target = str(response.url.join(response.headers["location"]))
            parsed = parse_link(target)
            if parsed and parsed[0] == "spotify":
                return parsed[1]
            if not parsed or parsed[0] != "spotify_short":
                break
            url = parsed[1]
    except (httpx.HTTPError, ValueError, MessageError):
        pass
    raise MessageError("sr_spotify_link_unavailable")
