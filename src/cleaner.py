"""Etapa 1 — Ingestão e limpeza do .txt exportado do WhatsApp.

Suporta os formatos mais comuns:
  Android:  24/11/2026 17:05 - Fulano: mensagem
            24/11/26, 5:05 PM - Fulano: mensagem
  iOS:      [24/11/2026, 17:05:33] Fulano: mensagem
            [24/11/26 17:05:33] Fulano: mensagem
Linhas sem timestamp são anexadas à mensagem anterior (multilinha).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

# Caracteres invisíveis que o WhatsApp injeta (LRM, RLM, NBSP estreito, BOM...)
_INVISIVEIS = re.compile(r"[‎‏‪-‮⁦-⁩﻿]")

_DATA = r"(?P<data>\d{1,2}/\d{1,2}/\d{2,4})"
_HORA = r"(?P<hora>\d{1,2}:\d{2}(?::\d{2})?)(?:\s?(?P<ampm>[AaPp]\.?\s?[Mm]\.?))?"

RE_ANDROID = re.compile(rf"^{_DATA},?\s{_HORA}\s[-–]\s(?P<resto>.*)$")
RE_IOS = re.compile(rf"^\[{_DATA},?\s{_HORA}\]\s(?P<resto>.*)$")
# Autor: até o primeiro ": " (nomes não costumam ter dois-pontos)
RE_AUTOR = re.compile(r"^(?P<autor>[^:]{1,80}?):\s(?P<texto>.*)$", re.DOTALL)

RE_URL = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
RE_TELEFONE = re.compile(r"^\+?[\d\s()\-]{8,}$")

# Mensagens inteiras que são só ruído (comparação após strip/lower)
RUIDO_EXATO = {
    "<mídia oculta>", "<media omitted>", "<arquivo de mídia oculto>",
    "mensagem apagada", "esta mensagem foi apagada", "você apagou esta mensagem",
    "this message was deleted", "you deleted this message",
    "imagem ocultada", "vídeo omitido", "video omitido", "áudio ocultado",
    "audio omitido", "áudio omitido", "figurinha omitida", "sticker omitted",
    "gif omitido", "documento omitido", "imagem omitida", "image omitted",
    "video omitted", "audio omitted", "gif omitted", "document omitted",
    "contato omitido", "contact card omitted", "null",
    "vídeo ocultado", "documento ocultado", "figurinha ocultada", "gif ocultado",
    "imagem omitida", "localização omitida", "enquete omitida",
    "mensagem de visualização única", "view once message",
    "aguardando mensagem. isso pode levar algum tempo.",
    "waiting for this message. this may take a while.",
}

# Trechos que denunciam mensagem de sistema
RUIDO_SISTEMA = [
    "as mensagens e ligações são protegidas", "as mensagens e as ligações são protegidas",
    "mensagens e ligações são protegidas", "messages and calls are end-to-end encrypted",
    "entrou usando o link", "joined using this group's invite link",
    "mudou a imagem do grupo", "changed this group's icon", "apagou a imagem do grupo",
    "mudou a descrição do grupo", "changed the group description",
    "mudou o nome do grupo", "changed the subject", "mudou o assunto",
    " adicionou ", " added ", " removeu ", " removed ",
    "criou o grupo", "created group", "agora é admin", "now an admin",
    "mudou para", "changed to", "código de segurança", "security code",
    "mensagens temporárias", "disappearing messages", "fixou uma mensagem", "pinned a message",
    "mudou as configurações do grupo", "changed this group's settings",
]

MARCADOR_EDITADA = re.compile(r"\s*<(mensagem editada|this message was edited)>\s*$", re.IGNORECASE)
RE_ANEXO = re.compile(r"^.*\.(jpg|jpeg|png|webp|opus|mp4|mp3|pdf|vcf|gif)\s*\((arquivo anexado|file attached)\)$",
                      re.IGNORECASE)


@dataclass
class Mensagem:
    ts: str      # ISO 8601
    autor: str
    texto: str

    @property
    def datetime(self) -> datetime:
        return datetime.fromisoformat(self.ts)


@dataclass
class EstatisticasLimpeza:
    linhas_originais: int = 0
    mensagens_brutas: int = 0
    mensagens_sistema: int = 0
    mensagens_ruido: int = 0
    mensagens_fora_periodo: int = 0
    mensagens_limpas: int = 0
    linhas_ignoradas_inicio: int = 0
    formato: str = "?"
    ordem_data: str = "DMY"


def _parse_datetime(data_s: str, hora_s: str, ampm: str | None, ordem: str) -> datetime:
    p1, p2, ano = data_s.split("/")
    dia, mes = (int(p1), int(p2)) if ordem == "DMY" else (int(p2), int(p1))
    ano_i = int(ano)
    if ano_i < 100:
        ano_i += 2000
    partes = [int(x) for x in hora_s.split(":")]
    h, m = partes[0], partes[1]
    s = partes[2] if len(partes) > 2 else 0
    if ampm:
        pm = ampm.lower().startswith("p")
        if pm and h < 12:
            h += 12
        elif not pm and h == 12:
            h = 0
    return datetime(ano_i, mes, dia, h, m, s)


def _detectar_ordem_data(datas: list[str]) -> str:
    """Decide se é DD/MM ou MM/DD olhando valores > 12 em cada posição."""
    primeiro_maior = any(int(d.split("/")[0]) > 12 for d in datas)
    segundo_maior = any(int(d.split("/")[1]) > 12 for d in datas)
    if segundo_maior and not primeiro_maior:
        return "MDY"
    return "DMY"  # padrão brasileiro


def carregar_aliases(caminho: Path) -> dict[str, str]:
    if not caminho.exists():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return {k: v for k, v in dados.items() if not k.startswith("_") and v}


def _normaliza_chave(nome: str) -> str:
    return re.sub(r"[\s()\-]", "", nome).lower()


def aplicar_alias(autor: str, aliases: dict[str, str]) -> str:
    if autor in aliases:
        return aliases[autor]
    chave = _normaliza_chave(autor)
    for k, v in aliases.items():
        if _normaliza_chave(k) == chave:
            return v
    return autor


RUIDO_EXATO = {r.strip("<>") for r in RUIDO_EXATO}  # compara sem os <>
# Placeholders de mídia/sistema embutidos no texto: <imagem ocultada>, <mensagem de voz omitida>,
# <Mensagem de album>, <Media omitted>... (removidos; a legenda, se houver, fica)
RE_PLACEHOLDER = re.compile(r"<[^<>\n]{0,60}(omitid|ocultad|oculta\b|omitted|mensagem de |message_|anexado)"
                            r"[^<>\n]{0,60}>", re.IGNORECASE)
RE_RISADA = re.compile(r"\b([kK])\1{4,}\b")
RE_ENCAMINHADA = re.compile(r"^\s*\[(encaminhada|forwarded)\]\s*", re.IGNORECASE)


def _eh_ruido(texto: str) -> bool:
    # "[Encaminhada] <áudio omitido>" -> "áudio omitido"
    t = RE_PLACEHOLDER.sub("", RE_ENCAMINHADA.sub("", texto)).strip().lower().strip("<>").strip()
    if not t or t in RUIDO_EXATO:
        return True
    if RE_ANEXO.match(t):
        return True
    return False


def _eh_sistema_sem_autor(resto: str) -> bool:
    """Linha com timestamp mas sem 'Autor: ' => mensagem de sistema."""
    return RE_AUTOR.match(resto) is None


def _limpar_texto(texto: str, aliases: dict[str, str], max_chars: int) -> str:
    texto = MARCADOR_EDITADA.sub("", texto)
    texto = RE_PLACEHOLDER.sub("", texto)
    texto = RE_RISADA.sub(lambda m: m.group(1) * 4, texto)
    texto = RE_URL.sub("[LINK]", texto)
    # menções @5511999999999 -> @Apelido
    if aliases:
        def _troca_mencao(m: re.Match) -> str:
            num = m.group(1)
            for k, v in aliases.items():
                if re.sub(r"\D", "", k) == num:
                    return f"@{v}"
            return m.group(0)
        texto = re.sub(r"@(\d{8,15})", _troca_mencao, texto)
    texto = re.sub(r"[ \t]+", " ", texto).strip()
    if len(texto) > max_chars:
        texto = texto[:max_chars].rstrip() + " [...]"
    return texto


def parse_whatsapp(
    linhas: list[str],
    aliases: dict[str, str] | None = None,
    data_inicio: date | None = None,
    data_fim: date | None = None,
    max_chars: int = 1500,
) -> tuple[list[Mensagem], EstatisticasLimpeza]:
    aliases = aliases or {}
    stats = EstatisticasLimpeza(linhas_originais=len(linhas))
    linhas = [_INVISIVEIS.sub("", l).replace(" ", " ").replace(" ", " ").rstrip("\r\n")
              for l in linhas]

    # Detecta formato pelas primeiras linhas com timestamp
    amostra = linhas[:500]
    n_android = sum(1 for l in amostra if RE_ANDROID.match(l))
    n_ios = sum(1 for l in amostra if RE_IOS.match(l))
    regex = RE_IOS if n_ios > n_android else RE_ANDROID
    stats.formato = "iOS" if regex is RE_IOS else "Android"
    datas = [m.group("data") for l in linhas[:3000] if (m := regex.match(l))]
    if not datas:
        raise ValueError("Nenhuma linha no formato do WhatsApp foi reconhecida. "
                         "Confira se o arquivo é o .txt exportado (sem mídia).")
    ordem = _detectar_ordem_data(datas)
    stats.ordem_data = ordem

    # Agrupa multilinhas
    brutas: list[tuple[datetime, str]] = []
    for linha in linhas:
        m = regex.match(linha)
        if m:
            dt = _parse_datetime(m.group("data"), m.group("hora"), m.group("ampm"), ordem)
            brutas.append((dt, m.group("resto")))
        elif brutas:
            dt, resto = brutas[-1]
            brutas[-1] = (dt, resto + "\n" + linha)
        else:
            stats.linhas_ignoradas_inicio += 1
    stats.mensagens_brutas = len(brutas)

    mensagens: list[Mensagem] = []
    for dt, resto in brutas:
        if data_inicio and dt.date() < data_inicio or data_fim and dt.date() > data_fim:
            stats.mensagens_fora_periodo += 1
            continue
        m = RE_AUTOR.match(resto)
        if m is None:
            stats.mensagens_sistema += 1
            continue
        autor, texto = m.group("autor").strip(), m.group("texto")
        # Sistema cujo texto tem ": " (ex.: 'Fulano mudou o nome do grupo para "A: B"')
        if any(k in autor.lower() for k in RUIDO_SISTEMA):
            stats.mensagens_sistema += 1
            continue
        tl = texto.lower()
        # Mensagens de sistema com "autor" aparente (raras): curtas e com palavras-chave
        if len(tl) < 120 and any(k in tl for k in ("criptografia de ponta a ponta", "end-to-end encrypted")):
            stats.mensagens_sistema += 1
            continue
        if _eh_ruido(texto):
            stats.mensagens_ruido += 1
            continue
        texto = _limpar_texto(texto, aliases, max_chars)
        if not texto or texto == "[LINK]":
            stats.mensagens_ruido += 1
            continue
        mensagens.append(Mensagem(ts=dt.isoformat(), autor=aplicar_alias(autor, aliases), texto=texto))

    stats.mensagens_limpas = len(mensagens)
    return mensagens, stats


def contar_autores(mensagens: list[Mensagem]) -> Counter:
    return Counter(m.autor for m in mensagens)


def parece_telefone(nome: str) -> bool:
    return bool(RE_TELEFONE.match(nome.strip()))


def ler_arquivo(caminho: Path) -> list[str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return caminho.read_text(encoding=enc).splitlines()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Não consegui decodificar {caminho}")


def salvar_mensagens(mensagens: list[Mensagem], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as f:
        for m in mensagens:
            f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")


def carregar_mensagens(caminho: Path) -> list[Mensagem]:
    with caminho.open(encoding="utf-8") as f:
        return [Mensagem(**json.loads(l)) for l in f if l.strip()]
