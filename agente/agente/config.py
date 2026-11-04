"""Configuracao do agente, lida do ambiente. O agente nao recebe o segredo do
requestState: ele nunca abre o estado, so guarda e ecoa."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str
    porta: int
    caminho_a2a: str
    url_publica: str
    mcp_url: str
    mcp_timeout: float


def carregar(ambiente: dict[str, str] | None = None) -> Config:
    amb = dict(os.environ if ambiente is None else ambiente)
    host = amb.get("AGENTE_HOST", "127.0.0.1")
    porta = int(amb.get("AGENTE_PORT", "7300"))
    return Config(
        host=host,
        porta=porta,
        caminho_a2a=amb.get("AGENTE_A2A_PATH", "/a2a"),
        url_publica=amb.get("AGENTE_PUBLIC_URL", f"http://127.0.0.1:{porta}").rstrip("/"),
        mcp_url=amb.get("MCP_URL", "http://127.0.0.1:7301/mcp"),
        mcp_timeout=float(amb.get("MCP_TIMEOUT_SECONDS", "10")),
    )
