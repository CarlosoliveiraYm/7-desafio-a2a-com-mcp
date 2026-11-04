"""Ponta a ponta: servidor MCP real (subprocesso) + agente (em processo).

Entre os dois fica um proxy espiao que registra cada request MCP do agente
(headers e corpo), para conferir _meta, capability, headers espelhados, id e
requestState de TODOS os requests, nao so de um.

Cobre o que o validador nao consegue ver: o requestState real do agente nunca
aparece em resposta A2A (a V34 procura um token que nunca passou pelo agente),
trace-id em todos os requests da Task no stderr do MCP, ids distintos no retry,
GetTask com artifact, history com a mensagem da tool e recusa de mensagens em
Tasks CANCELED e FAILED.

Cada teste usa um dia proprio: reservas de um teste nao interferem no outro.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from agente import config as configuracao
from agente.__main__ import criar_app

RAIZ = Path(__file__).resolve().parents[2]
CAP_FORM = {"elicitation": {"form": {}}}


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _aguardar_porta(porta: int, processo: subprocess.Popen, limite: float = 60) -> None:
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if processo.poll() is not None:
            raise RuntimeError("servidor MCP terminou antes de abrir a porta")
        try:
            with socket.create_connection(("127.0.0.1", porta), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"porta {porta} nao abriu")


@pytest.fixture(scope="module")
def mcp_real(tmp_path_factory) -> Iterator[tuple[str, Path]]:
    porta = _porta_livre()
    log = tmp_path_factory.mktemp("mcp") / "stderr.log"
    env = {
        **os.environ,
        "REQUEST_STATE_SECRET": secrets.token_hex(32),
        "MCP_PORT": str(porta),
        "VIRTUAL_ENV": "",
    }
    with open(log, "w") as saida:
        processo = subprocess.Popen(
            ["uv", "run", "--frozen", "python", "-m", "servidor_mcp"],
            cwd=RAIZ / "servidor-mcp",
            env=env,
            stdout=saida,
            stderr=subprocess.STDOUT,
        )
        try:
            _aguardar_porta(porta, processo)
            yield f"http://127.0.0.1:{porta}/mcp", log
        finally:
            processo.terminate()
            processo.wait(timeout=10)


class Espiao:
    """Proxy HTTP que registra e repassa cada request MCP ao servidor real."""

    def __init__(self, destino: str) -> None:
        self.destino = destino
        self.requests: list[dict[str, Any]] = []
        espiao = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                corpo = self.rfile.read(int(self.headers["Content-Length"]))
                cabecalhos = {k.lower(): v for k, v in self.headers.items()}
                req = urllib.request.Request(espiao.destino, data=corpo, method="POST")
                for k, v in self.headers.items():
                    if k.lower() not in ("host", "content-length", "connection"):
                        req.add_header(k, v)
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        status, resposta, tipo = r.status, r.read(), r.headers.get("Content-Type", "application/json")
                except urllib.error.HTTPError as e:
                    status, resposta, tipo = e.code, e.read(), e.headers.get("Content-Type", "application/json")
                espiao.requests.append(
                    {"headers": cabecalhos, "corpo": json.loads(corpo), "resposta": json.loads(resposta or b"{}")}
                )
                self.send_response(status)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(resposta)))
                self.end_headers()
                self.wfile.write(resposta)

            def log_message(self, *a: Any) -> None:
                pass

        self._servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._servidor.server_address[1]}/mcp"
        threading.Thread(target=self._servidor.serve_forever, daemon=True).start()

    def parar(self) -> None:
        self._servidor.shutdown()


@pytest.fixture(scope="module")
def espiao(mcp_real) -> Iterator[Espiao]:
    e = Espiao(mcp_real[0])
    yield e
    e.parar()


@pytest.fixture(scope="module")
def agente(espiao) -> Iterator[tuple[TestClient, Any]]:
    cfg = configuracao.carregar({"MCP_URL": espiao.url})
    app = criar_app(cfg)
    with TestClient(app, base_url="http://127.0.0.1:7300") as cliente:
        yield cliente, app


class A2A:
    def __init__(self, cliente: TestClient) -> None:
        self.cliente = cliente
        self.respostas_brutas: list[str] = []

    def rpc(self, metodo: str, params: dict[str, Any], traceparent: str | None = None) -> dict[str, Any]:
        cabecalhos = {"traceparent": traceparent} if traceparent else {}
        corpo = {"jsonrpc": "2.0", "id": secrets.token_hex(4), "method": metodo, "params": params}
        r = self.cliente.post("/a2a", json=corpo, headers=cabecalhos)
        self.respostas_brutas.append(r.text)
        return r.json()

    def enviar(self, texto: str, task_id: str | None = None, traceparent: str | None = None) -> dict[str, Any]:
        msg: dict[str, Any] = {"messageId": f"msg-{secrets.token_hex(6)}", "role": "ROLE_USER", "parts": [{"text": texto}]}
        if task_id:
            msg["taskId"] = task_id
        return self.rpc("SendMessage", {"message": msg}, traceparent)

    def get(self, task_id: str) -> dict[str, Any]:
        r = self.rpc("GetTask", {"id": task_id})["result"]
        return r.get("task", r)


def task(resposta: dict[str, Any]) -> dict[str, Any]:
    return resposta["result"]["task"]


def estado(t: dict[str, Any]) -> str:
    return t["status"]["state"]


def msg_status(t: dict[str, Any]) -> str:
    return " ".join(p.get("text", "") for p in t["status"]["message"]["parts"])


def pedido(dia: str, sala: str, inicio: str, fim: str, quem: str = "Teste") -> str:
    return f"reservar sala={sala} inicio={dia}T{inicio}:00-03:00 fim={dia}T{fim}:00-03:00 responsavel={quem}"


def pausar(a2a: A2A, dia: str, traceparent: str | None = None) -> dict[str, Any]:
    """Ocupa a garagem 14-15 no dia e pede a mesma sala: a Task pausa."""
    assert estado(task(a2a.enviar(pedido(dia, "sala-garagem", "14:00", "15:00", "Ocupante"))) ) == "TASK_STATE_COMPLETED"
    t = task(a2a.enviar(pedido(dia, "sala-garagem", "14:00", "15:00", "Marty"), traceparent=traceparent))
    assert estado(t) == "TASK_STATE_INPUT_REQUIRED"
    assert msg_status(t) == "alternativas: sala-fusca, sala-mirante"
    return t


# --- 13: requestState do agente nunca vaza ----------------------------------------------


def test_request_state_real_do_agente_nunca_aparece_em_resposta_a2a(agente):
    cliente, app = agente
    a2a = A2A(cliente)
    estados: set[str] = set()

    t1 = pausar(a2a, "2026-12-01")
    t2 = task(a2a.enviar(pedido("2026-12-01", "sala-garagem", "14:30", "15:30", "Biff")))
    assert estado(t2) == "TASK_STATE_INPUT_REQUIRED"
    estados |= {p.request_state for p in app.state.pausas.instantaneo().values()}
    assert len(estados) >= 2

    a2a.enviar("escolha=sala-aquario", task_id=t1["id"])  # fora do enum
    a2a.get(t1["id"])
    a2a.get(t2["id"])
    a2a.enviar("escolha=sala-fusca", task_id=t1["id"])
    a2a.enviar("escolha=recusar", task_id=t2["id"])
    a2a.get(t1["id"])

    tudo = "\n".join(a2a.respostas_brutas)
    for estado_mcp in estados:
        assert estado_mcp not in tudo
        assert estado_mcp[:40] not in tudo
    assert "requestState" not in tudo and "request_state" not in tudo


# --- 15, 16, 17: terminal, GetTask com artifact, history ----------------------------------


def test_get_task_devolve_artifact_e_history_completo(agente):
    a2a = A2A(agente[0])
    t = pausar(a2a, "2026-12-02")
    a2a.enviar("escolha=sala-mirante", task_id=t["id"])
    final = a2a.get(t["id"])
    assert estado(final) == "TASK_STATE_COMPLETED" and final["contextId"] == t["contextId"]
    [artifact] = final["artifacts"]
    conteudo = json.loads(artifact["parts"][0]["text"])
    assert artifact["name"] == "reserva"
    assert (conteudo["sala"], conteudo["politica"], conteudo["responsavel"]) == ("sala-mirante", "2026-11-01", "Marty")
    historico = [(m["role"], m["parts"][0]["text"]) for m in final["history"]]
    assert historico[1:] == [
        ("ROLE_AGENT", "alternativas: sala-fusca, sala-mirante"),
        ("ROLE_USER", "escolha=sala-mirante"),
        ("ROLE_AGENT", f"Reserva {conteudo['reserva']} confirmada na sala-mirante."),
    ]


def test_mensagem_da_tool_fica_no_history_da_task_failed(agente):
    a2a = A2A(agente[0])
    t = task(a2a.enviar(pedido("2026-12-03", "sala-delorean", "09:00", "10:00")))
    assert estado(t) == "TASK_STATE_FAILED"
    historico = [m["parts"][0]["text"] for m in a2a.get(t["id"])["history"]]
    assert any("Sala inexistente: sala-delorean" in texto for texto in historico)


@pytest.mark.parametrize("terminal", ["TASK_STATE_CANCELED", "TASK_STATE_FAILED"])
def test_mensagem_para_task_canceled_ou_failed_e_recusada(agente, terminal):
    a2a = A2A(agente[0])
    if terminal == "TASK_STATE_CANCELED":
        t = pausar(a2a, "2026-12-04")
        assert estado(task(a2a.enviar("escolha=recusar", task_id=t["id"]))) == terminal
    else:
        t = task(a2a.enviar("pedido fora do formato"))
        assert estado(t) == terminal and msg_status(t).startswith("Pedido fora do formato")
    resposta = a2a.enviar("escolha=sala-fusca", task_id=t["id"])
    assert "error" in resposta
    assert estado(a2a.get(t["id"])) == terminal


# --- 18: trace-id em todos os requests da Task, ids distintos ------------------------------


def test_trace_id_em_todos_os_requests_da_task_no_stderr_do_mcp(agente, mcp_real):
    a2a = A2A(agente[0])
    trace_id = secrets.token_hex(16)
    t = pausar(a2a, "2026-12-05", traceparent=f"00-{trace_id}-{secrets.token_hex(8)}-01")
    a2a.enviar("escolha=sala-fusca", task_id=t["id"])  # continuacao SEM header: reaproveita o trace da Task

    time.sleep(0.3)
    linhas = [linha for linha in mcp_real[1].read_text().splitlines() if trace_id in linha]
    metodos = [re.search(r"method=(\S+)", linha).group(1) for linha in linhas]
    assert metodos == ["tools/list", "resources/read", "tools/call", "tools/call"]
    spans = {re.search(rf"traceparent=00-{trace_id}-([0-9a-f]{{16}})-01", linha).group(1) for linha in linhas}
    assert len(spans) == 4  # span-id novo por request
    ids_tools_call = [re.search(r" id=(\S+)", linha).group(1) for linha in linhas if "method=tools/call" in linha]
    assert ids_tools_call[0] != ids_tools_call[1]  # retry com id novo


# --- 19: todos os requests do agente, vistos pelo espiao ----------------------------------


def test_todo_request_do_agente_tem_meta_capability_form_e_headers(agente, espiao):
    a2a = A2A(agente[0])
    inicio = len(espiao.requests)
    t = pausar(a2a, "2026-12-06")
    a2a.enviar("escolha=sala-fusca", task_id=t["id"])
    capturados = espiao.requests[inicio:]
    assert capturados

    for req in capturados:
        corpo, cab = req["corpo"], req["headers"]
        meta = corpo["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        assert meta["io.modelcontextprotocol/clientCapabilities"] == CAP_FORM
        assert cab["mcp-protocol-version"] == "2026-07-28"
        assert cab["mcp-method"] == corpo["method"]
        if corpo["method"] == "tools/call":
            assert cab["mcp-name"] == corpo["params"]["name"]
        if corpo["method"] == "resources/read":
            assert cab["mcp-name"] == corpo["params"]["uri"]

    # Ordem por Task: descoberta antes do primeiro tools/call.
    metodos = [r["corpo"]["method"] for r in capturados]
    assert metodos.index("tools/list") < metodos.index("tools/call")

    # O par da pausa: o ultimo input_required e o retry seguinte.
    chamadas = [r for r in capturados if r["corpo"]["method"] == "tools/call"]
    pausa = next(r for r in reversed(chamadas) if r["resposta"].get("result", {}).get("resultType") == "input_required")
    retry = chamadas[chamadas.index(pausa) + 1]
    res_pausa = pausa["resposta"]["result"]
    chave = next(iter(res_pausa["inputRequests"]))
    assert retry["corpo"]["id"] != pausa["corpo"]["id"]
    assert retry["corpo"]["params"]["arguments"] == pausa["corpo"]["params"]["arguments"]
    assert retry["corpo"]["params"]["requestState"] == res_pausa["requestState"]  # byte a byte
    assert retry["corpo"]["params"]["inputResponses"] == {chave: {"action": "accept", "content": {"sala": "sala-fusca"}}}
    assert retry["resposta"]["result"]["structuredContent"]["sala"] == "sala-fusca"
