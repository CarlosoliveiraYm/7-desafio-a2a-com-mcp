# Central de Salas — Agente A2A com MCP

Dois processos:

- **`servidor-mcp/`**: servidor MCP `central-de-salas` em Streamable HTTP (porta **7301**, `/mcp`), com as tools `listar_salas`, `consultar_disponibilidade` e `reservar_sala`, o resource `politica://uso` e o ciclo MRTR completo na reserva.
- **`agente/`**: o agente **Central de Salas**. Por fora é servidor A2A v1.0 (porta **7300**, JSON-RPC em `/a2a`, card em `/.well-known/agent-card.json`). Por dentro é host MCP: descobre as tools e chama o servidor por HTTP.

O agente não usa LLM. Ele interpreta o pedido em formato fixo e decide por regra.

A documentação de projeto fica em [`docs/`](docs/): [PRD](docs/PRD.md) (requisitos), [ADR](docs/ADR.md) (decisões) e [FDD](docs/FDD.md) (desenho técnico).

## Como rodar

Pré-requisitos: **Python ≥ 3.10** e **[uv](https://docs.astral.sh/uv/)** (`curl -LsSf https://astral.sh/uv/install.sh | sh`). As versões estão travadas em `pyproject.toml` e `uv.lock` em cada processo.

```bash
git clone <url-do-fork> && cd 7-desafio-a2a-com-mcp
```

**Terminal 1: servidor MCP.** O segredo do `requestState` vem **somente** de `REQUEST_STATE_SECRET` (hex, ≥ 32 bytes). Sem ele, o processo não sobe. Gere um valor seu; não há valor no repositório.

```bash
export REQUEST_STATE_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
cd servidor-mcp
uv sync --frozen
uv run --frozen python -m servidor_mcp
```

> Para testar o retry depois de reiniciar o servidor MCP, reinicie **no mesmo terminal**: o mesmo `REQUEST_STATE_SECRET` precisa continuar exportado. Também dá para guardar o valor num `.env` local (modelo em [`.env.example`](.env.example); `.env` está no `.gitignore`) e carregá-lo com `set -a; . ../.env; set +a`.

**Terminal 2: agente.**

```bash
cd agente
uv sync --frozen
uv run --frozen python -m agente
```

**Terminal 3: validador.** Rode com os dois processos recém-iniciados: as reservas criadas por uma execução mudam o resultado da seguinte.

```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Testes automatizados (opcionais), que cobrem também o que o validador não vê:

- `cd servidor-mcp && uv run --frozen pytest`: domínio, configuração e protocolo MCP em processo (capabilities, -32020, `outputSchema` de todas as tools, `requestState` expirado, retry após restart, `cancel`, log no stderr).
- `cd agente && uv run --frozen pytest`: parser, ponte, máquina de estados da Task e **ponta a ponta**. O teste sobe o servidor MCP real e um proxy espião e confere que o `requestState` do agente nunca aparece em resposta A2A, que todos os requests levam `_meta` e headers corretos, e que o retry tem id novo.

Variáveis de ambiente opcionais (com seus padrões) estão em [`.env.example`](.env.example).

## Onde a ponte acontece

A ponte fica em `agente/agente/ponte.py`, nos dois sentidos. **Ida:** quando a sala está ocupada, o resolver `escolha_de_sala` do servidor ([`servidor-mcp/servidor_mcp/servidor.py:74`](servidor-mcp/servidor_mcp/servidor.py#L74)) devolve um `Elicit`, e o SDK termina a resposta com `resultType: input_required`, a elicitation e o `requestState` selado. O agente chama `ClientSession.call_tool(..., allow_input_required=True)` e recebe esse resultado cru em `McpHost.chamar_tool` ([`agente/agente/mcp_host.py:210`](agente/agente/mcp_host.py#L210)). Em `Ponte._traduzir` ([`agente/agente/ponte.py:128`](agente/agente/ponte.py#L128)), o `input_required` vira estado nomeado da Task: a chave do `inputRequests`, o `requestState`, as opções do `enum` e os argumentos originais ficam guardados em `PausasRepository`, indexados pelo id da Task. O executor ([`agente/agente/executor.py:99`](agente/agente/executor.py#L99)) então coloca a Task em `TASK_STATE_INPUT_REQUIRED` com a linha `alternativas: …`. **Volta:** o `SendMessage` de continuação traz `taskId` e `escolha=…`; o executor ([`agente/agente/executor.py:90`](agente/agente/executor.py#L90)) confere se a escolha pertence ao `enum` recebido. Se pertence, `Ponte.retomar` ([`agente/agente/ponte.py:92`](agente/agente/ponte.py#L92)) devolve o `requestState` ao servidor ([`ponte.py:106-107`](agente/agente/ponte.py#L106)): um `tools/call` **novo**, com id JSON-RPC novo, os mesmos argumentos, `inputResponses` na mesma chave (`accept` com a sala, ou `decline` para `escolha=recusar`) e o `requestState` ecoado byte a byte.

## Decisões técnicas

**Proteção do `requestState`.** Usamos o utilitário do próprio SDK, `RequestStateSecurity` (`mcp==2.3.0`), com a chave de `REQUEST_STATE_SECRET`:

- **Codec**: o padrão é AES-GCM (AEAD, chave derivada por HKDF). O estado fica cifrado **e** autenticado, e qualquer caractere trocado é detectado.
- **O que fica amarrado**: o envelope selado amarra a tool, um digest dos argumentos, a expiração e a audience. Um retry com argumentos diferentes dos selados é rejeitado. Todas as falhas respondem `-32602 "Invalid or expired requestState"`; o motivo real (`seal`, `request binding`, `unknown key`, expiração) vai só para o log.
- **Restart**: o estado guarda tudo o que o servidor precisa, e o servidor não guarda nada entre as idas. Com o mesmo segredo e a audience fixa `central-de-salas`, o retry funciona depois de um restart.
- **Validade**: **10 minutos** (`REQUEST_STATE_TTL_SECONDS=600`, aceito entre 300 e 1800). O SDK renova o prazo a cada rodada, então o limite vale para o tempo de cada resposta.
- **Escolha no retry**: o resolver roda de novo e só aceita a resposta se a pergunta selada for idêntica à atual. A escolha é validada contra o `enum`, então uma sala fora da lista não vira reserva.

**Estado das Tasks.** Fica em memória no agente:

- `InMemoryTaskStore` do `a2a-sdk` guarda id, contextId, status, history e artifacts, e é o que o `GetTask` lê.
- `PausasRepository` (`agente/agente/pausas.py`) guarda o estado da ponte por Task: chave, `requestState`, opções, argumentos e trace.
- O `requestState` **nunca** entra na Task (nem em `metadata`, nem em mensagem, nem no artifact). Ele também não aparece em log nem no `repr` da pausa.
- Reservas e Tasks não sobrevivem a restart (fora de escopo). Só o `requestState` sobrevive, porque viaja com o cliente.

**Outras decisões** (detalhes em [docs/ADR.md](docs/ADR.md)):

- **Python** com `mcp==2.3.0` e `a2a-sdk==1.3.0`. O wire do servidor reproduz o de `exemplos/wire/`.
- **Capability exata `{"elicitation": {"form": {}}}`**: com callback registrado, o SDK declara `{"form": {}, "url": {}}`. Uma subclasse mínima de `ClientSession` (`SessaoCentralDeSalas`) restringe a declaração a form mode. O callback é uma sentinela que nunca responde: a pergunta sempre volta para a Task A2A.
- **Descoberta por Task**: `tools/list` e `resources/read politica://uso` são feitos no início de cada Task, antes do `tools/call`. A versão da política no artifact é a lida do resource pelo agente.
- **`traceparent`**: o trace-id do header A2A segue no `_meta` de todos os requests MCP da Task, com um span-id novo por request. O servidor registra em stderr uma linha por request (`method`, `id`, `name`, `traceparent`, `status`).
- **Mensagem final no histórico**: o `a2a-sdk` só move a mensagem de status para o `history` na atualização seguinte, e nada vem depois de um estado terminal. O executor publica a mesma mensagem em `WORKING` e depois no estado final, e com isso a mensagem da tool (ex.: `Sala inexistente: …`) aparece no histórico, como no wire 10.

### Diferenças entre os SDKs e o contrato, com a solução adotada

| Ponto | Evidência | O que fizemos |
|---|---|---|
| `a2a-sdk` 1.3.0 trata request **sem** header `A2A-Version` como v0.3 e recusa `SendMessage` com `-32009` | `a2a/utils/version_validator.py`: "If the header is missing or empty, it is interpreted as … '0.3'". O validador e o `exemplos/wire/08` não enviam o header | `ContextoV1` (`agente/agente/contexto.py`), um `ServerCallContextBuilder`, ponto de extensão oficial, trata a ausência do header como `1.0`. O endpoint só serve v1.0. Um header explícito com outra versão continua sendo recusado |
| O cliente do SDK `mcp` 2.3.0 declara elicitation em form **e** url mode sempre que há callback; o enunciado pede exatamente `{"elicitation": {"form": {}}}` | `mcp/client/session.py:635`, em `_build_capabilities`: `types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability()) if self._elicitation_callback is not _default_elicitation_callback` | `SessaoCentralDeSalas` (`agente/agente/mcp_host.py`) sobrescreve só `_build_capabilities` e mantém `form`. O protocolo não é reescrito: `_meta`, headers, ids e o MRTR continuam com o SDK. O método é privado, então a versão está travada e um teste confere a forma emitida em todos os requests |
| Na continuação, a mensagem traz `taskId` sem `contextId`, e o SDK gera um `context_id` novo no `RequestContext` | Erro `Context in event doesn't match TaskManager` | O executor usa o `contextId` da Task armazenada (`context.current_task.context_id`) |
| `GetTask` responde a Task **sem** envelope `{"task": …}` (o wire 09 tem envelope) | `jsonrpc_dispatcher._handle_get_task` → `MessageToDict(task)` | Mantido (o validador aceita as duas formas) |
| O Agent Card servido inclui campos de compatibilidade v0.3 (`url`, `preferredTransport`, …) além dos campos v1.0 | `agent_card_routes` → `agent_card_to_dict` | Mantido. `supportedInterfaces[]` com `protocolBinding: JSONRPC` e `protocolVersion: 1.0`, e a skill `reservar-sala`, estão presentes |
| A telemetria OTel do `a2a-sdk` polui o stderr ("Failed to detach context") | `a2a/utils/telemetry.py` | Desligada com `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false` no entrypoint. Spans estão fora de escopo |
| `asyncio: Task was destroyed but it is pending` ao retomar uma Task pausada | Interno do `a2a-sdk` (`EventQueueSource._dispatch_loop`) | Inofensivo para o resultado; só registrado aqui |
| O `MCPServer` serve `subscriptions/listen` e anuncia `listChanged`/`subscribe` por padrão. Subscriptions estão fora de escopo | `mcp/server/mcpserver/server.py`: `on_subscriptions_listen=None if subscriptions is False else ListenHandler(self._subscriptions)` | `MCPServer(..., subscriptions=False)`, opção oficial do construtor: o método deixa de ser servido (-32601) e as flags saem falsas. Coberto por teste |
| O `MCPServer` sempre registra o handler de prompts, então anuncia a capability `prompts` mesmo sem nenhum prompt. Prompts estão fora de escopo | `mcp/server/mcpserver/server.py:227`: `on_list_prompts=self._handle_list_prompts` (sem opção para desligar); `lowlevel/server.py:606`: `if "prompts/list" in self._request_handlers:` declara a capability | Nenhum prompt é registrado (`prompts/list` devolve vazio, coberto por teste). O anúncio fica como está, para não alterar estrutura privada do SDK |
| Um request MCP **sem** header `MCP-Protocol-Version` é roteado pelo SDK para a rota legada (com `initialize`) | `StreamableHTTPSessionManager._handle_request` | Fora do contrato: o validador e o agente sempre enviam `MCP-Protocol-Version: 2026-07-28` |

## Saída do validador

Última execução, num `git clone` limpo deste repositório, seguindo só os comandos de "Como rodar" e com os dois processos recém-iniciados:

```
trace-id desta execucao: 9fdb5f09c854279bb66602264d9f445b
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
