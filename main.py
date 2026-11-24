"""CLI do Extrator de Retrospectiva do WhatsApp com IA local (Ollama).

Uso rápido:
    python main.py                  # menu interativo
    python main.py status           # hardware, servidor e modelo selecionado
    python main.py setup            # baixa o modelo do perfil selecionado
    python main.py limpar           # etapa 1 (limpeza) + lista de participantes
    python main.py chunks           # etapa 2 (estatísticas de chunking)
    python main.py piloto -n 8      # teste piloto em 8 chunks espalhados
    python main.py map              # etapa 3 completa (retomável)
    python main.py reduce           # etapa 4 -> dossiê
    python main.py historia         # etapa 5 -> letra rimada e zoeira da música
    python main.py musica chat.txt -m qwen2.5:14b   # TUDO de uma vez (alias: rodar)

Seleção do modelo:  --perfil auto|teste|producao  --modelo NOME  --host URL  --vram GB
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from config import settings
from src import chunker, cleaner
from src.llm_client import OllamaClient, OllamaErro, Selecao, detectar_gpus, host_eh_local, selecionar
from src.map_phase import carregar_checkpoint, escolher_amostra, rodar_map
from src.historia import Escritor, blocos_do_dossie, periodo_do_dossie
from src.reduce_phase import Reducer, extracoes_por_mes

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ARQ_MENSAGENS = settings.PROCESSED_DIR / settings.NOME_ARQUIVO_MENSAGENS
ARQ_META = settings.PROCESSED_DIR / "limpeza_meta.json"


# ==========================================================================
# Utilidades
# ==========================================================================
def titulo(txt: str) -> None:
    print(f"\n\033[1;36m== {txt} ==\033[0m")


def ok(txt: str) -> None:
    print(f"\033[32m✔\033[0m {txt}")


def aviso(txt: str) -> None:
    print(f"\033[33m⚠ {txt}\033[0m")


def erro(txt: str) -> None:
    print(f"\033[31m✖ {txt}\033[0m", file=sys.stderr)


def confirmar(pergunta: str, assume_sim: bool = False) -> bool:
    if assume_sim:
        return True
    if not sys.stdin.isatty():
        return False
    return input(f"{pergunta} [s/N] ").strip().lower() in {"s", "sim", "y", "yes"}


def parse_data(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Data inválida: {s} (use dd/mm/aaaa)")


def extrair_zip(caminho: Path) -> Path:
    """O export do iPhone (e o "anexar" do Android) vem num .zip: extrai o .txt da conversa."""
    import zipfile
    with zipfile.ZipFile(caminho) as z:
        txts = [i for i in z.infolist() if i.filename.lower().endswith(".txt") and not i.is_dir()]
        if not txts:
            raise SystemExit(f"Nenhum .txt dentro de {caminho.name}.")
        # prefere o "_chat.txt" / "Conversa do WhatsApp com ...txt"; senão, o maior
        txts.sort(key=lambda i: ("chat" in i.filename.lower() or "conversa" in i.filename.lower(), i.file_size),
                  reverse=True)
        destino = settings.PROCESSED_DIR / "conversa_extraida.txt"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(z.read(txts[0]))
    print(f"  Extraído {txts[0].filename} de {caminho.name}")
    return destino


def achar_arquivo_raw(arquivo: str | None) -> Path:
    if arquivo:
        p = Path(arquivo)
        if not p.exists():
            raise SystemExit(f"Arquivo não encontrado: {p}")
        return extrair_zip(p) if p.suffix.lower() == ".zip" else p
    txts = sorted(settings.RAW_DIR.glob("*.txt"))
    if not txts:
        zips = sorted(settings.RAW_DIR.glob("*.zip"))
        if len(zips) == 1:
            return extrair_zip(zips[0])
        raise SystemExit(f"Coloque o .txt (ou .zip) exportado do WhatsApp em {settings.RAW_DIR} "
                         f"(ou passe o caminho do arquivo).")
    if len(txts) > 1:
        nomes = "\n  ".join(t.name for t in txts)
        raise SystemExit(f"Há mais de um .txt em {settings.RAW_DIR}; escolha com --arquivo:\n  {nomes}")
    return txts[0]


def obter_selecao(args) -> Selecao:
    sel = selecionar(perfil=args.perfil, modelo=args.modelo, host=args.host, vram_gb=args.vram)
    return sel


def mostrar_selecao(sel: Selecao) -> None:
    print(f"  Servidor : {sel.host}")
    print(f"  Perfil   : \033[1m{sel.perfil}\033[0m — {sel.config['descricao']}")
    print(f"  Motivo   : {sel.motivo}")
    print(f"  Modelo   : \033[1m{sel.modelo}\033[0m {'(instalado)' if sel.instalado else '(NÃO instalado)'}")
    for a in sel.avisos:
        aviso(a)


def pasta_saida(sel: Selecao) -> Path:
    return settings.OUTPUT_DIR / sel.slug


def carregar_msgs_ou_sair() -> list[cleaner.Mensagem]:
    if not ARQ_MENSAGENS.exists():
        raise SystemExit("Ainda não há mensagens limpas. Rode primeiro: python main.py limpar")
    return cleaner.carregar_mensagens(ARQ_MENSAGENS)


def gerar_chunks_para(sel: Selecao, msgs: list[cleaner.Mensagem]) -> list[chunker.Chunk]:
    return chunker.gerar_chunks(msgs, alvo_chars=sel.config["chunk_chars"], overlap_msgs=settings.OVERLAP_MSGS)


def garantir_modelo(sel: Selecao, assume_sim: bool) -> None:
    cli = OllamaClient(sel.host)
    if not cli.online():
        raise SystemExit(f"O Ollama não respondeu em {sel.host}. Ele está rodando? "
                         f"(Windows: abra o app Ollama; Linux: `ollama serve`)")
    if sel.instalado:
        return
    if confirmar(f"O modelo '{sel.modelo}' não está em {sel.host}. Baixar agora?", assume_sim):
        baixar(cli, sel.modelo)
        sel.instalado = True
    else:
        raise SystemExit(f"Baixe o modelo com: python main.py setup --perfil {sel.perfil}")


def baixar(cli: OllamaClient, modelo: str) -> None:
    from tqdm import tqdm
    barra = None
    ultimo = ""

    def prog(status, feito, total):
        nonlocal barra, ultimo
        if total:
            if barra is None or barra.total != total:
                if barra:
                    barra.close()
                barra = tqdm(total=total, unit="B", unit_scale=True, desc=f"pull {modelo}")
            barra.n = feito or 0
            barra.refresh()
        elif status != ultimo:
            print(f"  {status}")
            ultimo = status

    cli.baixar_modelo(modelo, prog)
    if barra:
        barra.close()
    ok(f"Modelo {modelo} pronto.")


# ==========================================================================
# Comandos
# ==========================================================================
def cmd_status(args) -> None:
    titulo("Hardware local")
    gpus = detectar_gpus()
    if gpus:
        for g in gpus:
            print(f"  GPU: {g.nome} — {g.vram_total_gb:.1f} GB VRAM ({g.vram_livre_gb:.1f} GB livres)")
    else:
        print("  Nenhuma GPU NVIDIA detectada.")

    titulo("Servidor Ollama")
    cli = OllamaClient(args.host)
    if cli.online():
        ok(f"{cli.host} online (versão {cli.versao()})")
        try:
            inst = cli.modelos_instalados()
            print(f"  Modelos instalados: {', '.join(inst) or '(nenhum)'}")
        except OllamaErro as e:
            aviso(str(e))
    else:
        aviso(f"{cli.host} offline")
    if not host_eh_local(cli.host) and args.vram is None and args.perfil == "auto":
        print("  (servidor remoto: a VRAM não é detectável pela API; use --vram ou --perfil se preciso)")

    titulo("Seleção de modelo")
    sel = obter_selecao(args)
    mostrar_selecao(sel)

    titulo("Perfis disponíveis")
    for nome, cfg in settings.PERFIS.items():
        marca = "→" if nome == sel.perfil else " "
        print(f" {marca} {nome:<9} VRAM ≥ {cfg['vram_min_gb']:>2} GB  modelos: {', '.join(cfg['modelos'])}")

    titulo("Pipeline")
    if ARQ_MENSAGENS.exists():
        msgs = cleaner.carregar_mensagens(ARQ_MENSAGENS)
        chunks = gerar_chunks_para(sel, msgs)
        feitos = carregar_checkpoint(pasta_saida(sel) / settings.NOME_ARQUIVO_MAP)
        validos = sum(1 for c in chunks if feitos.get(c.id, {}).get("hash") == c.hash)
        relevantes = sum(1 for c in chunks if feitos.get(c.id, {}).get("hash") == c.hash
                         and feitos[c.id].get("relevante"))
        print(f"  Mensagens limpas: {len(msgs)}  ({msgs[0].ts[:10]} → {msgs[-1].ts[:10]})" if msgs else
              "  Mensagens limpas: 0")
        print(f"  Chunks ({sel.config['chunk_chars']} chars): {len(chunks)}")
        print(f"  MAP concluído: {validos}/{len(chunks)} ({relevantes} relevantes)")
        dossie = pasta_saida(sel) / settings.NOME_ARQUIVO_DOSSIE
        print(f"  Dossiê: {dossie if dossie.exists() else '(ainda não gerado)'}")
        hist = pasta_saida(sel) / settings.NOME_ARQUIVO_HISTORIA
        print(f"  História: {hist if hist.exists() else '(ainda não gerada)'}")
    else:
        print("  Nada processado ainda. Próximo passo: python main.py limpar")


def cmd_setup(args) -> None:
    titulo("Setup")
    settings.RAW_DIR.mkdir(parents=True, exist_ok=True)
    settings.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sel = obter_selecao(args)
    mostrar_selecao(sel)
    cli = OllamaClient(sel.host)
    if not cli.online():
        raise SystemExit(f"O Ollama não respondeu em {sel.host}. Instale em https://ollama.com e deixe rodando.")
    if sel.instalado:
        ok(f"'{sel.modelo}' já está instalado.")
    else:
        baixar(cli, sel.modelo)
    if not any(settings.RAW_DIR.glob("*.txt")):
        aviso(f"Coloque o .txt exportado do WhatsApp em {settings.RAW_DIR}")


def cmd_limpar(args) -> list[cleaner.Mensagem]:
    titulo("Etapa 1 — Limpeza")
    arq = achar_arquivo_raw(args.arquivo)
    inicio, fim = args.inicio, args.fim
    if args.ano:
        inicio, fim = date(args.ano, 1, 1), date(args.ano, 12, 31)
    aliases = cleaner.carregar_aliases(settings.ALIASES_FILE)
    linhas = cleaner.ler_arquivo(arq)
    msgs, st = cleaner.parse_whatsapp(linhas, aliases, inicio, fim, settings.MAX_MSG_CHARS)
    cleaner.salvar_mensagens(msgs, ARQ_MENSAGENS)
    ARQ_META.write_text(json.dumps({"arquivo": str(arq), "inicio": str(inicio), "fim": str(fim),
                                    "stats": st.__dict__}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  Arquivo          : {arq.name}  (formato {st.formato}, datas {st.ordem_data})")
    print(f"  Linhas originais : {st.linhas_originais}")
    print(f"  Mensagens brutas : {st.mensagens_brutas}")
    print(f"  − sistema        : {st.mensagens_sistema}")
    print(f"  − mídia/apagadas : {st.mensagens_ruido}")
    if inicio or fim:
        print(f"  − fora do período: {st.mensagens_fora_periodo}  ({inicio or '…'} a {fim or '…'})")
    print(f"  = Mensagens limpas: \033[1m{st.mensagens_limpas}\033[0m")
    if msgs:
        print(f"  Período          : {msgs[0].ts[:10]} → {msgs[-1].ts[:10]}")
    ok(f"Salvo em {ARQ_MENSAGENS.relative_to(settings.ROOT_DIR)}")

    autores = cleaner.contar_autores(msgs)
    titulo("Participantes")
    for nome, qtd in autores.most_common():
        print(f"  {qtd:>6}  {nome}{'   ← parece telefone, defina um apelido' if cleaner.parece_telefone(nome) else ''}")

    # Acrescenta ao aliases.json os nomes que parecem telefone (para o usuário preencher)
    fones = [n for n in autores if cleaner.parece_telefone(n)]
    if fones:
        atual = json.loads(settings.ALIASES_FILE.read_text(encoding="utf-8")) if settings.ALIASES_FILE.exists() else {}
        novos = [f for f in fones if f not in atual]
        if novos:
            for f in novos:
                atual[f] = ""
            settings.ALIASES_FILE.write_text(json.dumps(atual, ensure_ascii=False, indent=2), encoding="utf-8")
            aviso(f"{len(novos)} número(s) adicionados em config/aliases.json — preencha os apelidos "
                  f"e rode `limpar` de novo.")
    return msgs


def cmd_chunks(args) -> None:
    titulo("Etapa 2 — Chunking")
    sel = obter_selecao(args)
    msgs = carregar_msgs_ou_sair()
    chunks = gerar_chunks_para(sel, msgs)
    destino = settings.PROCESSED_DIR / f"chunks_{sel.config['chunk_chars']}.jsonl"
    chunker.salvar_chunks(chunks, destino)
    tamanhos = [len(c.texto) for c in chunks]
    print(f"  Perfil {sel.perfil}: alvo {sel.config['chunk_chars']} chars, overlap {settings.OVERLAP_MSGS} msgs")
    print(f"  Chunks gerados   : \033[1m{len(chunks)}\033[0m")
    if chunks:
        print(f"  Tamanho (chars)  : mín {min(tamanhos)}, médio {sum(tamanhos) // len(tamanhos)}, máx {max(tamanhos)}")
        print(f"  ≈ tokens/chunk   : ~{sum(tamanhos) // len(tamanhos) // 3}")
    ok(f"Salvo em {destino.relative_to(settings.ROOT_DIR)}")


def cmd_piloto(args) -> None:
    titulo("Fase 3 — Teste piloto")
    sel = obter_selecao(args)
    mostrar_selecao(sel)
    garantir_modelo(sel, args.sim)
    msgs = carregar_msgs_ou_sair()
    chunks = gerar_chunks_para(sel, msgs)
    amostra = escolher_amostra(chunks, args.n, args.semente)
    saida = pasta_saida(sel)
    ckpt = saida / "piloto.jsonl"
    if ckpt.exists():
        ckpt.unlink()  # piloto é sempre do zero (serve para calibrar o prompt)
    print(f"  Amostra: chunks {[c.id for c in amostra]} de {len(chunks)}")
    resumo = rodar_map(amostra, sel, ckpt, verbose=not args.quieto)
    feitos = carregar_checkpoint(ckpt)
    md = [f"# Piloto — {sel.modelo}", ""]
    for c in amostra:
        reg = feitos.get(c.id)
        if not reg:
            continue
        md += [f"## Chunk {c.id} ({c.data_inicio[:10]} → {c.data_fim[:10]}, {reg['segundos']}s)", "",
               reg["resposta"] or "_NADA_RELEVANTE_", ""]
    (saida / "piloto.md").write_text("\n".join(md), encoding="utf-8")
    ok(f"{resumo['processados']} chunks, {resumo['relevantes']} relevantes, {resumo['erros']} erros.")
    ok(f"Resultado legível em {(saida / 'piloto.md').relative_to(settings.ROOT_DIR)}")
    if resumo["processados"]:
        media = sum(r["segundos"] for r in feitos.values()) / len(feitos)
        total_s = media * len(chunks)
        est = f"~{total_s / 3600:.1f} h" if total_s >= 3600 else f"~{max(1, round(total_s / 60))} min"
        print(f"  Tempo médio por chunk: {media:.0f}s → estimativa do MAP completo ({len(chunks)} chunks): {est}")
    print("  Calibre prompts/map_prompt.txt se vier coisa demais (ou de menos) e rode o piloto de novo.")


def cmd_map(args) -> None:
    titulo("Etapa 3 — MAP")
    sel = obter_selecao(args)
    mostrar_selecao(sel)
    garantir_modelo(sel, args.sim)
    msgs = carregar_msgs_ou_sair()
    chunks = gerar_chunks_para(sel, msgs)
    ckpt = pasta_saida(sel) / settings.NOME_ARQUIVO_MAP
    refazer = set(range(len(chunks))) if args.refazer else set()
    try:
        r = rodar_map(chunks, sel, ckpt, limite=args.limite, refazer_ids=refazer, verbose=args.verbose)
    except KeyboardInterrupt:
        print()
        aviso("Interrompido. O progresso está salvo — rode `map` de novo para continuar.")
        raise SystemExit(130)
    print(f"  Já feitos antes: {r['pulados']}  ·  processados agora: {r['processados']}  ·  "
          f"relevantes: {r['relevantes']}  ·  erros: {r['erros']}")
    feitos = carregar_checkpoint(ckpt)
    faltam = sum(1 for c in chunks if feitos.get(c.id, {}).get("hash") != c.hash)
    if faltam:
        aviso(f"Faltam {faltam} chunks. Rode `map` novamente para continuar.")
    else:
        ok(f"MAP completo ({len(chunks)} chunks). Próximo passo: python main.py reduce")


def cmd_reduce(args) -> None:
    titulo("Etapa 4 — REDUCE")
    sel = obter_selecao(args)
    mostrar_selecao(sel)
    garantir_modelo(sel, args.sim)
    msgs = carregar_msgs_ou_sair()
    chunks = gerar_chunks_para(sel, msgs)
    saida = pasta_saida(sel)
    por_mes, info = extracoes_por_mes(chunks, saida / settings.NOME_ARQUIVO_MAP)
    print(f"  Chunks com resultado: {info['com_resultado']}/{info['chunks']}  ·  relevantes: {info['relevantes']}")
    if info["faltando"]:
        aviso(f"{info['faltando']} chunks ainda sem MAP — o dossiê sairá incompleto.")
        if not confirmar("Continuar mesmo assim?", args.sim or args.parcial):
            raise SystemExit("Rode `python main.py map` até terminar (ou use --parcial).")
    if not por_mes:
        raise SystemExit("Nenhuma extração relevante para consolidar.")
    participantes = cleaner.contar_autores(msgs).most_common()
    reducer = Reducer(sel, saida / settings.NOME_ARQUIVO_MESES)
    texto, meta = reducer.dossie(por_mes, participantes)
    destino = saida / settings.NOME_ARQUIVO_DOSSIE
    destino.write_text(texto, encoding="utf-8")
    print(f"  Reduce {'hierárquico (por mês)' if meta['hierarquico'] else 'direto'}: "
          f"{meta['chars_brutos']} → {meta['chars_consolidados']} chars")
    ok(f"Dossiê salvo em {destino.relative_to(settings.ROOT_DIR)}")
    print("  Próximo passo: python main.py historia  (texto para a IA de música)")


def cmd_historia(args) -> None:
    titulo("Etapa 5 — Letra da música")
    sel = obter_selecao(args)
    mostrar_selecao(sel)
    saida = pasta_saida(sel)
    dossie = Path(args.dossie) if args.dossie else saida / settings.NOME_ARQUIVO_DOSSIE
    if not dossie.exists():
        raise SystemExit(f"Dossiê não encontrado em {dossie}. Rode primeiro: python main.py reduce "
                         f"(ou aponte outro com --dossie)")
    garantir_modelo(sel, args.sim)
    md = dossie.read_text(encoding="utf-8")
    alvo = args.bloco or sel.config["reduce_max_chars"] // 4
    blocos = blocos_do_dossie(md, alvo)
    if not blocos:
        raise SystemExit("O dossiê está vazio — nada para contar.")
    ckpt = saida / settings.NOME_ARQUIVO_HISTORIA_CKPT
    if args.refazer and ckpt.exists():
        ckpt.unlink()
    print(f"  Dossiê: {dossie}  ({len(md)} chars → {len(blocos)} blocos de ~{alvo} chars)")

    participantes = ""
    if ARQ_MENSAGENS.exists():
        participantes = ", ".join(n for n, _ in cleaner.contar_autores(cleaner.carregar_mensagens(ARQ_MENSAGENS))
                                  .most_common(40))
    periodo = periodo_do_dossie(md)
    escritor = Escritor(sel, ckpt)
    try:
        rascunho = escritor.rascunho(blocos, periodo, participantes or "(veja os nomes nas anotações)",
                                     verbose=args.verbose)
        if not rascunho:
            raise SystemExit("A IA não achou nada para contar no dossiê.")
        (saida / settings.NOME_ARQUIVO_RASCUNHO).write_text(
            f"# Rascunho da história — {periodo}\n\n{rascunho}\n", encoding="utf-8")
        print("Compondo a letra rimada (pode demorar alguns minutos)...")
        texto = escritor.final(rascunho, periodo, args.palavras)
    except RuntimeError as e:
        raise SystemExit(str(e))
    except KeyboardInterrupt:
        print()
        aviso("Interrompido. O progresso está salvo — rode `historia` de novo para continuar.")
        raise SystemExit(130)
    destino = saida / settings.NOME_ARQUIVO_HISTORIA
    destino.write_text(texto + "\n", encoding="utf-8")
    print(f"\n{texto}\n")
    print(f"  {len(texto.split())} palavras · {len(texto)} caracteres")
    ok(f"Rascunho completo em {(saida / settings.NOME_ARQUIVO_RASCUNHO).relative_to(settings.ROOT_DIR)}")
    ok(f"Letra para a IA de música em {destino.relative_to(settings.ROOT_DIR)}")


def cmd_rodar(args) -> None:
    """Comando único: conversa → limpeza → MAP → dossiê → letra da música."""
    if getattr(args, "conversa", None):
        args.arquivo = args.conversa
    sel = obter_selecao(args)
    titulo("Retrospectiva completa")
    mostrar_selecao(sel)
    print("  Todas as etapas usam este modelo. É retomável: se parar, rode o mesmo comando de novo.")
    sim_antes = args.sim
    args.sim = True  # sem perguntas no meio do caminho (baixa o modelo se faltar)
    try:
        cmd_limpar(args)
        cmd_chunks(args)
        cmd_map(args)
        cmd_reduce(args)
        cmd_historia(args)
    finally:
        args.sim = sim_antes  # no menu interativo, não vale para os próximos comandos

    saida = pasta_saida(sel)
    titulo("Pronto!")
    for nome, desc in [(settings.NOME_ARQUIVO_DOSSIE, "dossiê (resumo de tudo: linha do tempo, pessoas, piadas)"),
                       (settings.NOME_ARQUIVO_RASCUNHO, "rascunho da zoeira, bloco a bloco"),
                       (settings.NOME_ARQUIVO_HISTORIA, "letra da música, pronta para o Suno/Udio")]:
        arq = saida / nome
        if arq.exists():
            print(f"  {desc:<58} {arq.relative_to(settings.ROOT_DIR)}")


def cmd_resetar(args) -> None:
    sel = obter_selecao(args)
    saida = pasta_saida(sel)
    alvos = [saida / settings.NOME_ARQUIVO_MAP, saida / settings.NOME_ARQUIVO_MESES,
             saida / settings.NOME_ARQUIVO_HISTORIA_CKPT]
    if args.tudo:
        alvos += [saida / settings.NOME_ARQUIVO_DOSSIE, saida / "piloto.jsonl", saida / "piloto.md",
                  saida / settings.NOME_ARQUIVO_HISTORIA, saida / settings.NOME_ARQUIVO_RASCUNHO]
    existentes = [a for a in alvos if a.exists()]
    if not existentes:
        ok("Nada para apagar.")
        return
    print("Serão apagados:\n  " + "\n  ".join(str(a.relative_to(settings.ROOT_DIR)) for a in existentes))
    if confirmar("Confirmar?", args.sim):
        for a in existentes:
            a.unlink()
        ok("Checkpoints apagados.")


# ==========================================================================
# Menu interativo
# ==========================================================================
def menu(args) -> None:
    opcoes = [
        ("Status (hardware, servidor, modelo)", cmd_status),
        ("Setup (baixar modelo do perfil)", cmd_setup),
        ("1. Limpar conversa", cmd_limpar),
        ("2. Gerar chunks", cmd_chunks),
        ("3a. Piloto (8 chunks)", cmd_piloto),
        ("3b. MAP completo (retomável)", cmd_map),
        ("4. REDUCE → dossiê", cmd_reduce),
        ("5. História para a música", cmd_historia),
        ("Rodar tudo (conversa → dossiê → letra)", cmd_rodar),
        ("Trocar perfil (auto/teste/producao)", None),
    ]
    defaults = dict(arquivo=None, inicio=None, fim=None, ano=None, n=8, semente=42, quieto=False,
                    limite=None, refazer=False, verbose=False, parcial=False, tudo=False,
                    dossie=None, bloco=None, palavras=settings.HISTORIA_PALAVRAS)
    for k, v in defaults.items():
        if not hasattr(args, k):
            setattr(args, k, v)
    while True:
        print(f"\n\033[1;35mRetrospectiva WhatsApp\033[0m — perfil: {args.perfil}"
              f"{' / modelo: ' + args.modelo if args.modelo else ''}")
        for i, (rot, _) in enumerate(opcoes, 1):
            print(f"  {i}) {rot}")
        print("  0) Sair")
        esc = input("> ").strip()
        if esc in {"0", "q", "sair", ""}:
            return
        if not esc.isdigit() or not 1 <= int(esc) <= len(opcoes):
            continue
        rot, fn = opcoes[int(esc) - 1]
        if fn is None:
            p = input("Perfil [auto/teste/producao]: ").strip() or "auto"
            if p in {"auto", *settings.PERFIS}:
                args.perfil = p
            continue
        try:
            fn(args)
        except SystemExit as e:
            if e.code not in (0, None):
                erro(str(e.code))
        except KeyboardInterrupt:
            print()
        except Exception as e:  # noqa: BLE001
            erro(f"{type(e).__name__}: {e}")


# ==========================================================================
# Argparse
# ==========================================================================
def construir_parser() -> argparse.ArgumentParser:
    def fazer_comum(sub: bool) -> argparse.ArgumentParser:
        # Nos subcomandos o default é SUPPRESS, senão eles sobrescrevem
        # flags passadas antes do subcomando (ex.: `--perfil teste status`).
        d = (lambda v: argparse.SUPPRESS) if sub else (lambda v: v)
        c = argparse.ArgumentParser(add_help=False)
        g = c.add_argument_group("seleção de modelo")
        g.add_argument("--perfil", default=d(settings.PERFIL_PADRAO), choices=["auto", *settings.PERFIS],
                       help="auto (padrão) detecta pela VRAM; ou force teste/producao")
        g.add_argument("-m", "--modelo", default=d(None),
                       help="modelo do Ollama usado em todas as etapas (ex.: gemma3:12b)")
        g.add_argument("--host", default=d(settings.OLLAMA_HOST),
                       help="URL do Ollama (padrão: $OLLAMA_HOST ou localhost)")
        g.add_argument("--vram", type=float, default=d(None), help="VRAM em GB (útil para servidor remoto)")
        g.add_argument("-y", "--sim", action="store_true", default=d(False), help="responde 'sim' às confirmações")
        return c

    comum = fazer_comum(sub=True)

    filtro = argparse.ArgumentParser(add_help=False)
    f = filtro.add_argument_group("entrada")
    f.add_argument("--arquivo", help="caminho do .txt (padrão: único .txt em data/raw)")
    f.add_argument("--inicio", type=parse_data, help="data inicial dd/mm/aaaa")
    f.add_argument("--fim", type=parse_data, help="data final dd/mm/aaaa")
    f.add_argument("--ano", type=int, help="atalho: só mensagens deste ano")

    p = argparse.ArgumentParser(prog="retro", description="Retrospectiva do WhatsApp com IA local (Ollama)",
                                parents=[fazer_comum(sub=False)], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__)
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", parents=[comum], help="hardware, servidor e modelo selecionado").set_defaults(fn=cmd_status)
    sub.add_parser("setup", parents=[comum], help="baixa o modelo do perfil").set_defaults(fn=cmd_setup)
    sub.add_parser("limpar", parents=[comum, filtro], help="etapa 1: limpeza").set_defaults(fn=cmd_limpar)
    sub.add_parser("chunks", parents=[comum], help="etapa 2: chunking").set_defaults(fn=cmd_chunks)

    sp = sub.add_parser("piloto", parents=[comum], help="teste piloto em poucos chunks")
    sp.add_argument("-n", type=int, default=8, help="quantidade de chunks (padrão 8)")
    sp.add_argument("--semente", type=int, default=42, help="muda a amostra sorteada")
    sp.add_argument("--quieto", action="store_true", help="não imprime as respostas")
    sp.set_defaults(fn=cmd_piloto)

    sm = sub.add_parser("map", parents=[comum], help="etapa 3: extração (retomável)")
    sm.add_argument("--limite", type=int, help="processa no máximo N chunks nesta rodada")
    sm.add_argument("--refazer", action="store_true", help="ignora o checkpoint e refaz tudo")
    sm.add_argument("-v", "--verbose", action="store_true", help="imprime cada resposta")
    sm.set_defaults(fn=cmd_map)

    sr = sub.add_parser("reduce", parents=[comum], help="etapa 4: consolidação e dossiê")
    sr.add_argument("--parcial", action="store_true", help="gera o dossiê mesmo com MAP incompleto")
    sr.set_defaults(fn=cmd_reduce)

    sh = sub.add_parser("historia", parents=[comum], help="etapa 5: texto narrativo para IA de música")
    sh.add_argument("--dossie", help="caminho do dossiê (padrão: o do modelo selecionado)")
    sh.add_argument("--palavras", type=int, default=settings.HISTORIA_PALAVRAS,
                    help=f"tamanho máximo do texto final (padrão {settings.HISTORIA_PALAVRAS})")
    sh.add_argument("--bloco", type=int, help="tamanho dos blocos lidos do dossiê, em caracteres")
    sh.add_argument("--refazer", action="store_true", help="ignora o checkpoint e escreve do zero")
    sh.add_argument("-v", "--verbose", action="store_true", help="imprime o trecho escrito a cada bloco")
    sh.set_defaults(fn=cmd_historia)

    sa = sub.add_parser("rodar", aliases=["musica"], parents=[comum, filtro],
                        help="COMANDO ÚNICO: conversa → dossiê → letra da música",
                        description="Recebe a conversa exportada (.txt ou .zip), limpa, extrai, gera o dossiê "
                                    "e compõe a letra. Ex.: retro musica chat.txt --modelo qwen2.5:14b --ano 2026")
    sa.add_argument("conversa", nargs="?", help="o .txt ou .zip exportado (padrão: o que estiver em data/raw)")
    sa.add_argument("--limite", type=int, help=argparse.SUPPRESS)
    sa.add_argument("--refazer", action="store_true", help="refaz o MAP e a letra do zero")
    sa.add_argument("-v", "--verbose", action="store_true", help="imprime as respostas da IA")
    sa.add_argument("--parcial", action="store_true", help=argparse.SUPPRESS)
    sa.add_argument("--palavras", type=int, default=settings.HISTORIA_PALAVRAS,
                    help=f"tamanho máximo da letra (padrão {settings.HISTORIA_PALAVRAS})")
    sa.set_defaults(dossie=None, bloco=None)
    sa.set_defaults(fn=cmd_rodar)

    sx = sub.add_parser("resetar", parents=[comum], help="apaga checkpoints do modelo selecionado")
    sx.add_argument("--tudo", action="store_true", help="apaga também dossiê, piloto e história")
    sx.set_defaults(fn=cmd_resetar)
    return p


def main(argv: list[str] | None = None) -> None:
    args = construir_parser().parse_args(argv)
    if not getattr(args, "fn", None):
        menu(args)
        return
    try:
        args.fn(args)
    except OllamaErro as e:
        erro(str(e))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
