"""Etapa 4 — Fase REDUCE: consolidação hierárquica (mês → ano) e dossiê final."""
from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from config import settings

from .chunker import Chunk
from .llm_client import OllamaClient, Selecao
from .map_phase import carregar_checkpoint, eh_nada_relevante, limpar_resposta

MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
            "agosto", "setembro", "outubro", "novembro", "dezembro"]


def nome_mes(chave: str) -> str:
    ano, mes = chave.split("-")
    return f"{MESES_PT[int(mes) - 1].capitalize()}/{ano}"


def _hash(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


def extracoes_por_mes(chunks: list[Chunk], checkpoint: Path) -> tuple["OrderedDict[str, list[str]]", dict]:
    """Junta as respostas válidas do MAP (só de chunks atuais, ignorando NADA_RELEVANTE)."""
    feitos = carregar_checkpoint(checkpoint)
    por_mes: "OrderedDict[str, list[str]]" = OrderedDict()
    info = {"chunks": len(chunks), "com_resultado": 0, "relevantes": 0, "faltando": 0}
    for c in chunks:
        reg = feitos.get(c.id)
        if not reg or reg.get("hash") != c.hash:
            info["faltando"] += 1
            continue
        info["com_resultado"] += 1
        resposta = limpar_resposta(reg.get("resposta", ""))
        if reg.get("relevante") and not eh_nada_relevante(resposta):
            info["relevantes"] += 1
            por_mes.setdefault(c.mes, []).append(resposta)
    return por_mes, info


class Reducer:
    def __init__(self, selecao: Selecao, cache_path: Path, cliente: OllamaClient | None = None):
        self.sel = selecao
        self.cfg = selecao.config
        self.cliente = cliente or OllamaClient(selecao.host)
        self.cache_path = cache_path
        self.cache = self._carregar_cache()
        self.tpl_mes = (settings.PROMPTS_DIR / "reduce_mes_prompt.txt").read_text(encoding="utf-8")
        self.tpl_final = (settings.PROMPTS_DIR / "reduce_prompt.txt").read_text(encoding="utf-8")

    # --- cache (checkpoint do reduce) -------------------------------------
    def _carregar_cache(self) -> dict[str, str]:
        cache: dict[str, str] = {}
        if self.cache_path.exists():
            for linha in self.cache_path.read_text(encoding="utf-8").splitlines():
                try:
                    reg = json.loads(linha)
                    cache[reg["chave"]] = reg["resposta"]
                except (ValueError, KeyError):
                    continue
        return cache

    def _salvar_cache(self, chave: str, periodo: str, resposta: str) -> None:
        self.cache[chave] = resposta
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"chave": chave, "periodo": periodo, "modelo": self.sel.modelo,
                                "resposta": resposta}, ensure_ascii=False) + "\n")

    def _chamar(self, prompt: str) -> str:
        return self.cliente.chat(self.sel.modelo, prompt, temperature=self.cfg["temperature"],
                                 num_ctx=self.cfg["num_ctx_reduce"], num_predict=self.cfg["num_predict_reduce"],
                                 think=self.cfg.get("think"))

    def resumir(self, periodo: str, textos: list[str], nivel: int = 0) -> str:
        """Resume uma lista de anotações de um período; divide em lotes se não couber."""
        limite = self.cfg["reduce_max_chars"]
        lotes: list[list[str]] = [[]]
        tam = 0
        for t in textos:
            t = t[:limite]  # segurança: uma anotação nunca estoura sozinha
            if lotes[-1] and tam + len(t) > limite:
                lotes.append([])
                tam = 0
            lotes[-1].append(t)
            tam += len(t) + 2
        parciais = []
        for i, lote in enumerate(lotes):
            extrato = "\n\n---\n\n".join(lote)
            rotulo = periodo if len(lotes) == 1 else f"{periodo} (parte {i + 1}/{len(lotes)})"
            chave = f"{self.sel.modelo}|{rotulo}|{_hash(extrato)}"
            if chave not in self.cache:
                prompt = self.tpl_mes.replace("{periodo}", rotulo).replace("{extrato}", extrato)
                self._salvar_cache(chave, rotulo, self._chamar(prompt))
            parciais.append(self.cache[chave])
        if len(parciais) == 1:
            return parciais[0]
        # Nível hierárquico extra — só se juntar lotes realmente reduz a quantidade.
        # (Se cada resumo sozinho já enche o limite, recursão não convergiria.)
        if len(lotes) >= len(textos) or nivel >= 3:
            return "\n\n".join(parciais)
        return self.resumir(periodo, parciais, nivel + 1)

    def dossie(self, por_mes: "OrderedDict[str, list[str]]", participantes: list[tuple[str, int]]) -> tuple[str, dict]:
        limite = self.cfg["reduce_max_chars"]
        bruto_total = sum(len(t) for ts in por_mes.values() for t in ts)
        hierarquico = bruto_total > limite

        if hierarquico:
            resumos: "OrderedDict[str, str]" = OrderedDict()
            for mes, textos in tqdm(por_mes.items(), desc="REDUCE por mês", unit="mês"):
                resumos[mes] = self.resumir(nome_mes(mes), textos)
        else:
            resumos = OrderedDict((m, "\n\n".join(ts)) for m, ts in por_mes.items())

        blocos = [f"## {nome_mes(m)}\n{t}" for m, t in resumos.items()]
        consolidado = "\n\n".join(blocos)
        # Se nem os resumos mensais couberem, agrupa meses vizinhos (trimestres etc.)
        while len(consolidado) > limite * 1.3 and len(blocos) > 1:
            novos = []
            for i in range(0, len(blocos), 2):
                par = blocos[i:i + 2]
                cab = par[0].splitlines()[0][3:] + (f" a {par[-1].splitlines()[0][3:]}" if len(par) > 1 else "")
                novos.append(f"## {cab}\n" + self.resumir(cab, par))
            blocos = novos
            consolidado = "\n\n".join(blocos)

        meses = list(por_mes)
        periodo = f"{nome_mes(meses[0])} a {nome_mes(meses[-1])}" if meses else "?"
        lista_part = ", ".join(f"{n} ({q})" for n, q in participantes[:40]) or "(desconhecidos)"
        prompt = (self.tpl_final.replace("{periodo}", periodo)
                  .replace("{participantes}", lista_part)
                  .replace("{extrato_consolidado}", consolidado))
        print("Gerando o dossiê final (pode demorar alguns minutos)...")
        corpo = self._chamar(prompt)
        meta = {"periodo": periodo, "hierarquico": hierarquico, "chars_brutos": bruto_total,
                "chars_consolidados": len(consolidado)}
        return self._formatar(corpo, resumos, meta, participantes), meta

    def _formatar(self, corpo: str, resumos: "OrderedDict[str, str]", meta: dict,
                  participantes: list[tuple[str, int]]) -> str:
        cab = [
            "# Dossiê de Retrospectiva",
            "",
            f"> Período: **{meta['periodo']}** · Modelo: `{self.sel.modelo}` (perfil {self.sel.perfil}) · "
            f"Gerado em {datetime.now():%d/%m/%Y %H:%M}",
            "> Gerado por IA local — revise nomes e contexto antes de usar na música.",
            "",
            "",
        ]
        # O modelo às vezes repete o título; removemos para não duplicar
        corpo = re.sub(r"\A\s*#{1,2}\s*Dossi[êe][^\n]*\n+", "", corpo.strip(), flags=re.IGNORECASE)
        part = ["", "---", "", "## Anexo A — Quem mais falou", ""]
        part += [f"- {n}: {q} mensagens" for n, q in participantes]
        anexo = ["", "## Anexo B — Anotações por mês (material bruto para curadoria)", ""]
        for mes, texto in resumos.items():
            # rebaixa os títulos internos (### Eventos -> #### Eventos) para ficarem sob o mês
            texto = re.sub(r"^(#{1,5})\s", lambda m: "#" * max(4, len(m.group(1)) + 1) + " ",
                           texto.strip(), flags=re.MULTILINE)
            anexo += [f"### {nome_mes(mes)}", "", texto, ""]
        return "\n".join(cab) + corpo.strip() + "\n" + "\n".join(part + anexo)
