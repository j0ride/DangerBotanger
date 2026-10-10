import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from dangerbot.twitch import Twitch


class ActionMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_reply_is_sent_as_one_irc_action_with_viewer_mention(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b":viewer!viewer@host PRIVMSG #channel :!help\r\n")
        sent = []
        class Writer:
            def write(self, data):
                sent.append(data)
                if data.startswith(b"PRIVMSG"):
                    reader.feed_eof()
            async def drain(self):
                pass
            def close(self):
                pass
            async def wait_closed(self):
                pass
        service = SimpleNamespace(handle=AsyncMock(return_value="Olá\r\nviewer\x01!"))
        twitch = Twitch(SimpleNamespace(channel="channel", bot_name="bot"), None, service)
        with patch.object(twitch, "validate", new_callable=AsyncMock, return_value="example-token"), \
                patch("dangerbot.twitch.asyncio.open_connection", new_callable=AsyncMock,
                      return_value=(reader, Writer())):
            with self.assertRaises(ExceptionGroup):
                await twitch.connect()
        replies = [line for line in sent if line.startswith(b"PRIVMSG")]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0], "PRIVMSG #channel :\x01ACTION @viewer Olá viewer!\x01\r\n".encode())
        self.assertEqual(replies[0].count(b"\x01"), 2)
        self.assertTrue(any(b"twitch.tv/commands" in line for line in sent))
