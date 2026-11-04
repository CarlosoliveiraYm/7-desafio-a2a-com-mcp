"""W3C Trace Context: o trace-id do cliente A2A atravessa ate o servidor MCP;
cada request MCP ganha um span-id novo."""

from __future__ import annotations

import re
import secrets

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_TRACE_ID_NULO = "0" * 32


def trace_id_de(traceparent: str | None) -> str | None:
    """Extrai o trace-id de um header traceparent valido; None caso contrario."""
    if not traceparent:
        return None
    m = _TRACEPARENT.match(traceparent.strip().lower())
    if not m or m.group(1) == _TRACE_ID_NULO:
        return None
    return m.group(1)


def novo_traceparent(trace_id: str | None) -> str | None:
    if trace_id is None:
        return None
    return f"00-{trace_id}-{secrets.token_hex(8)}-01"
