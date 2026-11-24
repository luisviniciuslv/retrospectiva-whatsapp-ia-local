"""Testes rápidos (sem IA): python -m unittest discover tests"""
import unittest
from datetime import date

from src import chunker, cleaner
from src.llm_client import _perfil_por_vram, host_eh_local, selecionar
from src.map_phase import eh_nada_relevante

ANDROID = """24/11/2026 17:05 - As mensagens e ligações são protegidas com a criptografia de ponta a ponta.
24/11/2026 17:06 - Fulano entrou usando o link de convite deste grupo
24/11/2026 17:07 - Zé: primeira linha
segunda linha
24/11/2026 17:08 - +55 11 98765-4321: <Mídia oculta>
24/11/2026 17:09 - +55 11 98765-4321: olha https://exemplo.com/abc?x=1 kkk
24/11/2026 17:10 - Ana: Mensagem apagada
24/11/2026 17:11 - Ana: editei aqui <Mensagem editada>
25/12/2026 09:00 - Zé: feliz natal""".splitlines()

IOS = """‎[24/11/26, 17:05:33] Grupo: ‎As mensagens e as ligações são protegidas com a criptografia de ponta a ponta.
[24/11/26, 17:06:01] Zé: oi gente
[24/11/26, 5:07:02 PM] Ana: ‎imagem ocultada
[24/11/26, 17:08:00] Ana: tudo bem?
e aí""".splitlines()


class TestCleaner(unittest.TestCase):
    def test_android(self):
        msgs, st = cleaner.parse_whatsapp(ANDROID, {"+55 11 98765-4321": "Gordo"})
        self.assertEqual(st.formato, "Android")
        self.assertEqual(st.mensagens_sistema, 2)
        self.assertEqual(st.mensagens_ruido, 2)
        self.assertEqual([m.autor for m in msgs], ["Zé", "Gordo", "Ana", "Zé"])
        self.assertEqual(msgs[0].texto, "primeira linha\nsegunda linha")
        self.assertIn("[LINK]", msgs[1].texto)
        self.assertEqual(msgs[2].texto, "editei aqui")

    def test_periodo(self):
        msgs, st = cleaner.parse_whatsapp(ANDROID, data_inicio=date(2026, 12, 1))
        self.assertEqual(len(msgs), 1)
        self.assertGreater(st.mensagens_fora_periodo, 0)

    def test_ios(self):
        msgs, st = cleaner.parse_whatsapp(IOS)
        self.assertEqual(st.formato, "iOS")
        self.assertEqual([m.texto for m in msgs], ["oi gente", "tudo bem?\ne aí"])

    def test_ampm(self):
        msgs, _ = cleaner.parse_whatsapp(["11/24/26, 5:07 PM - Ana: oi", "11/25/26, 12:01 AM - Zé: tchau"])
        self.assertEqual(msgs[0].ts, "2026-11-24T17:07:00")  # detecta MM/DD
        self.assertEqual(msgs[1].ts, "2026-11-25T00:01:00")


class TestChunker(unittest.TestCase):
    def test_overlap_e_progresso(self):
        msgs = [cleaner.Mensagem(ts=f"2026-01-01T10:{i % 60:02d}:00", autor="Zé", texto="x" * 100)
                for i in range(300)]
        chunks = chunker.gerar_chunks(msgs, alvo_chars=2000, overlap_msgs=5)
        self.assertEqual(sum(c.n_mensagens for c in chunks), 300)
        self.assertEqual(chunks[0].n_overlap, 0)
        self.assertTrue(all(c.n_overlap == 5 for c in chunks[1:]))
        self.assertTrue(all(len(c.texto) <= 2200 for c in chunks))


class TestSelecao(unittest.TestCase):
    def test_vram(self):
        self.assertEqual(_perfil_por_vram(0), "teste")
        self.assertEqual(_perfil_por_vram(8), "teste")
        self.assertEqual(_perfil_por_vram(12), "producao")
        self.assertEqual(_perfil_por_vram(24), "producao")

    def test_host(self):
        self.assertTrue(host_eh_local("http://localhost:11434"))
        self.assertFalse(host_eh_local("http://192.168.0.50:11434"))

    def test_selecionar_offline(self):
        sel = selecionar(perfil="auto", host="http://127.0.0.1:1", vram_gb=16)
        self.assertEqual(sel.perfil, "producao")
        self.assertEqual(sel.modelo, "qwen2.5:14b")
        self.assertFalse(sel.instalado)

    def test_nada(self):
        self.assertTrue(eh_nada_relevante("NADA_RELEVANTE"))
        self.assertTrue(eh_nada_relevante("**NADA_RELEVANTE**."))
        self.assertFalse(eh_nada_relevante("### Eventos\n- (15/01) Caio passou no concurso"))


if __name__ == "__main__":
    unittest.main()
