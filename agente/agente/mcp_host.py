"""O agente como host MCP: um cliente MCP de verdade, falando HTTP com o servidor.

Duas escolhas que sustentam a ponte:

1. A capability declarada e exatamente {"elicitation": {"form": {}}}. No SDK,
   registrar um `elicitation_callback` E a declaracao da capability, e o SDK
   2.3.0 declara {"form": {}, "url": {}}. `SessaoCentralDeSalas` restringe a
   form mode. O callback e uma sentinela que nunca responde.
2. As chamadas usam `ClientSession.call_tool(..., allow_input_required=True)`,
   que devolve o `InputRequiredResult` cru. Quem roda o loop MRTR sozinho e
   `Client.call_tool`, que nao e usado: a pergunta precisa voltar para a Task
   A2A, nao ser respondida aqui.

O `requestState` e opaco: recebido, devolvido intacto ao chamador e reenviado
byte a byte. Nunca e aberto, interpretado ou registrado em log.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import mcp.types as types
from mcp.client.client import Client
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError

PROTOCOLO = "2026-07-28"
URI_POLITICA = "politica://uso"
CLIENT_INFO = types.Implementation(name="agente-central-de-salas", version="1.0.0")

log = logging.getLogger("agente.mcp")


class SessaoCentralDeSalas(ClientSession):
    """Declara elicitation somente em form mode, na forma exigida pelo enunciado."""

    def _build_capabilities(self, version: str) -> types.ClientCapabilities:
        capacidades = super()._build_capabilities(version)
        if capacidades.elicitation is None:
            return capacidades
        return capacidades.model_copy(
            update={"elicitation": types.ElicitationCapability(form=types.FormElicitationCapability())}
        )


class ClienteCentralDeSalas(Client):
    """`Client` do SDK com a sessao acima. Os argumentos da sessao continuam
    sendo montados pelo SDK; so a classe muda (ela nao adiciona estado)."""

    async def _build_session(self, exit_stack: AsyncExitStack) -> ClientSession:
        sessao = await super()._build_session(exit_stack)
        sessao.__class__ = SessaoCentralDeSalas
        return sessao


async def _sentinela_de_elicitation(contexto: Any, params: Any) -> Any:
    raise RuntimeError("elicitation deve passar pela Task A2A, nunca ser respondida pelo cliente MCP")


# --- resultados de protocolo devolvidos ao chamador ---------------------------------


@dataclass(frozen=True)
class Completo:
    """`resultType: complete`."""

    estruturado: dict[str, Any] | None
    texto: str
    is_error: bool


@dataclass(frozen=True)
class EntradaNecessaria:
    """`resultType: input_required` com uma unica elicitation em form mode."""

    chave: str
    opcoes: tuple[str, ...]  # enum (ou [const]) do requestedSchema, na ordem recebida
    request_state: str


class ErroDeProtocolo(Exception):
    """Erro JSON-RPC devolvido pelo servidor MCP."""

    def __init__(self, codigo: int, mensagem: str) -> None:
        super().__init__(f"{codigo}: {mensagem}")
        self.codigo = codigo
        self.mensagem = mensagem


class ServidorIndisponivel(Exception):
    """Falha de transporte ao falar com o servidor MCP."""


class RespostaNaoSuportada(Exception):
    """input_required fora do contrato (mais de uma entrada, sem enum/const...)."""


def _meta(traceparent: str | None) -> dict[str, Any] | None:
    # As chaves io.modelcontextprotocol/* (versao, capabilities, clientInfo) sao
    # carimbadas pelo SDK em todo request; aqui so entra o trace context.
    return {"traceparent": traceparent} if traceparent else None


def _opcoes_da_elicitation(pedido: Any) -> tuple[str, ...]:
    if getattr(pedido, "method", None) != "elicitation/create":
        raise RespostaNaoSuportada(f"input request de tipo {getattr(pedido, 'method', None)!r}")
    params = pedido.params
    if getattr(params, "mode", "form") != "form":
        raise RespostaNaoSuportada("elicitation fora de form mode")
    schema = params.requested_schema
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump(by_alias=True, exclude_none=True)
    campo = ((schema or {}).get("properties") or {}).get("sala") or {}
    if "enum" in campo:
        opcoes = tuple(str(v) for v in campo["enum"])
    elif "const" in campo:
        opcoes = (str(campo["const"]),)
    else:
        raise RespostaNaoSuportada("requestedSchema sem enum/const em 'sala'")
    if not opcoes:
        raise RespostaNaoSuportada("requestedSchema com enum vazio")
    return opcoes


def _texto(conteudo: list[Any]) -> str:
    return " ".join(getattr(bloco, "text", "") for bloco in conteudo if getattr(bloco, "type", None) == "text")


class McpHost:
    def __init__(self, url: str, timeout: float) -> None:
        self._url = url
        self._timeout = timeout
        self._cliente: ClienteCentralDeSalas | None = None

    async def __aenter__(self) -> McpHost:
        self._cliente = ClienteCentralDeSalas(
            self._url,
            mode=PROTOCOLO,  # adota 2026-07-28 sem handshake: o primeiro request visto e o tools/list
            client_info=CLIENT_INFO,
            elicitation_callback=_sentinela_de_elicitation,
            read_timeout_seconds=self._timeout,
            cache=None,  # descoberta em runtime: tools/list sempre vai ao servidor
        )
        await self._cliente.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._cliente is not None:
            await self._cliente.__aexit__(*exc)

    @property
    def _sessao(self) -> ClientSession:
        if self._cliente is None:
            raise RuntimeError("McpHost nao iniciado")
        return self._cliente.session

    async def _protegido(self, metodo: str, chamada: Any) -> Any:
        try:
            return await chamada
        except MCPError as e:
            log.info("mcp %s recusado: %s %s", metodo, e.error.code, e.error.message)
            raise ErroDeProtocolo(e.error.code, e.error.message) from e
        except (ErroDeProtocolo, RespostaNaoSuportada):
            raise
        except Exception as e:  # transporte: conexao recusada, timeout, HTTP inesperado
            log.warning("mcp %s falhou: %r", metodo, e)
            raise ServidorIndisponivel(str(e)) from e

    async def listar_tools(self, traceparent: str | None) -> list[str]:
        params = types.PaginatedRequestParams.model_validate({"_meta": _meta(traceparent)} if traceparent else {})
        resultado = await self._protegido("tools/list", self._sessao.list_tools(params=params))
        return [t.name for t in resultado.tools]

    async def ler_versao_politica(self, traceparent: str | None) -> str:
        resultado = await self._protegido(
            "resources/read", self._sessao.read_resource(URI_POLITICA, meta=_meta(traceparent))
        )
        texto = next((getattr(c, "text", None) for c in resultado.contents if getattr(c, "text", None)), "")
        primeira = texto.splitlines()[0] if texto else ""
        chave, _, valor = primeira.partition(":")
        if chave.strip() != "versao" or not valor.strip():
            raise RespostaNaoSuportada(f"politica sem versao na primeira linha: {primeira!r}")
        return valor.strip()

    async def chamar_tool(
        self,
        nome: str,
        argumentos: dict[str, Any],
        traceparent: str | None,
        resposta: tuple[str, types.ElicitResult] | None = None,
        request_state: str | None = None,
    ) -> Completo | EntradaNecessaria:
        """Uma ida de tools/call. No retry, `resposta` (chave, ElicitResult) e
        `request_state` acompanham os MESMOS argumentos; o id JSON-RPC e novo
        porque o despachante do SDK aloca um id por request."""
        resultado = await self._protegido(
            "tools/call",
            self._sessao.call_tool(
                nome,
                argumentos,
                input_responses={resposta[0]: resposta[1]} if resposta else None,
                request_state=request_state,
                meta=_meta(traceparent),
                allow_input_required=True,
            ),
        )
        if isinstance(resultado, types.InputRequiredResult):
            pedidos = resultado.input_requests or {}
            if len(pedidos) != 1 or not resultado.request_state:
                raise RespostaNaoSuportada(f"input_required com {len(pedidos)} entradas")
            chave, pedido = next(iter(pedidos.items()))
            return EntradaNecessaria(
                chave=chave, opcoes=_opcoes_da_elicitation(pedido), request_state=resultado.request_state
            )
        estruturado = resultado.structured_content if isinstance(resultado.structured_content, dict) else None
        return Completo(estruturado=estruturado, texto=_texto(resultado.content), is_error=bool(resultado.is_error))
