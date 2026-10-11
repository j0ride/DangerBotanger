# Avaliação dos exemplos de SR MATCHES 02

Avaliação de 10/10/2026, comparando a versão 0.1.6 (commit `94774eb`),
a versão 0.1.7 e o RapidFuzz 3.14.6. Os nomes dos títulos e artistas foram
reproduzidos a partir do feedback. As respostas da API foram simuladas;
não houve consultas autenticadas ao Spotify nem inclusão de músicas na conta real.

As porcentagens abaixo são pontuações de similaridade, não probabilidades
estatísticas de a música estar correta. O limite de aceitação continua em 90%.

## Pontuações dos exemplos

| Pedido | Resultado | Bot 0.1.6 | RapidFuzz token_sort_ratio | RapidFuzz token_set_ratio | RapidFuzz WRatio | Bot 0.1.7 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `hitgs - aha` | A-HA! — HITGS | 0% | 94,74% | 94,74% | 90,00% | 100% |
| `txt setsuna hanabi` | セツナハナビ (Setsuna Hanabi) — TOMORROW X TOGETHER | 0% | 57,63% | 87,50% | 85,50% | 100% |

Para os cálculos do RapidFuzz, cada pedido e o texto `título - artistas`
passaram por `Spotify.words`, com casefold, remoção de acentos e pontuação,
e foram reconstruídos com espaços. O resultado depende tanto dessa
normalização quanto da função escolhida; não existe uma porcentagem única
de match do RapidFuzz.

O primeiro caso zerava porque `aha` não correspondia às palavras `a`, `ha`.
O segundo zerava porque `txt` não correspondia ao nome completo do artista.
A comparação por cobertura ainda contava o japonês e a romanização como
partes diferentes do título, em vez de duas grafias do mesmo nome.
Reduzir o limite não corrige essas rejeições estruturais.

## Por que não reduzir o limite de forma global

Controles simulados expõem erros que uma comparação única do texto inteiro aceita:

| Pedido | Resultado incorreto | RapidFuzz token_set_ratio | Bot 0.1.7 |
| --- | --- | ---: | ---: |
| `txt setsuna hanabi` | Setsuna Hanabi — Other Artist | 87,50% | 0% |
| `txt setsuna hanabi` | セツナハナビ (Setsuna Hanabi) - Live — TOMORROW X TOGETHER | 87,50% | 0% |
| `not cute anymore ilit` | CUTE ANYMORE — ILLIT | 87,18% | 0% |
| `cute cute ilit` | CUTE — ILLIT | 94,74% | 0% |

Um limite de 87,5% para `token_set_ratio` aceitaria o exemplo do TXT,
o artista errado e a versão ao vivo. Esse comparador também elimina
duplicações de palavras. A [documentação oficial do RapidFuzz](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html)
explica que um subconjunto de palavras pode alcançar 100% mesmo com conteúdo
adicional. Por isso, título, artista, palavras repetidas e versão precisam
de validações próprias.

## Mudanças implementadas

- Grafias alternativas com pontuação entre letras: `A-HA`, `AHA`, `AC/DC`, `ACDC`, `Don't`, `Dont`. Espaços e separadores numéricos não são concatenados.
- Siglas exatas das iniciais de artistas com pelo menos três palavras, obtidas do nome do próprio candidato: `TXT` / `TOMORROW X TOGETHER`, `RATM` / `Rage Against The Machine`. Não se aplica tolerância de erro às siglas nem se presume uma abreviação de duas letras.
- Títulos impressos em dois alfabetos, como `セツナハナビ (Setsuna Hanabi)` e a ordem inversa, podem ser comparados por qualquer das grafias. A romanização precisa estar no próprio resultado; não é gerada ou adivinhada.
- Sufixos e informações de versão são preservados. Um pedido sem `live`, `remix`, `cover` ou outra versão reconhecida precisa confirmar um resultado que tenha essa informação, e uma versão pedida precisa estar no resultado.
- As mesmas grafias alternativas também são usadas na busca por título de vídeo do YouTube.

A comparação por cobertura de termos distintos permanece; não foi acrescentada
uma dependência de produção do RapidFuzz. Ele foi instalado apenas em
`build/rapidfuzz-evaluation`, ignorado pelo Git e pelo empacotamento, para a comparação.

## Desempenho e validação

Microbenchmark local no Python 3.14.1, medindo lotes de dez comparações da
amostra, com 50 repetições e o melhor tempo médio de três séries:

| Comparador | Tempo por lote de dez |
| --- | ---: |
| Bot 0.1.6 | 3,70 ms |
| Bot 0.1.7 | 5,38 ms |
| RapidFuzz token_set_ratio, textos já normalizados | 0,022 ms |

O RapidFuzz é mais rápido nesse microbenchmark, mas executa menos validações;
esses tempos não incluem consultas à API ou operações da fila. Com dez
candidatos por busca, o custo local observado do algoritmo completo permanece
pequeno. A precisão dos exemplos, e não a velocidade, motivou esta mudança.

Os testes reproduzem os dois pedidos chegando à fila mesmo quando o primeiro
resultado é errado. Também cobrem ordem invertida, artista intercalado,
alfabetos alternativos, siglas, títulos com pontuação, artista errado, sigla
com erro, palavras ausentes, versões e o fluxo de YouTube. Todos os 120 testes
passaram. A amostra valida esses comportamentos; não mede a taxa de acerto
de todas as pesquisas reais.
