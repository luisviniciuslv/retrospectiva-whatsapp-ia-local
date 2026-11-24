"""Etapa 2 — Chunking com overlap de contexto."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .cleaner import Mensagem

MARCA_CONTEXTO = "--- contexto anterior (já analisado, use só para entender o resto) ---"
MARCA_NOVO = "--- trecho novo ---"


@dataclass
class Chunk:
    id: int
    data_inicio: str   # ISO (primeira mensagem NOVA)
    data_fim: str      # ISO (última mensagem)
    n_mensagens: int   # mensagens novas
    n_overlap: int
    texto: str
    hash: str

    @property
    def mes(self) -> str:
        """Mês 'dominante' (AAAA-MM, pelo ponto médio do chunk) — usado para agrupar no reduce."""
        ini, fim = datetime.fromisoformat(self.data_inicio), datetime.fromisoformat(self.data_fim)
        return (ini + (fim - ini) / 2).strftime("%Y-%m")


def formatar_mensagem(m: Mensagem) -> str:
    dt = datetime.fromisoformat(m.ts)
    texto = m.texto.replace("\n", "\n    ")
    return f"[{dt:%d/%m %H:%M}] {m.autor}: {texto}"


def gerar_chunks(mensagens: list[Mensagem], alvo_chars: int = 10000, overlap_msgs: int = 12) -> list[Chunk]:
    linhas = [formatar_mensagem(m) for m in mensagens]
    chunks: list[Chunk] = []
    i = 0
    n = len(mensagens)
    while i < n:
        ini_overlap = max(0, i - overlap_msgs) if chunks else i
        contexto = linhas[ini_overlap:i]
        tamanho = sum(len(l) + 1 for l in contexto)
        j = i
        novas: list[str] = []
        # Sempre inclui ao menos 1 mensagem nova (garante progresso)
        while j < n and (not novas or tamanho + len(linhas[j]) + 1 <= alvo_chars):
            novas.append(linhas[j])
            tamanho += len(linhas[j]) + 1
            j += 1
        partes = []
        if contexto:
            partes += [MARCA_CONTEXTO, *contexto, MARCA_NOVO]
        partes += novas
        texto = "\n".join(partes)
        chunks.append(Chunk(
            id=len(chunks),
            data_inicio=mensagens[i].ts,
            data_fim=mensagens[j - 1].ts,
            n_mensagens=j - i,
            n_overlap=len(contexto),
            texto=texto,
            hash=hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12],
        ))
        i = j
    return chunks


def salvar_chunks(chunks: list[Chunk], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")


def carregar_chunks(caminho: Path) -> list[Chunk]:
    with caminho.open(encoding="utf-8") as f:
        return [Chunk(**json.loads(l)) for l in f if l.strip()]
