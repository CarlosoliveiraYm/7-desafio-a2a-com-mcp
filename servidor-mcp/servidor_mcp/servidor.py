"""MCPServer central-de-salas: tres tools, um resource e o MRTR da reserva.

O MRTR nao e callback. Quando a sala esta ocupada, o resolver
`escolha_de_sala` devolve um `Elicit`; na revisao 2026-07-28 o SDK transforma
isso em `resultType: input_required` com um `requestState` selado
(AES-GCM, chave de REQUEST_STATE_SECRET) e termina a resposta. O cliente volta
com um tools/call novo trazendo `inputResponses` e o `requestState`; o SDK
verifica o selo (expiracao, audience, argumentos) e reinjeta a resposta.
O servidor nao guarda nada entre as duas idas.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, create_model

from mcp.server.mcpserver import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    Elicit,
    ElicitationResult,
    MCPServer,
    RequestStateSecurity,
    Resolve,
)

from servidor_mcp.config import Config
from servidor_mcp.dados import DadosIniciais
from servidor_mcp.dominio import MSG_SEM_ALTERNATIVAS, ErroDeNegocio, validar_pedido
from servidor_mcp.modelos import (
    ConflitoOut,
    Disponibilidade,
    EscolhaSala,
    ListaDeSalas,
    ReservaOut,
    SalaOut,
)
from servidor_mcp.repositorio import Agenda

NOME_SERVIDOR = "central-de-salas"
VERSAO_SERVIDOR = "1.0.0"
URI_POLITICA = "politica://uso"
MENSAGEM_ELICITATION = "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa."

# Estado do processo, preenchido por criar_servidor(). O resolver e a tool
# ficam no nivel do modulo porque a chave do inputRequests e o nome
# qualificado do resolver (`servidor_mcp.servidor:escolha_de_sala`).
_agenda: Agenda | None = None
_dados: DadosIniciais | None = None


def _ctx() -> tuple[Agenda, DadosIniciais]:
    if _agenda is None or _dados is None:
        raise RuntimeError("servidor nao inicializado: chame criar_servidor()")
    return _agenda, _dados


def modelo_escolha(alternativas: list[str]) -> type[EscolhaSala]:
    """Modelo plano com `sala` restrita as alternativas, na ordem da regra.

    Um Literal de strings vira `enum` no requestedSchema; com uma unica
    alternativa o Pydantic emite `const`, forma tambem aceita pelo contrato.
    """
    tipo = Literal.__getitem__(tuple(alternativas))
    return create_model(
        "EscolhaSala",
        __base__=EscolhaSala,
        sala=(tipo, Field(title="Sala", description="Sala alternativa escolhida")),
    )


async def escolha_de_sala(sala: str, inicio: str, fim: str) -> EscolhaSala | Elicit[EscolhaSala]:
    """Resolver da sala efetiva da reserva.

    Valida o pedido antes de qualquer pergunta (erros de dominio viram isError
    mesmo para clientes sem a capability). Sala livre: devolve a propria sala,
    sem ida extra. Ocupada: pergunta qual alternativa usar, ou falha se nao
    houver nenhuma.
    """
    agenda, _ = _ctx()
    pedido = validar_pedido(agenda.salas, sala, inicio, fim)
    if agenda.livre(pedido.sala.id, pedido.inicio, pedido.fim):
        return EscolhaSala(sala=pedido.sala.id)
    alternativas = agenda.alternativas(pedido)
    if not alternativas:
        raise ErroDeNegocio(MSG_SEM_ALTERNATIVAS)
    return Elicit(MENSAGEM_ELICITATION, modelo_escolha(alternativas))


def listar_salas() -> ListaDeSalas:
    """Lista todas as salas com capacidade e recursos."""
    agenda, _ = _ctx()
    return ListaDeSalas(
        salas=[
            SalaOut(id=s.id, nome=s.nome, capacidade=s.capacidade, recursos=list(s.recursos))
            for s in agenda.salas.values()
        ]
    )


def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
    """Diz se uma sala esta livre no intervalo, e quais reservas conflitam."""
    agenda, _ = _ctx()
    pedido = validar_pedido(agenda.salas, sala, inicio, fim)
    conflitos = agenda.conflitos(pedido.sala.id, pedido.inicio, pedido.fim)
    return Disponibilidade(
        sala=pedido.sala.id,
        livre=not conflitos,
        conflitos=[
            ConflitoOut(id=r.id, inicio=r.inicio_texto, fim=r.fim_texto, responsavel=r.responsavel)
            for r in conflitos
        ],
    )


async def reservar_sala(
    sala: str,
    inicio: str,
    fim: str,
    responsavel: str,
    escolha: Annotated[ElicitationResult[EscolhaSala], Resolve(escolha_de_sala)],
) -> ReservaOut:
    """Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar."""
    agenda, dados = _ctx()
    match escolha:
        case AcceptedElicitation(data=EscolhaSala(sala=sala_efetiva)):
            reserva = await agenda.reservar(sala_efetiva, inicio, fim, responsavel)
            return ReservaOut(
                reserva=reserva.id,
                reservado=True,
                sala=reserva.sala,
                inicio=reserva.inicio_texto,
                fim=reserva.fim_texto,
                responsavel=reserva.responsavel,
                politica=dados.politica_versao,
            )
        case DeclinedElicitation() | CancelledElicitation():
            # Recusa nao e erro: conclui sem reservar.
            return ReservaOut(reserva=None, reservado=False, motivo="recusado")
        case _:
            raise ErroDeNegocio("Resposta de escolha nao reconhecida")


def criar_servidor(config: Config, dados: DadosIniciais) -> MCPServer:
    global _agenda, _dados
    _dados = dados
    _agenda = Agenda(dados)

    mcp = MCPServer(
        NOME_SERVIDOR,
        version=VERSAO_SERVIDOR,
        request_state_security=RequestStateSecurity(
            keys=[config.segredo],
            ttl=float(config.ttl_segundos),
            # Audience fixa: o token continua valido depois de um restart.
            audience=NOME_SERVIDOR,
        ),
        # Subscriptions estao fora de escopo; por padrao o SDK serve
        # subscriptions/listen e anuncia listChanged/subscribe.
        subscriptions=False,
    )
    mcp.tool(description="Lista todas as salas com capacidade e recursos.")(listar_salas)
    mcp.tool(description="Diz se uma sala esta livre no intervalo, e quais reservas conflitam.")(
        consultar_disponibilidade
    )
    mcp.tool(description="Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.")(
        reservar_sala
    )

    @mcp.resource(
        URI_POLITICA,
        name="politica-de-uso",
        description="Politica de uso das salas; a primeira linha declara a versao.",
        mime_type="text/markdown",
    )
    def politica_de_uso() -> str:
        return dados.politica_texto

    return mcp
