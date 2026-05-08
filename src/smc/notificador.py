import logging

import requests

logger = logging.getLogger(__name__)

_URL_TELEGRAM = "https://api.telegram.org/bot{token}/sendMessage"


class Notificador:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    def enviar_alerta(self, mensagem: str) -> bool:
        url = _URL_TELEGRAM.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": mensagem,
            "parse_mode": "HTML",
        }
        try:
            resposta = requests.post(url, json=payload, timeout=10)
            if resposta.status_code == 200:
                logger.info("Alerta enviado com sucesso ao Telegram.")
                return True
            logger.error(
                "Falha ao enviar alerta. Status: %d | Resposta: %s",
                resposta.status_code,
                resposta.text[:200],
            )
            return False
        except requests.RequestException as exc:
            logger.error("Exceção de rede ao enviar alerta Telegram: %s", exc)
            return False
