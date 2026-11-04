"""Regras de dominio: validacao do pedido, conflitos e alternativas.

As mensagens de erro sao a fonte unica de verdade do enunciado e precisam
aparecer byte a byte no resultado da tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from mcp.server.mcpserver.exceptions import ToolError

from servidor_mcp.dados import Sala

FUSO_SAO_PAULO = timezone(timedelta(hours=-3))
ABERTURA = time(8, 0)
FECHAMENTO = time(20, 0)
DURACAO_MAXIMA = timedelta(hours=2)
MAX_ALTERNATIVAS = 3

MSG_INTERVALO_INVALIDO = "Intervalo invalido: fim deve ser posterior a inicio"
MSG_FORA_DA_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
MSG_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"
MSG_SEM_ALTERNATIVAS = "Sem alternativas disponiveis no intervalo"


def msg_sala_inexistente(sala: str) -> str:
    return f"Sala inexistente: {sala}"


class ErroDeNegocio(ToolError):
    """Erro de execucao da tool. Precisa ser ToolError: o SDK so preserva a
    mensagem de ToolError/ResourceError; qualquer outra excecao vira um erro
    generico sem texto."""


@dataclass(frozen=True)
class Pedido:
    sala: Sala
    inicio: datetime
    fim: datetime


def ler_instante(valor: str) -> datetime:
    texto = valor.strip()
    if texto.endswith(("Z", "z")):  # fromisoformat do 3.10 nao aceita o sufixo Z
        texto = texto[:-1] + "+00:00"
    instante = datetime.fromisoformat(texto)
    if instante.tzinfo is None:
        raise ValueError("instante sem fuso horario")
    return instante


def validar_pedido(salas: dict[str, Sala], sala: str, inicio: str, fim: str) -> Pedido:
    """Valida na ordem: sala, formato, intervalo, janela de uso, duracao."""
    encontrada = salas.get(sala)
    if encontrada is None:
        raise ErroDeNegocio(msg_sala_inexistente(sala))
    try:
        dt_inicio = ler_instante(inicio)
        dt_fim = ler_instante(fim)
    except ValueError as e:
        # O enunciado nao define mensagem para data ilegivel: tratada como intervalo invalido.
        raise ErroDeNegocio(MSG_INTERVALO_INVALIDO) from e
    if dt_fim <= dt_inicio:
        raise ErroDeNegocio(MSG_INTERVALO_INVALIDO)
    local_inicio = dt_inicio.astimezone(FUSO_SAO_PAULO)
    local_fim = dt_fim.astimezone(FUSO_SAO_PAULO)
    dentro_da_janela = (
        local_inicio.date() == local_fim.date()
        and local_inicio.time() >= ABERTURA
        and local_fim.time() <= FECHAMENTO
    )
    if not dentro_da_janela:
        raise ErroDeNegocio(MSG_FORA_DA_JANELA)
    if dt_fim - dt_inicio > DURACAO_MAXIMA:
        raise ErroDeNegocio(MSG_DURACAO)
    return Pedido(sala=encontrada, inicio=dt_inicio, fim=dt_fim)


def sobrepoe(inicio_a: datetime, fim_a: datetime, inicio_b: datetime, fim_b: datetime) -> bool:
    """Intervalos semiabertos [inicio, fim): encostar nao conflita."""
    return inicio_a < fim_b and inicio_b < fim_a


def ordenar_alternativas(candidatas: list[Sala]) -> list[str]:
    """Capacidade crescente e, em empate, id em ordem alfabetica; no maximo tres."""
    return [s.id for s in sorted(candidatas, key=lambda s: (s.capacidade, s.id))[:MAX_ALTERNATIVAS]]
