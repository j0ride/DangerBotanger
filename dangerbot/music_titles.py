"""Conservative normalization of music video titles, independent of text requests.

Patterns researched in youtube_title_parse and yt-dlp metadata documentation:
https://github.com/lttkgp/youtube_title_parse
https://github.com/yt-dlp/yt-dlp#modifying-metadata
The implementation deliberately preserves unknown annotations and musical versions.
"""
from dataclasses import dataclass
from html import unescape
import re
import unicodedata


QUALITY = r"(?:hd|hq|(?:720|1080|1440|2160|4320)p|[248]k(?:\s+remaster(?:ed)?)?|\d{2,3}\s*fps)"
VIDEO = r"(?:music\s+video|video\s*clip|vídeo\s*clipe|videoclipe|video|vídeo|audio|áudio|visuali[sz]er|m/?v|p/?v)"
LYRICS = r"(?:(?:with\s+)?lyrics?|lyric\s+video|letra(?:s)?|legendas?|legendad[oa]|tradu[çc][ãa]o|subtitulad[oa]s?)(?:\s+(?:em|in)\s+(?:portugu[êe]s|english|ingl[êe]s|espa[ñn]ol|espanhol))?"
LABEL = rf"(?:(?:official|oficial)(?:\s+{VIDEO}|\s+{LYRICS})?|{VIDEO}(?:\s+(?:official|oficial))?|{LYRICS}|{QUALITY})"
SUFFIX = rf"(?:(?:official|oficial)(?:\s+{VIDEO}|\s+{LYRICS})?|{VIDEO}\s+(?:official|oficial)|{LYRICS}|{QUALITY})"
ANNOTATION = re.compile(rf"(?:{LABEL})(?:(?:\s*[/|,+&-]\s*|\s+(?:(?:com|and)\s+)?)(?:{LABEL}))*", re.I)
BRACKETS = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]|【([^【】]*)】|\{([^{}]*)\}")
COLLABORATION = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|part\.?|participação(?:\s+de)?)\s+", re.I)
VERSIONS = re.compile(r"\b(?:live|ao vivo|remix|cover|acoustic|acústic[oa]|instrumental|sped up|slowed|reverb|remaster(?:ed)?)\b", re.I)


def clean_video_title(title):
    title = unicodedata.normalize("NFKC", unescape(title))
    title = re.sub(r"[\u200b-\u200f\ufeff]", "", title)
    title = " ".join(title.split())
    def annotation(match):
        content = next(group for group in match.groups() if group is not None).strip()
        return " " if ANNOTATION.fullmatch(content) else match.group()
    title = BRACKETS.sub(annotation, title)
    title = re.sub(r"\*{2,}\s*(?:new|exclusive|novo|lançamento)\s*\*{2,}", "", title, flags=re.I)
    # Remove trailing promotional labels only; keep meaningful names in the body.
    for _ in range(6):
        previous = title
        candidate = re.sub(rf"(?:\s*[-|–—:]\s*|\s+)(?:{SUFFIX})(?:\s+(?:{SUFFIX}))*\s*$", "", title, flags=re.I)
        # A song literally named Lyrics/HD must retain its title.
        if re.search(r"\s[-|–—:]\s", title) and not re.search(r"\s[-|–—:]\s", candidate):
            break
        title = candidate
        title = " ".join(title.split()).strip(" -|–—:")
        if title == previous:
            break
    return title


@dataclass(frozen=True)
class MusicTitle:
    artist: str
    title: str


def parse_music_title(text):
    text = clean_video_title(text)
    # Require visible separators so AC/DC and Jay-Z stay intact.
    split = re.split(r"\s+(?:-+|[–—|:/]|///|_)\s+|\s*[–—]\s*", text, maxsplit=1)
    if len(split) == 2 and all(part.strip() for part in split):
        artist, title = split
    else:
        quoted = re.fullmatch(r'(.+?)\s+["“](.+?)["”]', text)
        by_artist = re.fullmatch(r"(.+?)\s+(?:by|por)\s+(.+)", text, re.I)
        if quoted:
            artist, title = quoted.groups()
        elif by_artist:
            title, artist = by_artist.groups()
        else:
            return None
    return MusicTitle(artist.strip().strip('"“”'), title.strip().strip('"“”'))


def song_and_guests(title):
    # Featured credits are often in the title on YouTube but separate artists on Spotify.
    title = re.sub(r"[([]\s*(?=(?:feat\.?|ft\.?|featuring|part\.?)\s)", " ", title, flags=re.I)
    split = COLLABORATION.split(title, maxsplit=1)
    return split[0].strip(), split[1].strip(" )]") if len(split) == 2 else ""


def version_words(title):
    return {unicodedata.normalize("NFKD", match.casefold()).encode("ascii", "ignore").decode()
            for match in VERSIONS.findall(title)}
