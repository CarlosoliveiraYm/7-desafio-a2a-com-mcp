"""Reservas em memoria: visiveis as consultas seguintes do mesmo processo,
perdidas no restart (fora de escopo persistir)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from servidor_mcp.dados import DadosIniciais, Sala
from servidor_mcp.dominio import (
    MSG_SEM_ALTERNATIVAS,
    ErroDeNegocio,
    Pedido,
    ler_instante,
    ordenar_alternativas,
    sobrepoe,
)


@dataclass(frozen=True)
class Reserva:
    id: str
    sala: str
    inicio: datetime
    fim: datetime
    inicio_texto: str
    fim_texto: str
    responsavel: str


class Agenda:
    def __init__(self, dados: DadosIniciais) -> None:
        self.salas: dict[str, Sala] = {s.id: s for s in dados.salas}
        self._reservas: list[Reserva] = []
        self._ultimo_numero = 0
        for r in dados.reservas:
            self._reservas.append(
                Reserva(
                    id=r["id"],
                    sala=r["sala"],
                    inicio=ler_instante(r["inicio"]),
                    fim=ler_instante(r["fim"]),
                    inicio_texto=r["inicio"],
                    fim_texto=r["fim"],
                    responsavel=r["responsavel"],
                )
            )
            numero = re.fullmatch(r"res-(\d+)", r["id"])
            if numero:
                self._ultimo_numero = max(self._ultimo_numero, int(numero.group(1)))

    def conflitos(self, sala: str, inicio: datetime, fim: datetime) -> list[Reserva]:
        return [r for r in self._reservas if r.sala == sala and sobrepoe(inicio, fim, r.inicio, r.fim)]

    def livre(self, sala: str, inicio: datetime, fim: datetime) -> bool:
        return not self.conflitos(sala, inicio, fim)

    def alternativas(self, pedido: Pedido) -> list[str]:
        candidatas = [
            s
            for s in self.salas.values()
            if s.id != pedido.sala.id
            and s.capacidade >= pedido.sala.capacidade
            and self.livre(s.id, pedido.inicio, pedido.fim)
        ]
        return ordenar_alternativas(candidatas)

    async def reservar(self, sala: str, inicio: str, fim: str, responsavel: str) -> Reserva:
        dt_inicio, dt_fim = ler_instante(inicio), ler_instante(fim)
        if not self.livre(sala, dt_inicio, dt_fim):
            # A sala escolhida foi ocupada entre a pergunta e o retry.
            raise ErroDeNegocio(MSG_SEM_ALTERNATIVAS)
        self._ultimo_numero += 1
        reserva = Reserva(
            id=f"res-{self._ultimo_numero:04d}",
            sala=sala,
            inicio=dt_inicio,
            fim=dt_fim,
            inicio_texto=inicio,
            fim_texto=fim,
            responsavel=responsavel,
        )
        self._reservas.append(reserva)
        return reserva
