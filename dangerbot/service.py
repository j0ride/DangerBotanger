import asyncio
import math
import logging
import sqlite3
import time
from dataclasses import dataclass
from .spotify import Spotify, SpotifyError
from .oauth import OAuthError
from .i18n import MessageError, error_message, translate


@dataclass(frozen=True)
class User:
    name: str
    roles: frozenset[str] = frozenset()


class RequestRejected(MessageError):
    pass


class Policy:
    def __init__(self, config, clock=time.monotonic):
        self.config, self.clock = config, clock
        self.users = {}
        self.global_until = 0

    def check_user(self, user):
        c = self.config
        if user.name.casefold() in c.blocked_users:
            raise RequestRejected("blocked_user")
        permitted = {"everyone": True, "subscriber": bool(user.roles & {"subscriber", "moderator", "broadcaster"}),
                     "moderator": bool(user.roles & {"moderator", "broadcaster"}),
                     "broadcaster": "broadcaster" in user.roles}
        if not permitted[c.permission]:
            raise RequestRejected("request_permission")
        if "broadcaster" in user.roles:
            return
        remaining = max(self.global_until, self.users.get(user.name, 0)) - self.clock()
        if remaining > 0:
            raise RequestRejected("request_cooldown", seconds=math.ceil(remaining))

    def check_track(self, track):
        c = self.config
        if track.id.casefold() in c.blocked_tracks or track.uri.casefold() in c.blocked_tracks:
            raise RequestRejected("blocked_track")
        if any(a.casefold() in c.blocked_artists for a in (*track.artists, *track.artist_ids)):
            raise RequestRejected("blocked_artist")
        if track.explicit and not c.allow_explicit:
            raise RequestRejected("explicit")
        if track.duration_ms > c.max_duration * 1000:
            raise RequestRejected("duration")

    def consume(self, user):
        if "broadcaster" in user.roles:
            return
        now = self.clock()
        self.users = {key: expiry for key, expiry in self.users.items() if expiry > now}
        self.users[user.name] = now + self.config.user_cooldown
        self.global_until = now + self.config.global_cooldown


class SongRequests:
    def __init__(self, spotify, queue, policy):
        self.spotify, self.queue, self.policy = spotify, queue, policy
        self.lock = asyncio.Lock()
        self.skip_until = 0
        self.language = self.queue.language()

    def reply(self, key, **values):
        return translate(self.language, key, **values)

    async def handle(self, user, message):
        command, _, query = message.strip().partition(" ")
        if command.lower() == "!setlang":
            if not user.roles & {"moderator", "broadcaster"}:
                return self.reply("lang_permission")
            language = query.strip().lower()
            if language not in {"br", "en"}:
                return self.reply("lang_usage")
            try:
                self.queue.set_language(language)
            except sqlite3.Error:
                logging.getLogger(__name__).warning("Não foi possível salvar o idioma.")
                return self.reply("lang_save_error")
            self.language = language
            return self.reply("lang_changed")
        if command.lower() == "!np":
            if query.strip():
                return self.reply("np_usage")
            try:
                playback = await self.spotify.playback()
                requester = self.queue.track_playback(playback)
                if not playback or not playback.get("item"):
                    return self.reply("np_empty")
                label = Spotify.item_label(playback["item"], self.language)
                key = "np_playing" if playback.get("is_playing") else "np_paused"
                response = self.reply(key, label=label[:220])
                if requester:
                    response += " " + self.reply("np_requester", user=requester)
                return response
            except (SpotifyError, OAuthError) as error:
                return error_message(self.language, error)
        if command.lower() == "!queue":
            argument = query.strip()
            if argument and (not argument.isascii() or not argument.isdecimal()
                             or len(argument) > 6 or int(argument) <= 0):
                return self.reply("queue_usage")
            page = int(argument) if argument else 1
            try:
                queue = await self.spotify.playback_queue()
                if not queue:
                    return self.reply("queue_empty")
                pages = math.ceil(len(queue) / 5)
                if page > pages:
                    return self.reply("queue_page", pages=pages)
                start = (page - 1) * 5
                items = "; ".join(f"{index}. {Spotify.item_label(item, self.language)[:45]}"
                                  for index, item in enumerate(queue[start:start + 5], start + 1))
                return self.reply("queue_list", page=page, pages=pages, items=items)
            except (SpotifyError, OAuthError) as error:
                return error_message(self.language, error)
        if command.lower() == "!skip":
            if query.strip():
                return self.reply("skip_usage")
            if not user.roles & {"moderator", "broadcaster"}:
                return self.reply("skip_permission")
            async with self.lock:
                now = self.policy.clock()
                if now < self.skip_until:
                    return self.reply("skip_cooldown", seconds=math.ceil(self.skip_until - now))
                self.skip_until = now + 5
                try:
                    await self.spotify.skip()
                except SpotifyError as error:
                    self.skip_until = max(self.skip_until, now + error.retry_after)
                    if error.uncertain:
                        return self.reply("skip_uncertain")
                    return error_message(self.language, error)
                except OAuthError as error:
                    return error_message(self.language, error)
                return self.reply("skip_success")
        if command.lower() != "!sr":
            return None
        if not query.strip():
            return self.reply("sr_usage")
        if len(query) > 200:
            return self.reply("sr_long")
        async with self.lock:
            try:
                self.policy.check_user(user)
                if self.queue.count() >= self.policy.config.max_pending:
                    raise RequestRejected("queue_full")
                track = await self.spotify.search(query.strip())
                if track is None:
                    return self.reply("sr_empty")
                self.policy.check_track(track)
                if self.queue.duplicate(track.uri):
                    raise RequestRejected("sr_duplicate")
                snapshot = await self.spotify.queue_snapshot()
                if any(isinstance(item, dict) and item.get("uri") == track.uri
                       for item in snapshot["queue"]):
                    raise RequestRejected("sr_duplicate_spotify")
                playback = await self.spotify.playback()
                self.queue.track_playback(playback)
                slots = self.queue.user_slots(user.name, snapshot["queue"],
                                              current_item=playback.get("item") if playback else None)
                if slots >= self.policy.config.max_user_requests:
                    raise RequestRejected("user_queue_limit", limit=self.policy.config.max_user_requests)
                self.queue.add(user.name, track)
                self.policy.consume(user)
                return self.reply("sr_received", name=track.name, artists=", ".join(track.artists))
            except (RequestRejected, SpotifyError, OAuthError) as error:
                return error_message(self.language, error)

    async def dispatch_once(self):
        row = self.queue.next()
        if row is None:
            return 2
        self.queue.update(row["id"], "sending")
        try:
            await self.spotify.enqueue(row["uri"])
        except SpotifyError as error:
            self.queue.update(row["id"], "uncertain" if error.uncertain else "pending")
            logging.getLogger(__name__).warning("Pedido #%s: %s", row["id"], error)
            return max(15, error.retry_after)
        except OAuthError:
            self.queue.update(row["id"], "pending")
            logging.getLogger(__name__).warning("OAuth indisponível; pedido #%s mantido pendente.", row["id"])
            return 60
        self.queue.update(row["id"], "sent")
        return 2

    async def worker(self):
        while True:
            delay = await self.dispatch_once()
            await asyncio.sleep(delay)

    async def monitor_playback(self):
        while True:
            try:
                self.queue.track_playback(await self.spotify.playback())
                delay = 10
            except SpotifyError as error:
                delay = max(10, error.retry_after)
            except OAuthError:
                delay = 60
            await asyncio.sleep(delay)
