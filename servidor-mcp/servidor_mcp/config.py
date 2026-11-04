"""Configuracao do servidor MCP, lida do ambiente.

O segredo do requestState vem SOMENTE de REQUEST_STATE_SECRET: nao ha valor
padrao no codigo. Sem ele, o processo nao sobe.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TTL_MINIMO = 300
TTL_MAXIMO = 1800
TTL_PADRAO = 600
BYTES_MINIMOS_SEGREDO = 32

INSTRUCAO_SEGREDO = (
    "Gere um segredo com:\n"
    "  export REQUEST_STATE_SECRET=\"$(python3 -c 'import secrets; print(secrets.token_hex(32))')\""
)


class ErroDeConfiguracao(Exception):
    """Configuracao invalida: o processo nao deve subir."""


@dataclass(frozen=True)
class Config:
    segredo: bytes
    ttl_segundos: int
    host: str
    porta: int
    caminho: str
    dados_dir: Path


def _ler_segredo(ambiente: dict[str, str]) -> bytes:
    bruto = ambiente.get("REQUEST_STATE_SECRET", "").strip()
    if not bruto:
        raise ErroDeConfiguracao(f"REQUEST_STATE_SECRET nao definido.\n{INSTRUCAO_SEGREDO}")
    try:
        segredo = bytes.fromhex(bruto)
    except ValueError as e:
        raise ErroDeConfiguracao(f"REQUEST_STATE_SECRET deve ser hexadecimal.\n{INSTRUCAO_SEGREDO}") from e
    if len(segredo) < BYTES_MINIMOS_SEGREDO:
        raise ErroDeConfiguracao(
            f"REQUEST_STATE_SECRET precisa de pelo menos {BYTES_MINIMOS_SEGREDO} bytes "
            f"({BYTES_MINIMOS_SEGREDO * 2} caracteres hex); veio {len(segredo)}.\n{INSTRUCAO_SEGREDO}"
        )
    return segredo


def _ler_ttl(ambiente: dict[str, str]) -> int:
    bruto = ambiente.get("REQUEST_STATE_TTL_SECONDS", str(TTL_PADRAO)).strip()
    try:
        ttl = int(bruto)
    except ValueError as e:
        raise ErroDeConfiguracao(f"REQUEST_STATE_TTL_SECONDS deve ser inteiro; veio {bruto!r}") from e
    if not TTL_MINIMO <= ttl <= TTL_MAXIMO:
        raise ErroDeConfiguracao(
            f"REQUEST_STATE_TTL_SECONDS deve estar entre {TTL_MINIMO} e {TTL_MAXIMO} (5 a 30 minutos); veio {ttl}"
        )
    return ttl


def carregar(ambiente: dict[str, str] | None = None) -> Config:
    amb = dict(os.environ if ambiente is None else ambiente)
    raiz_repo = Path(__file__).resolve().parents[2]
    return Config(
        segredo=_ler_segredo(amb),
        ttl_segundos=_ler_ttl(amb),
        host=amb.get("MCP_HOST", "127.0.0.1"),
        porta=int(amb.get("MCP_PORT", "7301")),
        caminho=amb.get("MCP_PATH", "/mcp"),
        dados_dir=Path(amb.get("DADOS_DIR", str(raiz_repo / "dados"))).resolve(),
    )
