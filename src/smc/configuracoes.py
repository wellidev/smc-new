import os

from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5

    TIMEFRAME_H4 = mt5.TIMEFRAME_H4
    TIMEFRAME_M15 = mt5.TIMEFRAME_M15
except ImportError:
    TIMEFRAME_H4 = 16408
    TIMEFRAME_M15 = 15

load_dotenv()

ATIVOS_MONITORADOS: list[str] = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",  # Majors
    "AUDJPY", "CHFJPY", "EURGBP", "EURJPY", "EURCHF", "GBPJPY", "NZDJPY",  # Minors
    "EURCAD", "EURAUD", "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF",  # Exotics
    "US30", "US500", "USTEC", "DE40", "UK100",  # Indices
    "XAUUSD", "XAGUSD", "BRENT",  # Commodities
    "BTCUSD", "ETHUSD",  # Crypto
]

TIMEFRAME_ESTRUTURAL: int = TIMEFRAME_H4
TIMEFRAME_GATILHO: int = TIMEFRAME_M15

VELAS_HISTORICO: int = 500
PERIODO_SWING: int = 10
LIMIAR_PAVIO: float = 0.30

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
    "🔨 Quebra de Estrutura (BOS): {direcao_bos} @ {nivel_bos:.5f}\n"
    "🟦 Order Block: {ob_fundo:.5f} – {ob_topo:.5f}\n"
    "⬜ FVG Pendente: {fvg_fundo:.5f} – {fvg_topo:.5f}\n"
    "🎯 Zona de Entrada (OB∩FVG): {overlap_fundo:.5f} – {overlap_topo:.5f}\n"
    "⏰ {timestamp}"
)
