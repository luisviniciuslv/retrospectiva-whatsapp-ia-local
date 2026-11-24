"""Configurações centrais do pipeline de retrospectiva.

Quase tudo aqui pode ser sobrescrito por variáveis de ambiente (útil na
máquina de produção) ou por flags da CLI (`python main.py --help`).
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Caminhos
# --------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "output"
PROMPTS_DIR = ROOT_DIR / "prompts"
ALIASES_FILE = CONFIG_DIR / "aliases.json"

# --------------------------------------------------------------------------
# Servidor Ollama
# --------------------------------------------------------------------------
# Na produção, aponte para a máquina parruda: OLLAMA_HOST=http://192.168.0.50:11434
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
REQUEST_TIMEOUT = int(os.environ.get("RETRO_TIMEOUT", "900"))  # segundos por chamada
MAX_RETRIES = 3
KEEP_ALIVE = "30m"

# --------------------------------------------------------------------------
# Perfis de modelo
# --------------------------------------------------------------------------
# A seleção automática escolhe o perfil de MAIOR `vram_min_gb` que caiba na
# VRAM detectada. Dentro do perfil, usa o primeiro candidato já instalado no
# servidor Ollama; se nenhum estiver instalado, usa o primeiro da lista
# (e oferece baixá-lo).
PERFIS: dict[str, dict] = {
    "teste": {
        "descricao": "Modelo leve para testar o pipeline (CPU ou GPU modesta)",
        "vram_min_gb": 0,
        "modelos": ["qwen2.5:3b", "llama3.2:3b", "qwen2.5:1.5b"],
        "num_ctx_map": 8192,
        "num_ctx_reduce": 8192,
        "num_predict_map": 900,
        "num_predict_reduce": 2500,
        "temperature": 0.2,
        "chunk_chars": 8000,
        "reduce_max_chars": 12000,  # acima disso, o reduce vira hierárquico
    },
    "producao": {
        "descricao": "Modelo parrudo para o processamento final (GPU 12GB+ VRAM)",
        "vram_min_gb": 12,
        "modelos": ["qwen2.5:14b", "gemma3:12b", "qwen3:14b"],
        "num_ctx_map": 8192,
        "num_ctx_reduce": 12288,
        "num_predict_map": 1200,
        "num_predict_reduce": 4000,
        "temperature": 0.25,
        "chunk_chars": 10000,
        "reduce_max_chars": 24000,
    },
}
PERFIL_PADRAO = os.environ.get("RETRO_PERFIL", "auto")  # auto | teste | producao

# --------------------------------------------------------------------------
# Limpeza e chunking
# --------------------------------------------------------------------------
OVERLAP_MSGS = 12            # mensagens do chunk anterior repetidas como contexto
MAX_MSG_CHARS = 1500         # mensagens gigantes (textões, copypastas) são cortadas
NOME_ARQUIVO_MENSAGENS = "mensagens.jsonl"
NOME_ARQUIVO_CHUNKS = "chunks.jsonl"
NOME_ARQUIVO_MAP = "map_results.jsonl"
NOME_ARQUIVO_MESES = "reduce_meses.jsonl"
NOME_ARQUIVO_DOSSIE = "dossie_retrospectiva.md"
NOME_ARQUIVO_HISTORIA = "letra_musica.txt"             # letra final, pronta para colar
NOME_ARQUIVO_RASCUNHO = "historia_rascunho.md"         # história completa, bloco a bloco
NOME_ARQUIVO_HISTORIA_CKPT = "historia_passos.jsonl"   # checkpoint (retomável)
HISTORIA_PALAVRAS = 400     # tamanho máximo da letra final
