# Especificação do DangerBotanger 0.2

> Modelo para preencher antes da implementação. Os campos entre colchetes representam informações a definir.

| Campo | Valor |
| --- | --- |
| Versão-alvo | 0.2.0 |
| Versão-base | 0.1.9 |
| Status | Rascunho |
| Responsável | [Nome] |
| Data / revisão | [AAAA-MM-DD / número] |

## Como preencher

Descreva o resultado desejado e use uma seção por funcionalidade, com IDs estáveis como `F-01`. Copie o bloco de funcionalidade para cada item e substitua os campos entre colchetes. Quando algo não se aplicar, escreva “não se aplica”; registre as dúvidas em decisões pendentes.

Consulte o [README](../README.md) e o [guia de uso](guia-de-uso.md) para distinguir o comportamento atual das mudanças propostas. Relatos de falha devem trazer entrada, resposta observada e contexto. Os casos de [SR MATCHES 02](sr-matching-evaluation.md) já possuem correções documentadas; uma nova falha precisa ser reproduzida na versão-base.

## Objetivo da versão

[Qual problema esta versão deve resolver, para quem e qual resultado permitirá avaliar o sucesso?]

### Escopo

- [Resultado ou capacidade que deve ser entregue.]
- [Outro resultado, se necessário.]

### Fora do escopo

- [Item que não será incluído nesta versão.]

## Lista de funcionalidades

Prioridades: **P0** = necessária para lançar; **P1** = importante; **P2** = opcional. Dependências devem citar o ID de outra funcionalidade ou uma condição externa.

| ID | Funcionalidade | Prioridade | Dependências | Situação |
| --- | --- | --- | --- | --- |
| F-01 | [Nome curto] | [P0/P1/P2] | [IDs ou nenhuma] | [Proposta/em análise/definida] |

## F-01 — [Nome da funcionalidade]

### Contexto e resultado

- **Tipo:** [Nova funcionalidade / melhoria / correção / mudança incompatível.]
- **Usuário e permissão:** [Viewer, subscriber, moderador ou dono; quem pode usar.]
- **Problema atual:** [O que acontece hoje, com um exemplo se possível.]
- **Resultado esperado:** [O que deve acontecer na versão 0.2.]
- **Ponto de uso:** [Comando do chat, interface gráfica, terminal ou processo automático.]

### Comportamento

- **Gatilho e entradas:** [Como começa; argumentos, formatos e valores aceitos.]
- **Fluxo principal:** [Passos da interação até concluir a ação.]
- **Alternativas e falhas:** [Entrada inválida, falta de permissão, ausência de dispositivo, erro de API, timeout e cancelamento, conforme aplicável.]
- **Regras:** [Limites, cooldown, duplicatas, ordenação, bloqueios e relação com regras existentes.]
- **Resposta ao usuário:** [Mensagem ou mudança visível na interface; definir português e inglês quando houver resposta no chat.]
- **Dados:** [O que precisa ser salvo, por quanto tempo e o que ocorre após reiniciar.]
- **Integrações e dependências:** [Twitch, Spotify ou outro serviço; permissões e restrições necessárias.]

### Exemplos concretos

| Contexto / entrada | Resultado atual | Resultado esperado |
| --- | --- | --- |
| [Usuário, cargo, estado da fila e ação] | [Resposta ou estado observado] | [Resposta e efeito esperado] |
| [Entrada inválida ou falha relevante] | [Comportamento atual] | [Erro esperado e estado que deve permanecer] |

### Critérios de aceitação

Escreva condições observáveis. Indique a resposta ao usuário e os efeitos na fila ou nos dados quando forem relevantes.

- [ ] **CA-01 — Fluxo principal:** dado [contexto], quando [ação], então [resposta e efeito verificável].
- [ ] **CA-02 — Regra ou falha:** dado [restrição/falha], quando [ação], então [tratamento esperado].
- [ ] **CA-03 — Persistência ou regressão:** dado [estado existente], quando [reinício ou interação com recurso atual], então [resultado esperado].

### Compatibilidade e validação

- **Comportamentos existentes afetados:** [Comandos, permissões, busca, configuração ou interface.]
- **Estrutura afetada:** [Módulos prováveis, tabelas ou arquivos, se conhecidos; pode ficar “a definir”.]
- **Migração necessária:** [Dados/configurações a converter; ou não se aplica.]
- **Verificação:** [Teste automatizado, roteiro manual ou integração real; dados necessários para reproduzir.]
- **Limitações e dúvidas:** [O que ainda exige decisão antes da implementação.]

<!-- Copie a seção F-01 completa para cada funcionalidade adicional. -->

## Requisitos gerais da versão

Defina apenas os requisitos que afetam a versão inteira. Evite metas vagas; forneça cenário e limite mensurável quando necessário.

| Área | Requisito / decisão |
| --- | --- |
| Desempenho | [Carga esperada, tempo aceitável e forma de medição.] |
| Confiabilidade | [Como tratar falhas, reconexão, entregas incertas e reinícios.] |
| Credenciais e dados | [Armazenamento, acesso, retenção e tratamento de logs.] |
| Idiomas e interface | [Mensagens, idiomas e mudanças de navegação.] |
| Plataformas e distribuição | [Sistemas suportados, Python e executável Windows.] |

## Compatibilidade e migração da 0.1.x

| Item | Preservar / migrar / descontinuar | Ação necessária |
| --- | --- | --- |
| Comandos e permissões | [Decisão] | [Mudanças e orientação ao usuário.] |
| Configuração `.env` | [Decisão] | [Novos campos, valores padrão e campos removidos.] |
| Tokens e autorizações OAuth | [Decisão] | [Se será necessário autorizar novamente e por quê.] |
| Banco SQLite e histórico | [Decisão] | [Conversão, backup, recuperação e tratamento de pedidos pendentes/incertos.] |
| Dados do executável Windows | [Decisão] | [Compatibilidade com a pasta de dados atual.] |

## Decisões pendentes

| ID | Questão | Opções / impacto | Responsável | Decisão |
| --- | --- | --- | --- | --- |
| D-01 | [Questão que afeta a implementação] | [Alternativas e consequências] | [Nome] | [A definir] |

## Condições para lançar a 0.2

- [ ] Todas as funcionalidades P0 atendem aos critérios de aceitação.
- [ ] Os comandos e fluxos atuais afetados foram verificados.
- [ ] A migração e a recuperação de dados foram validadas, quando aplicáveis.
- [ ] A documentação e as instruções de distribuição refletem a versão entregue.
