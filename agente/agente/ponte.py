"""A PONTE: onde o estado do MCP (input_required + requestState) encontra o
estado do A2A (Task).

- `iniciar`: descobre as tools, le a politica e faz o tools/call. Se o servidor
  responde input_required, a pausa (chave, requestState, opcoes, argumentos) e
  guardada ligada a Task e o desfecho e `Pausado`: o executor coloca a Task em
  TASK_STATE_INPUT_REQUIRED.
- `retomar`: a continuacao A2A vira um tools/call NOVO (id JSON-RPC novo), com
  os mesmos argumentos, inputResponses na mesma chave e o requestState ecoado
  sem modificacao.

O agente nao decide dominio: conflito, politica e alternativas vem do
servidor MCP. A unica checagem local e de protocolo: a escolha pertence ao
enum que o proprio servidor mandou.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import mcp.types as types

from agente import mensagens
from agente.mcp_host import (
    Completo,
    EntradaNecessaria,
    ErroDeProtocolo,
    McpHost,
    RespostaNaoSuportada,
    ServidorIndisponivel,
)
from agente.parser import Escolha, PedidoReserva
from agente.pausas import PausaMcp, PausasRepository
from agente.trace import novo_traceparent

TOOL_RESERVA = "reservar_sala"
INVALID_PARAMS = -32602

log = logging.getLogger("agente.ponte")


@dataclass(frozen=True)
class Concluido:
    artefato: dict[str, Any]


@dataclass(frozen=True)
class Pausado:
    opcoes: tuple[str, ...]


@dataclass(frozen=True)
class Recusado:
    pass


@dataclass(frozen=True)
class Falhou:
    mensagem: str


Desfecho = Concluido | Pausado | Recusado | Falhou


class Ponte:
    def __init__(self, host: McpHost, pausas: PausasRepository) -> None:
        self._host = host
        self._pausas = pausas

    async def pausa_de(self, task_id: str) -> PausaMcp | None:
        return await self._pausas.obter(task_id)

    async def iniciar(self, task_id: str, pedido: PedidoReserva, trace_id: str | None) -> Desfecho:
        try:
            # Descoberta em runtime, antes do primeiro tools/call desta Task.
            tools = await self._host.listar_tools(novo_traceparent(trace_id))
            if TOOL_RESERVA not in tools:
                return Falhou(mensagens.TOOL_AUSENTE)
            versao = await self._host.ler_versao_politica(novo_traceparent(trace_id))
            argumentos = pedido.argumentos()
            resultado = await self._host.chamar_tool(TOOL_RESERVA, argumentos, novo_traceparent(trace_id))
        except ServidorIndisponivel:
            return Falhou(mensagens.MCP_INDISPONIVEL)
        except ErroDeProtocolo as e:
            return Falhou(e.mensagem)
        except RespostaNaoSuportada:
            return Falhou(mensagens.RESPOSTA_NAO_SUPORTADA)
        return await self._traduzir(task_id, resultado, argumentos, trace_id, versao)

    async def retomar(self, task_id: str, escolha: Escolha, trace_id: str | None) -> Desfecho:
        pausa = await self._pausas.obter(task_id)
        if pausa is None:
            return Falhou(mensagens.ESTADO_INVALIDO)
        trace = trace_id or pausa.trace_id
        if escolha.recusar:
            resposta = types.ElicitResult(action="decline")
        else:
            resposta = types.ElicitResult(action="accept", content={"sala": escolha.valor})
        try:
            resultado = await self._host.chamar_tool(
                pausa.tool,
                pausa.argumentos,
                novo_traceparent(trace),
                resposta=(pausa.chave, resposta),
                request_state=pausa.request_state,
            )
        except ServidorIndisponivel:
            await self._pausas.remover(task_id)
            return Falhou(mensagens.MCP_INDISPONIVEL)
        except ErroDeProtocolo as e:
            await self._pausas.remover(task_id)
            return Falhou(mensagens.ESTADO_INVALIDO if e.codigo == INVALID_PARAMS else e.mensagem)
        except RespostaNaoSuportada:
            await self._pausas.remover(task_id)
            return Falhou(mensagens.RESPOSTA_NAO_SUPORTADA)
        return await self._traduzir(task_id, resultado, pausa.argumentos, trace, pausa.versao_politica)

    async def _traduzir(
        self,
        task_id: str,
        resultado: Completo | EntradaNecessaria,
        argumentos: dict[str, Any],
        trace_id: str | None,
        versao: str,
    ) -> Desfecho:
        if isinstance(resultado, EntradaNecessaria):
            # input_required -> pausa. Uma nova pergunta (ex.: as alternativas
            # mudaram entre as idas) substitui a pausa anterior.
            await self._pausas.guardar(
                task_id,
                PausaMcp(
                    tool=TOOL_RESERVA,
                    argumentos=argumentos,
                    chave=resultado.chave,
                    request_state=resultado.request_state,
                    opcoes=resultado.opcoes,
                    trace_id=trace_id,
                    versao_politica=versao,
                ),
            )
            log.info("task=%s pausada opcoes=%s", task_id, ",".join(resultado.opcoes))
            return Pausado(resultado.opcoes)

        await self._pausas.remover(task_id)
        if resultado.is_error:
            return Falhou(resultado.texto)  # mensagem da tool, sem alteracao
        dados = resultado.estruturado or {}
        if dados.get("reservado") is False:
            return Recusado()
        if not dados.get("reserva"):
            return Falhou(mensagens.RESPOSTA_NAO_SUPORTADA)
        return Concluido(
            artefato={
                "reserva": dados["reserva"],
                "sala": dados.get("sala"),
                "inicio": dados.get("inicio"),
                "fim": dados.get("fim"),
                "responsavel": dados.get("responsavel"),
                "politica": versao,  # versao lida do resource politica://uso pelo agente
            }
        )
