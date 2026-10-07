# DangerBot

Bot Twitch em Python: `!sr <música e artista>` busca o primeiro resultado na Spotify Web API, aplica regras e salva o pedido na fila local. Um worker envia os pedidos em ordem à fila do Spotify. `!queue` mostra quantos pedidos aguardam envio.

## Preparação

Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

Cadastre um aplicativo no [Spotify Dashboard](https://developer.spotify.com/dashboard) e outro no [Twitch Developer Console](https://dev.twitch.tv/console/apps). Configure em ambos a redirect URI `http://127.0.0.1:8888/callback`. Preencha os client IDs e secrets no `.env`, além de `TWITCH_CHANNEL` e `TWITCH_BOT_NAME`. A conta autorizada na Twitch deve ser a conta de `TWITCH_BOT_NAME`.

```powershell
python main.py auth spotify
python main.py auth twitch
python main.py run
```

Autorize cada plataforma no navegador. Spotify solicita `user-modify-playback-state user-read-playback-state`; Twitch solicita `chat:read chat:edit`. Tokens são salvos em `data/`, renovados automaticamente e persistidos quando o refresh token é substituído. A autorização inicial e o preenchimento das credenciais precisam ser feitos localmente pelo dono das contas.

A [fila do Spotify](https://developer.spotify.com/documentation/web-api/reference/add-to-queue) exige Premium. Abra um dispositivo e inicie uma música antes de usar o bot; `SPOTIFY_DEVICE_ID` é opcional. O acesso também depende das restrições e usuários autorizados do aplicativo no Spotify Dashboard. O callback usa [loopback explícito](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri) e valida OAuth state.

## Regras

- `USER_COOLDOWN` e `GLOBAL_COOLDOWN`: segundos, consumidos apenas após aceitar o pedido.
- `REQUEST_PERMISSION`: everyone, subscriber, moderator ou broadcaster. Moderadores e broadcaster também passam pela regra subscriber.
- `BLACKLIST_USERS`: logins separados por vírgulas.
- `BLACKLIST_TRACKS`: IDs ou URIs Spotify separados por vírgulas.
- `BLACKLIST_ARTISTS`: nomes exatos ou IDs separados por vírgulas.
- `ALLOW_EXPLICIT`, `MAX_DURATION_SECONDS` e `MAX_PENDING_REQUESTS` limitam os pedidos.

Duplicatas são bloqueadas enquanto aguardam envio. As regras são carregadas na inicialização; reinicie após editar o `.env`. Cooldowns ficam em memória e reiniciam junto com o processo.

## Arquitetura e limites

`config.py` carrega regras; `oauth.py` autoriza e renova tokens; `spotify.py` encapsula a Web API; `queue.py` mantém a fila SQLite; `service.py` aplica políticas e despacha pedidos; `twitch.py` conecta via IRC TLS, identifica cargos, responde a PING e reconecta.

A fila própria funciona como uma outbox persistente: pending → sending → sent. **Sent significa entregue ao Spotify, não reproduzido.** Ela não sincroniza a reprodução nem permite remover músicas já enviadas. Falhas definitivas de entrega mantêm o pedido pendente e suspendem novas tentativas por alguns segundos; HTTP 429 respeita Retry-After. O pedido mais antigo bloqueia os posteriores enquanto estiver pendente.

Timeouts/erros de servidor durante a adição e reinícios com um pedido em sending o marcam como uncertain. Como a adição no Spotify não oferece chave de idempotência, esses pedidos precisam de revisão manual no SQLite antes de eventual reenvio. Evita-se duplicação automática. Execute apenas uma instância do bot por banco. O chat confirma a aceitação local; falhas posteriores não geram aviso individual.

Tokens são arquivos locais sem criptografia: `.env` e `data/` são ignorados pelo Git. Não compartilhe esses arquivos. Nenhuma credencial é incluída no repositório.

## Validação

```powershell
python -m unittest discover -s tests -v
```

Testes usam HTTP simulado e SQLite temporário; verificam cooldowns, permissões, blacklist, duplicatas, limite da fila, refresh concorrente, HTTP 401/429, entrega incerta e recuperação após reinício. A integração real requer as contas autorizadas e um dispositivo Spotify.
