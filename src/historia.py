"""Etapa 5 — História: lê o dossiê em blocos e compõe a letra (rimada e zoeira) da música.

Funciona como um "refine": a cada bloco do dossiê, a IA recebe o material escrito até
agora e acrescenta só o que é novo e engraçado (em prosa, para não perder os fatos). No
fim, uma última chamada transforma esse rascunho na letra da música, com versos rimados
e marcações [Verso]/[Refrão]/[Ponte], pronta para colar num gerador de música.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tqdm import tqdm

from config import settings

from .llm_client import OllamaClient, Selecao

NADA_NOVO = "NADA_NOVO"
RE_TITULO = re.compile(r"^(#{1,6})\s+(.*)$")
# Seções do dossiê que não ajudam a contar a história
SECOES_IGNORADAS = re.compile(r"anexo a|quem mais falou", re.IGNORECASE)


def _hash(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


def _norm(linha: str) -> str:
    s = re.sub(r"^\s*[-*•]\s*(\(?\d{1,2}/\d{1,2}[^)\s]*\)?)?\s*", "", linha.lower())
    return re.sub(r"[^\w]+", " ", s).strip()


# --------------------------------------------------------------------------
# Leitura do dossiê
# --------------------------------------------------------------------------
def limpar_dossie(md: str) -> list[str]:
    """Linhas úteis do dossiê: sem cabeçalho, sem anexo de contagem, sem itens repetidos
    e sem títulos que ficaram vazios."""
    linhas: list[str] = []
    vistos: set[str] = set()
    pulando_nivel: int | None = None
    for bruta in md.splitlines():
        l = bruta.rstrip()
        m = RE_TITULO.match(l.strip())
        if m:
            nivel = len(m.group(1))
            if pulando_nivel is not None and nivel <= pulando_nivel:
                pulando_nivel = None
            if pulando_nivel is not None:
                continue
            if nivel == 1:  # "# Dossiê de Retrospectiva"
                continue
            if SECOES_IGNORADAS.search(m.group(2)):
                pulando_nivel = nivel
                continue
            linhas.append(l.strip())
            continue
        if pulando_nivel is not None:
            continue
        s = l.strip()
        if not s or s.startswith(">") or set(s) <= set("-*_ "):
            continue  # vazio, citação do cabeçalho ou separador ---
        n = _norm(s)
        if not n or n in vistos:
            continue
        vistos.add(n)
        linhas.append(s)

    # remove títulos sem conteúdo até o próximo título de nível igual ou maior
    saida: list[str] = []
    for i, l in enumerate(linhas):
        m = RE_TITULO.match(l)
        if m:
            nivel = len(m.group(1))
            tem_conteudo = False
            for prox in linhas[i + 1:]:
                mp = RE_TITULO.match(prox)
                if mp and len(mp.group(1)) <= nivel:
                    break
                if not mp:
                    tem_conteudo = True
                    break
            if not tem_conteudo:
                continue
        saida.append(l)
    return saida


def blocos_do_dossie(md: str, alvo_chars: int) -> list[str]:
    """Divide o dossiê em blocos de ~alvo_chars, sem cortar itens no meio. Cada bloco
    novo repete os títulos em que está inserido, para a IA não perder o contexto."""
    blocos: list[str] = []
    atual: list[str] = []
    tam = 0
    pilha: list[tuple[int, str]] = []  # (nível, título) do ponto atual

    def fechar() -> None:
        nonlocal atual, tam
        if any(not RE_TITULO.match(x) for x in atual):
            blocos.append("\n".join(atual))
        atual = [t for _, t in pilha]
        tam = sum(len(t) + 1 for t in atual)

    for l in limpar_dossie(md):
        l = l[:alvo_chars]
        m = RE_TITULO.match(l)
        if tam + len(l) + 1 > alvo_chars and any(not RE_TITULO.match(x) for x in atual):
            fechar()
        if m:
            nivel = len(m.group(1))
            pilha = [(n, t) for n, t in pilha if n < nivel] + [(nivel, l)]
            # se o bloco só tem títulos herdados, troca pelos atuais
            if all(RE_TITULO.match(x) for x in atual):
                atual = [t for _, t in pilha]
                tam = sum(len(t) + 1 for t in atual)
                continue
        atual.append(l)
        tam += len(l) + 1
    if any(not RE_TITULO.match(x) for x in atual):
        blocos.append("\n".join(atual))
    return blocos


def periodo_do_dossie(md: str) -> str:
    m = re.search(r"Período:\s*\**([^*·\n]+)", md)
    return m.group(1).strip() if m else "o ano"


RE_PREAMBULO = re.compile(r"^(aqui (está|esta|vai)|segue|claro|certo|ok\b|abaixo)", re.IGNORECASE)
RE_DATA = re.compile(r"\(?\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b\)?\s*[-–—:]?\s*")


def limpar_trecho(resposta: str, vistos: set[str] | None = None) -> str:
    """Deixa só prosa: tira títulos, listas, separadores, datas exatas, o NADA_NOVO e
    parágrafos repetidos (inclusive os já contados, se `vistos` for passado)."""
    r = resposta.strip()
    if not r or r.strip("*`'\". ").upper().startswith(NADA_NOVO) or (NADA_NOVO in r.upper() and len(r) < 60):
        return ""
    vistos = set() if vistos is None else vistos
    paragrafos: list[str] = []
    for p in re.split(r"\n\s*\n", r):
        linhas = []
        for l in p.splitlines():
            s = l.strip()
            if not s or RE_TITULO.match(s) or NADA_NOVO in s.upper() or set(s) <= set("-*_= "):
                continue
            s = RE_DATA.sub("", re.sub(r"^[-*•]\s+", "", s)).strip()
            if s:
                linhas.append(s)
        texto = " ".join(linhas)
        if RE_PREAMBULO.match(texto) and texto.rstrip().endswith(":"):
            continue  # "Aqui está a versão final:" e afins
        n = _norm(texto)
        if n and n not in vistos:
            vistos.add(n)
            paragrafos.append(texto)
    return "\n\n".join(paragrafos)


SECOES_LETRA = re.compile(
    r"^(verso|estrofe|refr[aã]o|pr[eé][- ]?refr[aã]o|ponte|intro|introdu[cç][aã]o|outro|final|"
    r"encerramento|coro|chorus|verse|bridge|hook)\b[\w\s]{0,12}$", re.IGNORECASE)


def limpar_letra(resposta: str) -> str:
    """Deixa só a letra: preserva as quebras de verso, padroniza as marcações de seção como
    [Verso 1]/[Refrão] e tira preâmbulos, títulos, negrito e cercas de código."""
    linhas: list[str] = []
    for bruta in resposta.strip().splitlines():
        s = bruta.strip()
        if s.startswith("```"):
            continue
        m = RE_TITULO.match(s)
        nome = (m.group(2) if m else s).replace("*", "").replace("_", " ").strip().strip("[]():").strip()
        marcada = m or s.startswith(("[", "(", "*")) or s.endswith(":")
        if SECOES_LETRA.match(nome) and (marcada or re.fullmatch(r"[^\W\d]+(\s+\d+)?", nome)):
            if linhas and linhas[-1]:
                linhas.append("")
            linhas.append(f"[{nome[0].upper()}{nome[1:]}]")
            continue
        if m:
            continue  # título da música ou qualquer outro cabeçalho
        s = s.replace("**", "").strip()
        if not any(l and not l.startswith("[") for l in linhas):
            # antes do primeiro verso: descarta "Aqui está a letra:", "Título: ..." etc.
            if not s or (RE_PREAMBULO.match(s) and s.endswith(":")) or s.lower().startswith(("título", "titulo")):
                continue
        if not s and (not linhas or not linhas[-1]):
            continue  # sem linhas em branco duplicadas
        linhas.append(s)
    while linhas and (not linhas[-1] or linhas[-1].startswith("[")):
        linhas.pop()  # marcação solta ou linha vazia no fim
    if not any(l and not l.startswith("[") for l in linhas):
        return ""
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Escritor
# --------------------------------------------------------------------------
class Escritor:
    def __init__(self, selecao: Selecao, cache_path: Path, cliente: OllamaClient | None = None):
        self.sel = selecao
        self.cfg = selecao.config
        self.cliente = cliente or OllamaClient(selecao.host)
        self.cache_path = cache_path
        self.cache = self._carregar_cache()
        self.tpl_bloco = (settings.PROMPTS_DIR / "historia_bloco_prompt.txt").read_text(encoding="utf-8")
        self.tpl_final = (settings.PROMPTS_DIR / "historia_final_prompt.txt").read_text(encoding="utf-8")
        self.tpl_resumo = (settings.PROMPTS_DIR / "historia_resumo_prompt.txt").read_text(encoding="utf-8")
        # quanto da história anterior cabe no prompt junto com o bloco novo
        self.max_contexto = self.cfg["reduce_max_chars"] // 2

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

    def _salvar_cache(self, chave: str, etapa: str, resposta: str) -> None:
        self.cache[chave] = resposta
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"chave": chave, "etapa": etapa, "modelo": self.sel.modelo,
                                "resposta": resposta}, ensure_ascii=False) + "\n")

    def _chamar(self, chave: str, etapa: str, prompt: str, temperatura: float) -> str:
        if chave not in self.cache:
            resp = self.cliente.chat(self.sel.modelo, prompt, temperature=temperatura,
                                     num_ctx=self.cfg["num_ctx_reduce"],
                                     num_predict=self.cfg["num_predict_reduce"], think=self.cfg.get("think"))
            self._salvar_cache(chave, etapa, resp)
        return self.cache[chave]

    def _contexto(self, historia: str) -> str:
        if not historia:
            return "(ainda nada — este é o começo da história)"
        if len(historia) <= self.max_contexto:
            return historia
        corte = historia[-self.max_contexto:]
        corte = corte[corte.find("\n") + 1:] if "\n" in corte else corte
        return "[...início omitido...]\n" + corte

    def rascunho(self, blocos: list[str], periodo: str, participantes: str, verbose: bool = False) -> str:
        """Lê os blocos em ordem e vai acrescentando parágrafos à história."""
        temp = min(0.6, self.cfg["temperature"] + 0.15)  # um pouco mais solto que a extração
        historia = ""
        vistos: set[str] = set()
        for i, bloco in enumerate(tqdm(blocos, desc="História (lendo o dossiê)", unit="bloco"), 1):
            ctx = self._contexto(historia)
            # o hash do prompt entra na chave: editou o prompt, a IA escreve de novo
            chave = f"{self.sel.modelo}|bloco|{_hash(self.tpl_bloco)}|{_hash(bloco)}|{_hash(ctx)}"
            prompt = (self.tpl_bloco.replace("{periodo}", periodo)
                      .replace("{participantes}", participantes)
                      .replace("{parte}", f"{i}/{len(blocos)}")
                      .replace("{historia}", ctx)
                      .replace("{bloco}", bloco))
            trecho = limpar_trecho(self._chamar(chave, f"bloco {i}/{len(blocos)}", prompt, temp), vistos)
            if verbose:
                tqdm.write(f"\n--- bloco {i}/{len(blocos)} ---\n{trecho or '(nada novo)'}")
            if trecho:
                historia = f"{historia}\n\n{trecho}".strip()
        return historia

    def final(self, rascunho: str, periodo: str, max_palavras: int) -> str:
        """Transforma o rascunho na letra da música (rimada, com refrão)."""
        texto = rascunho
        limite = self.cfg["reduce_max_chars"]
        if len(texto) > limite:
            # rascunho grande demais para uma chamada: enxuga por partes (em prosa) antes
            partes, atual = [], ""
            for p in texto.split("\n\n"):
                if atual and len(atual) + len(p) > limite:
                    partes.append(atual)
                    atual = ""
                atual = f"{atual}\n\n{p}".strip()
            partes.append(atual)
            por_parte = max(150, max_palavras * 3 // len(partes))
            texto = "\n\n".join(self._enxugar(p, periodo, por_parte, f"parte {i + 1}/{len(partes)}")
                                for i, p in enumerate(partes))
        return self._compor_letra(texto, periodo, max_palavras)

    def _prompt(self, tpl: str, texto: str, periodo: str, max_palavras: int) -> str:
        return (tpl.replace("{periodo}", periodo)
                .replace("{max_palavras}", str(max_palavras))
                .replace("{rascunho}", texto))

    def _enxugar(self, texto: str, periodo: str, max_palavras: int, etapa: str) -> str:
        chave = f"{self.sel.modelo}|resumo|{_hash(self.tpl_resumo)}|{max_palavras}|{_hash(texto)}"
        resp = self._chamar(chave, etapa, self._prompt(self.tpl_resumo, texto, periodo, max_palavras),
                            min(0.6, self.cfg["temperature"] + 0.15))
        return limpar_trecho(resp) or texto

    def _compor_letra(self, texto: str, periodo: str, max_palavras: int) -> str:
        chave = f"{self.sel.modelo}|letra|{_hash(self.tpl_final)}|{max_palavras}|{_hash(texto)}"
        # mais criativo: rima pede liberdade (os fatos já vêm amarrados pelo rascunho)
        resp = self._chamar(chave, "letra", self._prompt(self.tpl_final, texto, periodo, max_palavras),
                            min(0.8, self.cfg["temperature"] + 0.45))
        letra = limpar_letra(resp)
        if not letra:
            raise RuntimeError("A IA não devolveu uma letra. Rode `historia` de novo ou troque de modelo.")
        return letra
