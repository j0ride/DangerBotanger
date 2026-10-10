"""Desktop configuration storage; never expose credentials in error messages."""
import math
import os
import re
import ssl
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values, set_key


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    default: str = ""
    choices: tuple[str, ...] = ()
    secret: bool = False


FIELDS = (
    Field("TWITCH_CHANNEL", "Canal da live (login)", "Twitch"),
    Field("TWITCH_BOT_NAME", "Conta do bot (login)", "Twitch"),
    Field("TWITCH_CLIENT_ID", "Client ID", "Twitch"),
    Field("TWITCH_CLIENT_SECRET", "Client secret", "Twitch", secret=True),
    Field("TWITCH_REDIRECT_URI", "URL de callback", "Twitch", "https://localhost:8888/callback"),
    Field("SPOTIFY_CLIENT_ID", "Client ID", "Spotify"),
    Field("SPOTIFY_CLIENT_SECRET", "Client secret", "Spotify", secret=True),
    Field("SPOTIFY_REDIRECT_URI", "URL de callback", "Spotify", "http://127.0.0.1:8888/callback"),
    Field("SPOTIFY_DEVICE_ID", "ID do dispositivo (opcional)", "Spotify"),
    Field("REQUEST_PERMISSION", "Quem pode pedir", "Pedidos", "everyone",
          ("everyone", "subscriber", "moderator", "broadcaster")),
    Field("USER_COOLDOWN", "Intervalo por pessoa (segundos)", "Pedidos", "5"),
    Field("GLOBAL_COOLDOWN", "Intervalo do canal (segundos)", "Pedidos", "5"),
    Field("MAX_DURATION_SECONDS", "Duração máxima (segundos)", "Pedidos", "600"),
    Field("MAX_PENDING_REQUESTS", "Máximo de pedidos em envio", "Pedidos", "30"),
    Field("MAX_USER_REQUESTS", "Máximo de pedidos por pessoa", "Pedidos", "10"),
    Field("ALLOW_EXPLICIT", "Permitir músicas explícitas", "Pedidos", "true", ("true", "false")),
    Field("BLACKLIST_USERS", "Usuários bloqueados", "Pedidos"),
    Field("BLACKLIST_TRACKS", "Músicas bloqueadas (IDs ou URIs)", "Pedidos"),
    Field("BLACKLIST_ARTISTS", "Artistas bloqueados (nomes ou IDs)", "Pedidos"),
    Field("OAUTH_TLS_CERT", "Arquivo do certificado HTTPS", "Avançado", "data/localhost-cert.pem"),
    Field("OAUTH_TLS_KEY", "Arquivo da chave HTTPS", "Avançado", "data/localhost-key.pem"),
)


def load_settings(path):
    stored = dotenv_values(path, encoding="utf-8-sig", interpolate=False) if Path(path).exists() else {}
    return {field.key: stored.get(field.key) or field.default for field in FIELDS}


def validate_settings(values, *, provider=None, require_accounts=False):
    result = {field.key: str(values.get(field.key, field.default)).strip() for field in FIELDS}
    for field in FIELDS:
        value = result[field.key]
        if any(c in value for c in "\r\n\0"):
            raise ValueError(f"{field.label}: use apenas uma linha.")
        if field.choices and value not in field.choices:
            raise ValueError(f"{field.label}: selecione uma das opções disponíveis.")
    for key in ("TWITCH_CHANNEL", "TWITCH_BOT_NAME"):
        result[key] = result[key].lower().lstrip("#")
        if result[key] and not re.fullmatch(r"[a-z0-9_]{1,25}", result[key]):
            raise ValueError("Use apenas o login no canal e na conta do bot, sem URL.")
        if require_accounts and not result[key]:
            raise ValueError("Preencha o canal e a conta do bot na aba Twitch.")
    for key in ("USER_COOLDOWN", "GLOBAL_COOLDOWN"):
        try:
            number = float(result[key])
        except ValueError:
            raise ValueError("Os intervalos precisam ser números em segundos (exemplo: 5 ou 1.5).") from None
        if not math.isfinite(number) or number < 0:
            raise ValueError("Os intervalos precisam ser números finitos maiores ou iguais a zero.")
    for key in ("MAX_DURATION_SECONDS", "MAX_PENDING_REQUESTS", "MAX_USER_REQUESTS"):
        if not result[key].isascii() or not result[key].isdecimal() or int(result[key]) <= 0:
            raise ValueError("Duração e limites de pedidos precisam ser números inteiros maiores que zero.")
    for platform in ("spotify", "twitch"):
        redirect = urlparse(result[platform.upper() + "_REDIRECT_URI"])
        try:
            port = redirect.port
        except ValueError:
            port = None
        hosts = {"localhost", "127.0.0.1"} if platform == "twitch" else {"127.0.0.1"}
        if (redirect.scheme not in {"http", "https"} or redirect.hostname not in hosts or not port
                or not redirect.path or redirect.query or redirect.fragment or redirect.username or redirect.password):
            raise ValueError(f"Confira a URL de callback na aba {platform.title()}; use um endereço local com porta e caminho.")
        if (require_accounts or provider == platform) and not all(
                result[platform.upper() + suffix] for suffix in ("_CLIENT_ID", "_CLIENT_SECRET")):
            raise ValueError(f"Preencha Client ID e client secret na aba {platform.title()}.")
    if not result["OAUTH_TLS_CERT"] or not result["OAUTH_TLS_KEY"]:
        raise ValueError("Preencha os caminhos do certificado e da chave na aba Avançado.")
    return result


def save_settings(path, values):
    """Atomically update known keys while preserving comments and unknown settings."""
    values = validate_settings(values)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".dangerbot-settings-", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        if path.exists():
            temporary.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
        for key, value in values.items():
            set_key(temporary, key, value, quote_mode="always", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return values


def apply_settings(values):
    # Override inherited environment so saved changes take effect without restarting the GUI.
    os.environ.update(values)


def ensure_certificate(cert_path, key_path):
    """Create a loopback certificate only when neither configured file exists."""
    cert_path, key_path = Path(cert_path), Path(key_path)
    if cert_path.resolve() == key_path.resolve():
        raise ValueError("Certificado e chave precisam ser arquivos diferentes.")
    if cert_path.exists() or key_path.exists():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(cert_path, key_path)
        except (OSError, ssl.SSLError):
            raise ValueError("Certificado/chave existente ausente ou inválido. Escolha novos caminhos na aba Avançado.") from None
        return False
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting another instance's certificate or key.
    with key_path.open("xb") as target:
        target.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        with cert_path.open("xb") as target:
            target.write(cert.public_bytes(serialization.Encoding.PEM))
    except BaseException:
        key_path.unlink(missing_ok=True)
        raise
    return True
