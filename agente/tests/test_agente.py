from typing import Any

import mcp.types as types
import pytest

from agente import mensagens
from agente.mcp_host import Completo, EntradaNecessaria, ErroDeProtocolo, SessaoCentralDeSalas
from agente.parser import Escolha, ler_escolha, ler_pedido
from agente.pausas import PausasRepository
from agente.ponte import Concluido, Falhou, Pausado, Ponte, Recusado
from agente.trace import novo_traceparent, trace_id_de

PEDIDO = "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


# --- parser e mensagens -------------------------------------------------------------


def test_pedido_valido():
    p = ler_pedido(f"  {PEDIDO}  ")
    assert p is not None and p.argumentos() == {
        "sala": "sala-garagem",
        "inicio": "2026-11-03T14:00:00-03:00",
        "fim": "2026-11-03T15:00:00-03:00",
        "responsavel": "Marty",
    }


def test_responsavel_com_espaco():
    p = ler_pedido(PEDIDO.replace("Marty", "Marty McFly"))
    assert p is not None and p.responsavel == "Marty McFly"


@pytest.mark.parametrize("texto", ["", "reservar", "reserve sala=a inicio=b fim=c responsavel=d", "escolha=x"])
def test_pedido_invalido(texto):
    assert ler_pedido(texto) is None


def test_escolhas():
    assert ler_escolha("escolha=sala-fusca") == Escolha("sala-fusca")
    assert ler_escolha("escolha=recusar").recusar
    assert ler_escolha("escolha = sala-fusca") is None
    assert ler_escolha(PEDIDO) is None


def test_linha_de_alternativas_byte_a_byte():
    assert mensagens.alternativas(("sala-fusca", "sala-mirante")) == "alternativas: sala-fusca, sala-mirante"


# --- trace context ------------------------------------------------------------------


def test_trace_preserva_trace_id_e_troca_span():
    trace_id = trace_id_de(TP)
    assert trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    novo = novo_traceparent(trace_id)
    assert novo.startswith(f"00-{trace_id}-") and novo != TP and trace_id_de(novo) == trace_id


@pytest.mark.parametrize("valor", [None, "", "lixo", "00-" + "0" * 32 + "-00f067aa0ba902b7-01"])
def test_trace_invalido(valor):
    assert trace_id_de(valor) is None
    assert novo_traceparent(None) is None


# --- capability exatamente form mode ------------------------------------------------


def test_capability_somente_form():
    sessao = SessaoCentralDeSalas.__new__(SessaoCentralDeSalas)
    base = types.ClientCapabilities(
        elicitation=types.ElicitationCapability(
            form=types.FormElicitationCapability(), url=types.UrlElicitationCapability()
        )
    )

    # Simula o retorno do SDK (form + url) e confere a restricao a form.
    original = SessaoCentralDeSalas.__mro__[1]._build_capabilities
    try:
        SessaoCentralDeSalas.__mro__[1]._build_capabilities = lambda self, version: base
        caps = sessao._build_capabilities("2026-07-28")
    finally:
        SessaoCentralDeSalas.__mro__[1]._build_capabilities = original
    assert caps.model_dump(by_alias=True, mode="json", exclude_none=True)["elicitation"] == {"form": {}}


# --- ponte com host falso -----------------------------------------------------------


class HostFalso:
    def __init__(self, respostas: list[Any]) -> None:
        self.respostas = respostas
        self.chamadas: list[dict[str, Any]] = []

    async def listar_tools(self, traceparent):
        self.chamadas.append({"metodo": "tools/list", "tp": traceparent})
        return ["listar_salas", "consultar_disponibilidade", "reservar_sala"]

    async def ler_versao_politica(self, traceparent):
        self.chamadas.append({"metodo": "resources/read", "tp": traceparent})
        return "2026-11-01"

    async def chamar_tool(self, nome, argumentos, traceparent, resposta=None, request_state=None):
        self.chamadas.append(
            {"metodo": "tools/call", "args": argumentos, "tp": traceparent, "resposta": resposta, "estado": request_state}
        )
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


SC_OK = {"reserva": "res-0003", "reservado": True, "sala": "sala-fusca", "inicio": "a", "fim": "b", "responsavel": "Marty"}
PAUSA = EntradaNecessaria(chave="k", opcoes=("sala-fusca", "sala-mirante"), request_state="ESTADO-OPACO")


async def _pausar(host: HostFalso, pausas: PausasRepository) -> Ponte:
    ponte = Ponte(host, pausas)
    desfecho = await ponte.iniciar("t1", ler_pedido(PEDIDO), "4bf92f3577b34da6a3ce929d0e0e4736")
    assert desfecho == Pausado(("sala-fusca", "sala-mirante"))
    return ponte


async def test_pausa_e_retomada_ecoam_estado_e_chave():
    host = HostFalso([PAUSA, Completo(SC_OK, "", False)])
    pausas = PausasRepository()
    ponte = await _pausar(host, pausas)
    assert [c["metodo"] for c in host.chamadas] == ["tools/list", "resources/read", "tools/call"]

    desfecho = await ponte.retomar("t1", Escolha("sala-fusca"), None)
    assert isinstance(desfecho, Concluido) and desfecho.artefato["politica"] == "2026-11-01"
    retry = host.chamadas[-1]
    assert retry["estado"] == "ESTADO-OPACO" and retry["args"] == host.chamadas[2]["args"]
    chave, resposta = retry["resposta"]
    assert chave == "k" and resposta.action == "accept" and resposta.content == {"sala": "sala-fusca"}
    assert retry["tp"].split("-")[1] == "4bf92f3577b34da6a3ce929d0e0e4736"  # trace da pausa reaproveitado
    assert await pausas.obter("t1") is None


async def test_recusa_vira_decline_e_cancelado():
    host = HostFalso([PAUSA, Completo({"reservado": False, "motivo": "recusado"}, "", False)])
    ponte = await _pausar(host, PausasRepository())
    assert await ponte.retomar("t1", Escolha("recusar"), None) == Recusado()
    assert host.chamadas[-1]["resposta"][1].action == "decline"


async def test_erro_da_tool_vira_falha_com_texto_intacto():
    texto = "Error executing tool reservar_sala: Sala inexistente: sala-delorean"
    ponte = Ponte(HostFalso([Completo(None, texto, True)]), PausasRepository())
    assert await ponte.iniciar("t1", ler_pedido(PEDIDO), None) == Falhou(texto)


async def test_estado_rejeitado_no_retry():
    host = HostFalso([PAUSA, ErroDeProtocolo(-32602, "Invalid or expired requestState")])
    ponte = await _pausar(host, PausasRepository())
    assert await ponte.retomar("t1", Escolha("sala-fusca"), None) == Falhou(mensagens.ESTADO_INVALIDO)


async def test_pausas_isoladas_por_task():
    outra = EntradaNecessaria(chave="k2", opcoes=("sala-mirante",), request_state="OUTRO")
    host = HostFalso([PAUSA, outra, Completo(SC_OK, "", False), Completo(SC_OK, "", False)])
    pausas = PausasRepository()
    ponte = Ponte(host, pausas)
    await ponte.iniciar("a", ler_pedido(PEDIDO), None)
    await ponte.iniciar("b", ler_pedido(PEDIDO), None)
    await ponte.retomar("b", Escolha("sala-mirante"), None)
    await ponte.retomar("a", Escolha("sala-fusca"), None)
    estados = [c["estado"] for c in host.chamadas if c["metodo"] == "tools/call" and c["estado"]]
    assert estados == ["OUTRO", "ESTADO-OPACO"]


def test_estado_opaco_fora_do_repr():
    from agente.pausas import PausaMcp

    p = PausaMcp("reservar_sala", {}, "k", "SEGREDO-NAO-LOGAR", ("a",), None, "v")
    assert "SEGREDO-NAO-LOGAR" not in repr(p)
