import asyncio
import logging
import ssl
import time
import httpx
from .oauth import OAuthError
from .service import User

log = logging.getLogger(__name__)


def parse_message(line):
    tags = {}
    if line.startswith("@"):
        raw, line = line[1:].split(" ", 1)
        tags = dict(part.split("=", 1) for part in raw.split(";") if "=" in part)
    if not line.startswith(":") or " PRIVMSG " not in line:
        return None
    prefix, _, tail = line.partition(" PRIVMSG ")
    channel, separator, text = tail.partition(" :")
    if not separator:
        return None
    name = prefix[1:].split("!", 1)[0].lower()
    badges = {item.split("/")[0] for item in tags.get("badges", "").split(",")}
    roles = badges & {"subscriber", "moderator", "broadcaster"}
    if "founder" in badges:
        roles.add("subscriber")
    if tags.get("mod") == "1":
        roles.add("moderator")
    return channel, User(name, frozenset(roles)), text


class Twitch:
    def __init__(self, config, oauth, service):
        self.config, self.oauth, self.service = config, oauth, service
        self.last_reply = 0

    async def validate(self):
        token = await self.oauth.access_token()
        response = await self.oauth.client.get("https://id.twitch.tv/oauth2/validate",
                                               headers={"Authorization": "OAuth " + token})
        if response.status_code == 401:
            token = await self.oauth.access_token(force=True)
            response = await self.oauth.client.get("https://id.twitch.tv/oauth2/validate",
                                                   headers={"Authorization": "OAuth " + token})
        if response.status_code != 200:
            raise RuntimeError("Falha ao validar OAuth Twitch.")
        data = response.json()
        if data.get("login", "").lower() != self.config.bot_name or data.get("client_id") != self.oauth.client_id:
            raise RuntimeError("Token Twitch não pertence ao bot/client configurado.")
        if not {"chat:read", "chat:edit"}.issubset(data.get("scopes", [])):
            raise RuntimeError("Autorize Twitch com chat:read e chat:edit.")
        return token

    async def connect(self):
        token = await self.validate()
        reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6697,
                                                       ssl=ssl.create_default_context())
        async def send(line):
            writer.write((line + "\r\n").encode())
            await writer.drain()
        try:
            await send("PASS oauth:" + token)
            await send("NICK " + self.config.bot_name)
            await send("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
            await send("JOIN #" + self.config.channel)
            log.info("Conectado ao chat de %s", self.config.channel)
            validation_at = time.monotonic()
            # Command worker stays separate so Spotify requests never delay IRC PING.
            inbox = asyncio.Queue(maxsize=100)
            async def commands():
                while True:
                    user, text = await inbox.get()
                    reply = await self.service.handle(user, text)
                    if reply:
                        await asyncio.sleep(max(0, self.last_reply + 1.6 - time.monotonic()))
                        safe = " ".join(reply.replace("\r", " ").replace("\n", " ").split())[:350]
                        await send(f"PRIVMSG #{self.config.channel} :@{user.name} {safe}")
                        self.last_reply = time.monotonic()

            async with asyncio.TaskGroup() as group:
                group.create_task(commands())
                while True:
                    raw = await asyncio.wait_for(reader.readline(), timeout=300)
                    if not raw:
                        raise ConnectionError("Twitch encerrou a conexão.")
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if line.startswith("PING "):
                        await send("PONG " + line[5:])
                    elif " RECONNECT" in line:
                        raise ConnectionError("Twitch solicitou reconexão.")
                    elif "Login authentication failed" in line or "Login unsuccessful" in line:
                        raise RuntimeError("Twitch recusou o login; execute auth twitch.")
                    else:
                        message = parse_message(line)
                        if message and message[0].lower() == "#" + self.config.channel:
                            _, user, text = message
                            if user.name != self.config.bot_name and text.split(" ", 1)[0].lower() in {"!sr", "!queue", "!np", "!skip", "!remove", "!setlang"}:
                                if not inbox.full():
                                    inbox.put_nowait((user, text))
                    if time.monotonic() - validation_at >= 3600:
                        await self.validate()
                        validation_at = time.monotonic()
        finally:
            writer.close()
            await writer.wait_closed()

    async def run(self):
        while True:
            try:
                await self.connect()
            except (OSError, RuntimeError, OAuthError, httpx.HTTPError, ExceptionGroup) as error:
                # Never log raw responses or tokens.
                log.warning("Conexão Twitch interrompida (%s); nova tentativa em 15s.", type(error).__name__)
                await asyncio.sleep(15)
