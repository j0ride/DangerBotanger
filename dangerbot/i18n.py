"""Chat response catalog. Music metadata is never translated."""

MESSAGES = {
    "user_queue_limit": {
        "br": "Você já tem {limit} pedidos na fila do Spotify ou em envio. Aguarde uma música sair da fila.",
        "en": "You already have {limit} requests in the Spotify queue or being sent. Wait for a song to leave the queue."
    },
    "lang_usage": {
        "br": "Uso: !setlang br|en",
        "en": "Usage: !setlang br|en"
    },
    "lang_permission": {
        "br": "Somente moderadores e o dono do canal podem mudar o idioma.",
        "en": "Only moderators and the channel owner can change the language."
    },
    "lang_changed": {
        "br": "Idioma do bot alterado para português (BR).",
        "en": "Bot language changed to English."
    },
    "lang_save_error": {
        "br": "Não foi possível salvar o idioma. Tente novamente.",
        "en": "Could not save the language. Please try again."
    },
    "blocked_user": {
        "br": "Você está bloqueado para pedidos.",
        "en": "You are blocked from requesting songs."
    },
    "request_permission": {
        "br": "Você não tem permissão para pedir músicas.",
        "en": "You do not have permission to request songs."
    },
    "request_cooldown": {
        "br": "Aguarde {seconds}s para pedir novamente.",
        "en": "Wait {seconds}s before requesting another song."
    },
    "blocked_track": {
        "br": "Música bloqueada.",
        "en": "This song is blocked."
    },
    "blocked_artist": {
        "br": "Artista bloqueado.",
        "en": "This artist is blocked."
    },
    "explicit": {
        "br": "Músicas explícitas não são permitidas.",
        "en": "Explicit songs are not allowed."
    },
    "duration": {
        "br": "Música excede a duração máxima.",
        "en": "This song exceeds the maximum duration."
    },
    "np_usage": {
        "br": "Uso: !np",
        "en": "Usage: !np"
    },
    "np_empty": {
        "br": "Nenhuma música em reprodução no Spotify.",
        "en": "Nothing is currently playing on Spotify."
    },
    "np_playing": {
        "br": "Tocando agora: {label}.",
        "en": "Now playing: {label}."
    },
    "np_paused": {
        "br": "Spotify pausado: {label}.",
        "en": "Spotify paused: {label}."
    },
    "queue_usage": {
        "br": "Uso: !queue [página]. Exemplo: !queue 2",
        "en": "Usage: !queue [page]. Example: !queue 2"
    },
    "queue_empty": {
        "br": "A fila do Spotify está vazia.",
        "en": "The Spotify queue is empty."
    },
    "queue_page": {
        "br": "Página inválida. A fila retornada pelo Spotify tem {pages} página(s).",
        "en": "Invalid page. The queue returned by Spotify has {pages} page(s)."
    },
    "queue_list": {
        "br": "Fila Spotify ({page}/{pages}): {items}",
        "en": "Spotify queue ({page}/{pages}): {items}"
    },
    "skip_usage": {
        "br": "Uso: !skip",
        "en": "Usage: !skip"
    },
    "skip_permission": {
        "br": "Somente moderadores e o dono do canal podem pular músicas.",
        "en": "Only moderators and the channel owner can skip songs."
    },
    "skip_cooldown": {
        "br": "Aguarde {seconds}s para pular novamente.",
        "en": "Wait {seconds}s before skipping again."
    },
    "skip_uncertain": {
        "br": "Não foi possível confirmar o skip. Confira o Spotify antes de tentar novamente.",
        "en": "Could not confirm the skip. Check Spotify before trying again."
    },
    "skip_success": {
        "br": "Música pulada.",
        "en": "Song skipped."
    },
    "sr_usage": {
        "br": "Uso: !sr <música e artista>",
        "en": "Usage: !sr <song and artist>"
    },
    "sr_long": {
        "br": "Pedido muito longo (máximo 200 caracteres).",
        "en": "Request is too long (maximum 200 characters)."
    },
    "queue_full": {
        "br": "Fila cheia. Tente mais tarde.",
        "en": "The request queue is full. Try again later."
    },
    "sr_empty": {
        "br": "Nenhuma música encontrada.",
        "en": "No songs found."
    },
    "sr_duplicate": {
        "br": "Esta música já está aguardando envio.",
        "en": "This song is already waiting to be sent."
    },
    "sr_received": {
        "br": "{name} adicionada à fila.",
        "en": "{name} added to the queue."
    },
    "spotify_network": {
        "br": "Falha de rede no Spotify.",
        "en": "Spotify network error."
    },
    "spotify_invalid": {
        "br": "Spotify retornou uma resposta inválida. Tente novamente.",
        "en": "Spotify returned an invalid response. Please try again."
    },
    "spotify_rate": {
        "br": "Spotify limitou as requisições.",
        "en": "Spotify rate limit reached."
    },
    "spotify_auth": {
        "br": "Autorize o Spotify novamente.",
        "en": "Authorize Spotify again."
    },
    "spotify_access": {
        "br": "Spotify recusou acesso: verifique Premium e permissões.",
        "en": "Spotify denied access: check Premium and permissions."
    },
    "spotify_device": {
        "br": "Abra o Spotify e inicie a reprodução em um dispositivo.",
        "en": "Open Spotify and start playback on a device."
    },
    "spotify_unavailable": {
        "br": "Spotify indisponível.",
        "en": "Spotify is unavailable."
    },
    "spotify_item": {
        "br": "Spotify retornou um item inválido.",
        "en": "Spotify returned an invalid item."
    },
    "spotify_playback": {
        "br": "Spotify retornou uma reprodução inválida.",
        "en": "Spotify returned invalid playback data."
    },
    "spotify_queue": {
        "br": "Spotify retornou uma fila inválida.",
        "en": "Spotify returned an invalid queue."
    },
    "item_unavailable": {
        "br": "Item indisponível",
        "en": "Unavailable item"
    },
    "oauth_error": {
        "br": "Falha na autorização. Confira as credenciais e autorize a conta novamente.",
        "en": "Authorization failed. Check credentials and authorize the account again."
    }
}


def translate(language, key, **values):
    return MESSAGES[key][language].format(**values)


class MessageError(Exception):
    def __init__(self, key, **values):
        self.key = key
        self.values = values
        super().__init__(translate("br", key, **values))


def error_message(language, error):
    if isinstance(error, MessageError):
        return translate(language, error.key, **error.values)
    message = str(error)
    for translations in MESSAGES.values():
        if translations["br"] == message:
            return translations[language]
    if language == "br":
        return message
    from .oauth import OAuthError
    return translate(language, "oauth_error" if isinstance(error, OAuthError) else "spotify_unavailable")
