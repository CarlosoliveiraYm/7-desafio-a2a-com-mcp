"""Carga dos arquivos de dominio (somente leitura) de dados/."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sala:
    id: str
    nome: str
    capacidade: int
    recursos: tuple[str, ...]


@dataclass(frozen=True)
class DadosIniciais:
    salas: tuple[Sala, ...]
    reservas: tuple[dict, ...]
    politica_texto: str
    politica_versao: str


def extrair_versao(texto: str) -> str:
    """A primeira linha da politica declara a versao: `versao: <valor>`."""
    primeira = texto.splitlines()[0] if texto else ""
    chave, separador, valor = primeira.partition(":")
    if chave.strip() != "versao" or not separador or not valor.strip():
        raise ValueError(f"primeira linha da politica nao declara a versao: {primeira!r}")
    return valor.strip()


def carregar(dados_dir: Path) -> DadosIniciais:
    salas_json = json.loads((dados_dir / "salas.json").read_text(encoding="utf-8"))
    reservas_json = json.loads((dados_dir / "reservas.json").read_text(encoding="utf-8"))
    politica = (dados_dir / "politica-de-uso.md").read_text(encoding="utf-8")
    salas = tuple(
        Sala(id=s["id"], nome=s["nome"], capacidade=int(s["capacidade"]), recursos=tuple(s["recursos"]))
        for s in salas_json
    )
    return DadosIniciais(
        salas=salas,
        reservas=tuple(reservas_json),
        politica_texto=politica,
        politica_versao=extrair_versao(politica),
    )
