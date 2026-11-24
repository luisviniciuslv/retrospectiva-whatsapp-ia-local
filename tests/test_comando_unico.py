"""Ponta a ponta do comando único (`retro musica`) com um Ollama de mentira.

Roda tudo numa pasta temporária: não precisa do Ollama ligado e não toca em data/.
"""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import main
from config import settings
from src import llm_client

EXEMPLO = settings.ROOT_DIR / "exemplos" / "exemplo_chat.txt"

LETRA_DA_IA = """Claro! Aqui está a letra:

**Título: Os Pertubados**

**[Verso 1]**
O Zé caiu da bike lá em junho
E a Ana gravou tudo, testemunho

**[Refrão]**
É o grupo mais zoado do Brasil
Ninguém fala sério, ninguém nunca viu

**[Ponte]**
Mas no fim a gente se ama
Mesmo com o Zé no meio da lama
"""


def ia_de_mentira(self, modelo, prompt, **kw):
    """Responde de acordo com a etapa (reconhecida pelo texto do prompt)."""
    ia_de_mentira.prompts.append(prompt)
    if "LETRA de uma música" in prompt:
        return LETRA_DA_IA
    if "Enxugue o trecho" in prompt:
        return "Zé caiu da bike e Ana riu."
    if "roteirista da zoeira" in prompt:
        return "Em junho, Zé caiu da bike e Ana disse \"eu avisei\"."
    return "## Linha do tempo\n- Zé caiu da bike na frente de todo mundo.\n- Ana ganhou o bolão."


class TestComandoUnico(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        ia_de_mentira.prompts = []
        self.patches = [
            mock.patch.object(settings, "RAW_DIR", base / "raw"),
            mock.patch.object(settings, "PROCESSED_DIR", base / "processed"),
            mock.patch.object(settings, "OUTPUT_DIR", base / "output"),
            mock.patch.object(settings, "ROOT_DIR", base),
            mock.patch.object(settings, "ALIASES_FILE", base / "aliases.json"),
            mock.patch.object(main, "ARQ_MENSAGENS", base / "processed" / settings.NOME_ARQUIVO_MENSAGENS),
            mock.patch.object(main, "ARQ_META", base / "processed" / "limpeza_meta.json"),
            mock.patch.object(llm_client.OllamaClient, "online", lambda self: True),
            mock.patch.object(llm_client.OllamaClient, "versao", lambda self: "fake"),
            mock.patch.object(llm_client.OllamaClient, "modelos_instalados", lambda self: ["fake:1b"]),
            mock.patch.object(llm_client.OllamaClient, "chat", ia_de_mentira),
        ]
        for p in self.patches:
            p.start()
        self.base = base
        self.zip = base / "WhatsApp Chat - Pertubados.zip"
        with zipfile.ZipFile(self.zip, "w") as z:
            z.write(EXEMPLO, "_chat.txt")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_zip_ate_a_letra_com_o_modelo_escolhido(self):
        main.main(["musica", str(self.zip), "-m", "fake:1b", "--perfil", "teste"])

        saida = self.base / "output" / "fake-1b"  # pasta do modelo passado em -m
        dossie = saida / settings.NOME_ARQUIVO_DOSSIE
        letra = saida / settings.NOME_ARQUIVO_HISTORIA
        self.assertTrue(dossie.exists(), "o dossiê tem que ficar no output")
        self.assertIn("Zé caiu da bike", dossie.read_text(encoding="utf-8"))
        self.assertEqual(letra.read_text(encoding="utf-8"),
                         "[Verso 1]\nO Zé caiu da bike lá em junho\nE a Ana gravou tudo, testemunho\n\n"
                         "[Refrão]\nÉ o grupo mais zoado do Brasil\nNinguém fala sério, ninguém nunca viu\n\n"
                         "[Ponte]\nMas no fim a gente se ama\nMesmo com o Zé no meio da lama\n")
        self.assertTrue((saida / settings.NOME_ARQUIVO_RASCUNHO).exists())
        # todas as etapas passaram pela IA, e a da letra recebeu o rascunho
        self.assertTrue(any("LETRA de uma música" in p and "eu avisei" in p for p in ia_de_mentira.prompts))

        # rodar de novo é retomável: nada é pedido à IA outra vez
        antes = len(ia_de_mentira.prompts)
        main.main(["musica", str(self.zip), "-m", "fake:1b", "--perfil", "teste"])
        self.assertEqual(len(ia_de_mentira.prompts), antes)


if __name__ == "__main__":
    unittest.main()
