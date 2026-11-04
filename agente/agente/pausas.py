"""Estado pausado por Task: o que o agente precisa lembrar entre o
input_required do MCP e a continuacao A2A.

Fica FORA do objeto Task (nem history, nem metadata, nem artifact): o
requestState nunca pode aparecer numa resposta A2A.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PausaMcp:
    tool: str
    argumentos: dict[str, Any]  # reenviados identicos no retry
    chave: str  # chave unica de inputRequests, devolvida igual em inputResponses
    request_state: str = field(repr=False)  # opaco: ecoado byte a byte, nunca aberto nem logado
    opcoes: tuple[str, ...]  # enum da elicitation, na ordem recebida
    trace_id: str | None
    versao_politica: str


class PausasRepository:
    def __init__(self) -> None:
        self._pausas: dict[str, PausaMcp] = {}

    async def guardar(self, task_id: str, pausa: PausaMcp) -> None:
        self._pausas[task_id] = pausa

    async def obter(self, task_id: str) -> PausaMcp | None:
        return self._pausas.get(task_id)

    def instantaneo(self) -> dict[str, PausaMcp]:
        """Copia das pausas atuais (uso em testes)."""
        return dict(self._pausas)

    async def remover(self, task_id: str) -> None:
        self._pausas.pop(task_id, None)
