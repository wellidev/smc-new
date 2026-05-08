# Spec: configuracoes.py

## Responsabilidade
Centralizar todos os parâmetros de configuração. Não contém lógica de negócio.

## Contratos

| Constante | Tipo | Valor padrão | Descrição |
|-----------|------|--------------|-----------|
| `ATIVOS_MONITORADOS` | `list[str]` | Ver abaixo | Símbolos do MT5 |
| `TIMEFRAME_ESTRUTURAL` | `int` | `mt5.TIMEFRAME_H4` | Timeframe de análise estrutural |
| `TIMEFRAME_GATILHO` | `int` | `mt5.TIMEFRAME_M15` | Timeframe de refinamento do gatilho |
| `VELAS_HISTORICO` | `int` | `500` | Janela de candles carregados por símbolo |
| `PERIODO_SWING` | `int` | `10` | N candles em cada lado para swing high/low |
| `LIMIAR_PAVIO` | `float` | `0.30` | 30% do range total para validar captura de liquidez |
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
🚨 SINAL SMC — {simbolo}
📊 Timeframe: H4 (zona) + M15 (gatilho)
📍 Captura de Liquidez: {direcao_captura} @ {preco_varredura:.5f}
🔨 Quebra de Estrutura (BOS): {direcao_bos} @ {nivel_bos:.5f}
🟦 Order Block: {ob_fundo:.5f} – {ob_topo:.5f}
⬜ FVG Pendente: {fvg_fundo:.5f} – {fvg_topo:.5f}
⏰ {timestamp}
```

## Cenários de Teste
N/A — módulo de constantes puras. Verificação via import sem erro.
