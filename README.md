# DangerBotanger

Bot Twitch em Python: `!sr <música e artista>` compara até 10 resultados na Spotify Web API, aplica regras e salva o pedido na fila local. Um worker envia os pedidos em ordem à fila do Spotify. `!np` consulta a reprodução atual e `!queue` consulta a fila real do Spotify.

Comandos no chat:

| Comando | Ação | Permissão |
| --- | --- | --- |
| `!sr <música e artista>` | Solicita música | Configurada em REQUEST_PERMISSION |
| `!sr confirmar` / `!sr confirm` | Aceita a sugestão pendente do seu último pedido, em até 60 segundos | Configurada em REQUEST_PERMISSION |
| `!help` | Lista os comandos e indica quais são restritos a moderadores/dono | Todos |
| `!np` / `!song` / `!currentsong` | Mostra música, artista e quem pediu via !sr, quando identificado; informa se está pausado | Todos |
| `!queue [página]` | Mostra a fila real do Spotify, 5 itens por página | Todos |
| `!skip` | Pula a música atual no Spotify | Moderadores e dono do canal |
| `!play` | Retoma a reprodução atual do Spotify, sem selecionar outra música | Moderadores e dono do canal |
| `!pause` | Pausa a reprodução atual do Spotify | Moderadores e dono do canal |
| `!volume <0-60>` | Ajusta o volume do Spotify; valores fora do intervalo são recusados | Moderadores e dono do canal |
| `!setlang br` / `!setlang en` | Define português brasileiro ou inglês para as respostas no chat | Moderadores e dono do canal |

O idioma padrão é `br`. A escolha de `!setlang` vale para todo o canal, muda imediatamente e fica salva em data/queue.sqlite3 para os próximos reinícios. Nomes de músicas e artistas não são traduzidos. Os comandos continuam com os mesmos nomes nos dois idiomas. Logs de terminal e instruções de configuração permanecem em português.

A busca compara as palavras do título e do artista, aceitando ambas as ordens e ignorando diferenças de maiúsculas, acentos e pontuação. Pequenos erros de digitação em palavras longas são tolerados. Correspondências suficientes entram diretamente na fila. Quando nenhuma passa na validação, o bot mostra o primeiro resultado reproduzível do Spotify como sugestão, sem adicionar nem consumir cooldown. Isso permite pedidos por descrição, como `!sr musica triste do naruto`: se a sugestão estiver correta, o mesmo viewer envia `!sr confirmar` (ou `!sr confirm`) em até 60 segundos. A confirmação usa a faixa sugerida, sem uma nova busca, e verifica novamente permissões, cooldown, bloqueios, duplicatas e limites da fila. Outro pedido substitui a sugestão anterior; sugestões não sobrevivem ao reinício.

Se nenhum resultado for retornado, refine o pedido. A validação não garante identificar a intenção em pedidos ambíguos ou títulos iguais de artistas diferentes; informe o artista para melhorar a precisão. As palavras `confirmar` e `confirm`, usadas sozinhas após `!sr`, ficam reservadas para a confirmação; para pedir uma música com esse título, inclua o artista.

Exemplo: `!volume 30` ajusta para 30%; `!volume 0` silencia; `!volume 61` é recusado. O limite de 60 vale para comandos do bot, não para alterações manuais. O comando usa o dispositivo configurado em SPOTIFY_DEVICE_ID, ou o ativo quando não configurado, e as permissões OAuth existentes. O dispositivo precisa permitir controle de volume pela API. Consulte a [referência de volume do Spotify](https://developer.spotify.com/documentation/web-api/reference/set-volume-for-users-playback).

`!play` e `!pause` não recebem argumentos. Usam o dispositivo configurado em SPOTIFY_DEVICE_ID, ou o ativo quando não configurado. `!play` retoma o contexto atual; se não houver dispositivo/contexto disponível, abra o Spotify e inicie uma música manualmente. As permissões OAuth existentes já permitem esses comandos. Consulte [retomar reprodução](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback) e [pausar reprodução](https://developer.spotify.com/documentation/web-api/reference/pause-a-users-playback).

O bot acompanha a reprodução a cada 10 segundos e ao consultar !np. A associação entre uma reprodução e um pedido fica salva no SQLite; consultas repetidas não consomem pedidos repetidos da mesma música. Exemplo: `Tocando agora: Song - Artist. Pedida por @viewer.` Uma música sem pedido ativo identificado é exibida sem solicitante. Pedidos que começam a tocar liberam uma vaga do limite por usuário. O Spotify não fornece IDs de ocorrências: adições manuais da mesma faixa, repetições não observadas enquanto o bot está desligado e retrocessos de mais de 5 segundos podem tornar a atribuição ambígua. A associação é feita pelo histórico e pelas mudanças observadas de URI/progresso/dispositivo, não por autoria fornecida pelo Spotify.

Exemplo: `!queue 2` mostra os itens 6 a 10 da fila retornada pela API. As posições mudam conforme a reprodução avança e não são IDs dos pedidos locais. A fila consultada pode incluir músicas da playlist/contexto de reprodução, além dos pedidos do bot. A API retorna uma visão da fila, que pode não incluir todos os itens mostrados pelo aplicativo. As consultas refletem a conta Spotify autorizada; SPOTIFY_DEVICE_ID direciona operações de escrita, não as consultas de reprodução e fila.

A Web API documentada oferece consulta e adição à fila, mas não remoção de seus itens. Remova músicas diretamente pelo aplicativo Spotify. `!skip` pula somente a música atual, tem cooldown compartilhado de 5 segundos e respeita Retry-After em HTTP 429; não reenvia automaticamente uma operação com resultado incerto. As permissões Spotify necessárias já fazem parte do OAuth existente. Consulte a [referência do Player](https://developer.spotify.com/documentation/web-api/reference/get-queue).

## Preparação

### Executável para compartilhar (Windows 64 bits)

Distribua `dist/DangerBotanger-0.1.0-Windows-x64.zip`. Ele contém `DangerBotanger.exe` e um guia de primeiro uso. Seu amigo extrai o ZIP, abre o executável, preenche suas credenciais e autoriza as contas pela interface. Python e as dependências já estão dentro do executável.

A versão empacotada salva o `.env`, os tokens, o certificado e a fila em `%LOCALAPPDATA%\DangerBotanger`, independentemente da pasta em que o executável foi colocado. Atualizar o executável preserva esses dados. A versão em código continua usando a pasta do projeto. Para migrar os dados atuais, feche o bot e copie seu `.env` e sua pasta `data/` para a pasta de configurações do executável; confira caminhos de certificados personalizados. Compartilhe somente o ZIP, sem seus dados locais.

Para gerar novamente no Windows, usando o ambiente do projeto:

```powershell
python -m pip install -e ".[build]"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1
```

O build usa [PyInstaller](https://pyinstaller.org/en/stable/usage.html), inclui a interface Tkinter e a geração do certificado, e não inclui `.env`, `data/`, testes ou feedbacks. A arquitetura do executável segue o Python utilizado no build; este pacote foi gerado com Python Windows x64. O número da versão está em `pyproject.toml`, `scripts/windows-version.txt` e nos nomes do ZIP/guia; atualize-os juntos nas próximas versões.

### Interface gráfica

Depois de instalar as dependências (`python -m pip install -e .` no ambiente virtual), dê dois cliques em `abrir-interface.bat`. Também é possível abrir com `python main.py gui`. O lançador usa o Python da pasta `.venv` e abre a janela sem terminal. Não é necessário criar ou editar o `.env` manualmente: a interface carrega o arquivo existente ou inicia uma configuração vazia com os valores padrão.

1. Na aba **Twitch**, preencha o canal da live, o login da conta do bot e as credenciais do aplicativo. O botão **Abrir painel de aplicativos** leva ao cadastro do aplicativo. Cadastre a mesma URL de callback mostrada na interface.
2. Na aba **Spotify**, preencha as credenciais do aplicativo e cadastre sua URL de callback. O ID do dispositivo é opcional.
3. Ajuste permissões, intervalos, limites e bloqueios na aba **Pedidos**. A aba **Avançado** contém os caminhos do certificado local.
4. Clique em **Salvar configurações**. Os secrets ficam ocultos por padrão. Campos e opções ficam bloqueados enquanto uma operação está em andamento; pare o bot antes de alterar a configuração.
5. Use **Autorizar Spotify** e **Autorizar Twitch**, um de cada vez. Cada botão salva os valores e abre o navegador. Para Twitch, entre com a conta do bot. Você tem 3 minutos para concluir cada autorização. O certificado HTTPS local é criado automaticamente, sem exigir OpenSSL; no aviso do navegador, prossiga somente para seu callback local configurado. Um certificado existente não é substituído.
6. Abra o Spotify e inicie uma música, depois clique em **Iniciar bot**. A área **Atividade** mostra o andamento. **Parar / cancelar** encerra o bot ou cancela a autorização; fechar a janela também encerra a operação.

A indicação **autorização salva** significa que existe um token local, não que a conexão já foi validada. Ao trocar as credenciais de um aplicativo ou a conta utilizada, autorize novamente. Configurações, tokens, certificado e fila permanecem locais; cada streamer deve configurar suas próprias contas. Ao compartilhar o projeto, não inclua `.env`, `data/` ou `.venv/`. O `.bat` exige o ambiente Python preparado; para amigos que não usam Python, compartilhe o ZIP do executável descrito acima.

Se usar uma conta separada para o bot, **Canal da live** é o login do streamer e **Conta do bot** é o login dessa outra conta. O navegador precisa estar conectado à conta do bot no momento da autorização. O botão Twitch solicita novamente o consentimento (`force_verify=true`) e verifica a identidade antes de salvar o token. Se outra conta autorizar, o token anterior é preservado e a mensagem informa a conta recebida e a esperada. Saia da conta atual no navegador, entre na conta do bot e clique em **Autorizar Twitch** novamente. O aplicativo pode continuar cadastrado na conta do streamer; a conta que autoriza precisa corresponder à conta do bot. Consulte a [autorização OAuth da Twitch](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth) e a [validação de tokens](https://dev.twitch.tv/docs/authentication/validate-tokens/).

### Linha de comando

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

- `USER_COOLDOWN`: 5 segundos por padrão entre pedidos aceitos por usuário. `GLOBAL_COOLDOWN`: 5 segundos por padrão entre pedidos do canal. O broadcaster ignora ambos e seus pedidos não iniciam cooldown global; moderadores seguem os intervalos.
- `MAX_USER_REQUESTS`: 10 pedidos simultâneos por pessoa, incluindo broadcaster. O bot consulta a fila Spotify a cada pedido e associa suas URIs aos solicitantes salvos no SQLite. Músicas que começam a tocar deixam de ocupar uma vaga. Envios pendentes/em andamento e entregas incertas reservam vagas para impedir ultrapassar 10 durante o envio.
- `REQUEST_PERMISSION`: everyone, subscriber, moderator ou broadcaster. Moderadores e broadcaster também passam pela regra subscriber.
- `BLACKLIST_USERS`: logins separados por vírgulas.
- `BLACKLIST_TRACKS`: IDs ou URIs Spotify separados por vírgulas.
- `BLACKLIST_ARTISTS`: nomes exatos ou IDs separados por vírgulas.
- `ALLOW_EXPLICIT`, `MAX_DURATION_SECONDS` e `MAX_PENDING_REQUESTS` limitam os pedidos.

Duplicatas são bloqueadas tanto enquanto aguardam envio quanto quando o resultado selecionado pela busca já está na fila de reprodução retornada pelo Spotify. A comparação usa a URI da faixa e inclui músicas adicionadas manualmente ou pelo autoplay; o bot não escolhe outro resultado para contornar o bloqueio. Pedidos rejeitados não consomem cooldown. As regras são carregadas na inicialização; reinicie após editar o `.env`. Cooldowns ficam em memória e reiniciam junto com o processo.

O limite por usuário usa a fila real, além das reservas de envio; MAX_PENDING_REQUESTS continua sendo um limite separado da outbox local. A atribuição de pedidos sobrevive aos reinícios. O Spotify não fornece solicitantes nem IDs de ocorrências: adições manuais da mesma música são ambíguas. A contagem depende da visão da fila retornada pela API. Em snapshots com 20 ou mais itens, pedidos ausentes continuam reservados por precaução, pois podem estar na parte omitida. Em filas menores, pedidos confirmados como ausentes liberam vagas; um envio recente ainda não observado tem 60 segundos de tolerância. Entregas incertas sem confirmação permanecem reservadas até revisão manual ou identificação na fila. Consultas que falham impedem novos pedidos.

Registros antigos, anteriores ao controle de vagas e sem timestamp de envio, só contam se encontrados na fila atual. Se ausentes, são encerrados mesmo quando o Spotify retorna uma lista longa de autoplay. O histórico é preservado. Músicas automáticas sem um pedido correspondente nunca são atribuídas ao broadcaster apenas por ele ser dono da conta Spotify.

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
