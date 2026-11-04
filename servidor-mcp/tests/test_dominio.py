from pathlib import Path

import pytest

from servidor_mcp import config, dados
from servidor_mcp.dominio import (
    MSG_DURACAO,
    MSG_FORA_DA_JANELA,
    MSG_INTERVALO_INVALIDO,
    ErroDeNegocio,
    validar_pedido,
)
from servidor_mcp.repositorio import Agenda

DADOS = dados.carregar(Path(__file__).resolve().parents[2] / "dados")
DIA = "2026-11-03"


def h(hora: str) -> str:
    return f"{DIA}T{hora}:00-03:00"


@pytest.fixture
def agenda() -> Agenda:
    return Agenda(DADOS)


def test_versao_da_politica():
    assert DADOS.politica_versao == "2026-11-01"


@pytest.mark.parametrize(
    ("sala", "inicio", "fim", "mensagem"),
    [
        ("sala-delorean", h("09:00"), h("10:00"), "Sala inexistente: sala-delorean"),
        ("sala-aquario", h("10:00"), h("09:00"), MSG_INTERVALO_INVALIDO),
        ("sala-aquario", h("10:00"), h("10:00"), MSG_INTERVALO_INVALIDO),
        ("sala-aquario", "ontem", h("10:00"), MSG_INTERVALO_INVALIDO),
        ("sala-aquario", f"{DIA}T09:00:00", h("10:00"), MSG_INTERVALO_INVALIDO),
        ("sala-aquario", h("07:00"), h("08:00"), MSG_FORA_DA_JANELA),
        ("sala-aquario", h("19:30"), h("20:30"), MSG_FORA_DA_JANELA),
        ("sala-aquario", h("09:00"), h("12:00"), MSG_DURACAO),
    ],
)
def test_erros_de_validacao(agenda, sala, inicio, fim, mensagem):
    with pytest.raises(ErroDeNegocio) as erro:
        validar_pedido(agenda.salas, sala, inicio, fim)
    assert str(erro.value) == mensagem


@pytest.mark.parametrize(
    ("inicio", "fim"),
    [(h("08:00"), h("10:00")), (h("18:00"), h("20:00")), ("2026-11-03T12:00:00Z", "2026-11-03T13:00:00Z")],
)
def test_bordas_validas(agenda, inicio, fim):
    validar_pedido(agenda.salas, "sala-aquario", inicio, fim)


def test_alternativas_da_garagem_ocupada(agenda):
    pedido = validar_pedido(agenda.salas, "sala-garagem", h("14:00"), h("15:00"))
    assert not agenda.livre("sala-garagem", pedido.inicio, pedido.fim)
    assert agenda.alternativas(pedido) == ["sala-fusca", "sala-mirante"]


def test_alternativas_limitadas_a_tres_e_ordenadas(agenda):
    pedido = validar_pedido(agenda.salas, "sala-aquario", h("09:00"), h("10:00"))
    assert agenda.alternativas(pedido) == ["sala-porao", "sala-fusca", "sala-garagem"]


def test_encostar_nao_conflita(agenda):
    pedido = validar_pedido(agenda.salas, "sala-garagem", h("15:00"), h("16:00"))
    assert agenda.livre("sala-garagem", pedido.inicio, pedido.fim)


async def test_ids_sequenciais(agenda):
    r = await agenda.reservar("sala-aquario", h("09:00"), h("10:00"), "Doc")
    assert r.id == "res-0003"
    with pytest.raises(ErroDeNegocio):
        await agenda.reservar("sala-aquario", h("09:30"), h("10:30"), "Biff")


@pytest.mark.parametrize(
    ("segredo", "ttl"),
    [("", "600"), ("zz", "600"), ("ab" * 31, "600"), ("ab" * 32, "299"), ("ab" * 32, "1801"), ("ab" * 32, "x")],
)
def test_configuracao_invalida(segredo, ttl):
    with pytest.raises(config.ErroDeConfiguracao):
        config.carregar({"REQUEST_STATE_SECRET": segredo, "REQUEST_STATE_TTL_SECONDS": ttl})


def test_configuracao_padrao():
    cfg = config.carregar({"REQUEST_STATE_SECRET": "ab" * 32})
    assert (cfg.porta, cfg.caminho, cfg.ttl_segundos, len(cfg.segredo)) == (7301, "/mcp", 600, 32)
