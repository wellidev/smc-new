from unittest.mock import MagicMock, patch

import requests

from smc.notificador import Notificador


class TestNotificador:
    def _notificador(self) -> Notificador:
        return Notificador(token="token_teste", chat_id="123456")

    def test_envio_com_sucesso(self):
        notificador = self._notificador()
        mock_resposta = MagicMock()
        mock_resposta.status_code = 200

        with patch("requests.post", return_value=mock_resposta) as mock_post:
            resultado = notificador.enviar_alerta("Mensagem de teste")

        assert resultado is True
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["chat_id"] == "123456"
        assert kwargs["json"]["text"] == "Mensagem de teste"
        assert kwargs["timeout"] == 10

    def test_token_invalido_retorna_false(self):
        notificador = self._notificador()
        mock_resposta = MagicMock()
        mock_resposta.status_code = 401
        mock_resposta.text = '{"ok": false, "error_code": 401}'

        with patch("requests.post", return_value=mock_resposta):
            resultado = notificador.enviar_alerta("Mensagem")

        assert resultado is False

    def test_timeout_retorna_false_sem_raise(self):
        notificador = self._notificador()

        with patch("requests.post", side_effect=requests.Timeout("timeout simulado")):
            resultado = notificador.enviar_alerta("Mensagem")

        assert resultado is False

    def test_excecao_de_rede_retorna_false(self):
        notificador = self._notificador()

        with patch("requests.post", side_effect=requests.ConnectionError("conexão recusada")):
            resultado = notificador.enviar_alerta("Mensagem")

        assert resultado is False

    def test_parse_mode_html(self):
        notificador = self._notificador()
        mock_resposta = MagicMock()
        mock_resposta.status_code = 200

        with patch("requests.post", return_value=mock_resposta) as mock_post:
            notificador.enviar_alerta("<b>Teste HTML</b>")

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["parse_mode"] == "HTML"
