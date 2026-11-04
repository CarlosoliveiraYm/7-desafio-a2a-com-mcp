# PRD — Central de Salas (Agente A2A com MCP)

| Campo | Valor |
|---|---|
| Produto | Central de Salas — reserva de salas de reunião da Hill Valley Tech |
| Documento | Product Requirements Document (o **quê** e o **porquê**) |
| Documentos relacionados | [`ADR.md`](ADR.md) (decisões), [`FDD.md`](FDD.md) (desenho técnico) |
| Fonte de verdade | `prompt.md`, `validador/validar.py`, `exemplos/wire/`, `dados/` |
| Status | Rascunho para revisão |
| Data | 2026-11-04 |

---

## 1. Visão geral

A Hill Valley Tech tem cinco salas de reunião controladas por uma planilha compartilhada que ninguém respeita. A empresa decidiu expor as salas como **capacidade de agente**, para que qualquer assistente interno consiga reservar sem abrir a planilha.

A arquitetura já foi decidida e não está em discussão:

- as ferramentas de sala viram um **servidor MCP** (`central-de-salas`);
- a capacidade de reservar vira um **agente A2A** (`Central de Salas`) que qualquer outro agente da empresa pode descobrir e chamar.

O agente tem **papel triplo**: é servidor A2A para fora, host/cliente MCP para dentro e, no meio, a **ponte** que traduz o estado de um protocolo para o outro.

### 1.1 O problema real: estado entre protocolos sem sessão

Nenhum dos dois protocolos tem sessão:

| Protocolo | Mecanismo de estado | Onde vive |
|---|---|---|
| MCP (rev. 2026-07-28) | MRTR: `resultType: "input_required"` + `inputRequests` + `requestState` opaco | Viaja com o cliente; o servidor não guarda nada |
| A2A v1.0 | `Task` com `id`, `contextId`, `status.state`, `history` e `artifacts` | Mantida pelo agente |

Quando o servidor MCP responde que precisa de mais informação, o agente **não pode responder sozinho nem travar esperando**. Ele precisa:

1. interromper a Task (`TASK_STATE_INPUT_REQUIRED`);
2. devolver a pergunta ao cliente A2A;
3. guardar o `requestState` ligado àquela Task;
4. quando a resposta chegar, repetir o `tools/call` original com **id JSON-RPC novo**, levando `inputResponses` e o `requestState` sem modificação.

Esse encontro dos dois mecanismos de estado é o núcleo do produto.

---

## 2. Objetivos

| # | Objetivo | Medida de sucesso |
|---|---|---|
| O1 | Servidor MCP em Streamable HTTP com 3 tools, 1 resource, os dois tipos de erro e MRTR completo na reserva | Checks 01–20 do validador em PASS |
| O2 | Agente host MCP que descobre tools em runtime e lê a política pelo resource | Log do MCP mostra `tools/list` antes do 1º `tools/call`; artifact traz `politica` |
| O3 | Mesmo agente como servidor A2A v1.0 (Agent Card, `SendMessage`, `GetTask`) com Task passando por `TASK_STATE_INPUT_REQUIRED` | Checks 21–36 em PASS |
| O4 | Propagação do `traceparent` do cliente A2A até o servidor MCP, registrada em log | Trace-id impresso pelo validador aparece no stderr do MCP |
| O5 | README com comandos de subida a partir de clone limpo e explicação da ponte | Avaliador sobe tudo seguindo só o README |
| O6 | Validador do starter passando em todas as verificações | `python3 validador/validar.py ...` com exit code 0 (36/36) |

## 3. Não objetivos (fora de escopo)

- Autenticação e autorização em qualquer camada (sem OAuth, token, `securitySchemes` real no card).
- Streaming: nada de `SendStreamingMessage`, SSE de resposta ou push notification.
- Refinamento de Task, `referenceTaskIds` e continuidade entre Tasks diferentes.
- Card estendido autenticado e assinatura JWS do card.
- Prompts do MCP, subscriptions e progress.
- Container, compose, deploy e gateway.
- Spans e atributos de OpenTelemetry (só propagação de `traceparent` + log).
- Concorrência de escrita, transações e corrida entre reservas no mesmo milissegundo.
- Persistência em disco das reservas e sobrevivência delas a restart.
- Interface gráfica de qualquer tipo.
- Linguagem natural: o pedido tem formato fixo; **nenhum LLM** no caminho de execução.

---

## 4. Atores

| Ator | Papel | Interage com |
|---|---|---|
| **Cliente A2A** | Qualquer agente da empresa (no teste, o validador ou `curl`). Descobre o card, envia `SendMessage`, consulta `GetTask`, responde pausas. | Agente (porta 7300) |
| **Agente Central de Salas** | Servidor A2A por fora; host MCP por dentro; ponte MRTR ↔ Task. Traduz protocolo, **não decide domínio**. | Cliente A2A, Servidor MCP |
| **Servidor MCP `central-de-salas`** | Dono do domínio: salas, reservas, política, conflitos e alternativas. Stateless entre requests. | Agente, validador (direto) |
| **Avaliador / validador** | Roda `validador/validar.py` (36 checks) e o "Fluxo do avaliador" manual (restart, logs, curl). | Os dois processos |

---

## 5. Domínio

### 5.1 Salas (`dados/salas.json`, fixas)

| id | nome | capacidade | recursos |
|---|---|---|---|
| `sala-aquario` | Aquario | 4 | tv |
| `sala-porao` | Porao | 6 | quadro |
| `sala-garagem` | Garagem | 12 | tv, quadro |
| `sala-fusca` | Fusca | 12 | tv |
| `sala-mirante` | Mirante | 20 | tv, quadro, camera |

### 5.2 Reservas iniciais (`dados/reservas.json`)

| id | sala | início | fim | responsável |
|---|---|---|---|---|
| `res-0001` | `sala-garagem` | 2026-11-03T14:00:00-03:00 | 2026-11-03T15:00:00-03:00 | Marty |
| `res-0002` | `sala-fusca` | 2026-11-03T16:00:00-03:00 | 2026-11-03T17:00:00-03:00 | Jennifer |

Novas reservas recebem ids sequenciais a partir da última existente (`res-0003`, `res-0004`, …), como mostram os exemplos de wire.

### 5.3 Política de uso (`dados/politica-de-uso.md`, versão `2026-11-01`)

1. Reservas somente entre **08:00 e 20:00**, horário de São Paulo (**-03:00**) — início e fim precisam estar dentro da janela.
2. Duração máxima de **2 horas** por reserva.
3. Uma sala **não pode ter duas reservas sobrepostas**.

A primeira linha (`versao: 2026-11-01`) é a versão da política e precisa aparecer no artifact devolvido pelo agente.

### 5.4 Regras de produto já fechadas

- **Nenhuma regra depende da data de hoje.** Reservar no passado é permitido.
- **Alternativas** em caso de conflito: salas **livres** no mesmo intervalo, com **capacidade ≥** à da sala pedida, **no máximo três**, ordenadas por **capacidade crescente** e, em empate, por **id em ordem alfabética**. A sala pedida nunca é alternativa de si mesma.
- **Sem alternativas** → não há elicitation; a tool devolve erro de execução.
- **Persistência:** reservas em memória, visíveis às consultas seguintes do mesmo processo; não precisam sobreviver a restart.
- **Exceção:** o `requestState` **precisa** continuar válido após restart do servidor MCP — o estado viaja nele, não no servidor.

### 5.5 Exemplo de cálculo de alternativas

Pedido: `sala-garagem` (cap. 12), 2026-11-03 14:00–15:00, com `res-0001` ocupando a garagem.

| Candidata | Cap. ≥ 12? | Livre 14–15? | Resultado |
|---|---|---|---|
| `sala-aquario` | não (4) | — | descartada |
| `sala-porao` | não (6) | — | descartada |
| `sala-fusca` | sim (12) | sim | **alternativa 1** |
| `sala-mirante` | sim (20) | sim | **alternativa 2** |

Resultado: `["sala-fusca", "sala-mirante"]` → mensagem A2A `alternativas: sala-fusca, sala-mirante`.

---

## 6. Formatos fixos de entrada (A2A)

O agente interpreta **apenas** estes formatos, em uma `Part` de texto da mensagem do usuário:

| Uso | Formato |
|---|---|
| Pedido de reserva (nova Task) | `reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>` |
| Resposta à pausa — aceitar | `escolha=<id da sala>` (deve estar entre as alternativas oferecidas) |
| Resposta à pausa — recusar | `escolha=recusar` |

Exemplo: `reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty`

---

## 7. Requisitos funcionais

### 7.1 Servidor MCP (RF-MCP)

| ID | Requisito |
|---|---|
| RF-MCP-01 | Transporte **Streamable HTTP**, endpoint único `/mcp`, porta **7301** (parametrizável por env com esse default). |
| RF-MCP-02 | Rejeitar com **-32602 e HTTP 400** todo request sem `io.modelcontextprotocol/protocolVersion` ou sem `io.modelcontextprotocol/clientCapabilities` em `params._meta`, **sem inferir** de request anterior. |
| RF-MCP-03 | Recusar com **-32020** request cujos headers espelhados (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`) não batem com o corpo. |
| RF-MCP-04 | Declarar as capabilities de **tools** e **resources**. |
| RF-MCP-05 | Expor exatamente três tools, todas com `inputSchema` que é objeto JSON Schema válido (`type: "object"`). |
| RF-MCP-06 | `listar_salas` (sem parâmetros): devolve `structuredContent` conforme `outputSchema` declarado (`{ salas: [...] }`) **e** o mesmo JSON serializado em bloco de texto. |
| RF-MCP-07 | `consultar_disponibilidade(sala, inicio, fim)`: devolve `{ sala, livre, conflitos[] }`; aplica as **mesmas validações** de sala e política da reserva, com os mesmos erros de execução. |
| RF-MCP-08 | `reservar_sala(sala, inicio, fim, responsavel)`: cria a reserva quando livre e dentro da política; `structuredContent` traz `reserva`, `reservado`, `sala`, `inicio`, `fim`, `responsavel`, `politica` (e `motivo`). |
| RF-MCP-09 | Todas as tools devolvem `structuredContent` além do bloco de texto. |
| RF-MCP-10 | `tools/call` de tool inexistente é recusado (-32602 ou `isError: true`). |
| RF-MCP-11 | Resource `politica://uso` devolve o conteúdo de `dados/politica-de-uso.md` com `mimeType: text/markdown`. |
| RF-MCP-12 | `resources/read` de URI inexistente devolve **-32602** (nunca `contents` vazio). |
| RF-MCP-13 | Erros de execução com `isError: true` e as **mensagens exatas** da §8.1. |
| RF-MCP-14 | Registrar no **stderr** cada request recebido com, no mínimo, **método, id e `traceparent`** (quando vier no `_meta`). Sem usar o logging MCP depreciado. |

### 7.2 Ciclo MRTR na reserva (RF-MRTR)

| ID | Requisito |
|---|---|
| RF-MRTR-01 | Conflito de horário → `reservar_sala` responde `resultType: "input_required"`. |
| RF-MRTR-02 | `inputRequests` é um mapa com **uma única entrada**; o valor é um `elicitation/create` em `mode: "form"`. A chave é atribuída pelo servidor e devolvida idêntica pelo cliente. |
| RF-MRTR-03 | `requestedSchema` plano, com propriedade `sala` (`string`) restrita às alternativas (§5.4): `enum` com ≥2 alternativas; `enum` ou `const` com 1. Ordem conforme a regra. |
| RF-MRTR-04 | Conflito **sem alternativas** → sem elicitation; `isError: true` com `Sem alternativas disponiveis no intervalo`. |
| RF-MRTR-05 | Cliente que não declarou `elicitation.form` em `clientCapabilities` → **-32021** com `data.requiredCapabilities` e **HTTP 400** (nunca enviar a elicitation). |
| RF-MRTR-06 | `requestState` protegido por integridade (**HMAC ou AEAD**); assinar é obrigatório, cifrar é opcional. |
| RF-MRTR-07 | `requestState` carrega **expiração** entre 5 e 30 minutos (decisão: **10 min**). |
| RF-MRTR-08 | `requestState` carrega **tudo** que o servidor precisa para reconstruir o pedido; nada fica em memória entre `input_required` e retry; o retry funciona após **restart** do servidor. |
| RF-MRTR-09 | Chave de integridade lida de **`REQUEST_STATE_SECRET`** (≥32 bytes de aleatoriedade), nunca do código; README explica como gerar/exportar, sem expor o valor. |
| RF-MRTR-10 | `requestState` adulterado ou expirado → **-32602**. |
| RF-MRTR-11 | No retry, `inputResponses` e `requestState` são lidos de `params`; o pedido é reconstruído **a partir do `requestState`**. Argumentos reenviados divergentes **não tomam efeito** (usar os valores selados ou rejeitar). |
| RF-MRTR-12 | `action: "accept"` com sala válida conclui a reserva na sala escolhida (`resultType: "complete"`). |
| RF-MRTR-13 | `action: "decline"` ou `"cancel"` conclui **sem reservar**: `resultType: "complete"`, `isError` ausente/false, `structuredContent.reservado = false` + `motivo`. |

### 7.3 Agente como host MCP (RF-HOST)

| ID | Requisito |
|---|---|
| RF-HOST-01 | Falar com o servidor MCP **por HTTP**, como cliente MCP real (dois processos; sem importar código do servidor). |
| RF-HOST-02 | Descobrir as tools por **`tools/list` antes da primeira `tools/call`**; nenhuma lista fixa de tools no código. |
| RF-HOST-03 | Ler o resource `politica://uso` e extrair a versão da primeira linha (`versao: <valor>`). |
| RF-HOST-04 | Todo request MCP carrega `_meta` com `protocolVersion` e `clientCapabilities = {"elicitation": {"form": {}}}`. |
| RF-HOST-05 | Todo request MCP carrega os headers espelhados: `MCP-Protocol-Version`, `Mcp-Method` e, em `tools/call`/`resources/read`, `Mcp-Name`. |
| RF-HOST-06 | Se o cliente A2A enviar header `traceparent`, propagar o **mesmo trace-id** no `_meta.traceparent` de todos os requests MCP daquela Task (span-id pode ser novo). |
| RF-HOST-07 | **Não** registrar callback automático de elicitation: o agente precisa enxergar o `input_required` cru. |
| RF-HOST-08 | `isError: true` da tool → Task em **`TASK_STATE_FAILED`** com a mensagem exata da tool visível no histórico / `status.message`. |
| RF-HOST-09 | O agente **não implementa regra de domínio** (conflito, política, alternativas são do servidor MCP). |

### 7.4 Agente como servidor A2A (RF-A2A)

| ID | Requisito |
|---|---|
| RF-A2A-01 | Porta **7300**; `GET /.well-known/agent-card.json` devolve o Agent Card v1.0 (forma de `exemplos/wire/07-a2a-agent-card.json`). |
| RF-A2A-02 | O card declara identidade, `supportedInterfaces[]` com `url` do endpoint, `protocolBinding: "JSONRPC"` e `protocolVersion: "1.0"`, `capabilities` (streaming/push/extended = false) e a skill `id: "reservar-sala"`. |
| RF-A2A-03 | Endpoint JSON-RPC 2.0 em `/a2a` aceitando **`SendMessage`** e **`GetTask`**. |
| RF-A2A-04 | `SendMessage` sem `taskId` abre Task nova com `id` e `contextId` próprios. |
| RF-A2A-05 | A Task passa por `TASK_STATE_SUBMITTED` e `TASK_STATE_WORKING` antes de terminar; `GetTask` reflete o estado corrente a qualquer momento (id, contextId, status, history, artifacts). |
| RF-A2A-06 | Sucesso → `TASK_STATE_COMPLETED` com artifact `name: "reserva"` cujo texto é o JSON da reserva (`reserva`, `sala`, `inicio`, `fim`, `responsavel`, `politica` = versão lida do resource). |
| RF-A2A-07 | Estados terminais (`COMPLETED`, `CANCELED`, `FAILED`) são definitivos; `SendMessage` referenciando Task terminal é recusado com **erro JSON-RPC**. |
| RF-A2A-08 | Determinismo: o mesmo pedido produz sempre o mesmo resultado; nenhuma dependência de SDK de LLM. |
| RF-A2A-09 | Pedido fora do formato fixo é tratado de forma determinística (decisão de forma no FDD). |

### 7.5 A ponte (RF-PONTE)

| ID | Requisito |
|---|---|
| RF-PONTE-01 | `input_required` do MCP → Task em **`TASK_STATE_INPUT_REQUIRED`** com mensagem de texto **exatamente** `alternativas: <id1>, <id2>, …` (separador `", "`, ordem do `enum`; sem prefixo nem saudação). |
| RF-PONTE-02 | O `requestState` e a chave do `inputRequests` ficam guardados **no agente, por Task**; o `requestState` **nunca** aparece no card, artifact ou qualquer mensagem A2A. |
| RF-PONTE-03 | O `requestState` é **opaco** para o agente: guardar e ecoar, nunca abrir, interpretar ou reconstruir. |
| RF-PONTE-04 | Continuação: `SendMessage` com `taskId` e `escolha=<valor>`. Escolha fora do `enum` mantém `TASK_STATE_INPUT_REQUIRED` e repete a mesma linha de alternativas. |
| RF-PONTE-05 | Retry MCP com **id JSON-RPC diferente** do request inicial, `inputResponses` com a **mesma chave** e o `requestState` **ecoado sem modificação**. |
| RF-PONTE-06 | `escolha=<id>` → `action: "accept"`, `content: {"sala": "<id>"}` → Task `TASK_STATE_COMPLETED` com artifact `reserva` na sala escolhida. |
| RF-PONTE-07 | `escolha=recusar` → `action: "decline"` → Task `TASK_STATE_CANCELED`. |
| RF-PONTE-08 | Estado pausado **isolado por Task**: duas Tasks pausadas ao mesmo tempo concluem cada uma com sua reserva, sem trocar `requestState`. |
| RF-PONTE-09 | Um único `SendMessage` de sala ocupada causa, em sequência: abertura da Task → chamada MCP → `input_required` → pausa; a continuação causa retomada com id novo → reserva criada. |

### 7.6 Documentação e entrega (RF-DOC)

| ID | Requisito |
|---|---|
| RF-DOC-01 | `README.md` com as seções: **Como rodar**, **Onde a ponte acontece**, **Decisões técnicas**, **Saída do validador**. |
| RF-DOC-02 | Comandos funcionam a partir de **clone limpo** (incluindo geração/exportação de `REQUEST_STATE_SECRET`). |
| RF-DOC-03 | Saída completa da última execução do validador colada em bloco de código. |
| RF-DOC-04 | Limitações reais de SDK documentadas com evidência, em vez de contornadas reescrevendo o protocolo. |
| RF-DOC-05 | `dados/`, `validador/` e `exemplos/` intactos; estrutura do starter mantida. |

---

## 8. Catálogo de mensagens e erros

### 8.1 Erros de execução (`result.isError: true`, `resultType: "complete"`)

O texto exato precisa aparecer no conteúdo do resultado; prefixo do SDK (ex.: `Error executing tool reservar_sala: `) é aceito.

| Situação | Mensagem exata |
|---|---|
| Sala não existe em `dados/salas.json` | `Sala inexistente: <id informado>` |
| Início ou fim fora da janela 08:00–20:00 (-03:00) | `Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00` |
| Intervalo > 2 horas | `Duracao acima do limite: a politica permite no maximo 2 horas` |
| `fim <= inicio` | `Intervalo invalido: fim deve ser posterior a inicio` |
| Conflito sem nenhuma alternativa válida | `Sem alternativas disponiveis no intervalo` |

### 8.2 Erros de protocolo (`error` JSON-RPC)

| Situação | Código | HTTP | Observação |
|---|---|---|---|
| `_meta` sem `protocolVersion` ou `clientCapabilities` | -32602 | 400 | Sem inferência de request anterior |
| Header espelhado diverge do corpo | -32020 | 400 | `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` |
| Elicitation necessária sem capability `elicitation.form` | -32021 | 400 | `data.requiredCapabilities: {"elicitation": {"form": {}}}` |
| `requestState` adulterado ou expirado | -32602 | — | Mensagem livre (a do SDK) |
| `resources/read` de URI inexistente | -32602 | — | Proibido devolver `contents` vazio |
| Tool inexistente | -32602 (ou `isError`) | — | Os dois aceitos |
| `SendMessage` em Task terminal (A2A) | erro JSON-RPC | — | Qualquer `error` presente na resposta |

### 8.3 Não é erro

Recusa na elicitation (`action: "decline"` ou `"cancel"`): `resultType: "complete"`, `isError` ausente/false, `structuredContent` com `reservado: false` e `motivo` (ex.: `"recusado"`).

### 8.4 Mensagens A2A do agente

| Momento | Estado | Texto de `status.message` |
|---|---|---|
| Pausa | `TASK_STATE_INPUT_REQUIRED` | `alternativas: sala-fusca, sala-mirante` (byte a byte) |
| Escolha fora do enum | `TASK_STATE_INPUT_REQUIRED` | Repete a mesma linha `alternativas: …` |
| Sucesso | `TASK_STATE_COMPLETED` | Ex.: `Reserva res-0005 confirmada na sala-mirante.` + artifact `reserva` |
| Recusa | `TASK_STATE_CANCELED` | Texto informativo (ex.: `Reserva recusada pelo usuario.`) |
| Erro de execução da tool | `TASK_STATE_FAILED` | Mensagem exata da tool (ex.: `Sala inexistente: sala-delorean`) |

---

## 9. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | **Dois processos separados** comunicando por HTTP (servidor MCP e agente). |
| RNF-02 | **Sem sessão**: nenhum lado infere versão, capabilities ou contexto de request anterior ou conexão aberta (manter um objeto cliente vivo é permitido). |
| RNF-03 | **Determinismo** e **sem LLM** no caminho de execução; sem dependência de SDK de provedor de LLM. |
| RNF-04 | **Python ≥ 3.10**; SDK oficial `mcp` v2 alinhado à spec **2026-07-28**; A2A **v1.0** binding JSON-RPC 2.0 sobre HTTP. |
| RNF-05 | **Versões travadas** em `pyproject.toml` (+ lockfile `uv.lock`). |
| RNF-06 | Portas e paths parametrizáveis por variável de ambiente, com defaults: agente `7300` (`/a2a`, `/.well-known/agent-card.json`), MCP `7301` (`/mcp`). |
| RNF-07 | **Segurança do `requestState`**: tratado como entrada controlada por atacante; integridade verificada; segredo apenas por env; repositório público sem segredo. |
| RNF-08 | **Observabilidade mínima**: logs em stderr com método, id e `traceparent`; propagação W3C Trace Context sem spans OTel. |
| RNF-09 | **Isolamento por Task** no agente; operações de Tasks distintas não compartilham estado mutável. |
| RNF-10 | Reprodutibilidade: o validador deve passar com os **dois processos recém-iniciados**. |

---

## 10. Critérios de aceite e rastreabilidade

Legenda: **Vxx** = verificação nº xx de `validador/validar.py`; **Ax** = passo x do "Fluxo do avaliador" (`prompt.md`); **CR** = revisão de código/README.

### 10.1 Servidor MCP

| Critério de aceite | Requisitos | Evidência |
|---|---|---|
| Sobe em 7301 (Streamable HTTP) e `tools/list` traz as três tools | RF-MCP-01, 05 | V01 |
| Toda tool tem `inputSchema` objeto | RF-MCP-05 | V02 |
| `listar_salas`: `structuredContent` == JSON do bloco de texto | RF-MCP-06, 09 | V03 |
| `_meta` sem `protocolVersion` / sem `clientCapabilities` → -32602 + HTTP 400 | RF-MCP-02 | V04, V05 |
| Tool inexistente recusada | RF-MCP-10 | V06 |
| `politica://uso` devolve a política; URI inexistente → -32602 | RF-MCP-11, 12 | V07, V08 |
| Sala inexistente, janela, duração e intervalo invertido com mensagens exatas | RF-MCP-07, 13 | V09, V10, V11, V12 |
| stderr registra método, id e `traceparent` | RF-MCP-14 | A2, A5, A6 |
| Headers espelhados divergentes → -32020 | RF-MCP-03 | CR |

### 10.2 MRTR

| Critério de aceite | Requisitos | Evidência |
|---|---|---|
| Conflito → `input_required`, 1 entrada em `inputRequests`, form mode, `requestState` presente | RF-MRTR-01, 02 | V13 |
| Schema plano com alternativas na ordem da regra | RF-MRTR-03 | V14 |
| Sem capability `elicitation.form` → -32021 + `requiredCapabilities` + HTTP 400 | RF-MRTR-05 | V15, A13 |
| Retry com `inputResponses` + `requestState` → `complete` na sala escolhida | RF-MRTR-11, 12 | V16 |
| `requestState` adulterado → -32602 | RF-MRTR-06, 10 | V17, A11 |
| Retry com argumentos adulterados não toma efeito | RF-MRTR-11 | V18 |
| Recusa (`decline`) conclui sem reservar e sem `isError` | RF-MRTR-13 | V19 |
| Conflito sem alternativa → `isError` com mensagem exata | RF-MRTR-04 | V20 |
| Retry válido após restart do servidor MCP | RF-MRTR-07, 08 | A12 |
| Chave de `REQUEST_STATE_SECRET`, ≥32 bytes, sem hardcode | RF-MRTR-09 | CR |

### 10.3 Agente como host MCP

| Critério de aceite | Requisitos | Evidência |
|---|---|---|
| Log do MCP mostra `tools/list` antes do 1º `tools/call` do agente | RF-HOST-02 | A5 |
| Log do MCP mostra o mesmo trace-id enviado pelo validador | RF-HOST-06 | A5 (trace-id na 1ª linha do validador) |
| Requests do agente declaram `elicitation.form` | RF-HOST-04 | A2 (log), V27 indireto |
| Artifact traz `politica` lida do resource | RF-HOST-03 | V25 |
| Sala inexistente → `TASK_STATE_FAILED` com mensagem exata | RF-HOST-08 | V35, A10 |

### 10.4 Agente como servidor A2A

| Critério de aceite | Requisitos | Evidência |
|---|---|---|
| Card responde 200 com JSON | RF-A2A-01 | V21, A3 |
| Card com interface JSON-RPC, URL e versão 1.0 | RF-A2A-02 | V22, A3 |
| Card com skill `reservar-sala` | RF-A2A-02 | V23, A3 |
| Sala livre → `TASK_STATE_COMPLETED` | RF-A2A-04, 05 | V24 |
| Artifact `reserva` com `politica` e sala correta | RF-A2A-06 | V25 |
| `GetTask` devolve id, contextId, estado corrente | RF-A2A-05 | V26 |
| `SendMessage` em Task terminal → erro | RF-A2A-07 | V31 |
| Determinismo (mesmo pedido → mesma pausa) | RF-A2A-08 | V36 |

### 10.5 A ponte

| Critério de aceite | Requisitos | Evidência |
|---|---|---|
| Sala ocupada → `TASK_STATE_INPUT_REQUIRED` | RF-PONTE-01, 09 | V27, A7 |
| Linha `alternativas: sala-fusca, sala-mirante` na ordem do enum | RF-PONTE-01 | V28, A7 |
| Escolha fora do enum mantém a pausa | RF-PONTE-04 | V29 |
| `escolha=<id>` conclui na sala escolhida | RF-PONTE-05, 06 | V30, A8 |
| Retry com id diferente do inicial | RF-PONTE-05 | A6 |
| `escolha=recusar` → `TASK_STATE_CANCELED` | RF-PONTE-07 | V32, A9 |
| Duas Tasks pausadas concluem cada uma com a sua reserva | RF-PONTE-08 | V33 |
| `requestState` não aparece em nenhuma resposta A2A | RF-PONTE-02, 03 | V34 |

### 10.6 Validação e entrega

| Critério de aceite | Requisitos | Evidência |
|---|---|---|
| Validador 36/36 com exit code 0, processos recém-iniciados | O6, RNF-10 | Execução completa |
| `dados/`, `validador/`, `exemplos/` inalterados | RF-DOC-05 | `git diff` contra o starter |
| Sem dependência de LLM nos `pyproject.toml` | RNF-03 | CR |
| README com as 4 seções e comandos de clone limpo | RF-DOC-01..03 | A1 |

### 10.7 Roteiro de dados do validador (para depuração)

Como as reservas acumulam ao longo da execução, o resultado de cada check depende dos anteriores. Sequência relevante (dia 2026-11-03):

| Check | Ação | Efeito esperado no estado |
|---|---|---|
| V13 | garagem 14–15 (conflito com Marty) | `input_required` [fusca, mirante]; nada gravado |
| V16 | fusca 16–17 → aceita `sala-garagem` | cria garagem 16–17 |
| V18 | garagem 09–10 "Ocupante" (livre) → cria; depois "Doc" conflita → retry com args adulterados escolhe fusca | cria fusca 09–10 para **Doc** (valores selados) |
| V19 | decline sobre o `requestState` de V13 | nada gravado |
| V20 | mirante 11–12 duas vezes | 1ª cria; 2ª conflita sem alternativa (nenhuma sala cap ≥20) |
| V24 | A2A porão 09–10 | cria porão 09–10 |
| V27–V30 | A2A garagem 14–15 → escolhe fusca | cria fusca 14–15 |
| V32 | A2A garagem 14:30–15:30 → [mirante] → recusa | nada gravado |
| V33 | A2A fusca 16–17 e garagem 14–15 pausadas → ambas escolhem mirante | cria mirante 16–17 e mirante 14–15 |
| V36 | A2A porão 09–10 duas vezes | ambas pausam com `alternativas: sala-mirante` |

---

## 11. Riscos e mitigações

| Risco | Impacto | Mitigação |
|---|---|---|
| Implementar MRTR como **callback** (servidor chamando o cliente) | Erro "sem canal de volta" no transporte stateless; projeto não funciona | Usar a API `input_required` do SDK; o servidor **termina** a resposta pedindo informação |
| Cliente MCP do SDK com **callback de elicitation** | O ciclo fecha sozinho; a Task nunca pausa | Agente sem callback; trata `input_required` cru (RF-HOST-07) |
| **Reaproveitar o id** JSON-RPC no retry | Falha na verificação do avaliador (A6) | Gerador de ids monotônico/aleatório por request |
| `requestState` em base64 sem assinatura | Adulteração aceita; V17/V18 falham | HMAC/AEAD via utilitário do SDK; verificação antes de qualquer uso |
| Segredo hardcoded ou commitado | Reprovação + vazamento | Apenas env `REQUEST_STATE_SECRET`; `.env` no `.gitignore` |
| Estado do validador entre execuções | Falsos negativos | Reiniciar os dois processos antes de cada execução |
| Vazamento de `requestState` em resposta A2A | V34 falha | Guardar só em estrutura interna fora do modelo da Task |
| Divergência entre SDK e letra da spec | Checks com formas diferentes | Critérios aceitam as duas formas; documentar evidência no README |
| Mensagem de pausa com texto extra | V28/V36 falham (comparação byte a byte) | Formatação centralizada `alternativas: ` + `", ".join(enum)` |

---

## 12. Premissas e pontos em aberto

- **Confirmada (doc. do SDK v2.3.0):** o `mcp` Python expõe MRTR de primeira classe (`InputRequiredResult`, `Resolve`) e sela o `requestState` com `RequestStateSecurity` (ADR-005/006).
- **Confirmada (doc. do `a2a-sdk`):** o SDK suporta A2A v1.0 (`supported_interfaces`, `TASK_STATE_*`, rotas JSON-RPC) (ADR-008).
- **Fechado (ADR-011, FDD §4.4):** pedido A2A fora do formato numa Task nova → `TASK_STATE_FAILED` com mensagem de formato; numa Task pausada → mantém `INPUT_REQUIRED` e repete as alternativas.
- **Fechado (FDD §3.5):** data ISO ilegível ou sem fuso no servidor MCP → `Intervalo invalido: fim deve ser posterior a inicio`.
