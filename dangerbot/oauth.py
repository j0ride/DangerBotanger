import asyncio
import json
import os
import secrets
import ssl
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

PROVIDERS = {
    "spotify": ("https://accounts.spotify.com/authorize", "https://accounts.spotify.com/api/token",
                "user-modify-playback-state user-read-playback-state"),
    "twitch": ("https://id.twitch.tv/oauth2/authorize", "https://id.twitch.tv/oauth2/token",
               "chat:read chat:edit"),
}


class OAuthError(Exception):
    pass


class OAuth:
    def __init__(self, provider, client, directory=Path("data")):
        self.provider = provider
        self.client = client
        self.path = directory / (provider + "-tokens.json")
        self.client_id = os.getenv(provider.upper() + "_CLIENT_ID", "")
        self.secret = os.getenv(provider.upper() + "_CLIENT_SECRET", "")
        if not self.client_id or not self.secret:
            raise OAuthError(f"Configure {provider.upper()}_CLIENT_ID e CLIENT_SECRET.")
        self.lock = asyncio.Lock()
        self.tokens = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self, data):
        previous = self.tokens.get("refresh_token")
        self.tokens = dict(data)
        if not self.tokens.get("refresh_token") and previous:
            self.tokens["refresh_token"] = previous
        self.tokens["expires_at"] = time.time() + int(data.get("expires_in", 3600))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.tokens), encoding="utf-8")
        os.replace(temporary, self.path)

    async def exchange(self, data):
        try:
            response = await self.client.post(PROVIDERS[self.provider][1], data={
                **data, "client_id": self.client_id, "client_secret": self.secret})
        except httpx.HTTPError:
            raise OAuthError(f"Falha de rede ao renovar OAuth {self.provider}.") from None
        if response.status_code != 200:
            raise OAuthError(f"OAuth {self.provider} recusado (HTTP {response.status_code}); execute auth novamente.")
        self.save(response.json())

    async def access_token(self, force=False):
        async with self.lock:
            if not force and self.tokens.get("expires_at", 0) > time.time() + 60:
                return self.tokens["access_token"]
            if not self.tokens.get("refresh_token"):
                raise OAuthError(f"Execute: python main.py auth {self.provider}")
            await self.exchange({"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"]})
            return self.tokens["access_token"]


async def authorize(provider):
    load_dotenv()
    default_redirect = ("https://localhost:8888/callback" if provider == "twitch"
                        else "http://127.0.0.1:8888/callback")
    redirect = os.getenv(provider.upper() + "_REDIRECT_URI", default_redirect)
    parsed = urlparse(redirect)
    allowed_hosts = {"localhost", "127.0.0.1"} if provider == "twitch" else {"127.0.0.1"}
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts or not parsed.port
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise OAuthError(f"Use {provider.upper()}_REDIRECT_URI={default_redirect}.")
    state = secrets.token_urlsafe(32)
    result = {}
    tls_context = None
    if parsed.scheme == "https":
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            tls_context.load_cert_chain(
                os.getenv("OAUTH_TLS_CERT", "data/localhost-cert.pem"),
                os.getenv("OAUTH_TLS_KEY", "data/localhost-key.pem"))
        except (OSError, ssl.SSLError):
            raise OAuthError("Certificado HTTPS ausente ou inválido. Execute scripts/create-local-cert.ps1.") from None

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            params = parse_qs(url.query)
            valid = url.path == parsed.path and secrets.compare_digest(params.get("state", [""])[0], state)
            if not valid:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Callback invalido.")
                return
            result.update({key: values[0] for key, values in params.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Autorizacao recebida. Pode fechar esta janela.")

        def log_message(self, *args):
            pass

    async with httpx.AsyncClient(timeout=20) as client:
        oauth = OAuth(provider, client)
        url = PROVIDERS[provider][0] + "?" + urlencode({
            "client_id": oauth.client_id, "response_type": "code", "redirect_uri": redirect,
            "scope": PROVIDERS[provider][2], "state": state})
        with HTTPServer(("127.0.0.1", parsed.port), Callback) as server:
            if tls_context is not None:
                server.socket = tls_context.wrap_socket(server.socket, server_side=True)
            server.timeout = 1
            print("Abra a URL para autorizar sua conta:\n" + url)
            webbrowser.open(url)
            deadline = time.monotonic() + 180
            while not result and time.monotonic() < deadline:
                await asyncio.to_thread(server.handle_request)
        if not result.get("code"):
            raise OAuthError("Autorização negada ou tempo esgotado.")
        await oauth.exchange({"grant_type": "authorization_code", "code": result["code"],
                              "redirect_uri": redirect})
        print(f"OAuth {provider} salvo em data/; tokens renovados automaticamente.")
