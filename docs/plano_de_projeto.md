# Plano de Projeto: Extrator de Retrospectiva do WhatsApp com IA Local

## 1. Visão Geral e Objetivo
Desenvolver um sistema **100% local** em Python integrado a um modelo de linguagem (LLM) rodando via **Ollama**. O sistema receberá um arquivo `.txt` exportado de um grupo do WhatsApp, processará a conversa em blocos (*chunks*) e extrairá os momentos mais marcantes, piadas internas, bordões e acontecimentos do período.

O resultado final será um **Dossiê de Retrospectiva** estruturado e limpo, pronto para ser usado posteriormente (fora deste projeto) como matéria-prima para a composição de uma música.

### Princípios do Projeto
* **Privacidade Total:** Nenhum dado da conversa sai da máquina local.
* **Resiliência (Checkpoint):** Como conversas de um ano inteiro podem levar horas para processar localmente, o sistema deve salvar o progresso a cada bloco processado, permitindo pausar e retomar sem perda de trabalho.
* **Custo Zero:** Baseado exclusivamente em ferramentas open-source.

---

## 2. Arquitetura do Sistema (Padrão Map-Reduce)

O fluxo de dados segue um pipeline linear dividido em 5 etapas:

```text
[chat_whatsapp.txt]
        │
        ▼
1. Pré-processamento & Limpeza (Regex + Filtros + Padronização de Nomes)
        │
        ▼
2. Chunking Inteligente (Blocos de tamanho controlado + Overlap de contexto)
        │
        ▼
3. Extração — Etapa MAP (LLM Local analisa chunk por chunk + Checkpoint)
        │
        ▼
4. Consolidação — Etapa REDUCE (Agrupamento por mês/tema + Remoção de duplicatas)
        │
        ▼
[dossie_retrospectiva.md] ──▶ (Uso externo: criação da letra da música)
```

---

## 3. Stack Tecnológica e Requisitos

| Camada | Tecnologia | Função |
| :--- | :--- | :--- |
| **Linguagem** | Python 3.10+ | Orquestração do pipeline, manipulação de texto e chamadas de API. |
| **Motor de IA** | **Ollama** | Execução do LLM localmente via servidor HTTP (`localhost:11434`). |
| **Bibliotecas Python** | `requests` (ou `ollama`), `re`, `json`, `tqdm` | Comunicação com a IA, limpeza via Regex, checkpoints e barra de progresso. |

### Escolha do Modelo Local (PT-BR)
A escolha depende da memória de vídeo (VRAM) da placa de vídeo ou da RAM disponível:
* **Hardware Modesto (CPU ou GPU até 6GB VRAM):** `qwen2.5:7b` (quantização padrão Q4) ou `llama3.2:3b` (mais rápido, porém menos atento a nuances).
* **Hardware Intermediário (GPU 8GB a 12GB VRAM) — *Recomendado*:** `qwen2.5:7b`, `gemma2:9b` ou `llama3.1:8b`. Excelente compreensão de português informal e gírias.
* **Hardware Avançado (GPU 16GB+ VRAM):** `qwen2.5:14b`. Melhor capacidade de identificar ironia, contexto implícito e piadas internas.

---

## 4. Estrutura de Pastas do Projeto

```text
retrospectiva-wpp/
├── config/
│   ├── settings.py          # Configurações (modelo, temperatura, tamanho do chunk)
│   └── aliases.json         # (Opcional) Mapeia números/apelidos para nomes reais
├── data/
│   ├── raw/                 # Colocar o arquivo original exportado do WhatsApp (.txt)
│   ├── processed/           # Mensagens limpas e chunks gerados
│   └── output/              # Checkpoints (.jsonl) e o Dossiê Final (.md)
├── prompts/
│   ├── map_prompt.txt       # Prompt de extração por chunk
│   └── reduce_prompt.txt    # Prompt de consolidação final
├── src/
│   ├── cleaner.py           # Limpeza, parser de datas e remoção de ruído
│   ├── chunker.py           # Divisão em blocos com overlap
│   ├── llm_client.py        # Wrapper de chamada para a API do Ollama
│   ├── map_phase.py         # Loop de extração com salvamento incremental
│   └── reduce_phase.py      # Consolidação hierárquica e formatação final
├── main.py                  # Ponto de entrada (CLI simples para rodar etapas)
└── requirements.txt
```

---

## 5. Detalhamento das Etapas do Pipeline

### Etapa 1: Ingestão e Limpeza (`cleaner.py`)
O arquivo exportado do WhatsApp varia ligeiramente entre Android e iOS e contém muito ruído que consome tokens à toa.
* **Parser de Mensagens:** Usar Regex para identificar o padrão de data/hora (`DD/MM/AAAA HH:MM - Autor: Mensagem` ou `[DD/MM/AA, HH:MM:SS] Autor: Mensagem`).
* **Tratamento de Multilinhas:** Linhas que não começam com timestamp devem ser anexadas à mensagem anterior.
* **Filtro de Período:** Permitir definir `data_inicio` e `data_fim` (ex: apenas o ano corrente).
* **Remoção de Ruído:**
  * Descartar mensagens de sistema (`entrou usando o link`, `mudou a imagem do grupo`, `Mensagens e ligações são protegidas...`).
  * Descartar `<Mídia oculta>`, `Mensagem apagada`, `figurinha omitida`.
  * Substituir URLs longas por `[LINK]` para economizar contexto.
* **Padronização de Nomes (`aliases.json`):** Substituir números de telefone não salvos ou nomes confusos pelo apelido pelo qual a pessoa é conhecida no grupo.

### Etapa 2: Chunking Inteligente (`chunker.py`)
Dividir a conversa limpa em blocos que o LLM consiga ler com alta atenção sem "esquecer" o meio do texto.
* **Tamanho Alvo:** Entre **8.000 e 12.000 caracteres** por chunk (aproximadamente 2.000 a 3.000 tokens).
* **Overlap (Sobreposição):** Incluir as últimas **10 a 15 mensagens** do chunk anterior no início do chunk atual. Isso impede que uma piada ou história que começou no final de um bloco perca o sentido no bloco seguinte.
* **Metadados:** Cada chunk guarda `id`, `data_inicio`, `data_fim` e o texto formatado.

### Etapa 3: Extração — Fase MAP (`map_phase.py`)
Cada chunk é enviado individualmente ao Ollama.
* **Parâmetros do Modelo:**
  * `temperature`: `0.2` a `0.3` (evita criatividade excessiva e alucinações).
  * `num_ctx`: `4096` ou `8192` (garante folga para prompt + chunk + resposta).
* **Critérios de Extração:** O prompt instrui a IA a buscar apenas:
  1. **Eventos marcantes:** viagens, festas, encontros, conquistas, mudanças.
  2. **Perrengues e histórias engraçadas:** situações absurdas ou cômicas relatadas.
  3. **Piadas internas e memes:** assuntos que geram muitas risadas ou voltam a ser citados.
  4. **Bordões e pérolas:** frases icônicas (sempre preservando **quem** falou).
* **Descarte de Irrelevância:** Se o bloco contiver apenas conversas cotidianas ("bom dia", combinações triviais de horário), a IA deve responder estritamente `NADA_RELEVANTE`.
* **Sistema de Checkpoint:** Cada resposta é gravada imediatamente em `data/output/map_results.jsonl` vinculada ao `chunk_id`. Ao reiniciar o script, chunks já presentes no arquivo são pulados automaticamente.

### Etapa 4: Consolidação — Fase REDUCE (`reduce_phase.py`)
Filtra todos os blocos marcados como `NADA_RELEVANTE` e une os resumos válidos.
* **Reduce Hierárquico (se necessário):** Se o volume de extrações ainda for grande demais para uma única chamada de contexto (ex: mais de 20 páginas de anotações), o script agrupa as extrações **por mês**, pede para a IA sintetizar cada mês e, por fim, faz uma síntese geral do ano.
* **Formato do Dossiê Final (`dossie_retrospectiva.md`):**
  * **Linha do Tempo:** Os grandes acontecimentos organizados mês a mês.
  * **Destaque por Participante:** O que marcou o ano de cada integrante do grupo.
  * **Dicionário de Piadas Internas & Bordões:** Termos que só o grupo entende.
  * **Banco de Pérolas (Citações Diretas):** Frases prontas que podem virar versos ou refrão da música.

---

## 6. Esboço dos Prompts

### Prompt da Fase MAP (`map_prompt.txt`)
```text
Você é um curador analisando um trecho de conversa de um grupo de amigos no WhatsApp.
Seu objetivo é extrair APENAS momentos memoráveis que mereçam entrar em uma música de retrospectiva do ano.

Extraia, em tópicos curtos e objetivos:
- Eventos importantes (viagens, rolês, festas, conquistas, namoros, mudanças).
- Perrengues, fofocas engraçadas ou histórias absurdas.
- Piadas internas, apelidos novos ou memes do grupo.
- Frases icônicas / pérolas (cite EXATAMENTE quem falou).

Regras estritas:
1. NÃO invente nada que não esteja explicitamente no texto.
2. Ignore conversas triviais ("bom dia", logística simples, risadas soltas sem contexto de mídia).
3. Se não houver nada realmente marcante neste trecho, responda APENAS com a palavra: NADA_RELEVANTE

Trecho da conversa ({data_inicio} a {data_fim}):
{texto_do_chunk}
```

### Prompt da Fase REDUCE (`reduce_prompt.txt`)
```text
Abaixo estão anotações extraídas ao longo de um ano inteiro de um grupo de WhatsApp.
Consolide essas informações em um Dossiê de Retrospectiva bem organizado, removendo repetições e destacando o que realmente definiu o ano do grupo.

Organize a resposta em Markdown com as seguintes seções:
1. Grandes Momentos (Linha do tempo resumida)
2. Destaques Individuais (O que marcou cada pessoa do grupo)
3. Piadas Internas e Bordões
4. Pérolas e Frases Marcantes (com o nome de quem disse)

Anotações brutas:
{extrato_consolidado}
```

---

## 7. Cronograma de Execução (Fases)

1. **Fase 1 — Setup do Ambiente**
   * Instalar o Ollama e baixar o modelo escolhido (`ollama pull qwen2.5:7b`).
   * Configurar o ambiente virtual Python (`venv`) e instalar dependências.
   * Exportar o `.txt` do grupo (sem mídia).
2. **Fase 2 — Limpeza e Calibração**
   * Desenvolver `cleaner.py` e `chunker.py`.
   * Validar estatísticas: quantas linhas originais vs. quantas linhas limpas e quantos chunks foram gerados.
   * Preencher o `aliases.json` caso haja números sem nome.
3. **Fase 3 — Teste Piloto (Amostragem)**
   * Rodar a fase MAP em apenas **5 a 10 chunks** variados.
   * Ler o resultado e calibrar o `map_prompt.txt` (ex: se a IA estiver trazendo coisa demais, deixar o filtro mais rígido; se estiver ignorando piadas, afrouxar).
4. **Fase 4 — Processamento Completo (Batch)**
   * Executar o pipeline completo sobre todo o histórico (pode ser deixado rodando durante a noite, com checkpoint ativo).
5. **Fase 5 — Consolidação e Curadoria Humana**
   * Rodar a fase REDUCE para gerar o `dossie_retrospectiva.md`.
   * Fazer uma leitura rápida final para corrigir eventuais nomes ou adicionar algum detalhe de contexto que só existia em foto/áudio antes de usar o texto para compor a música.

---

## 8. Riscos e Mitigações

| Risco | Impacto | Mitigação |
| :--- | :--- | :--- |
| **Alucinação da IA** (inventar falas ou misturar autores) | Alto | Manter `temperature <= 0.3` e exigir no prompt que só cite o que está explícito no texto. |
| **Contexto perdido por mídias** (risadas de fotos/áudios) | Médio | Instruir o prompt a ignorar reações sem contexto textual; aceitar essa limitação inerente ao `.txt`. |
| **Tempo de processamento longo** | Médio | Usar checkpoints por chunk (`.jsonl`) para permitir pausas e retomadas sem retrabalho. |
| **Formato de data diferente** (Android vs. iOS) | Baixo | Criar padrões de Regex flexíveis no `cleaner.py` e testar com as primeiras 50 linhas do arquivo real antes de rodar tudo. |