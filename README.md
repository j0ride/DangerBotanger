# DangerBotanger

Bot Twitch em Python: `!sr <música e artista>` busca o primeiro resultado na Spotify Web API, aplica regras e salva o pedido na fila local. Um worker envia os pedidos em ordem à fila do Spotify. `!np` consulta a reprodução atual e `!queue` consulta a fila real do Spotify.

Comandos no chat:

| Comando | Ação | Permissão |
| --- | --- | --- |
| `!sr <música e artista>` | Solicita música | Configurada em REQUEST_PERMISSION |
| `!np` | Mostra música e artista atuais; informa se está pausado | Todos |
| `!queue [página]` | Mostra a fila real do Spotify, 5 itens por página | Todos |
| `!skip` | Pula a música atual no Spotify | Moderadores e dono do canal |
| `!setlang br` / `!setlang en` | Define português brasileiro ou inglês para as respostas no chat | Moderadores e dono do canal |

O idioma padrão é `br`. A escolha de `!setlang` vale para todo o canal, muda imediatamente e fica salva em data/queue.sqlite3 para os próximos reinícios. Nomes de músicas e artistas não são traduzidos. Os comandos continuam com os mesmos nomes nos dois idiomas. Logs de terminal e instruções de configuração permanecem em português.

Exemplo: `!queue 2` mostra os itens 6 a 10 da fila retornada pela API. As posições mudam conforme a reprodução avança e não são IDs dos pedidos locais. A fila consultada pode incluir músicas da playlist/contexto de reprodução, além dos pedidos do bot. A API retorna uma visão da fila, que pode não incluir todos os itens mostrados pelo aplicativo. As consultas refletem a conta Spotify autorizada; SPOTIFY_DEVICE_ID direciona operações de escrita, não as consultas de reprodução e fila.

A Web API documentada oferece consulta e adição à fila, mas não remoção de seus itens. Remova músicas diretamente pelo aplicativo Spotify. `!skip` pula somente a música atual, tem cooldown compartilhado de 5 segundos e respeita Retry-After em HTTP 429; não reenvia automaticamente uma operação com resultado incerto. As permissões Spotify necessárias já fazem parte do OAuth existente. Consulte a [referência do Player](https://developer.spotify.com/documentation/web-api/reference/get-queue).

## Preparação

No Windows, com o ambiente e as credenciais configurados, dê dois cliques em `iniciar-bot.bat` para abrir o PowerShell e iniciar o bot sem a IDE. O lançador usa o Python da pasta .venv e define a pasta do projeto como diretório de trabalho. A janela permanece aberta se ocorrer um erro. Para parar o bot, pressione Ctrl+C; para fechar o PowerShell, digite exit. É possível criar um atalho para o .bat na área de trabalho. Execute somente uma instância por vez.

Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

Cadastre um aplicativo no [Spotify Dashboard](https://developer.spotify.com/dashboard) e outro no [Twitch Developer Console](https://dev.twitch.tv/console/apps). No Spotify cadastre `http://127.0.0.1:8888/callback` (SPOTIFY_REDIRECT_URI); na Twitch cadastre `https://localhost:8888/callback` (TWITCH_REDIRECT_URI). O callback Twitch usa HTTPS com certificado local; o Spotify usa a exceção HTTP para loopback. Antes de autorizar a Twitch, gere o certificado com scripts/create-local-cert.ps1 (ou execute o comando OpenSSL contido no script se a política do PowerShell impedir scripts). OAUTH_TLS_CERT e OAUTH_TLS_KEY indicam os arquivos PEM em data/. O certificado é autoassinado e o navegador pode mostrar um aviso: prossiga apenas para o callback local https://localhost:8888. Nenhum certificado é instalado no Windows. O certificado expira em 365 dias. Preencha os client IDs e secrets no `.env`, além de `TWITCH_CHANNEL` e `TWITCH_BOT_NAME`. A conta autorizada na Twitch deve ser a conta de `TWITCH_BOT_NAME`.

```powershell
python main.py auth spotify
python main.py auth twitch
python main.py run
```

Autorize cada plataforma no navegador. Spotify solicita `user-modify-playback-state user-read-playback-state`; Twitch solicita `chat:read chat:edit`. Tokens são salvos em `data/`, renovados automaticamente e persistidos quando o refresh token é substituído. A autorização inicial e o preenchimento das credenciais precisam ser feitos localmente pelo dono das contas.

A [fila do Spotify](https://developer.spotify.com/documentation/web-api/reference/add-to-queue) exige Premium. Abra um dispositivo e inicie uma música antes de usar o bot; `SPOTIFY_DEVICE_ID` é opcional. O acesso também depende das restrições e usuários autorizados do aplicativo no Spotify Dashboard. O callback usa [loopback explícito](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri) e valida OAuth state.

## Regras

- `USER_COOLDOWN`: 30 segundos por padrão entre pedidos aceitos. `GLOBAL_COOLDOWN`: 5 segundos por padrão entre pedidos do canal. O broadcaster ignora ambos e seus pedidos não iniciam cooldown global; moderadores seguem os intervalos.
- `MAX_USER_REQUESTS`: 10 pedidos simultâneos por pessoa, incluindo broadcaster. O bot consulta a fila Spotify a cada pedido e associa suas URIs aos solicitantes salvos no SQLite. Músicas que começam a tocar deixam de ocupar uma vaga. Envios pendentes/em andamento e entregas incertas reservam vagas para impedir ultrapassar 10 durante o envio.
- `REQUEST_PERMISSION`: everyone, subscriber, moderator ou broadcaster. Moderadores e broadcaster também passam pela regra subscriber.
- `BLACKLIST_USERS`: logins separados por vírgulas.
- `BLACKLIST_TRACKS`: IDs ou URIs Spotify separados por vírgulas.
- `BLACKLIST_ARTISTS`: nomes exatos ou IDs separados por vírgulas.
- `ALLOW_EXPLICIT`, `MAX_DURATION_SECONDS` e `MAX_PENDING_REQUESTS` limitam os pedidos.

Duplicatas são bloqueadas enquanto aguardam envio. As regras são carregadas na inicialização; reinicie após editar o `.env`. Cooldowns ficam em memória e reiniciam junto com o processo.

O limite por usuário usa a fila real, além das reservas de envio; MAX_PENDING_REQUESTS continua sendo um limite separado da outbox local. A atribuição de pedidos sobrevive aos reinícios. O Spotify não fornece solicitantes nem IDs de ocorrências: adições manuais da mesma música são ambíguas. A contagem depende da visão da fila retornada pela API. Em snapshots com 20 ou mais itens, pedidos ausentes continuam reservados por precaução, pois podem estar na parte omitida. Em filas menores, pedidos confirmados como ausentes liberam vagas; um envio recente ainda não observado tem 60 segundos de tolerância. Entregas incertas sem confirmação permanecem reservadas até revisão manual ou identificação na fila. Consultas que falham impedem novos pedidos.

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
