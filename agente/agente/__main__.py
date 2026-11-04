"""Entrypoint: `uv run python -m agente`."""

from __future__ import annotations

import os

# Spans OpenTelemetry estao fora do escopo (so a propagacao do traceparent e
# exigida). A instrumentacao do a2a-sdk le esta variavel na importacao e, ligada,
# polui o stderr com "Failed to detach context". Precisa vir antes de importar a2a.
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

import contextlib  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import uvicorn  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from agente import config as configuracao  # noqa: E402
from agente.card import criar_card  # noqa: E402
from agente.contexto import ContextoV1  # noqa: E402
from agente.executor import CentralDeSalasExecutor  # noqa: E402
from agente.mcp_host import McpHost  # noqa: E402
from agente.pausas import PausasRepository  # noqa: E402
from agente.ponte import Ponte  # noqa: E402


def criar_app(cfg: configuracao.Config) -> Starlette:
    card = criar_card(f"{cfg.url_publica}{cfg.caminho_a2a}")
    host = McpHost(cfg.mcp_url, cfg.mcp_timeout)
    pausas = PausasRepository()
    ponte = Ponte(host, pausas)
    handler = DefaultRequestHandler(
        agent_executor=CentralDeSalasExecutor(ponte),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    @contextlib.asynccontextmanager
    async def ciclo_de_vida(_: Starlette) -> AsyncIterator[None]:
        # Um cliente MCP vivo por processo (permitido): nao ha sessao de
        # protocolo, cada request leva versao e capabilities no _meta.
        async with host:
            yield

    app = Starlette(
        routes=[
            *create_agent_card_routes(agent_card=card),
            *create_jsonrpc_routes(request_handler=handler, rpc_url=cfg.caminho_a2a, context_builder=ContextoV1()),
        ],
        lifespan=ciclo_de_vida,
    )
    # Exposto so para os testes ponta a ponta conferirem que o requestState
    # guardado nunca aparece numa resposta A2A. Nenhuma rota le isto.
    app.state.pausas = pausas
    return app


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for ruidoso in ("httpx", "httpx2"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    cfg = configuracao.carregar()
    print(
        f"agente Central de Salas em {cfg.url_publica}{cfg.caminho_a2a} "
        f"(card em {cfg.url_publica}/.well-known/agent-card.json; MCP em {cfg.mcp_url})",
        file=sys.stderr,
        flush=True,
    )
    uvicorn.run(criar_app(cfg), host=cfg.host, port=cfg.porta, access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
