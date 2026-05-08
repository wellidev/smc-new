# Spec: notificador.py

## Responsabilidade
Enviar mensagens de alerta via Telegram Bot API. Stateless — não persiste nada.

## Classe: Notificador

### `__init__(self, token: str, chat_id: str) -> None`
- Armazena token e chat_id como atributos privados
- Não realiza nenhuma chamada de rede no construtor

### `enviar_alerta(self, mensagem: str) -> bool`
- POST para `https://api.telegram.org/bot{token}/sendMessage`
- Payload: `{"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"}`
- Timeout: 10 segundos
- Retorna `True` se `status_code == 200`
- Em caso de `requests.RequestException`: loga o erro e retorna `False` (sem raise)
- Em caso de `status_code != 200`: loga o corpo da resposta e retorna `False`

## Template de Mensagem (definido em configuracoes.py)
```
🚨 SINAL SMC — {simbolo}
📊 Timeframe: H4 (zona) + M15 (gatilho)
📍 Captura de Liquidez: {direcao_captura} @ {preco_varredura:.5f}
🔨 Quebra de Estrutura (BOS): {direcao_bos} @ {nivel_bos:.5f}
🟦 Order Block: {ob_fundo:.5f} – {ob_topo:.5f}
⬜ FVG Pendente: {fvg_fundo:.5f} – {fvg_topo:.5f}
⏰ {timestamp}
```

## Cenários de Teste

| # | Cenário | Mock | Resultado esperado |
|---|---------|------|--------------------|
| 1 | Envio com sucesso | `status_code=200` | `True` |
| 2 | Token inválido | `status_code=401` | `False`, log de erro, sem raise |
| 3 | Timeout de rede | `requests.Timeout` | `False`, log de erro, sem raise |
