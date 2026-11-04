"""Interpretacao por regra do formato fixo. Sem LLM: o mesmo texto produz
sempre o mesmo resultado."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PEDIDO = re.compile(r"^reservar\s+sala=(\S+)\s+inicio=(\S+)\s+fim=(\S+)\s+responsavel=(.+)$")
_ESCOLHA = re.compile(r"^escolha=(\S+)$")
RECUSAR = "recusar"


@dataclass(frozen=True)
class PedidoReserva:
    sala: str
    inicio: str
    fim: str
    responsavel: str

    def argumentos(self) -> dict[str, str]:
        return {"sala": self.sala, "inicio": self.inicio, "fim": self.fim, "responsavel": self.responsavel}


@dataclass(frozen=True)
class Escolha:
    valor: str

    @property
    def recusar(self) -> bool:
        return self.valor == RECUSAR


def ler_pedido(texto: str) -> PedidoReserva | None:
    m = _PEDIDO.match(texto.strip())
    if not m:
        return None
    sala, inicio, fim, responsavel = m.groups()
    return PedidoReserva(sala=sala, inicio=inicio, fim=fim, responsavel=responsavel.strip())


def ler_escolha(texto: str) -> Escolha | None:
    m = _ESCOLHA.match(texto.strip())
    return Escolha(m.group(1)) if m else None
