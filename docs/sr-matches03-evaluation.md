# Avaliação do SR MATCHES 03

Avaliação de 10/10/2026, comparando a versão 0.1.8 (`8735ee4`) com a 0.1.9.
Pedidos, títulos e artistas foram reproduzidos do feedback. Respostas do
Spotify foram simuladas; não houve consultas autenticadas nem inclusão de
músicas em uma conta real. As pontuações são regras de similaridade, não
probabilidades estatísticas. O limite de aceitação permanece em 90%.

| Pedido | Resultado | Bot 0.1.8 | Bot 0.1.9 |
| --- | --- | ---: | ---: |
| `classy teardrop` | Tear Drop — CLASSy | 0% | 97,75% |
| `meovv ddi do ri` | DDI RO RI — MEOVV | 0% | 96,20% |
| `kiikii 404 new era` | 404 (New Era) — KiiiKiii | 99,00% | 99,00% |

## Onde a pontuação era rejeitada

**Tear Drop:** artista 100%. O título era comparado como um termo
(`teardrop`) contra dois (`tear`, `drop`): cobertura de 50%, abaixo de 80%,
e nenhuma palavra individual correspondia. A validação rejeitava o título,
zerando o resultado final. Reduzir o limite de 90% não resolveria essa regra.

**DDI RO RI:** artista 100%. As palavras `ddi` e `ri` correspondiam exatamente,
mas `do` contra `ro` recebia zero: palavras com menos de quatro caracteres
não toleravam erros. Como todo termo do pedido precisa corresponder,
a validação rejeitava o título inteiro e a pontuação final era zero.

**KiiiKiii:** título 100%. A repetição de letras no nome do artista era
normalizada (`kiiikiii` → `kiikii`), recebendo 96%. A combinação de 75% do
título e 25% do artista resultava em `0,75 × 1 + 0,25 × 0,96 = 0,99`.
Esse caso já era aceito na versão 0.1.8 para os metadados apresentados.
O registro isolado não permite determinar a versão executada nem os demais
metadados retornados no momento da sugestão.

## Melhorias implementadas

Diferenças de espaços são comparadas pelas letras do título inteiro, na
mesma ordem. Só são elegíveis palavras alfabéticas de pelo menos três letras;
negações são protegidas, números não são concatenados e versões precisam
corresponder. Não são geradas siglas nem descartadas palavras.
`Tear Drop/teardrop` recebe 97% no título; com artista 100%, resulta em 97,75%.
Grafias com os mesmos espaços recebem pontuação maior e são preferidas.

Um único erro de substituição em uma palavra de duas ou três letras pode
ser reconhecido com contexto. São exigidos:

- Título inteiro com o mesmo número de palavras, na mesma ordem.
- Pelo menos duas outras palavras do título exatamente iguais.
- Artista informado com pontuação de pelo menos 96%.
- Ausência de erro em números, negações ou indicação de versão.
- Um único título plausível entre os candidatos retornados pelo Spotify.

Não é uma tolerância global para palavras curtas. Pedidos apenas pelo título,
artistas com erro comum, siglas com erro, duas palavras diferentes e títulos
parciais continuam exigindo confirmação. A ambiguidade considera título e
artistas, permitindo entradas repetidas da mesma música em álbuns distintos.
Uma correspondência pelos critérios anteriores tem preferência e não depende
da nova regra de palavra curta.

No exemplo MEOVV, a substituição recebe 84% e as outras duas palavras, 100%.
A média, combinada com a cobertura completa do título, resulta em
`0,95 × ((1 + 0,84 + 1) / 3) + 0,05 = 0,949333…`.
Com artista 100%, a pontuação final é
`0,75 × 0,949333… + 0,25 = 0,962`, ou 96,20%.

## Validação

Os três pedidos entram na fila nos testes mesmo com um resultado incorreto
em primeiro lugar. Também foram testados artista no começo, no final e entre
as palavras do título, espaços nos dois sentidos, versões explícitas,
artista errado, palavras extras/repetidas/ausentes, erros em negações e
números, mais de um erro curto, dois títulos plausíveis e álbuns duplicados.
Grafias exatas vencem candidatos aproximados. Todos os 132 testes passaram.

Nenhuma API ou dependência foi acrescentada. Estes casos validam o
comportamento nos candidatos simulados; não medem a taxa global de acerto
das buscas reais.
