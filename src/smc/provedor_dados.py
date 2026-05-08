import logging
from datetime import datetime, timezone

import pandas as pd

from smc.configuracoes import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
from smc.repositorio import Repositorio

logger = logging.getLogger(__name__)

_COLUNAS_DF = ["tempo", "abertura", "maxima", "minima", "fechamento", "volume"]

_SEGUNDOS_POR_TIMEFRAME: dict[int, int] = {
    1: 60, 2: 120, 3: 180, 4: 240, 5: 300, 6: 360, 10: 600,
    12: 720, 15: 900, 20: 1200, 30: 1800,
    16385: 3600, 16386: 7200, 16387: 10800, 16388: 14400,
    16390: 21600, 16392: 28800, 16396: 43200,
    16408: 86400, 32769: 604800, 49153: 2592000,
}


def _detectar_offset_servidor(mt5) -> int:
    # 1. Obter o último tick de um ativo líquido (ex: EURUSD)
    tick = mt5.symbol_info_tick("EURUSD")
    server_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)

    # 2. Obter o horário UTC atual do sistema
    utc_now = datetime.now(timezone.utc)

    # 3. Calcular a diferença aproximada (arredonde para a hora inteira)
    return round((server_time - utc_now).total_seconds())


class ProvedorDados:
    def __init__(self, repo: Repositorio) -> None:
        self._repo = repo
        self._mt5_conectado = False
        self._offset_servidor: int = 0

    def conectar(self) -> bool:
        try:
            import MetaTrader5 as mt5
            kwargs: dict = {}
            if MT5_PATH:
                kwargs["path"] = MT5_PATH
            if MT5_LOGIN:
                kwargs["login"] = int(MT5_LOGIN)
            if MT5_PASSWORD:
                kwargs["password"] = MT5_PASSWORD
            if MT5_SERVER:
                kwargs["server"] = MT5_SERVER
            if not mt5.initialize(**kwargs):
                erro = mt5.last_error()
                logger.error("Falha ao inicializar MT5: %s", erro)
                return False
            self._mt5_conectado = True
            self._offset_servidor = _detectar_offset_servidor(mt5)
            logger.info("MT5 conectado com sucesso. Offset do servidor: %ds", self._offset_servidor)
            return True
        except Exception as exc:
            logger.error("Exceção ao conectar ao MT5: %s", exc)
            return False

    def desconectar(self) -> None:
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
            self._mt5_conectado = False
            logger.info("MT5 desconectado.")
        except Exception as exc:
            logger.warning("Exceção ao desconectar MT5: %s", exc)

    def obter_velas(self, simbolo: str, timeframe: int, quantidade: int) -> pd.DataFrame | None:
        if not self._mt5_conectado:
            logger.warning("MT5 não conectado. Chame conectar() primeiro.")
            return None

        try:
            import MetaTrader5 as mt5

            ultimo_tempo = self._repo.obter_ultimo_tempo_vela(simbolo, timeframe)

            if ultimo_tempo is None:
                registros = mt5.copy_rates_from_pos(simbolo, timeframe, 0, quantidade)
                if registros is None or len(registros) == 0:
                    logger.warning("Nenhuma vela retornada para %s TF=%d", simbolo, timeframe)
                    return None
                self._repo.persistir_velas(
                    simbolo, timeframe,
                    _converter_registros(simbolo, timeframe, registros, self._offset_servidor),
                )
            else:
                agora = datetime.now(timezone.utc)
                segundos_passados = (agora - ultimo_tempo).total_seconds()
                duracao_vela = _SEGUNDOS_POR_TIMEFRAME.get(timeframe, 900)
                n_velas = min(int(segundos_passados / duracao_vela) + 2, quantidade)
                registros = mt5.copy_rates_from_pos(simbolo, timeframe, 0, n_velas)
                if registros is not None and len(registros) >= 1:
                    self._repo.persistir_velas(
                        simbolo, timeframe,
                        _converter_registros(simbolo, timeframe, registros, self._offset_servidor),
                    )

            linhas = self._repo.carregar_velas(simbolo, timeframe, quantidade)
            return _linhas_para_dataframe(linhas)

        except Exception as exc:
            logger.error("Erro ao obter velas para %s: %s", simbolo, exc)
            return None


def _converter_registros(simbolo: str, timeframe: int, registros, offset: int = 0) -> list[tuple]:
    linhas = []
    for r in registros:
        tempo_utc = datetime.fromtimestamp(r["time"] - offset, tz=timezone.utc).isoformat()
        volume = int(r["real_volume"] or r["tick_volume"])
        linhas.append((simbolo, timeframe, tempo_utc, float(r["open"]), float(r["high"]),
                       float(r["low"]), float(r["close"]), volume))
    return linhas


def _linhas_para_dataframe(linhas: list[tuple]) -> pd.DataFrame | None:
    if not linhas:
        return None
    df = pd.DataFrame(linhas, columns=_COLUNAS_DF)
    df["tempo"] = pd.to_datetime(df["tempo"], utc=True)
    df["abertura"] = df["abertura"].astype(float)
    df["maxima"] = df["maxima"].astype(float)
    df["minima"] = df["minima"].astype(float)
    df["fechamento"] = df["fechamento"].astype(float)
    df["volume"] = df["volume"].astype(int)
    return df.reset_index(drop=True)
