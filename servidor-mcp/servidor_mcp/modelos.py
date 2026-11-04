"""Modelos de saida das tools.

Os nomes das classes e dos campos reproduzem os outputSchema de
exemplos/wire/01-tools-list.json (ListaDeSalas, SalaOut, Disponibilidade,
ConflitoOut, ReservaOut).
"""

from __future__ import annotations

from pydantic import BaseModel


class SalaOut(BaseModel):
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


class ListaDeSalas(BaseModel):
    salas: list[SalaOut]


class ConflitoOut(BaseModel):
    id: str
    inicio: str
    fim: str
    responsavel: str


class Disponibilidade(BaseModel):
    sala: str
    livre: bool
    conflitos: list[ConflitoOut]


class ReservaOut(BaseModel):
    reserva: str | None = None
    reservado: bool = True
    sala: str | None = None
    inicio: str | None = None
    fim: str | None = None
    responsavel: str | None = None
    politica: str | None = None
    motivo: str | None = None


class EscolhaSala(BaseModel):
    """Resposta da elicitation. O modelo enviado ao cliente e uma subclasse
    criada em tempo de execucao, com `sala` restrita as alternativas."""

    sala: str
