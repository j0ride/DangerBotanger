import argparse
import asyncio
import logging
import httpx
from .config import Config
from .oauth import OAuth, OAuthError, authorize
from .queue import RequestQueue
from .service import Policy, SongRequests
from .spotify import Spotify
from .twitch import Twitch


async def run():
    config = Config.load()
    async with httpx.AsyncClient(timeout=20) as client:
        spotify_auth = OAuth("spotify", client)
        twitch_auth = OAuth("twitch", client)
        await spotify_auth.access_token()
        await twitch_auth.access_token()
        queue = RequestQueue()
        try:
            service = SongRequests(Spotify(client, spotify_auth, config.device_id), queue, Policy(config))
            twitch = Twitch(config, twitch_auth, service)
            await twitch.validate()
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(service.worker())
                tasks.create_task(service.monitor_playback())
                tasks.create_task(twitch.run())
        finally:
            queue.close()


def main():
    parser = argparse.ArgumentParser(description="DangerBotanger Twitch + Spotify")
    sub = parser.add_subparsers(dest="command")
    auth = sub.add_parser("auth", help="Autorizar conta e salvar refresh token")
    auth.add_argument("provider", choices=["spotify", "twitch"])
    sub.add_parser("run", help="Iniciar bot")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(authorize(args.provider) if args.command == "auth" else run())
    except (ValueError, OAuthError, RuntimeError) as error:
        parser.exit(1, str(error) + "\n")
    except KeyboardInterrupt:
        pass
