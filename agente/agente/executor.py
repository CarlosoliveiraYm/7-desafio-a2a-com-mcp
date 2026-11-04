"""AgentExecutor do a2a-sdk: a maquina de estados da Task.

Task nova:     SUBMITTED -> WORKING -> {COMPLETED | FAILED | CANCELED | INPUT_REQUIRED}
Continuacao:   INPUT_REQUIRED -> {INPUT_REQUIRED (escolha invalida) | WORKING -> ...}
Estados terminais nao chegam aqui: o SDK recusa a mensagem com erro JSON-RPC.
"""

from __future__ import annotations

import json
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

from agente import mensagens
from agente.parser import ler_escolha, ler_pedido
from agente.ponte import Concluido, Desfecho, Falhou, Pausado, Ponte, Recusado
from agente.trace import trace_id_de

log = logging.getLogger("agente.executor")


def _trace_id(context: RequestContext) -> str | None:
    # O a2a-sdk grava os headers HTTP da chamada em call_context.state["headers"].
    estado = getattr(context.call_context, "state", None) or {}
    cabecalhos = estado.get("headers") or {}
    return trace_id_de(cabecalhos.get("traceparent"))


class CentralDeSalasExecutor(AgentExecutor):
    def __init__(self, ponte: Ponte) -> None:
        self._ponte = ponte

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        # Na continuacao, a mensagem traz taskId sem contextId e o SDK gera um
        # context_id novo no RequestContext. Os eventos precisam do contextId
        # da Task armazenada, senao o SDK os rejeita ("Context in event doesn't
        # match TaskManager").
        atual = context.current_task
        context_id = (atual.context_id if atual is not None else context.context_id) or ""
        updater = TaskUpdater(event_queue=event_queue, task_id=task_id, context_id=context_id)
        texto = context.get_user_input()
        trace_id = _trace_id(context)
        try:
            if context.current_task is None:
                desfecho = await self._nova_task(context, event_queue, updater, texto, trace_id)
            else:
                desfecho = await self._continuacao(task_id, updater, texto, trace_id)
        except Exception:  # nunca deixar a Task terminar sem mensagem
            log.exception("task=%s erro inesperado", task_id)
            desfecho = Falhou(mensagens.RESPOSTA_NAO_SUPORTADA)
        if desfecho is not None:
            await self._aplicar(updater, task_id, desfecho)

    async def _nova_task(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        updater: TaskUpdater,
        texto: str,
        trace_id: str | None,
    ) -> Desfecho:
        historico = [context.message] if context.message else []
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=historico,
            )
        )
        log.info("task=%s SUBMITTED trace_id=%s", context.task_id, trace_id or "-")
        await updater.start_work()
        pedido = ler_pedido(texto)
        if pedido is None:
            return Falhou(mensagens.FORMATO_INVALIDO)
        return await self._ponte.iniciar(context.task_id or "", pedido, trace_id)

    async def _continuacao(
        self, task_id: str, updater: TaskUpdater, texto: str, trace_id: str | None
    ) -> Desfecho | None:
        pausa = await self._ponte.pausa_de(task_id)
        if pausa is None:
            return Falhou(mensagens.ESTADO_INVALIDO)
        escolha = ler_escolha(texto)
        if escolha is None or (not escolha.recusar and escolha.valor not in pausa.opcoes):
            # Fora do enum (ou fora do formato): continua pausada, repetindo a lista.
            log.info("task=%s escolha invalida, segue INPUT_REQUIRED", task_id)
            return Pausado(pausa.opcoes)
        await updater.start_work()
        return await self._ponte.retomar(task_id, escolha, trace_id)

    async def _aplicar(self, updater: TaskUpdater, task_id: str, desfecho: Desfecho) -> None:
        match desfecho:
            case Pausado(opcoes=opcoes):
                await updater.requires_input(message=self._mensagem(updater, mensagens.alternativas(opcoes)))
                log.info("task=%s INPUT_REQUIRED", task_id)
            case Concluido(artefato=artefato):
                await updater.add_artifact(
                    parts=[Part(text=json.dumps(artefato, ensure_ascii=False))], name="reserva"
                )
                await self._terminar(
                    updater, TaskState.TASK_STATE_COMPLETED, mensagens.sucesso(artefato["reserva"], artefato["sala"])
                )
                log.info("task=%s COMPLETED reserva=%s", task_id, artefato["reserva"])
            case Recusado():
                await self._terminar(updater, TaskState.TASK_STATE_CANCELED, mensagens.RECUSA)
                log.info("task=%s CANCELED", task_id)
            case Falhou(mensagem=mensagem):
                await self._terminar(updater, TaskState.TASK_STATE_FAILED, mensagem)
                log.info("task=%s FAILED", task_id)

    @staticmethod
    def _mensagem(updater: TaskUpdater, texto: str):  # noqa: ANN205
        return updater.new_agent_message(parts=[Part(text=texto)])

    async def _terminar(self, updater: TaskUpdater, estado: TaskState, texto: str) -> None:
        """O SDK so move a mensagem de status para o history na atualizacao
        seguinte, e nada vem depois de um estado terminal. Publicar a mesma
        mensagem em WORKING e depois no estado final a deixa no history e em
        status.message (mesmo messageId), como no exemplo de wire 10."""
        mensagem = self._mensagem(updater, texto)
        await updater.update_status(TaskState.TASK_STATE_WORKING, message=mensagem)
        await updater.update_status(estado, message=mensagem)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue=event_queue, task_id=context.task_id or "", context_id=context.context_id or "")
        await updater.cancel()
