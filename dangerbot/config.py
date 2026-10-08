import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    channel: str
    bot_name: str
    user_cooldown: float
    global_cooldown: float
    permission: str
    blocked_users: frozenset[str]
    blocked_tracks: frozenset[str]
    blocked_artists: frozenset[str]
    allow_explicit: bool
    max_duration: int
    max_pending: int
    device_id: str
    max_user_requests: int = 10

    @classmethod
    def load(cls):
        load_dotenv()
        def csv(name):
            return frozenset(x.strip().casefold() for x in os.getenv(name, "").split(",") if x.strip())
        channel = os.getenv("TWITCH_CHANNEL", "").lower().lstrip("#")
        bot = os.getenv("TWITCH_BOT_NAME", "").lower()
        if not all(re.fullmatch(r"[a-z0-9_]{1,25}", x) for x in (channel, bot)):
            raise ValueError("Configure TWITCH_CHANNEL e TWITCH_BOT_NAME no .env.")
        permission = os.getenv("REQUEST_PERMISSION", "everyone")
        if permission not in {"everyone", "subscriber", "moderator", "broadcaster"}:
            raise ValueError("REQUEST_PERMISSION inválida.")
        user = float(os.getenv("USER_COOLDOWN", "30"))
        global_ = float(os.getenv("GLOBAL_COOLDOWN", "5"))
        duration = int(os.getenv("MAX_DURATION_SECONDS", "600"))
        pending = int(os.getenv("MAX_PENDING_REQUESTS", "30"))
        max_user = int(os.getenv("MAX_USER_REQUESTS", "10"))
        if min(user, global_) < 0 or min(duration, pending, max_user) <= 0:
            raise ValueError("Cooldowns devem ser >= 0; limites devem ser > 0.")
        explicit = os.getenv("ALLOW_EXPLICIT", "true").lower()
        if explicit not in {"true", "false"}:
            raise ValueError("ALLOW_EXPLICIT deve ser true ou false.")
        return cls(channel, bot, user, global_, permission, csv("BLACKLIST_USERS"),
                   csv("BLACKLIST_TRACKS"), csv("BLACKLIST_ARTISTS"),
                   explicit == "true", duration, pending, os.getenv("SPOTIFY_DEVICE_ID", ""), max_user)
