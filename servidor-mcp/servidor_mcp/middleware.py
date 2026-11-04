"""Log de cada request MCP no stderr: metodo, id, Mcp-Name, traceparent e status.

So observa. Validacao de _meta (-32602), de headers espelhados (-32020) e o
status HTTP dos erros sao do SDK. O logging MCP depreciado
(notifications/message) nao e usado.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


_SEGURO = re.compile(r"[\x21-\x7e]+")


def _seguro(valor: str) -> str:
    """Valores vindos do cliente vao para o log sem permitir forjar linhas ou
    campos: o que nao for ASCII visivel sem espaco (quebra de linha, espaco,
    controle, nao-ASCII) e gravado como string JSON escapada e entre aspas.
    Valores normais (metodo, traceparent W3C) ficam inalterados."""
    return valor if _SEGURO.fullmatch(valor) else json.dumps(valor)


def _campos(corpo: bytes) -> tuple[str, str, str]:
    try:
        msg: Any = json.loads(corpo)
    except (ValueError, UnicodeDecodeError):
        return "<json-invalido>", "-", "-"
    if not isinstance(msg, dict):
        return "<nao-objeto>", "-", "-"
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    metodo = str(msg.get("method", "-"))
    ident = json.dumps(msg["id"]) if "id" in msg else "-"
    traceparent = str(meta.get("traceparent", "-"))
    return _seguro(metodo), _seguro(ident), _seguro(traceparent)


class MetaLogMiddleware:
    def __init__(self, app: ASGIApp, caminho: str) -> None:
        self.app = app
        self.caminho = caminho

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != self.caminho:
            await self.app(scope, receive, send)  # inclui o lifespan
            return

        partes: list[bytes] = []
        mais = True
        while mais:
            evento = await receive()
            if evento["type"] == "http.disconnect":
                return
            partes.append(evento.get("body", b""))
            mais = evento.get("more_body", False)
        corpo = b"".join(partes)
        entregue = False

        async def receive_replay() -> Message:
            nonlocal entregue
            if not entregue:
                entregue = True
                return {"type": "http.request", "body": corpo, "more_body": False}
            return await receive()

        status = 0

        async def send_espiao(mensagem: Message) -> None:
            nonlocal status
            if mensagem["type"] == "http.response.start":
                status = mensagem["status"]
            await send(mensagem)

        metodo, ident, traceparent = _campos(corpo)
        nome = "-"
        for chave, valor in scope.get("headers", []):
            if chave == b"mcp-name":
                nome = _seguro(valor.decode("latin-1"))
        try:
            await self.app(scope, receive_replay, send_espiao)
        finally:
            print(
                f"{_agora()} mcp method={metodo} id={ident} name={nome} traceparent={traceparent} status={status}",
                file=sys.stderr,
                flush=True,
            )
