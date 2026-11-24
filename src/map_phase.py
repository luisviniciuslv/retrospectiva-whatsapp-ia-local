"""Etapa 3 — Fase MAP: extração chunk a chunk com checkpoint incremental."""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from config import settings

from .chunker import Chunk
from .llm_client import OllamaClient, Selecao

NADA = "NADA_RELEVANTE"


def eh_nada_relevante(resposta: str) -> bool:
    r = resposta.strip().strip("*`'\". ").upper()
    if not r:
        return True
    return r.startswith(NADA) or (NADA in r and len(r) < 80)


def _norm(s: str) -> str:
    s = re.sub(r"^\s*[-*•]\s*(\(?\d{1,2}/\d{1,2}[^)\s]*\)?)?\s*", "", s.lower())
    return re.sub(r"[^\w]+", " ", s).strip()


_ECO_CACHE: set[str] | None = None


def _linhas_do_prompt() -> set[str]:
    """Linhas do template (normalizadas) — se o modelo devolvê-las, é eco, não extração."""
    global _ECO_CACHE
    if _ECO_CACHE is None:
        tpl = (settings.PROMPTS_DIR / "map_prompt.txt").read_text(encoding="utf-8")
        _ECO_CACHE = {n for l in tpl.splitlines() if len(n := _norm(l.split(":", 1)[-1])) >= 15}
    return _ECO_CACHE


def _eh_eco(linha: str) -> bool:
    n = _norm(linha)
    return len(n) >= 15 and any(n in e or e in n for e in _linhas_do_prompt())


def limpar_resposta(resposta: str) -> str:
    """Remove itens '- NADA_RELEVANTE', ecos do prompt, duplicatas e títulos vazios."""
    vistos: set[str] = set()
    linhas = []
    for l in resposta.strip().splitlines():
        if NADA in l.upper() and len(l.strip("-*•: ").strip()) <= len(NADA) + 4:
            continue
        if not l.lstrip().startswith("#") and l.strip():
            if _eh_eco(l) or _norm(l) in vistos:
                continue
            vistos.add(_norm(l))
        linhas.append(l)
    saida: list[str] = []
    for i, l in enumerate(linhas):
        if l.lstrip().startswith("#"):
            prox = next((x for x in linhas[i + 1:] if x.strip()), "")
            if not prox or prox.lstrip().startswith("#"):
                continue  # título vazio
        saida.append(l)
    return "\n".join(saida).strip()


def montar_prompt(chunk: Chunk, template: str) -> str:
    ini = datetime.fromisoformat(chunk.data_inicio)
    fim = datetime.fromisoformat(chunk.data_fim)
    return (template
            .replace("{data_inicio}", f"{ini:%d/%m/%Y}")
            .replace("{data_fim}", f"{fim:%d/%m/%Y}")
            .replace("{texto_do_chunk}", chunk.texto))


def carregar_checkpoint(caminho: Path) -> dict[int, dict]:
    """Lê o .jsonl; em caso de duplicata, vale a última linha. Linhas corrompidas são ignoradas."""
    feitos: dict[int, dict] = {}
    if not caminho.exists():
        return feitos
    with caminho.open(encoding="utf-8") as f:
        for linha in f:
            try:
                reg = json.loads(linha)
                feitos[int(reg["chunk_id"])] = reg
            except (ValueError, KeyError):
                continue  # linha parcial de uma interrupção brusca
    return feitos


def escolher_amostra(chunks: list[Chunk], n: int, semente: int = 42) -> list[Chunk]:
    """Amostra espalhada ao longo do período (piloto da Fase 3 do plano)."""
    if n >= len(chunks):
        return chunks
    passo = len(chunks) / n
    rnd = random.Random(semente)
    idx = sorted({min(len(chunks) - 1, int(i * passo + rnd.random() * passo)) for i in range(n)})
    return [chunks[i] for i in idx]


def rodar_map(chunks: list[Chunk], selecao: Selecao, checkpoint: Path, *, cliente: OllamaClient | None = None,
              limite: int | None = None, refazer_ids: set[int] | None = None, verbose: bool = False) -> dict:
    cliente = cliente or OllamaClient(selecao.host)
    template = (settings.PROMPTS_DIR / "map_prompt.txt").read_text(encoding="utf-8")
    cfg = selecao.config
    feitos = carregar_checkpoint(checkpoint)
    refazer_ids = refazer_ids or set()

    def precisa(c: Chunk) -> bool:
        if c.id in refazer_ids:
            return True
        reg = feitos.get(c.id)
        # Se o chunk mudou (re-limpeza com outros filtros), refaz
        return reg is None or reg.get("hash") != c.hash

    pendentes = [c for c in chunks if precisa(c)]
    pulados = len(chunks) - len(pendentes)
    if limite is not None:
        pendentes = pendentes[:limite]

    resumo = {"total": len(chunks), "pulados": pulados, "processados": 0, "relevantes": 0, "erros": 0}
    if not pendentes:
        return resumo

    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    barra = tqdm(pendentes, desc=f"MAP [{selecao.modelo}]", unit="chunk")
    with checkpoint.open("a", encoding="utf-8") as f:
        for c in barra:
            barra.set_postfix_str(f"{c.data_inicio[:10]}")
            t0 = time.time()
            try:
                resposta = cliente.chat(
                    selecao.modelo, montar_prompt(c, template),
                    temperature=cfg["temperature"], num_ctx=cfg["num_ctx_map"],
                    num_predict=cfg["num_predict_map"], think=cfg.get("think"),
                )
            except Exception as e:  # noqa: BLE001 — registra e segue; reprocessa na próxima rodada
                resumo["erros"] += 1
                tqdm.write(f"  ! chunk {c.id}: {e}")
                continue
            resposta = limpar_resposta(resposta)
            nada = eh_nada_relevante(resposta)
            reg = {
                "chunk_id": c.id, "hash": c.hash, "mes": c.mes,
                "data_inicio": c.data_inicio, "data_fim": c.data_fim,
                "modelo": selecao.modelo, "relevante": not nada,
                "resposta": "" if nada else resposta,
                "segundos": round(time.time() - t0, 1),
                "gerado_em": datetime.now().isoformat(timespec="seconds"),
            }
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
            f.flush()
            resumo["processados"] += 1
            resumo["relevantes"] += int(not nada)
            if verbose:
                tqdm.write(f"\n=== chunk {c.id} ({c.data_inicio[:10]} → {c.data_fim[:10]}) ===\n"
                           f"{resposta if not nada else NADA}\n")
    return resumo
