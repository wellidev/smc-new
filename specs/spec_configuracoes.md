# Spec: configuracoes.py

## Responsabilidade
Centralizar todos os parâmetros de configuração. Não contém lógica de negócio.

## Contratos

| Constante | Tipo | Valor padrão | Descrição |
|-----------|------|--------------|-----------|
| `ATIVOS_MONITORADOS` | `list[str]` | Ver abaixo | Símbolos do MT5 |
| `TIMEFRAME_ESTRUTURAL` | `int` | `mt5.TIMEFRAME_H4` | Timeframe de análise estrutural |
| `TIMEFRAME_GATILHO` | `int` | `mt5.TIMEFRAME_M15` | Timeframe de refinamento do gatilho |
| `TIMEFRAME_D1` | `int` | `mt5.TIMEFRAME_D1` (fallback `16408`) | Timeframe diário para bias macro e premium/discount |
| `VELAS_HISTORICO` | `int` | `500` | Janela de candles H4 carregados por símbolo |
| `VELAS_D1_HISTORICO` | `int` | `50` | Janela de candles D1 para bias e premium/discount |
| `PERIODO_SWING` | `int` | `10` | N candles em cada lado para swing H4 |
| `PERIODO_SWING_D1` | `int` | `5` | N candles em cada lado para swing D1 (janela menor) |
| `LIMIAR_PAVIO` | `float` | `0.30` | 30% do range total para validar captura de liquidez |
| `SESSAO_LONDON_INICIO` | `int` | `8` | Hora UTC de início da sessão London |
| `SESSAO_LONDON_FIM` | `int` | `11` | Hora UTC de fim da sessão London |
| `SESSAO_NY_INICIO` | `int` | `13` | Hora UTC de início da sessão New York |
| `SESSAO_NY_FIM` | `int` | `17` | Hora UTC de fim da sessão New York |
| `CAMINHO_BANCO` | `str` | `"banco_dados/smc.db"` | Path relativo do SQLite |
| `TELEGRAM_TOKEN` | `str` | env `SMC_TELEGRAM_TOKEN` | Token do bot Telegram |
| `TELEGRAM_CHAT_ID` | `str` | env `SMC_TELEGRAM_CHAT_ID` | ID do chat/grupo Telegram |
| `INTERVALO_VARREDURA_SEGUNDOS` | `int` | `60` | Pausa entre ciclos do loop principal |
| `MENSAGEM_ALERTA` | `str` | Ver abaixo | Template f-string da mensagem Telegram |

## Ativos Monitorados (padrão)
```python
# Forex Majors
"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
# Índices
"US30", "US500", "US100", "GER40", "UK100",
# Commodities
"XAUUSD", "XAGUSD", "BRENT",
# Cripto
"BTCUSD", "ETHUSD"
```

## Template de Mensagem
```
🚨 <b>SINAL SMC — {simbolo}</b>
📊 Timeframe: H4 (zona) + M15 (gatilho)
📍 Captura de Liquidez: {direcao_captura} @ {preco_varredura:.5f}
🔨 BOS ({bos_qualidade}): {direcao_bos} @ {nivel_bos:.5f}
🟦 Order Block ({ob_qualidade}): {ob_fundo:.5f} – {ob_topo:.5f}
⬜ FVG ({fvg_qualidade}): {fvg_fundo:.5f} – {fvg_topo:.5f}
🎯 Zona de Entrada (OB∩FVG): {overlap_fundo:.5f} – {overlap_topo:.5f}
💰 SL: {sl:.5f} | TP: {tp:.5f} | R:R 1:{rr:.1f}
🔍 Sessão: {check_sessao} | Bias D1: {check_bias} | Zona: {check_zona}
⏰ {timestamp}
```

## Cenários de Teste
N/A — módulo de constantes puras. Verificação via import sem erro.
