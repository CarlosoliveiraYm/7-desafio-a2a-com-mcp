# ADR — Registro de Decisões de Arquitetura

| Campo | Valor |
|---|---|
| Produto | Central de Salas (Agente A2A com MCP) |
| Documentos relacionados | [`PRD.md`](PRD.md) (requisitos), [`FDD.md`](FDD.md) (desenho técnico) |
| Formato | Contexto / Decisão / Alternativas consideradas / Consequências / Status |
| Data | 2026-11-04 |

## Índice

| ADR | Título | Status |
|---|---|---|
| [ADR-001](#adr-001--python-e-sdk-oficial-mcp-v2-nos-dois-processos) | Python e SDK oficial `mcp` v2 nos dois processos | Aceita |
| [ADR-002](#adr-002--uv-com-um-pyprojecttoml-por-processo-e-lockfile) | `uv` com um `pyproject.toml` por processo e lockfile | Aceita |
| [ADR-003](#adr-003--dois-processos-via-http-e-agente-sem-regra-de-domínio) | Dois processos via HTTP e agente sem regra de domínio | Aceita |
| [ADR-004](#adr-004--streamable-http-stateless-na-revisão-2026-07-28) | Streamable HTTP stateless na revisão 2026-07-28 | Aceita |
| [ADR-005](#adr-005--mrtr-com-resolver-resolve-do-sdk-e-não-elicitation-síncrona) | MRTR com resolver (`Resolve`) do SDK, e não elicitation síncrona | Aceita |
| [ADR-006](#adr-006--proteção-do-requeststate-com-requeststatesecurity) | Proteção do `requestState` com `RequestStateSecurity` | Aceita |
| [ADR-007](#adr-007--agente-dirige-o-loop-mrtr-sem-callback-de-elicitation) | Agente dirige o loop MRTR, sem callback de elicitation | Aceita |
| [ADR-008](#adr-008--a2a-sdk-v10-com-binding-json-rpc) | `a2a-sdk` v1.0 com binding JSON-RPC | Aceita |
| [ADR-009](#adr-009--estado-das-tasks-e-do-requeststate-em-memória-no-agente) | Estado das Tasks e do `requestState` em memória no agente | Aceita |
| [ADR-010](#adr-010--propagação-do-traceparent-e-log-em-stderr) | Propagação do `traceparent` e log em stderr | Aceita |
| [ADR-011](#adr-011--interpretação-por-regra-determinística-sem-llm) | Interpretação por regra determinística, sem LLM | Aceita |
| [ADR-012](#adr-012--reservas-em-memória-no-servidor-mcp-com-ids-sequenciais) | Reservas em memória no servidor MCP, com ids sequenciais | Aceita |

> **Evidências de SDK.** As referências a APIs (`MCPServer`, `Resolve`, `InputRequiredResult`, `RequestStateSecurity`, `allow_input_required`, `AgentExecutor`, `TaskUpdater`, `create_jsonrpc_routes` etc.) vêm da documentação oficial consultada em 2026-11-04: `modelcontextprotocol/python-sdk` **v2.3.0** (`docs/handlers/multi-round-trip.md`, `docs/run/deploy.md`, `docs/run/index.md`, `docs/troubleshooting.md`) e `a2aproject/a2a-python` (`samples/hello_world_agent.py`, `src/a2a/server/agent_execution/agent_executor.py`). As assinaturas usadas no FDD foram conferidas também no código-fonte (`docs/run/asgi.md`, `src/mcp/server/mcpserver/resolve.py`, `src/mcp/server/request_state.py`, `src/mcp/client/session.py`, `a2a/server/tasks/task_updater.py`, `a2a/server/request_handlers/default_request_handler.py`). Divergências encontradas na implementação viram nota no README (RF-DOC-04).

---

## ADR-001 — Python e SDK oficial `mcp` v2 nos dois processos

**Contexto.** O enunciado exige o SDK oficial do MCP v2 alinhado à revisão 2026-07-28, em Node.js (`@modelcontextprotocol/server`) ou Python (`mcp`). A escolha não afeta a nota, mas afeta o atrito com o contrato: os exemplos de `exemplos/wire/` foram capturados de uma execução real cujo formato denuncia o SDK Python — chave de `inputRequests` `__main__:escolha_de_sala` (nome qualificado de um resolver Python), `inputSchema` com `title: "<tool>Arguments"`, `outputSchema` com `$defs` e `anyOf [..., null]` gerados por Pydantic, e a mensagem do -32021 `Client did not declare the form elicitation capability required by resolver '...'`, idêntica à documentada no SDK Python. O validador também é Python.

**Decisão.** Usar **Python ≥ 3.10** nos dois processos, com o pacote **`mcp` v2** (linha 2.3.x, versão exata travada) tanto no servidor (`MCPServer`) quanto no cliente do agente (`Client` / `ClientSession`).

**Alternativas consideradas.**
- *Node.js 20 + TypeScript (`@modelcontextprotocol/server`)*: válido, mas os formatos do wire divergiriam (chave do `inputRequests`, schemas), exigindo mais conferência manual.
- *Linguagens diferentes por processo*: aumenta o custo de setup e de README sem ganho.

**Consequências.**
- (+) O comportamento do servidor tende a reproduzir o wire de referência quase byte a byte.
- (+) Uma única toolchain (`uv`) para servidor, agente e validador.
- (−) Dependência de recursos recentes do SDK (MRTR, `RequestStateSecurity`); qualquer lacuna precisa ser documentada, não contornada.

**Status.** Aceita.

---

## ADR-002 — `uv` com um `pyproject.toml` por processo e lockfile

**Contexto.** O enunciado exige versões travadas e comandos que funcionem a partir de um clone limpo. Os dois processos são independentes e têm dependências diferentes (o agente precisa de `a2a-sdk`; o servidor não).

**Decisão.** Usar **`uv`**. Cada processo tem seu `pyproject.toml` (`servidor-mcp/pyproject.toml`, `agente/pyproject.toml`) com dependências diretas fixadas com `==` e um `uv.lock` commitado. A subida é feita com `uv run` dentro de cada pasta. `requires-python = ">=3.10"`.

**Alternativas consideradas.**
- *pip + venv*: sem ferramenta extra, mas sem lockfile transitivo nativo e com mais passos no README.
- *Um único `pyproject.toml` na raiz*: mistura dependências e enfraquece a separação física dos processos (ADR-003).

**Consequências.**
- (+) Instalação reprodutível (`uv sync --frozen`), versões transitivas também travadas.
- (+) README curto: um comando por processo.
- (−) O avaliador precisa ter `uv` instalado; o README inclui o comando de instalação oficial.

**Status.** Aceita.

---

## ADR-003 — Dois processos via HTTP e agente sem regra de domínio

**Contexto.** Restrições não negociáveis: dois processos separados; o agente fala com o servidor MCP por HTTP como um cliente MCP real; importar a função da tool no agente descaracteriza a entrega; o agente não implementa regra de sala (conflito, política, alternativas são do servidor). A tese do projeto é a opacidade: o agente traduz protocolo, não domínio.

**Decisão.**
- `servidor-mcp/` e `agente/` são pacotes Python independentes, **sem import cruzado** e sem módulo compartilhado.
- O agente conhece apenas: a URL do MCP, o nome da skill, o formato fixo do pedido e o mapeamento de protocolo (MRTR ↔ Task).
- A única validação que o agente faz sobre a escolha do usuário é **de protocolo**: verificar se o valor pertence ao `enum`/`const` do `requestedSchema` recebido (RF-PONTE-04). Ele não calcula alternativas, não checa horário nem capacidade.
- Toda decisão de domínio vem do servidor MCP via `structuredContent`, `isError` ou `input_required`.

**Alternativas consideradas.**
- *Validar o pedido (janela, duração) no agente para economizar chamadas*: viola a restrição e duplica regra.
- *Agente consultar `consultar_disponibilidade` antes de reservar*: desnecessário — a própria `reservar_sala` decide e pergunta.

**Consequências.**
- (+) O agente seria idêntico se por trás houvesse outro domínio com o mesmo protocolo.
- (+) Mensagens de erro chegam ao cliente A2A exatamente como o servidor as produziu.
- (−) Cada pedido custa pelo menos uma ida HTTP ao MCP (aceitável no escopo).

**Status.** Aceita.

---

## ADR-004 — Streamable HTTP stateless na revisão 2026-07-28

**Contexto.** A revisão 2026-07-28 não tem sessão: cada request carrega em `params._meta` a versão do protocolo (`io.modelcontextprotocol/protocolVersion`) e as capabilities do cliente (`io.modelcontextprotocol/clientCapabilities`), e o transporte espelha método/nome em headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`). O validador exige -32602 + HTTP 400 quando faltam os campos de `_meta`, sem inferência a partir de request anterior. O SDK documenta `-32020 HEADER_MISMATCH`, `-32021 MISSING_REQUIRED_CLIENT_CAPABILITY` e `-32022 UNSUPPORTED_PROTOCOL_VERSION`.

**Decisão.**
- Servir com `MCPServer("central-de-salas").run(transport="streamable-http", host=..., port=7301, streamable_http_path="/mcp", stateless_http=True, json_response=True)` — na prática via `mcp.streamable_http_app(stateless_http=True, json_response=True, streamable_http_path="/mcp")` envolvido pelo middleware (FDD §3.1); `streamable_http_app()` aceita os mesmos kwargs de `run("streamable-http", ...)` (`docs/run/asgi.md`).
  - `stateless_http=True`: transporte novo por request, sem tracking de sessão.
  - `json_response=True`: uma resposta JSON por POST, como no wire de referência (o validador lê o corpo com `json.loads`).
- Deixar com o SDK a validação de `_meta` (-32602) e dos headers espelhados (-32020), e o status HTTP 400 desses erros e do -32021. ✅ Confirmado em `src/mcp/shared/inbound.py` v2.3.0 (`classify_inbound_request` e a tabela `ERROR_CODE_HTTP_STATUS`). O único middleware nosso é o de log (ADR-010), que não valida nem altera nada.
- Declarar capabilities `tools` e `resources` (o `MCPServer` declara automaticamente ao registrar tools/resources).

**Alternativas consideradas.**
- *Respostas em SSE (padrão)*: funcionaria com `Accept: text/event-stream`, mas o validador faz `json.loads` do corpo inteiro; JSON simples elimina o risco.
- *Servidor com sessão (legacy)*: proibido pelo enunciado (RNF-02).

**Consequências.**
- (+) Qualquer request é autocontido; restart do processo não quebra clientes (exceto reservas em memória, por definição).
- (+) `ctx.elicit()` está fora de cogitação: num transporte sem back-channel ele lança `NoBackChannelError` — força o desenho correto (ADR-005).
- (−) Sem notificações por request (progress/log MCP) — fora de escopo de qualquer forma.

**Status.** Aceita.

---

## ADR-005 — MRTR com resolver (`Resolve`) do SDK, e não elicitation síncrona

**Contexto.** Quando a sala está ocupada, `reservar_sala` precisa perguntar qual alternativa usar. Na revisão 2026-07-28 o servidor **não inicia requests**: ele *termina* a resposta com `resultType: "input_required"`, `inputRequests` (mapa chave → `elicitation/create`) e `requestState`, e o cliente volta com um `tools/call` novo trazendo `inputResponses` e o `requestState`. O SDK Python oferece duas formas: (a) a tool devolve um `InputRequiredResult` construído à mão (low-level `Server`, `on_call_tool -> CallToolResult | InputRequiredResult`); (b) um parâmetro anotado com `Resolve(fn)` — uma **dependência** que o SDK resolve via MRTR na 2026-07-28, selando o `requestState` e checando a capability do cliente. A chave `__main__:escolha_de_sala` e a mensagem do -32021 do wire de referência saem da forma (b).

**Decisão.**
- Implementar `reservar_sala` no `MCPServer` com a **escolha da sala alternativa como dependência `Resolve(escolha_de_sala)`**, em que `escolha_de_sala` é uma função nossa que:
  1. recebe os argumentos já validados e calcula as alternativas (regra do PRD §5.4);
  2. se não houver alternativa, falha com o erro de execução `Sem alternativas disponiveis no intervalo` (sem elicitation);
  3. caso contrário, pede uma elicitation em **form mode** com `requestedSchema` plano: `{ sala: { type: string, enum: [...] } }`, `required: ["sala"]`, e mensagem `A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.`
- O resolver só é acionado **quando há conflito**; no caminho livre a reserva conclui na primeira ida (o FDD detalha como condicionar a dependência).
- A checagem de capability (`elicitation.form` em `clientCapabilities`) e a resposta **-32021** com `data.requiredCapabilities` e HTTP 400 ficam a cargo do SDK. ✅ Confirmado: `_require_capability` em `resolve.py` só roda quando o resolver vai de fato perguntar, e o status vem da tabela `ERROR_CODE_HTTP_STATUS`.
- Erros de domínio são levantados como **`ToolError`** (ou subclasse). ✅ Confirmado em `tools/base.py`: qualquer outra exceção vira crash genérico e perde a mensagem.
- **Plano B** (se `Resolve` não puder ser condicional ou não aceitar `enum` dinâmico): usar a API de baixo nível — devolver `InputRequiredResult` explicitamente e selar/verificar o estado com o mesmo `RequestStateSecurity` (ADR-006). O wire continua igual; muda apenas o formato da chave.

**Alternativas consideradas.**
- *`ctx.elicit()` / `ctx.session.elicit_form()` durante a execução*: é o "MRTR como callback"; falha com `NoBackChannelError` na 2026-07-28 — descartada.
- *Resultado de erro pedindo para o cliente reenviar com um parâmetro extra `sala_alternativa`*: não é MRTR, não gera `input_required` — descartada.

**Consequências.**
- (+) O SDK sela/verifica o `requestState`, amarra o estado aos argumentos e gera a chave do `inputRequests` — menos código de segurança escrito à mão.
- (+) Um único corpo de tool atende primeira ida e retry.
- (−) A chave do `inputRequests` depende do nome qualificado do módulo (ex.: `__main__:escolha_de_sala`); o cliente nunca fixa essa chave, apenas a ecoa.

**Status.** Aceita.

---

## ADR-006 — Proteção do `requestState` com `RequestStateSecurity`

**Contexto.** O `requestState` volta pelas mãos do cliente e é entrada controlada por atacante. Requisitos: integridade (HMAC ou AEAD), expiração entre 5 e 30 minutos, chave vinda de `REQUEST_STATE_SECRET` (≥ 32 bytes), rejeição com -32602 de estado adulterado/expirado, sobrevivência a restart, e argumentos reenviados divergentes não podem tomar efeito. Por padrão o SDK sela com uma chave gerada por `os.urandom(32)` na construção do servidor — **que se perde no restart** e invalidaria o requisito RF-MRTR-08. O SDK expõe `RequestStateSecurity(keys=[...], audience=..., ttl=...)`: `keys[0]` sela, todas as chaves da lista abrem (anel de rotação); o token carrega o **nome do servidor como audience**; `ttl` padrão é **600 s**; falhas respondem o erro congelado `-32602 "Invalid or expired requestState"` com `data.reason = "invalid_request_state"` e o motivo real vai para um `WARNING` no log; o selo inclui os argumentos ("replayed against different arguments" é rejeitado).

**Decisão.**
- Configurar o servidor com `RequestStateSecurity(keys=[<REQUEST_STATE_SECRET em bytes>], audience="central-de-salas", ttl=<REQUEST_STATE_TTL_SECONDS>)`.
- `REQUEST_STATE_SECRET`: obrigatório; hex de 64 caracteres gerado com `python3 -c "import secrets; print(secrets.token_hex(32))"`; convertido com `bytes.fromhex`. Ausente ou com menos de 32 bytes → o processo **não sobe** (mensagem clara no stderr). Nenhum valor default no código.
- `REQUEST_STATE_TTL_SECONDS`: opcional, default **600** (10 min), limitado a [300, 1800]; fora da faixa → erro de inicialização.
- Audience **explícita** e nome fixo do servidor, para que o token sobreviva a restart e a uma eventual segunda instância.
- **Argumentos adulterados no retry**: o estado é rejeitado com -32602 (caminho "rejeita", que passa na V18). ✅ Confirmado em `src/mcp/server/request_state.py` v2.3.0: o envelope amarra `(target, args-digest, principal)`, além de expiração e audience, e falha fechado.
- **Escolha da sala no retry**: o resolver roda de novo e só aceita a resposta se a pergunta renderizada (com o `enum`) for idêntica à selada. O `content` é validado contra o modelo da pergunta, e uma sala fora do `enum` gera `ToolError`, nunca uma reserva (FDD §3.6).
- **Cifra**: o codec padrão é `AESGCMRequestStateCodec` (AEAD), então o estado fica cifrado e autenticado. Vamos além do mínimo exigido, que é integridade.

**Alternativas consideradas.**
- *HMAC-SHA256 próprio sobre JSON em base64url*: atende, mas reimplementa o utilitário que o SDK já oferece e que o enunciado sugere usar.
- *Guardar o estado no servidor e mandar só um id*: viola "o servidor não guarda nada entre `input_required` e retry" e quebra após restart.
- *JSON em base64 sem assinatura*: falha V17 — explicitamente reprovado.

**Consequências.**
- (+) Adulteração, expiração, replay com outros argumentos e chave errada resultam no mesmo -32602, sem vazar o motivo no wire.
- (+) Restart do servidor MCP com o mesmo segredo mantém o retry válido (A12).
- (+) Rotação de chave possível adicionando a nova em `keys[0]`.
- (−) O segredo precisa estar exportado nos dois terminais de teste em que o servidor sobe; o README orienta a usar um `.env` (no `.gitignore`) ou `export`.

**Status.** Aceita.

---

## ADR-007 — Agente dirige o loop MRTR, sem callback de elicitation

**Contexto.** O cliente MCP do SDK, por padrão, dirige o loop sozinho: se houver `elicitation_callback`, ele responde a pergunta e refaz a chamada sem que ninguém de fora veja — e a Task A2A nunca pausaria. Porém, no SDK Python, **registrar o `elicitation_callback` é a declaração da capability** `elicitation.form`; sem ele, o servidor responde -32021. O SDK documenta o modo "Driving the loop yourself": `client.session.call_tool(..., allow_input_required=True)` devolve `CallToolResult | InputRequiredResult`, deixando `request_state` e `input_requests` nas mãos de quem chama. Requisitos adicionais: `tools/list` antes da primeira `tools/call`, leitura do resource `politica://uso`, headers espelhados em todo request, **id JSON-RPC novo no retry**.

**Decisão.**
- O agente mantém um `Client` MCP vivo (permitido pelo enunciado) apontando para `MCP_URL` (default `http://127.0.0.1:7301/mcp`), com protocolo `2026-07-28`.
- **Capability declarada sem responder sozinha**: registrar um `elicitation_callback` *sentinela* que só serve para declarar a capability e **nunca é usado**. Todas as chamadas a `reservar_sala` passam por `client.session.call_tool(..., allow_input_required=True)`, que devolve o `InputRequiredResult` cru ao agente; só `Client.call_tool` roda o loop MRTR sozinho (confirmado). Se o sentinela for invocado por qualquer caminho, ele levanta erro (fail fast) em vez de responder.
- **Forma exata da capability** (decisão do usuário, 2026-11-04): o SDK v2.3.0 declara `{"form": {}, "url": {}}` quando há callback. Para emitir exatamente `{"elicitation": {"form": {}}}`, uma subclasse de `ClientSession` sobrescreve `_build_capabilities(version)`, e uma subclasse de `Client` sobrescreve `_build_session` para instanciá-la (FDD §5.1). São métodos privados; o risco fica mitigado pela versão travada e por teste unitário.
- `Client(MCP_URL, mode="2026-07-28")`: adota a revisão 2026 sem handshake.
- **Fallback** (se o SDK impedir declarar a capability sem auto-loop, ou não permitir controlar `_meta`/headers/id): implementar um cliente MCP HTTP mínimo com `httpx`, montando corpo, `_meta`, headers espelhados e ids conforme os exemplos de wire. A limitação é documentada no README (RF-DOC-04).
- **Descoberta por Task**: no início de cada Task nova, antes da `tools/call`, o agente executa `tools/list` (e falha se `reservar_sala` não estiver na lista) e `resources/read politica://uso`, extraindo a versão da primeira linha (`versao: <valor>`). Fazer isso por Task (e não uma vez por processo) garante que esses requests também levem o `traceparent` da Task e reflitam mudanças do servidor em runtime (ver FDD §5.2).
- **Ids JSON-RPC**: sempre gerados pelo cliente, um por request; o retry nunca reaproveita o id do request inicial (o FDD especifica a geração).
- No retry o agente reenvia o **mesmo nome de tool e os mesmos `arguments`** do pedido original, mais `inputResponses` com a **mesma chave** e o `requestState` **byte a byte**.

**Alternativas consideradas.**
- *Usar o auto-loop do `Client` com callback que bloqueia esperando o usuário A2A*: mantém request HTTP pendurado, mistura Tasks e é exatamente o anti-padrão citado no enunciado.
- *Hardcode da lista de tools*: viola RF-HOST-02.

**Consequências.**
- (+) O agente enxerga o `input_required` cru e pode transformá-lo em pausa (ADR-009).
- (+) O `requestState` permanece opaco: o agente só o armazena e ecoa.
- (−) Dependência de um detalhe do SDK (callback como declaração de capability); mitigada pelo fallback.

**Status.** Aceita.

---

## ADR-008 — `a2a-sdk` v1.0 com binding JSON-RPC

**Contexto.** O agente precisa ser servidor A2A **v1.0** com binding JSON-RPC 2.0 sobre HTTP: Agent Card em `/.well-known/agent-card.json` (forma v1.0, `supportedInterfaces[]` com `url`, `protocolBinding`, `protocolVersion`), métodos `SendMessage` e `GetTask` em `/a2a`, Task com estados `TASK_STATE_*`. O SDK oficial `a2a-sdk` (Python) suporta v1.0: `AgentCard(supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=...)])`, `AgentExecutor.execute/cancel`, `TaskUpdater` (`start_work`, `add_artifact`, `complete`, …), `DefaultRequestHandler`, `InMemoryTaskStore`, `create_jsonrpc_routes(rpc_url=...)`, `create_agent_card_routes`. O docstring de `AgentExecutor` define o fluxo interrompido: publicar `TASK_STATE_INPUT_REQUIRED` e **retornar**; a requisição retoma (novo `execute`) quando chega o input.

**Decisão.**
- Usar **`a2a-sdk==1.3.0`** (suporte a v1.0, verificado no código desta tag), servindo com Starlette + `uvicorn` na porta **7300**. Nessa versão `DefaultRequestHandler` é um alias de `DefaultRequestHandlerV2`.
- Rotas: `create_agent_card_routes(agent_card)` e `create_jsonrpc_routes(request_handler, rpc_url="/a2a")`; compat v0.3 **desligada** (`enable_v0_3_compat=False`) para manter o card limpo na forma v1.0.
- Agent Card espelhando `exemplos/wire/07-a2a-agent-card.json`: nome `Central de Salas`, provider Hill Valley Tech, `version 1.0.0`, interface JSON-RPC `http://127.0.0.1:7300/a2a` (URL pública configurável por env), `capabilities` todas `false`, modos `text/plain`, skill `reservar-sala` com o exemplo do pedido.
- Implementar um `AgentExecutor` próprio (`CentralDeSalasExecutor`) que usa `TaskUpdater` para as transições e delega a ponte ao orquestrador (ADR-009).
- Recusa de `SendMessage` em Task terminal: comportamento nativo do SDK (`UnsupportedOperationError` em `active_task.py` v1.3.0), que vira erro JSON-RPC.
- **Histórico**: o SDK só move uma mensagem de status para o `history` na atualização seguinte. Nos estados terminais, o executor publica a mesma mensagem em `WORKING` e depois no estado final, para que ela apareça no histórico (FDD §4.5, V35).
- **Plano B**: se o SDK não produzir a forma v1.0 exigida pelo validador (nomes de métodos `SendMessage`/`GetTask`, `ROLE_USER`, `TASK_STATE_*`, `result.task`), implementar um endpoint JSON-RPC próprio sobre Starlette reaproveitando os tipos do SDK — documentando a evidência no README.

**Alternativas consideradas.**
- *Implementação própria do servidor A2A desde o início*: mais controle do wire, porém mais código e mais risco de divergir da spec.
- *Expor também REST e gRPC*: fora do escopo; só JSON-RPC é exigido.

**Consequências.**
- (+) Card, serialização e máquina de estados conforme o SDK de referência.
- (+) `GetTask` reflete o `TaskStore` a qualquer momento.
- (−) O `execute()` é chamado de novo para a continuação; o estado da ponte precisa morar fora do executor, por Task (ADR-009).
- **Achados da implementação (2026-11-04)**, todos registrados no README:
  - O `a2a-sdk` 1.3.0 trata a ausência do header `A2A-Version` como v0.3 e recusa `SendMessage` com -32009. O validador e o wire 08 não enviam o header. `ContextoV1`, um `ServerCallContextBuilder` (ponto de extensão oficial passado em `create_jsonrpc_routes(context_builder=...)`), lê a ausência como `1.0`, porque o endpoint só serve v1.0. Um header explícito com outra versão continua sendo recusado.
  - Na continuação, a mensagem traz `taskId` sem `contextId` e o SDK gera um `context_id` novo. O executor usa `context.current_task.context_id`; sem isso, os eventos são rejeitados com `Context in event doesn't match TaskManager`.
  - A telemetria OTel do `a2a-sdk` foi desligada com `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`: spans estão fora de escopo e ela poluía o stderr.
- (−) O serializador do card (`agent_card_to_dict`) acrescenta campos de compatibilidade v0.3 (`url`, `preferredTransport`…) ao lado dos campos v1.0. A V22 passa, e a diferença fica registrada no README.
- (−) `GetTask` responde a Task sem envelope `{"task": …}` (o wire 09 tem envelope). O validador aceita as duas formas, e a diferença fica registrada no README.

**Status.** Aceita.

---

## ADR-009 — Estado das Tasks e do `requestState` em memória no agente

**Contexto.** A Task A2A (id, contextId, status, history, artifacts) precisa ser consultável por `GetTask` e é retomada por um novo `SendMessage` com `taskId`. Entre a pausa e a retomada, o agente precisa lembrar, **por Task**: a chave do `inputRequests`, o `requestState`, o `enum` de alternativas, os argumentos originais da tool e o `traceparent`. O `requestState` não pode aparecer em nenhuma resposta A2A (V34) e é opaco. Persistência em disco está fora de escopo.

**Decisão.**
- **Tasks**: `InMemoryTaskStore` do `a2a-sdk` (dono de `history`, `status`, `artifacts`).
- **Estado da ponte**: um repositório próprio em memória, `PausasRepository`, um `dict[task_id, PausaMcp]`, onde `PausaMcp` contém `tool`, `arguments`, `chave_input`, `request_state`, `alternativas` (lista na ordem do enum) e `trace_id`. Ele **não** faz parte do objeto Task, do `metadata` da Task, do artifact nem das mensagens.
- A entrada é criada ao pausar e **removida** quando a Task chega a estado terminal (COMPLETED, CANCELED, FAILED).
- `request_state` é armazenado como `str` e devolvido sem transformação; nenhum log do agente imprime o valor (no máximo seu tamanho).
- Restart do agente perde Tasks e pausas — aceito (fora de escopo).

**Alternativas consideradas.**
- *Guardar o `requestState` em `task.metadata`*: seria serializado em `GetTask` → vazamento (V34).
- *Persistir em SQLite/arquivo*: fora de escopo, sem benefício para os critérios.

**Consequências.**
- (+) Isolamento natural por Task: duas Tasks pausadas não compartilham entrada (V33).
- (+) Fronteira clara: o que é do protocolo A2A fica no `TaskStore`; o que é do protocolo MCP fica no `PausasRepository`.
- (−) Estado volátil no agente; um restart do agente invalida Tasks pausadas.

**Status.** Aceita.

---

## ADR-010 — Propagação do `traceparent` e log em stderr

**Contexto.** Quando o cliente A2A manda o header `traceparent` (W3C Trace Context), o agente deve propagar o **mesmo trace-id** no `_meta.traceparent` de todos os requests MCP daquela Task; o span-id pode ser novo. O servidor MCP deve registrar em **stderr** método, id e `traceparent` de cada request (sem o logging MCP depreciado). Spans OpenTelemetry estão fora de escopo. O validador imprime o trace-id na primeira linha para o avaliador procurar no log.

**Decisão.**
- **Agente**: o executor lê o header em `context.call_context.state["headers"]["traceparent"]` (confirmado: o `a2a-sdk` grava ali os headers da requisição), sem middleware. O `trace_id` (32 hex) é validado com regex `^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$` e associado à Task na primeira mensagem; continuações sem header reutilizam o trace da Task, continuações com header válido usam o novo. Cada request MCP leva `traceparent = 00-<trace_id>-<span_id novo de 16 hex>-01`. Sem header, não envia `traceparent`.
- **Servidor MCP**: um middleware ASGI, antes do app MCP, faz parse do corpo JSON e escreve **uma linha por request** em stderr: `ts=<iso> method=<método> id=<id> name=<Mcp-Name|-> traceparent=<valor|-> status=<http>`. O corpo é repassado intacto ao app.
- Logs usam `logging` com handler em `sys.stderr`; nada é enviado como `notifications/message` do MCP.

**Alternativas consideradas.**
- *OpenTelemetry SDK com propagator*: atende, mas é fora de escopo e adiciona dependências.
- *Logar dentro de cada tool*: não cobre `tools/list`, `resources/read` nem requests rejeitados.

**Consequências.**
- (+) O avaliador encontra com `grep <trace-id>` o `tools/list`, o `tools/call` inicial e o retry com ids diferentes.
- (+) Requests rejeitados (-32602/-32021) também aparecem no log.
- (−) Ler o corpo no middleware exige recompor o stream ASGI para o app MCP (detalhado no FDD).

**Status.** Aceita.

---

## ADR-011 — Interpretação por regra determinística, sem LLM

**Contexto.** É proibido usar LLM no caminho de execução; o mesmo pedido deve produzir sempre o mesmo resultado (V36). O pedido e a resposta à pausa têm formato fixo. A tese é a opacidade do A2A: o cliente não sabe se por trás há um modelo, um grafo ou um `if`.

**Decisão.**
- Parser por expressão regular, ancorado e estrito:
  - pedido: `^reservar\s+sala=(\S+)\s+inicio=(\S+)\s+fim=(\S+)\s+responsavel=(.+)$` (após `strip()`);
  - continuação: `^escolha=(\S+)$`, em que `recusar` é palavra reservada.
- O texto considerado é a concatenação das `Part`s de texto da mensagem do usuário.
- O agente não converte nem normaliza datas: repassa as strings ISO como vieram; quem valida é o servidor MCP (ADR-003).
- Mensagem fora do formato numa Task nova → Task termina em `TASK_STATE_FAILED` com mensagem fixa de formato (texto no FDD). Mensagem fora do formato numa Task pausada → mantém `TASK_STATE_INPUT_REQUIRED` e repete a linha `alternativas: …` (mesmo tratamento de escolha fora do enum).
- Ids de Task/contexto/mensagem/artifact são aleatórios (não afetam determinismo do *resultado*, que é estado + mensagens + artifact).
- Nenhuma dependência de SDK de provedor de LLM em `pyproject.toml`.

**Alternativas consideradas.**
- *Parser tolerante (ordem livre dos campos, aspas)*: o formato está fechado pelo produto; tolerância cria ambiguidade sem ganho.

**Consequências.**
- (+) Resultado reproduzível; o validador compara duas pausas byte a byte (V36).
- (−) Qualquer variação de formato é rejeitada — intencional.

**Status.** Aceita.

---

## ADR-012 — Reservas em memória no servidor MCP, com ids sequenciais

**Contexto.** Reservas criadas durante a execução devem ser visíveis às consultas seguintes do mesmo processo e não precisam sobreviver a restart. Os exemplos de wire mostram ids `res-0003`, `res-0004`, … seguindo as reservas iniciais. Concorrência de escrita está fora de escopo.

**Decisão.**
- Na subida, o servidor carrega `dados/salas.json`, `dados/reservas.json` e `dados/politica-de-uso.md` (somente leitura; caminhos relativos à raiz do repositório, configuráveis por env `DADOS_DIR`).
- `ReservasRepository` em memória: lista de reservas + contador inicializado a partir do maior número existente; novo id = `res-{n:04d}`.
- Sem lock nem transação: concorrência de escrita está fora de escopo (enunciado).
- Datas armazenadas como `datetime` com fuso; serialização devolve a string ISO original do pedido.
- A versão da política é extraída da primeira linha do arquivo e usada no campo `politica` do `structuredContent`.

**Alternativas consideradas.**
- *Escrever de volta em `dados/reservas.json`*: proibido alterar `dados/`.
- *SQLite em memória*: complexidade sem benefício.

**Consequências.**
- (+) Execução do validador é reprodutível a cada restart.
- (−) Rodar o validador duas vezes sem reiniciar gera falsos negativos (documentado no README).

**Status.** Aceita.
