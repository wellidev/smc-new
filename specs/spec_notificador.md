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

## Template de Mensagem (MENSAGEM_SETUP em principal.py)

```
🔔 <b>SETUP SMC — {simbolo}</b>  [{score}/100] {score_emoji}

📊 {evento_tipo} {direcao}  |  {pool_tipo} @ {pool_preco:.5f}
🎯 POI: {poi_fundo:.5f} – {poi_topo:.5f}
📍 Entrada: {entrada:.5f}  |  SL: {sl:.5f} (-{sl_pips}p)  |  TP: {tp:.5f} (+{tp_pips}p)
📐 R:R 1:{rr:.1f}  |  Displacement: {displacement}

{check_sessao}
⚠️ {checks_aviso}

⏰ {timestamp}
```

### Campos calculados antes da formatação

| Campo | Origem | Descrição |
|-------|--------|-----------|
| `score_emoji` | score | 🟢 ≥ 70 · 🟡 55–69 · 🔴 < 55 |
| `pool_tipo` | `pool.tipo` | "EQH" / "EQL" / "PDH" / "PDL" — **não usar pool_id[:3]** |
| `pool_preco` | `pool.preco` | Preço real do pool, não `poi_fundo` |
| `entrada` | `confirmacao.preco_confirmacao` | Preço de entrada sugerido |
| `sl_pips` | `round(abs(entrada - sl) * pip_factor)` | Distância SL em pips |
| `tp_pips` | `round(abs(tp - entrada) * pip_factor)` | Distância TP em pips |
| `pip_factor` | 100 se "JPY" em símbolo, 10000 caso contrário | Conversão pips |
| `checks_aviso` | bias + zona com `⚠️` | Linha só com os filtros que falharam; omitida se todos `✅` |

### Regra `checks_aviso`
Concatena apenas os filtros com `⚠️`. Se sessão, bias e zona forem todos `✅`, a linha de aviso é omitida.
Exemplo com dois avisos: `"Bias D1: Neutro  |  Zona: sem confluência"`

## Cenários de Teste

| # | Cenário | Mock | Resultado esperado |
|---|---------|------|--------------------|
| 1 | Envio com sucesso | `status_code=200` | `True` |
| 2 | Token inválido | `status_code=401` | `False`, log de erro, sem raise |
| 3 | Timeout de rede | `requests.Timeout` | `False`, log de erro, sem raise |
