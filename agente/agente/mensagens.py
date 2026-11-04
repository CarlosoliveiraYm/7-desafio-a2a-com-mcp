"""Textos fixos devolvidos ao cliente A2A. A linha de alternativas e comparada
byte a byte pelo avaliador: sem prefixo, sem saudacao."""

from __future__ import annotations

from collections.abc import Sequence


def alternativas(ids: Sequence[str]) -> str:
    return "alternativas: " + ", ".join(ids)


def sucesso(reserva: str, sala: str) -> str:
    return f"Reserva {reserva} confirmada na {sala}."


RECUSA = "Reserva recusada pelo usuario."
FORMATO_INVALIDO = (
    "Pedido fora do formato: use reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>"
)
ESTADO_INVALIDO = "Nao foi possivel retomar a reserva: estado expirado ou invalido"
MCP_INDISPONIVEL = "Servidor de salas indisponivel"
RESPOSTA_NAO_SUPORTADA = "Resposta do servidor de salas nao suportada"
TOOL_AUSENTE = "Servidor de salas nao oferece a ferramenta reservar_sala"
