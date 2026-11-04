"""Testes de protocolo do servidor MCP, com JSON-RPC cru como o validador.

Cobrem o que o validador do starter nao verifica: capabilities, -32020,
consultar_disponibilidade no caminho feliz, structuredContent conforme o
outputSchema em todas as tools, mimeType do resource, requestState expirado,
retry depois de restart, action=cancel, escolha fora do enum e o log no stderr.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from servidor_mcp import dados as carga
from servidor_mcp.config import Config
from servidor_mcp.middleware import MetaLogMiddleware
from servidor_mcp.servidor import criar_servidor

DADOS_DIR = Path(__file__).resolve().parents[2] / "dados"
SEGREDO = bytes.fromhex("ab" * 32)
OUTRO_SEGREDO = bytes.fromhex("cd" * 32)
PROTOCOLO = "2026-07-28"
CAP_FORM = {"elicitation": {"form": {}}}
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
DIA = "2026-11-03"


def h(hora: str) -> str:
    return f"{DIA}T{hora}:00-03:00"


@contextmanager
def servidor(segredo: bytes = SEGREDO, ttl: float = 600) -> Iterator[TestClient]:
    """Um processo novo do servidor (reservas recarregadas de dados/)."""
    cfg = Config(segredo=segredo, ttl_segundos=ttl, host="127.0.0.1", porta=7301, caminho="/mcp", dados_dir=DADOS_DIR)
    mcp = criar_servidor(cfg, carga.carregar(DADOS_DIR))
    app = MetaLogMiddleware(mcp.streamable_http_app(stateless_http=True, json_response=True), "/mcp")
    with TestClient(app, base_url="http://127.0.0.1:7301") as cliente:
        yield cliente


_proximo_id = 0


def rpc(
    c: TestClient,
    metodo: str,
    params: dict[str, Any] | None = None,
    nome: str | None = None,
    caps: dict[str, Any] | None = None,
    traceparent: str | None = None,
    cabecalhos_extras: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    global _proximo_id
    _proximo_id += 1
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOLO,
        "io.modelcontextprotocol/clientCapabilities": CAP_FORM if caps is None else caps,
    }
    if traceparent:
        meta["traceparent"] = traceparent
    cabecalhos = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOLO,
        "Mcp-Method": metodo,
    }
    if nome:
        cabecalhos["Mcp-Name"] = nome
    cabecalhos.update(cabecalhos_extras or {})
    corpo = {"jsonrpc": "2.0", "id": _proximo_id, "method": metodo, "params": {**(params or {}), "_meta": meta}}
    r = c.post("/mcp", json=corpo, headers=cabecalhos)
    return r.status_code, r.json()


def tool(c: TestClient, nome: str, argumentos: dict[str, Any], **kw: Any) -> tuple[int, dict[str, Any]]:
    return rpc(c, "tools/call", {"name": nome, "arguments": argumentos, **kw.pop("extra", {})}, nome=nome, **kw)


def reservar(c: TestClient, sala: str, inicio: str, fim: str, responsavel: str = "Teste", **kw: Any):
    return tool(c, "reservar_sala", {"sala": sala, "inicio": inicio, "fim": fim, "responsavel": responsavel}, **kw)


def retry(c: TestClient, argumentos: dict[str, str], chave: str, resposta: dict[str, Any], estado: str):
    return tool(
        c,
        "reservar_sala",
        argumentos,
        extra={"inputResponses": {chave: resposta}, "requestState": estado},
    )


def conflito_garagem(c: TestClient) -> tuple[dict[str, str], str, str, list[str]]:
    argumentos = {"sala": "sala-garagem", "inicio": h("14:00"), "fim": h("15:00"), "responsavel": "Marty"}
    _, r = tool(c, "reservar_sala", argumentos)
    res = r["result"]
    assert res["resultType"] == "input_required"
    chave = next(iter(res["inputRequests"]))
    enum = res["inputRequests"][chave]["params"]["requestedSchema"]["properties"]["sala"]["enum"]
    return argumentos, chave, res["requestState"], enum


def texto(resultado: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in resultado.get("content", []))


def conforme(valor: Any, schema: dict[str, Any], defs: dict[str, Any]) -> bool:
    """Validador minimo de JSON Schema para os outputSchema gerados (type,
    properties, required, items, $ref, anyOf)."""
    if "$ref" in schema:
        return conforme(valor, defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return any(conforme(valor, s, defs) for s in schema["anyOf"])
    tipos = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    tipo = schema.get("type")
    if tipo and not isinstance(valor, tipos[tipo]):
        return False
    if tipo == "object":
        if any(k not in valor for k in schema.get("required", [])):
            return False
        props = schema.get("properties", {})
        return all(conforme(valor[k], props[k], defs) for k in valor if k in props)
    if tipo == "array":
        return all(conforme(v, schema.get("items", {}), defs) for v in valor)
    return True


# --- 1, 2: capabilities e headers ---------------------------------------------------


def test_capabilities_de_tools_e_resources():
    with servidor() as c:
        status, r = rpc(c, "server/discover")
    assert status == 200
    caps = r["result"]["capabilities"]
    assert "tools" in caps and "resources" in caps


@pytest.mark.parametrize(
    ("metodo", "nome", "extras"),
    [
        ("tools/list", None, {"Mcp-Method": "tools/call"}),
        ("tools/call", "listar_salas", {"Mcp-Name": "outra_tool"}),
        ("tools/list", None, {"MCP-Protocol-Version": "2026-07-29"}),
    ],
)
def test_header_divergente_do_corpo_e_recusado_com_32020(metodo, nome, extras):
    params = {"name": "listar_salas", "arguments": {}} if metodo == "tools/call" else {}
    with servidor() as c:
        status, r = rpc(c, metodo, params, nome=nome, cabecalhos_extras=extras)
    assert status == 400 and r["error"]["code"] == -32020


# --- 3: consultar_disponibilidade -----------------------------------------------------


def test_consultar_disponibilidade_livre_e_ocupada():
    with servidor() as c:
        _, livre = tool(c, "consultar_disponibilidade", {"sala": "sala-aquario", "inicio": h("09:00"), "fim": h("10:00")})
        _, ocupada = tool(
            c, "consultar_disponibilidade", {"sala": "sala-garagem", "inicio": h("14:30"), "fim": h("15:30")}
        )
    assert livre["result"]["structuredContent"] == {"sala": "sala-aquario", "livre": True, "conflitos": []}
    sc = ocupada["result"]["structuredContent"]
    assert sc["livre"] is False
    assert sc["conflitos"] == [
        {"id": "res-0001", "inicio": h("14:00"), "fim": h("15:00"), "responsavel": "Marty"}
    ]


def test_consultar_disponibilidade_com_sala_inexistente():
    with servidor() as c:
        _, r = tool(c, "consultar_disponibilidade", {"sala": "sala-delorean", "inicio": h("09:00"), "fim": h("10:00")})
    assert r["result"]["isError"] is True
    assert "Sala inexistente: sala-delorean" in texto(r["result"])


def test_consultar_nao_reserva():
    with servidor() as c:
        tool(c, "consultar_disponibilidade", {"sala": "sala-aquario", "inicio": h("09:00"), "fim": h("10:00")})
        _, r = reservar(c, "sala-aquario", h("09:00"), h("10:00"))
    assert r["result"]["structuredContent"]["reservado"] is True


# --- 4, 5: structuredContent + texto conforme outputSchema em todas as tools -----------


def test_todas_as_tools_devolvem_structured_conforme_output_schema_e_texto():
    with servidor() as c:
        _, lista = rpc(c, "tools/list")
        schemas = {t["name"]: t for t in lista["result"]["tools"]}
        chamadas = {
            "listar_salas": {},
            "consultar_disponibilidade": {"sala": "sala-aquario", "inicio": h("09:00"), "fim": h("10:00")},
            "reservar_sala": {"sala": "sala-aquario", "inicio": h("09:00"), "fim": h("10:00"), "responsavel": "Doc"},
        }
        for nome, argumentos in chamadas.items():
            ferramenta = schemas[nome]
            assert ferramenta["inputSchema"]["type"] == "object"
            props = ferramenta["inputSchema"].get("properties", {})
            assert set(props) == set(argumentos), f"{nome}: inputSchema expoe {sorted(props)}"
            _, r = tool(c, nome, argumentos)
            res = r["result"]
            sc = res["structuredContent"]
            assert res["resultType"] == "complete" and not res.get("isError")
            assert json.loads(texto(res)) == sc, nome
            out = ferramenta["outputSchema"]
            assert conforme(sc, out, out.get("$defs", {})), nome


def test_structured_da_reserva_concluida():
    with servidor() as c:
        _, r = reservar(c, "sala-aquario", h("09:00"), h("10:00"), "Doc")
    assert r["result"]["structuredContent"] == {
        "reserva": "res-0003",
        "reservado": True,
        "sala": "sala-aquario",
        "inicio": h("09:00"),
        "fim": h("10:00"),
        "responsavel": "Doc",
        "politica": "2026-11-01",
        "motivo": None,
    }


# --- 6: resource ------------------------------------------------------------------------


def test_resource_politica_com_mime_markdown_e_conteudo_integral():
    with servidor() as c:
        _, r = rpc(c, "resources/read", {"uri": "politica://uso"}, nome="politica://uso")
    conteudo = r["result"]["contents"][0]
    assert conteudo["mimeType"] == "text/markdown"
    assert conteudo["uri"] == "politica://uso"
    assert conteudo["text"] == (DADOS_DIR / "politica-de-uso.md").read_text(encoding="utf-8")


# --- 7, 8: expiracao e restart --------------------------------------------------------


def test_request_state_expirado_e_rejeitado():
    with servidor(ttl=1) as c:
        argumentos, chave, estado, _ = conflito_garagem(c)
        time.sleep(1.5)
        status, r = retry(c, argumentos, chave, {"action": "accept", "content": {"sala": "sala-fusca"}}, estado)
    assert r["error"]["code"] == -32602
    assert "result" not in r


def test_retry_valido_depois_de_restart_com_o_mesmo_segredo():
    with servidor() as c:
        argumentos, chave, estado, _ = conflito_garagem(c)
    with servidor() as novo_processo:  # nada em memoria sobrevive: so o requestState
        _, r = retry(novo_processo, argumentos, chave, {"action": "accept", "content": {"sala": "sala-fusca"}}, estado)
    sc = r["result"]["structuredContent"]
    assert r["result"]["resultType"] == "complete"
    assert (sc["reservado"], sc["sala"], sc["responsavel"]) == (True, "sala-fusca", "Marty")


def test_retry_depois_de_restart_com_outro_segredo_e_rejeitado():
    with servidor() as c:
        argumentos, chave, estado, _ = conflito_garagem(c)
    with servidor(segredo=OUTRO_SEGREDO) as novo_processo:
        _, r = retry(novo_processo, argumentos, chave, {"action": "accept", "content": {"sala": "sala-fusca"}}, estado)
    assert r["error"]["code"] == -32602


def test_retry_com_id_novo_e_o_mesmo_estado_conclui():
    with servidor() as c:
        argumentos, chave, estado, enum = conflito_garagem(c)
        assert enum == ["sala-fusca", "sala-mirante"]
        _, r = retry(c, argumentos, chave, {"action": "accept", "content": {"sala": "sala-mirante"}}, estado)
    assert r["result"]["structuredContent"]["sala"] == "sala-mirante"


# --- 9, 10: cancel e escolha fora do enum ---------------------------------------------


@pytest.mark.parametrize("acao", ["decline", "cancel"])
def test_recusa_ou_cancelamento_conclui_sem_reservar(acao):
    with servidor() as c:
        argumentos, chave, estado, _ = conflito_garagem(c)
        _, r = retry(c, argumentos, chave, {"action": acao}, estado)
        _, depois = tool(c, "consultar_disponibilidade", {"sala": "sala-fusca", "inicio": h("14:00"), "fim": h("15:00")})
    res = r["result"]
    assert res["resultType"] == "complete" and not res.get("isError")
    assert res["structuredContent"]["reservado"] is False and res["structuredContent"]["motivo"]
    assert depois["result"]["structuredContent"]["livre"] is True


def test_escolha_fora_do_enum_no_retry_nao_reserva():
    with servidor() as c:
        argumentos, chave, estado, _ = conflito_garagem(c)
        _, r = retry(c, argumentos, chave, {"action": "accept", "content": {"sala": "sala-aquario"}}, estado)
        _, depois = tool(c, "consultar_disponibilidade", {"sala": "sala-aquario", "inicio": h("14:00"), "fim": h("15:00")})
    assert r.get("error") or r["result"].get("isError") is True
    assert depois["result"]["structuredContent"]["livre"] is True


def test_uma_unica_alternativa_vira_const_ou_enum_de_um():
    with servidor() as c:
        reservar(c, "sala-fusca", h("14:00"), h("15:00"))  # sobra so a mirante para a garagem
        _, r = reservar(c, "sala-garagem", h("14:00"), h("15:00"))
    res = r["result"]
    campo = next(iter(res["inputRequests"].values()))["params"]["requestedSchema"]["properties"]["sala"]
    assert campo.get("enum", [campo.get("const")]) == ["sala-mirante"]


def test_sala_livre_nao_exige_capability_de_elicitation():
    with servidor() as c:
        _, r = reservar(c, "sala-aquario", h("09:00"), h("10:00"), caps={})
    assert r["result"]["structuredContent"]["reservado"] is True


# --- 11: log no stderr --------------------------------------------------------------------


def test_log_no_stderr_com_metodo_id_e_traceparent(capsys):
    with servidor() as c:
        capsys.readouterr()
        status, _ = rpc(c, "tools/list", traceparent=TRACEPARENT)
        rpc(c, "tools/list", caps=None, cabecalhos_extras={"Mcp-Method": "tools/call"})  # recusado tambem e logado
    linhas = [linha for linha in capsys.readouterr().err.splitlines() if " mcp method=" in linha]
    assert f"method=tools/list id={_proximo_id - 1} name=- traceparent={TRACEPARENT} status=200" in linhas[0]
    assert f"id={_proximo_id} " in linhas[1] and "status=400" in linhas[1]


# --- integridade do log exigido (metodo, id, traceparent) -------------------------------


def test_valores_do_cliente_nao_forjam_linhas_no_log(capsys):
    forjado = "tools/list\n2026-11-04T00:00:00Z mcp method=tools/list id=999 traceparent=" + TRACEPARENT
    with servidor() as c:
        capsys.readouterr()
        c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "a b\nc", "method": forjado, "params": {"_meta": {"traceparent": "x\nfalsa"}}},
            headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOLO, "Mcp-Method": "tools/list"},
        )
    linhas = [linha for linha in capsys.readouterr().err.splitlines() if "mcp method=" in linha]
    assert len(linhas) == 1  # uma request, uma linha: nada forjado
    linha = linhas[0]
    assert "method=" + json.dumps(forjado) in linha
    assert 'traceparent="x\\nfalsa"' in linha


def test_valores_normais_ficam_legiveis_no_log(capsys):
    with servidor() as c:
        capsys.readouterr()
        rpc(c, "tools/call", {"name": "listar_salas", "arguments": {}}, nome="listar_salas", traceparent=TRACEPARENT)
    [linha] = [x for x in capsys.readouterr().err.splitlines() if "mcp method=" in x]
    assert f"method=tools/call id={_proximo_id} name=listar_salas traceparent={TRACEPARENT} status=200" in linha


# --- itens fora de escopo nao ficam ativos -----------------------------------------------


def test_subscriptions_fora_de_escopo_nao_sao_servidas_nem_anunciadas():
    with servidor() as c:
        _, descoberta = rpc(c, "server/discover")
        status, listen = rpc(c, "subscriptions/listen", {"notifications": {"toolsListChanged": True}})
    caps = descoberta["result"]["capabilities"]
    assert not caps["resources"].get("subscribe") and not caps["resources"].get("listChanged")
    assert not caps["tools"].get("listChanged")
    assert listen["error"]["code"] == -32601 and status == 404  # metodo nao servido


def test_nenhum_prompt_registrado():
    with servidor() as c:
        _, r = rpc(c, "prompts/list")
    assert r["result"]["prompts"] == []
