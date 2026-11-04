"""Maquina de estados da Task, observada pelos eventos que o executor publica.

O validador so ve o estado final de cada SendMessage; aqui conferimos as
transicoes intermediarias (SUBMITTED, WORKING), as mensagens e o artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from a2a.types import Message, Part, Role, Task, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent

from agente import mensagens
from agente.executor import CentralDeSalasExecutor
from agente.parser import Escolha, PedidoReserva
from agente.pausas import PausaMcp
from agente.ponte import Concluido, Falhou, Pausado, Recusado

PEDIDO = "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
ARTEFATO = {
    "reserva": "res-0003",
    "sala": "sala-fusca",
    "inicio": "2026-11-03T14:00:00-03:00",
    "fim": "2026-11-03T15:00:00-03:00",
    "responsavel": "Marty",
    "politica": "2026-11-01",
}


class FilaFalsa:
    def __init__(self) -> None:
        self.eventos: list[Any] = []

    async def enqueue_event(self, evento: Any) -> None:
        self.eventos.append(evento)

    def estados(self) -> list[str]:
        saida = []
        for e in self.eventos:
            if isinstance(e, Task):
                saida.append(TaskState.Name(e.status.state))
            elif isinstance(e, TaskStatusUpdateEvent):
                saida.append(TaskState.Name(e.status.state))
        return saida

    def status(self) -> list[TaskStatusUpdateEvent]:
        return [e for e in self.eventos if isinstance(e, TaskStatusUpdateEvent)]

    def artifacts(self) -> list[TaskArtifactUpdateEvent]:
        return [e for e in self.eventos if isinstance(e, TaskArtifactUpdateEvent)]


@dataclass
class PonteFalsa:
    desfecho_inicio: Any = None
    desfecho_retomada: Any = None
    pausa: PausaMcp | None = None
    chamadas: list[tuple] = field(default_factory=list)

    async def pausa_de(self, task_id: str) -> PausaMcp | None:
        return self.pausa

    async def iniciar(self, task_id: str, pedido: PedidoReserva, trace_id: str | None):
        self.chamadas.append(("iniciar", task_id, pedido, trace_id))
        return self.desfecho_inicio

    async def retomar(self, task_id: str, escolha: Escolha, trace_id: str | None):
        self.chamadas.append(("retomar", task_id, escolha, trace_id))
        return self.desfecho_retomada


def contexto(texto: str, tarefa_atual: Task | None = None, traceparent: str | None = None) -> SimpleNamespace:
    mensagem = Message(message_id="m-usuario", role=Role.ROLE_USER, parts=[Part(text=texto)], task_id="t1")
    cabecalhos = {"traceparent": traceparent} if traceparent else {}
    return SimpleNamespace(
        task_id="t1",
        # Na continuacao o SDK gera um context_id novo; o executor deve usar o da Task.
        context_id="ctx-gerado-pelo-sdk" if tarefa_atual else "ctx-1",
        current_task=tarefa_atual,
        message=mensagem,
        call_context=SimpleNamespace(state={"headers": cabecalhos}),
        get_user_input=lambda: texto,
    )


def task_pausada() -> Task:
    return Task(id="t1", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED))


PAUSA = PausaMcp("reservar_sala", {}, "k", "ESTADO", ("sala-fusca", "sala-mirante"), TRACE_ID, "2026-11-01")


def textos(evento: TaskStatusUpdateEvent) -> str:
    return " ".join(p.text for p in evento.status.message.parts)


async def executar(ponte: PonteFalsa, ctx: SimpleNamespace) -> FilaFalsa:
    fila = FilaFalsa()
    await CentralDeSalasExecutor(ponte).execute(ctx, fila)
    return fila


# --- Task nova ------------------------------------------------------------------------


async def test_sucesso_passa_por_submitted_e_working_e_termina_com_artifact():
    ponte = PonteFalsa(desfecho_inicio=Concluido(ARTEFATO))
    fila = await executar(ponte, contexto(PEDIDO, traceparent=TP))
    assert fila.estados() == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED"]
    task_inicial = fila.eventos[0]
    assert isinstance(task_inicial, Task) and task_inicial.history[0].parts[0].text == PEDIDO
    [artifact] = fila.artifacts()
    assert artifact.artifact.name == "reserva"
    assert json.loads(artifact.artifact.parts[0].text) == ARTEFATO
    assert ponte.chamadas[0][3] == TRACE_ID  # trace-id do header A2A chega a ponte


async def test_mensagem_terminal_publicada_duas_vezes_para_entrar_no_history():
    fila = await executar(PonteFalsa(desfecho_inicio=Falhou("Error executing tool reservar_sala: Sala inexistente: x")),
                          contexto(PEDIDO))
    working, final = fila.status()[-2:]
    assert TaskState.Name(final.status.state) == "TASK_STATE_FAILED"
    # A mesma Message em WORKING e no estado final: o SDK move a primeira para o history.
    assert working.status.message.message_id == final.status.message.message_id
    assert textos(final) == "Error executing tool reservar_sala: Sala inexistente: x"
    assert final.status.message.role == Role.ROLE_AGENT


async def test_pausa_publica_linha_de_alternativas_exata():
    fila = await executar(PonteFalsa(desfecho_inicio=Pausado(("sala-fusca", "sala-mirante"))), contexto(PEDIDO))
    assert fila.estados() == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_INPUT_REQUIRED"]
    assert textos(fila.status()[-1]) == "alternativas: sala-fusca, sala-mirante"


async def test_pedido_fora_do_formato_termina_em_failed_sem_chamar_mcp():
    ponte = PonteFalsa()
    fila = await executar(ponte, contexto("me reserva a garagem amanha"))
    assert fila.estados()[-1] == "TASK_STATE_FAILED"
    assert textos(fila.status()[-1]) == mensagens.FORMATO_INVALIDO
    assert ponte.chamadas == []


async def test_excecao_inesperada_nunca_deixa_task_sem_mensagem():
    class PonteQuebrada(PonteFalsa):
        async def iniciar(self, *a):
            raise RuntimeError("boom")

    fila = await executar(PonteQuebrada(), contexto(PEDIDO))
    assert fila.estados()[-1] == "TASK_STATE_FAILED" and textos(fila.status()[-1])


# --- Continuacao ------------------------------------------------------------------------


async def test_escolha_fora_do_enum_repete_a_mesma_lista_sem_chamar_mcp():
    ponte = PonteFalsa(pausa=PAUSA)
    fila = await executar(ponte, contexto("escolha=sala-aquario", task_pausada()))
    assert fila.estados() == ["TASK_STATE_INPUT_REQUIRED"]
    assert textos(fila.status()[0]) == "alternativas: sala-fusca, sala-mirante"
    assert ponte.chamadas == []


async def test_texto_fora_do_formato_na_pausa_repete_a_lista():
    fila = await executar(PonteFalsa(pausa=PAUSA), contexto("quero a fusca", task_pausada()))
    assert fila.estados() == ["TASK_STATE_INPUT_REQUIRED"]
    assert textos(fila.status()[0]) == "alternativas: sala-fusca, sala-mirante"


async def test_escolha_valida_retoma_e_usa_context_id_da_task():
    ponte = PonteFalsa(pausa=PAUSA, desfecho_retomada=Concluido(ARTEFATO))
    fila = await executar(ponte, contexto("escolha=sala-fusca", task_pausada()))
    assert fila.estados() == ["TASK_STATE_WORKING", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED"]
    assert {e.context_id for e in fila.status()} == {"ctx-1"}
    assert ponte.chamadas == [("retomar", "t1", Escolha("sala-fusca"), None)]


async def test_recusar_termina_em_canceled():
    ponte = PonteFalsa(pausa=PAUSA, desfecho_retomada=Recusado())
    fila = await executar(ponte, contexto("escolha=recusar", task_pausada()))
    assert fila.estados()[-1] == "TASK_STATE_CANCELED"
    assert textos(fila.status()[-1]) == mensagens.RECUSA
    assert ponte.chamadas[0][2].recusar


async def test_nova_pausa_no_retry_volta_a_input_required():
    ponte = PonteFalsa(pausa=PAUSA, desfecho_retomada=Pausado(("sala-mirante",)))
    fila = await executar(ponte, contexto("escolha=sala-fusca", task_pausada()))
    assert fila.estados() == ["TASK_STATE_WORKING", "TASK_STATE_INPUT_REQUIRED"]
    assert textos(fila.status()[-1]) == "alternativas: sala-mirante"
