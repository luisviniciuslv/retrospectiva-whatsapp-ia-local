"""Formato iOS em inglês ([M/D/AA, H:MM:SS PM]), como exporta o WhatsApp num iPhone em inglês.
Nomes e mensagens são fictícios."""
import unittest

from src import cleaner

TRECHO = """[6/24/26, 7:49:40 PM] Bento: que jogo
[6/24/26, 7:49:49 PM] Caio Lima: GOLAÇO
[6/24/26, 8:25:24 PM] Você: falei que ia ganhar
[6/24/26, 8:28:10 PM] zl: Urgente: atacante torce o pé no aquecimento e não vai mais entrar
[6/24/26, 8:42:45 PM] Caio Lima: <figurinha omitida>
[6/24/26, 9:06:47 PM] Otto: [Encaminhada] <áudio omitido>
[6/24/26, 9:06:52 PM] Otto: o que esperar depois desse audio?
[6/24/26, 12:10:11 AM] Bento: Segunda 14h""".splitlines()


class TestFormatoIOS(unittest.TestCase):
    def test_trecho(self):
        msgs, st = cleaner.parse_whatsapp(TRECHO, {"Você": "Eu"})
        self.assertEqual(st.formato, "iOS")
        self.assertEqual(st.ordem_data, "MDY")
        self.assertEqual(st.mensagens_ruido, 2)  # figurinha + áudio encaminhado
        self.assertEqual([m.autor for m in msgs], ["Bento", "Caio Lima", "Eu", "zl", "Otto", "Bento"])
        self.assertEqual(msgs[0].ts, "2026-06-24T19:49:40")
        self.assertEqual(msgs[-1].ts, "2026-06-24T00:10:11")
        self.assertTrue(msgs[3].texto.startswith("Urgente: atacante"))

    def test_voce_so_no_autor(self):
        """'Você:' só é trocado como autor; 'você' dentro do texto fica intacto."""
        linhas = ["[6/24/26, 8:25:24 PM] Você: você viu o jogo?",
                  "[6/24/26, 8:26:00 PM] Bento: Você: vc é muito ruim kkkk",
                  "[6/24/26, 8:27:00 PM] Bento: você tá maluco"]
        msgs, _ = cleaner.parse_whatsapp(linhas, {"Você": "Eu"})
        self.assertEqual([(m.autor, m.texto) for m in msgs], [
            ("Eu", "você viu o jogo?"),
            ("Bento", "Você: vc é muito ruim kkkk"),
            ("Bento", "você tá maluco"),
        ])

    def test_placeholders(self):
        linhas = ["[6/24/26, 8:25:24 PM] zl: <mensagem de voz omitida>",
                  "[6/24/26, 8:25:25 PM] zl: <Mensagem de album>",
                  "[6/24/26, 8:25:26 PM] zl: <imagem ocultada> olha isso kkkkkkkkkkk"]
        msgs, st = cleaner.parse_whatsapp(linhas)
        self.assertEqual(st.mensagens_ruido, 2)
        self.assertEqual(msgs[0].texto, "olha isso kkkk")


if __name__ == "__main__":
    unittest.main()
