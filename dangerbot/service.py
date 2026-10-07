import asyncio
import math
import logging
import time
from dataclasses import dataclass
from .spotify import SpotifyError
from .oauth import OAuthError


@dataclass(frozen=True)
class User:
    name: str
    roles: frozenset[str] = frozenset()


class RequestRejected(Exception):
    pass


class Policy:
    def __init__(self, config, clock=time.monotonic):
        self.config, self.clock = config, clock
        self.users = {}
        self.global_until = 0

    def check_user(self, user):
        c = self.config
        if user.name.casefold() in c.blocked_users:
            raise RequestRejected("Você está bloqueado para pedidos.")
        permitted = {"everyone": True, "subscriber": bool(user.roles & {"subscriber", "moderator", "broadcaster"}),
                     "moderator": bool(user.roles & {"moderator", "broadcaster"}),
                     "broadcaster": "broadcaster" in user.roles}
        if not permitted[c.permission]:
            raise RequestRejected("Você não tem permissão para pedir músicas.")
        remaining = max(self.global_until, self.users.get(user.name, 0)) - self.clock()
        if remaining > 0:
            raise RequestRejected(f"Aguarde {math.ceil(remaining)}s para pedir novamente.")

    def check_track(self, track):
        c = self.config
        if track.id.casefold() in c.blocked_tracks or track.uri.casefold() in c.blocked_tracks:
            raise RequestRejected("Música bloqueada.")
        if any(a.casefold() in c.blocked_artists for a in (*track.artists, *track.artist_ids)):
            raise RequestRejected("Artista bloqueado.")
        if track.explicit and not c.allow_explicit:
            raise RequestRejected("Músicas explícitas não são permitidas.")
        if track.duration_ms > c.max_duration * 1000:
            raise RequestRejected("Música excede a duração máxima.")

    def consume(self, user):
        now = self.clock()
        self.users = {key: expiry for key, expiry in self.users.items() if expiry > now}
        self.users[user.name] = now + self.config.user_cooldown
        self.global_until = now + self.config.global_cooldown


class SongRequests:
    def __init__(self, spotify, queue, policy):
        self.spotify, self.queue, self.policy = spotify, queue, policy
        self.lock = asyncio.Lock()
        self.skip_until = 0

    async def handle(self, user, message):
        command, _, query = message.strip().partition(" ")
        if command.lower() == "!queue":
            items = "; ".join(f"#{row['id']} {row['label'][:60]}" for row in self.queue.pending())
            return f"Pedidos aguardando envio: {self.queue.count()}." + (f" {items}" if items else "")
        if command.lower() == "!skip":
            if query.strip():
                return "Uso: !skip"
            if not user.roles & {"moderator", "broadcaster"}:
                return "Somente moderadores e o dono do canal podem pular músicas."
            async with self.lock:
                now = self.policy.clock()
                if now < self.skip_until:
                    return f"Aguarde {math.ceil(self.skip_until - now)}s para pular novamente."
                self.skip_until = now + 5
                try:
                    await self.spotify.skip()
                except SpotifyError as error:
                    self.skip_until = max(self.skip_until, now + error.retry_after)
                    if error.uncertain:
                        return "Não foi possível confirmar o skip. Confira o Spotify antes de tentar novamente."
                    return str(error)
                except OAuthError as error:
                    return str(error)
                return "Música pulada no Spotify."
        if command.lower() == "!remove":
            argument = query.strip().removeprefix("#")
            if not argument.isascii() or not argument.isdecimal() or len(argument) > 18 or int(argument) <= 0:
                return "Uso: !remove <id do pedido>. Exemplo: !remove 12"
            request_id = int(argument)
            row = self.queue.get(request_id)
            if row is None:
                return f"Pedido #{request_id} não encontrado."
            if row["user"].casefold() != user.name.casefold() and not user.roles & {"moderator", "broadcaster"}:
                return "Você só pode remover seus próprios pedidos."
            if not self.queue.remove(request_id):
                return "Este pedido não está pendente. Não é possível remover músicas já enviadas ao Spotify."
            return f"Pedido #{request_id} removido da fila do bot."
        if command.lower() != "!sr":
            return None
        if not query.strip():
            return "Uso: !sr <música e artista>"
        if len(query) > 200:
            return "Pedido muito longo (máximo 200 caracteres)."
        async with self.lock:
            try:
                self.policy.check_user(user)
                if self.queue.count() >= self.policy.config.max_pending:
                    raise RequestRejected("Fila cheia. Tente mais tarde.")
                track = await self.spotify.search(query.strip())
                if track is None:
                    return "Nenhuma música encontrada."
                self.policy.check_track(track)
                if self.queue.duplicate(track.uri):
                    raise RequestRejected("Esta música já está aguardando envio.")
                request_id = self.queue.add(user.name, track)
                self.policy.consume(user)
                return f"Pedido #{request_id} recebido: {track.label}."
            except (RequestRejected, SpotifyError, OAuthError) as error:
                return str(error)

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
