import os

from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5

    TIMEFRAME_H4 = mt5.TIMEFRAME_H4
    TIMEFRAME_M15 = mt5.TIMEFRAME_M15
    TIMEFRAME_D1 = mt5.TIMEFRAME_D1
except ImportError:
    TIMEFRAME_H4 = 16388
    TIMEFRAME_M15 = 15
    TIMEFRAME_D1 = 16408

load_dotenv()

ATIVOS_MONITORADOS: list[str] = [
    "AUDUSD", "EURUSD", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",  # Majors
    "AUDJPY", "CHFJPY", "EURCHF", "EURGBP", "EURJPY", "GBPJPY",  # Minors
    "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF", "CADJPY", "EURAUD", "EURCAD", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF",
    "NZDCAD", "NZDCHF", "NZDJPY",  # Exotics
    "US30", "US500", "USTEC", "DE40", "UK100",  # Indices
    "XAUUSD", "XAGUSD", "BRENT",  # Commodities
    "BTCUSD", "ETHUSD", "LINKUSD", "LTCUSD", "SOLUSD", "XRPUSD",  # Crypto
]

TIMEFRAME_ESTRUTURAL: int = TIMEFRAME_H4
TIMEFRAME_GATILHO: int = TIMEFRAME_M15

VELAS_HISTORICO: int = 500
VELAS_D1_HISTORICO: int = 50
PERIODO_SWING: int = 10
PERIODO_SWING_D1: int = 5
LIMIAR_PAVIO: float = 0.30
IDADE_MAX_EVENTO_H4: int = 10

SESSAO_LONDON_INICIO: int = 8
SESSAO_LONDON_FIM: int = 11
SESSAO_NY_INICIO: int = 13
SESSAO_NY_FIM: int = 17

CAMINHO_BANCO: str = os.path.join("banco_dados", "smc.db")

MT5_LOGIN: str = os.getenv("SMC_MT5_LOGIN", "")
MT5_PASSWORD: str = os.getenv("SMC_MT5_PASSWORD", "")
MT5_SERVER: str = os.getenv("SMC_MT5_SERVER", "")
MT5_PATH: str = os.getenv("SMC_MT5_PATH", "")

TELEGRAM_TOKEN: str = os.getenv("SMC_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("SMC_TELEGRAM_CHAT_ID", "")

INTERVALO_VARREDURA_SEGUNDOS: int = 60

MENSAGEM_ALERTA: str = (
    "🚨 <b>SINAL SMC — {simbolo}</b>\n"
    "📊 Timeframe: H4 (zona) + M15 (gatilho)\n"
    "📍 Captura de Liquidez: {direcao_captura} @ {preco_varredura:.5f}\n"
    "🔨 BOS ({bos_qualidade}): {direcao_bos} @ {nivel_bos:.5f}\n"
    "🟦 Order Block ({ob_qualidade}): {ob_fundo:.5f} – {ob_topo:.5f}\n"
    "⬜ FVG ({fvg_qualidade}): {fvg_fundo:.5f} – {fvg_topo:.5f}\n"
    "🎯 Zona de Entrada (OB∩FVG): {overlap_fundo:.5f} – {overlap_topo:.5f}\n"
    "💰 SL: {sl:.5f} | TP: {tp:.5f} | R:R 1:{rr:.1f}\n"
    "🔍 Sessão: {check_sessao} | Bias D1: {check_bias} | Zona: {check_zona}\n"
    "⏰ {timestamp}"
)
