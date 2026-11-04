"""Entrypoint: `uv run python -m servidor_mcp`."""

from __future__ import annotations

import logging
import sys

import uvicorn

from servidor_mcp import config as configuracao
from servidor_mcp import dados as carga
from servidor_mcp.middleware import MetaLogMiddleware
from servidor_mcp.servidor import criar_servidor


def criar_app(cfg: configuracao.Config, dados: carga.DadosIniciais) -> MetaLogMiddleware:
    """App ASGI completo: MCPServer em Streamable HTTP stateless + log no stderr."""
    mcp = criar_servidor(cfg, dados)
    app = mcp.streamable_http_app(
        streamable_http_path=cfg.caminho,
        stateless_http=True,
        json_response=True,
        host=cfg.host,
    )
    return MetaLogMiddleware(app, cfg.caminho)


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = configuracao.carregar()
    except configuracao.ErroDeConfiguracao as e:
        print(f"erro de configuracao: {e}", file=sys.stderr)
        return 2
    dados = carga.carregar(cfg.dados_dir)
    app = criar_app(cfg, dados)
    print(
        f"servidor MCP central-de-salas em http://{cfg.host}:{cfg.porta}{cfg.caminho} "
        f"(politica {dados.politica_versao}, requestState ttl={cfg.ttl_segundos}s)",
        file=sys.stderr,
        flush=True,
    )
    uvicorn.run(app, host=cfg.host, port=cfg.porta, access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
