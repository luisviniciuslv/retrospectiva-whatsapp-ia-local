"""Wrapper da API HTTP do Ollama + seleção dinâmica de perfil/modelo."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from config import settings

RE_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OllamaErro(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Detecção de hardware
# --------------------------------------------------------------------------
@dataclass
class InfoGPU:
    nome: str
    vram_total_gb: float
    vram_livre_gb: float


def detectar_gpus() -> list[InfoGPU]:
    """Lê VRAM via nvidia-smi (só NVIDIA). Lista vazia se não houver."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    gpus = []
    for linha in out.strip().splitlines():
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) == 3:
            try:
                gpus.append(InfoGPU(partes[0], float(partes[1]) / 1024, float(partes[2]) / 1024))
            except ValueError:
                pass
    return gpus


def host_eh_local(host: str) -> bool:
    nome = urlparse(host if "://" in host else f"http://{host}").hostname or ""
    return nome in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


# --------------------------------------------------------------------------
# Cliente
# --------------------------------------------------------------------------
class OllamaClient:
    def __init__(self, host: str = settings.OLLAMA_HOST, timeout: int = settings.REQUEST_TIMEOUT):
        self.host = host.rstrip("/") if "://" in host else f"http://{host.rstrip('/')}"
        self.timeout = timeout

    def online(self) -> bool:
        try:
            return requests.get(f"{self.host}/api/version", timeout=5).ok
        except requests.RequestException:
            return False

    def versao(self) -> str | None:
        try:
            return requests.get(f"{self.host}/api/version", timeout=5).json().get("version")
        except (requests.RequestException, ValueError):
            return None

    def modelos_instalados(self) -> list[str]:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            raise OllamaErro(f"Não consegui falar com o Ollama em {self.host}: {e}") from e
        return [m["name"] for m in r.json().get("models", [])]

    def baixar_modelo(self, modelo: str, progresso=None) -> None:
        """`ollama pull` via API, com callback de progresso (status, completed, total)."""
        try:
            r = requests.post(f"{self.host}/api/pull", json={"model": modelo, "stream": True},
                              stream=True, timeout=None)
        except requests.RequestException as e:
            raise OllamaErro(f"Não consegui falar com o Ollama em {self.host}: {e}") from e
        with r:
            if not r.ok:
                try:
                    detalhe = r.json().get("error", r.text)
                except ValueError:
                    detalhe = r.text
                raise OllamaErro(f"O Ollama não conseguiu baixar '{modelo}': {detalhe.strip()}. "
                                 f"Confira o nome em https://ollama.com/search (ex.: usuario/modelo:tag).")
            for linha in r.iter_lines():
                if not linha:
                    continue
                evt = json.loads(linha)
                if "error" in evt:
                    raise OllamaErro(f"O Ollama não conseguiu baixar '{modelo}': {evt['error']}. "
                                     f"Confira o nome em https://ollama.com/search (ex.: usuario/modelo:tag).")
                if progresso:
                    progresso(evt.get("status", ""), evt.get("completed"), evt.get("total"))

    def chat(self, modelo: str, prompt: str, *, system: str | None = None, temperature: float = 0.2,
             num_ctx: int = 8192, num_predict: int = 1024, think: bool | None = None) -> str:
        mensagens = []
        if system:
            mensagens.append({"role": "system", "content": system})
        mensagens.append({"role": "user", "content": prompt})
        payload = {
            "model": modelo,
            "messages": mensagens,
            "stream": True,
            "keep_alive": settings.KEEP_ALIVE,
            "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict},
        }
        if think is not None:
            payload["think"] = think

        ultimo_erro: Exception | None = None
        for tentativa in range(1, settings.MAX_RETRIES + 1):
            try:
                partes: list[str] = []
                with requests.post(f"{self.host}/api/chat", json=payload, stream=True,
                                   timeout=(10, self.timeout)) as r:
                    if r.status_code == 404:
                        raise OllamaErro(f"Modelo '{modelo}' não encontrado no servidor. "
                                         f"Rode: python main.py setup")
                    r.raise_for_status()
                    for linha in r.iter_lines():
                        if not linha:
                            continue
                        evt = json.loads(linha)
                        if "error" in evt:
                            raise OllamaErro(evt["error"])
                        partes.append(evt.get("message", {}).get("content", ""))
                        if evt.get("done"):
                            break
                return RE_THINK.sub("", "".join(partes)).strip()
            except OllamaErro:
                raise
            except (requests.RequestException, ValueError) as e:
                ultimo_erro = e
                time.sleep(3 * tentativa)
        raise OllamaErro(f"Falha após {settings.MAX_RETRIES} tentativas: {ultimo_erro}")


# --------------------------------------------------------------------------
# Seleção dinâmica de perfil e modelo
# --------------------------------------------------------------------------
@dataclass
class Selecao:
    perfil: str
    modelo: str
    config: dict
    motivo: str
    instalado: bool
    host: str
    vram_gb: float | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        """Nome de pasta seguro para separar saídas por modelo."""
        return re.sub(r"[^A-Za-z0-9._-]+", "-", self.modelo).strip("-")


def _perfil_por_vram(vram_gb: float) -> str:
    candidatos = sorted(settings.PERFIS.items(), key=lambda kv: kv[1]["vram_min_gb"], reverse=True)
    for nome, cfg in candidatos:
        # placa "de 12 GB" reporta 12282 MiB = 11,99 GB: folga de 0,5 GB para não cair de perfil
        if vram_gb + 0.5 >= cfg["vram_min_gb"]:
            return nome
    return candidatos[-1][0]


def _casa_modelo(candidato: str, instalados: list[str]) -> str | None:
    """'qwen2.5:14b' casa com 'qwen2.5:14b'; 'gemma3' casa com 'gemma3:latest'."""
    if candidato in instalados:
        return candidato
    if ":" not in candidato and f"{candidato}:latest" in instalados:
        return f"{candidato}:latest"
    return None


def selecionar(perfil: str = "auto", modelo: str | None = None, host: str = settings.OLLAMA_HOST,
               vram_gb: float | None = None, cliente: OllamaClient | None = None) -> Selecao:
    cliente = cliente or OllamaClient(host)
    avisos: list[str] = []
    if modelo:
        # no Windows é comum digitar "usuario\modelo"; o Ollama só aceita "usuario/modelo"
        modelo = modelo.strip().strip("\"'").replace("\\", "/")
    try:
        instalados = cliente.modelos_instalados()
    except OllamaErro as e:
        instalados = []
        avisos.append(str(e))

    # 1) Perfil
    if perfil != "auto":
        if perfil not in settings.PERFIS:
            raise ValueError(f"Perfil desconhecido: {perfil}. Opções: {', '.join(settings.PERFIS)}, auto")
        motivo = f"perfil '{perfil}' escolhido manualmente"
    elif vram_gb is not None:
        perfil = _perfil_por_vram(vram_gb)
        motivo = f"VRAM informada = {vram_gb:.1f} GB"
    elif host_eh_local(cliente.host):
        gpus = detectar_gpus()
        if gpus:
            vram_gb = max(g.vram_total_gb for g in gpus)
            perfil = _perfil_por_vram(vram_gb)
            nome = max(gpus, key=lambda g: g.vram_total_gb).nome
            motivo = f"GPU local {nome} com {vram_gb:.1f} GB de VRAM"
        else:
            perfil = _perfil_por_vram(0)
            motivo = "nenhuma GPU NVIDIA detectada (rodando em CPU)"
    else:
        # Servidor remoto: não dá pra ler VRAM pela API do Ollama.
        # Se o modelo de produção já estiver lá, assume que a máquina aguenta.
        perfil_top = max(settings.PERFIS, key=lambda p: settings.PERFIS[p]["vram_min_gb"])
        if any(_casa_modelo(c, instalados) for c in settings.PERFIS[perfil_top]["modelos"]):
            perfil = perfil_top
            motivo = f"servidor remoto já tem um modelo do perfil '{perfil_top}'"
        else:
            perfil = _perfil_por_vram(0)
            motivo = "servidor remoto sem modelo de produção instalado (use --vram ou --perfil)"
    cfg = dict(settings.PERFIS[perfil])

    # 2) Modelo
    if modelo:
        escolhido = _casa_modelo(modelo, instalados) or modelo
        motivo += f"; modelo '{modelo}' forçado"
    else:
        escolhido = next((m for c in cfg["modelos"] if (m := _casa_modelo(c, instalados))), cfg["modelos"][0])
    instalado = _casa_modelo(escolhido, instalados) is not None
    if not instalado and instalados is not None:
        avisos.append(f"O modelo '{escolhido}' ainda não está instalado em {cliente.host}.")

    return Selecao(perfil=perfil, modelo=escolhido, config=cfg, motivo=motivo, instalado=instalado,
                   host=cliente.host, vram_gb=vram_gb, avisos=avisos)
