"""Versao A2A por request.

O a2a-sdk 1.3.0 le a versao do header `A2A-Version` e, quando ele falta,
assume 0.3 (`version_validator.validate_version`), recusando os metodos v1.0
com -32009. O contrato deste projeto (validador e exemplos/wire/08..10) envia
`SendMessage`/`GetTask` em v1.0 SEM esse header.

Este endpoint so serve v1.0 (o card declara apenas protocolVersion 1.0 e a
compatibilidade v0.3 esta desligada), entao a ausencia do header e lida como
1.0, pelo ponto de extensao oficial do SDK (`ServerCallContextBuilder`). Um
header explicito com outra versao continua sendo recusado pelo SDK.
"""

from __future__ import annotations

from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.utils import constants
from starlette.requests import Request

VERSAO_PADRAO = "1.0"


class ContextoV1(DefaultServerCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        contexto = super().build(request)
        cabecalhos = contexto.state.setdefault("headers", {})
        if not (cabecalhos.get(constants.VERSION_HEADER) or cabecalhos.get(constants.VERSION_HEADER.lower())):
            cabecalhos[constants.VERSION_HEADER.lower()] = VERSAO_PADRAO
        return contexto
