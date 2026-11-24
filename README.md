# 🎤 Retrospectiva do WhatsApp com IA local

Transforme o histórico de um grupo do WhatsApp em um **Dossiê de Retrospectiva**: a linha do
tempo do ano, o que marcou cada pessoa, as piadas internas e as frases mais icônicas. O dossiê é
a matéria-prima pronta para compor aquela música (ou vídeo, ou discurso) de fim de ano.

- 🔒 **100% local:** a conversa nunca sai da sua máquina. A IA roda via [Ollama](https://ollama.com).
- ⏸️ **Retomável:** o progresso é salvo a cada bloco. Pode interromper e continuar depois.
- 🧠 **Modelo escolhido sozinho:** usa um modelo leve para testes e um parrudo (GPU 12 GB+) para o resultado final.
- 💸 **Custo zero:** só ferramentas open-source.

```
chat.txt ─▶ 1. limpar ─▶ 2. chunks ─▶ 3. MAP (IA lê bloco a bloco) ─▶ 4. REDUCE (IA consolida) ─▶ dossie_retrospectiva.md
```

---

## 1. Requisitos

| O quê | Versão | Observação |
| :--- | :--- | :--- |
| Python | 3.10+ | |
| [Ollama](https://ollama.com/download) | recente | Windows, macOS ou Linux |
| GPU | opcional | Sem GPU funciona (mais devagar) com o perfil `teste` |

## 2. Instalação

**Windows (PowerShell)**
```powershell
git clone https://github.com/luisviniciuslv/retrospectiva-whatsapp-ia-local.git
cd retrospectiva-whatsapp-ia-local
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
winget install Ollama.Ollama      # ou baixe em https://ollama.com/download
```

**Linux / macOS**
```bash
git clone https://github.com/luisviniciuslv/retrospectiva-whatsapp-ia-local.git
cd retrospectiva-whatsapp-ia-local
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
curl -fsSL https://ollama.com/install.sh | sh
chmod +x retro
```

Os exemplos abaixo usam `.\retro.cmd` (Windows). No Linux/macOS, troque por `./retro`.

## 3. Exportar a conversa

No WhatsApp, abra o grupo e vá em **⋮ / nome do grupo → Mais → Exportar conversa → Sem mídia**.
Salve o `.txt` (no iPhone ele vem dentro de um `.zip`; extraia) e coloque em **`data/raw/`**.

Os formatos Android (`24/11/2026 17:05 - Nome: msg`) e iOS (`[11/24/26, 5:05:33 PM] Nome: msg`),
em português ou inglês, são detectados automaticamente.

## 4. Passo a passo

```powershell
.\retro.cmd setup              # confere o Ollama e baixa o modelo certo para a sua máquina
.\retro.cmd limpar --ano 2026  # limpa a conversa e lista os participantes
.\retro.cmd piloto -n 8        # testa a IA em 8 trechos espalhados (rápido)
.\retro.cmd map                # processa tudo (pode levar horas; Ctrl+C e rode de novo para continuar)
.\retro.cmd reduce             # gera o dossiê
```

Ou tudo de uma vez: `.\retro.cmd rodar --ano 2026 -y`. Rodando `.\retro.cmd` sem nada, abre um **menu interativo**.

O resultado fica em `data/output/<modelo>/dossie_retrospectiva.md`.

### Comandos

| Comando | O que faz |
| :--- | :--- |
| `status` | Mostra a GPU, o servidor Ollama, o perfil/modelo escolhido e o progresso |
| `setup` | Baixa o modelo do perfil selecionado |
| `limpar` | Etapa 1: remove mídia, mensagens de sistema e apagadas, troca links por `[LINK]` e aplica apelidos. Aceita `--ano`, `--inicio`, `--fim` (dd/mm/aaaa) e `--arquivo` |
| `chunks` | Etapa 2: mostra quantos blocos serão enviados à IA |
| `piloto -n N` | Roda a IA em N blocos e salva `piloto.md` para você avaliar. Também estima o tempo total |
| `map` | Etapa 3: extração completa e retomável (`--limite N`, `--refazer`, `-v`) |
| `reduce` | Etapa 4: consolida por mês e gera o dossiê (`--parcial` para gerar antes de o MAP terminar) |
| `rodar` | Todas as etapas em sequência |
| `resetar` | Apaga os checkpoints do modelo atual (`--tudo` inclui dossiê e piloto) |

Opções que valem para todos: `--perfil`, `--modelo`, `--host`, `--vram` e `-y` (responde "sim" a tudo).

## 5. Qual modelo é usado?

Com `--perfil auto` (o padrão), a VRAM da GPU é lida via `nvidia-smi`:

| Perfil | Quando | Modelos (usa o 1º que estiver instalado) |
| :--- | :--- | :--- |
| `teste` | VRAM < 12 GB ou sem GPU | `qwen2.5:3b`, `llama3.2:3b`, `qwen2.5:1.5b` |
| `producao` | VRAM ≥ 12 GB | `qwen2.5:14b`, `gemma3:12b`, `qwen3:14b` |

- Forçar: `--perfil producao`, `--modelo gemma3:12b` ou a variável `RETRO_PERFIL`.
- Cada modelo grava em sua própria pasta (`data/output/qwen2.5-3b/`, `data/output/qwen2.5-14b/`), então os testes não se misturam com o resultado final.
- Perfis, tamanho dos blocos, temperatura e contexto ficam em [`config/settings.py`](config/settings.py).

> 💡 O perfil `teste` serve para validar o fluxo; ele erra autores e traz pouca coisa.
> Para o dossiê de verdade, use o `producao`. Com 8 GB de VRAM, `--modelo qwen2.5:7b` é um bom meio-termo.

### Usando outra máquina como servidor de IA

Se a GPU boa estiver em outro computador da rede:

1. Lá: `OLLAMA_HOST=0.0.0.0 ollama serve` (libere a porta 11434 no firewall).
2. Aqui: `.\retro.cmd --host http://IP-DA-MAQUINA:11434 --perfil producao setup`, e depois `map` e `reduce` com os mesmos flags.

Para um servidor remoto, a VRAM não é detectável pela API. Nesse caso, o `auto` escolhe `producao`
se um modelo de produção já estiver instalado lá. Também dá para informar com `--vram 16`.
Atenção: nesse modo, o texto da conversa trafega pela sua rede local.

## 6. Apelidos (`config/aliases.json`)

Para trocar números sem nome ou nomes confusos pelo apelido do grupo:

```powershell
copy config\aliases.example.json config\aliases.json
```
```json
{
  "Você": "Seu Nome",
  "+55 11 98888-7777": "Zé"
}
```

- Só o **autor** da mensagem é trocado; o texto não muda (um "você" no meio da frase continua "você").
- `"Você"` é como o WhatsApp chama quem exportou a conversa. Coloque o seu nome para a IA não confundir.
- O `limpar` adiciona sozinho os autores que parecem telefone, com valor vazio. Preencha e rode `limpar` de novo.

## 7. Calibrando o resultado

1. Rode `piloto -n 8` e leia `data/output/<modelo>/piloto.md`.
2. Ajuste [`prompts/map_prompt.txt`](prompts/map_prompt.txt): deixe o filtro mais rígido se vier coisa demais, ou afrouxe se estiver ignorando piadas.
3. Rode o piloto de novo (ele sempre recomeça do zero) e repita até gostar.
4. Os prompts de consolidação são [`reduce_mes_prompt.txt`](prompts/reduce_mes_prompt.txt) e [`reduce_prompt.txt`](prompts/reduce_prompt.txt).

O dossiê inclui anexos com quem mais falou e as anotações brutas de cada mês, para a curadoria humana final.

## 8. Privacidade

O `.gitignore` já impede que você publique sem querer `data/` (conversa, mensagens limpas, resultados),
`config/aliases.json` e qualquer `.txt` fora das pastas de prompts e exemplos. Mesmo assim, **nunca
suba o export da conversa** para um repositório.

## 9. Problemas comuns

| Sintoma | Solução |
| :--- | :--- |
| `O Ollama não respondeu` | Abra o app Ollama (Windows/macOS) ou rode `ollama serve` (Linux) |
| `Nenhuma linha no formato do WhatsApp` | Confira se é o `.txt` exportado "sem mídia" e não o `.zip` |
| Datas trocadas (dia ↔ mês) | A ordem é detectada pelos valores maiores que 12. Arquivos muito curtos podem enganar. Use um período maior |
| Muito lento | Use o perfil `teste` para experimentar, ou uma GPU maior. O `map` é retomável e pode rodar a noite toda |
| Mudei filtros/apelidos | Rode `limpar` de novo. Só os blocos que mudaram são reprocessados no `map` |

## 10. Teste sem a sua conversa

Existe um chat fictício em `exemplos/`:
```powershell
.\retro.cmd limpar --arquivo exemplos\exemplo_chat.txt
.\retro.cmd map
.\retro.cmd reduce
```

Testes automatizados (não usam IA): `python -m unittest discover tests`

## Estrutura

```
config/      settings.py (perfis e parâmetros) · aliases.example.json
prompts/     map_prompt.txt · reduce_mes_prompt.txt · reduce_prompt.txt
src/         cleaner.py · chunker.py · llm_client.py (Ollama + seleção de modelo) · map_phase.py · reduce_phase.py
data/        raw/ (seu .txt) · processed/ · output/   ← ignorados pelo git
docs/        plano de projeto original
main.py      CLI · retro.cmd / retro (atalhos)
```

Licença: MIT.
