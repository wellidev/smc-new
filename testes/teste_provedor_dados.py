from datetime import datetime, timezone
from unittest.mock import patch

from smc.provedor_dados import ProvedorDados
from smc.repositorio import Repositorio


def _criar_repo_memoria() -> Repositorio:
    return Repositorio(":memory:")


def _criar_registro_mt5(timestamp_utc: datetime, open_: float, high: float, low: float, close: float):
    return {
        "time": int(timestamp_utc.timestamp()),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": 1000,
        "real_volume": 0,
    }


class TestConexao:
    def test_conectar_falha_retorna_false(self):
        provedor = ProvedorDados(_criar_repo_memoria())
        with patch("MetaTrader5.initialize", return_value=False):
            with patch("MetaTrader5.last_error", return_value=(-1, "erro")):
                assert provedor.conectar() is False

    def test_conectar_sucesso_retorna_true(self):
        from unittest.mock import MagicMock
        provedor = ProvedorDados(_criar_repo_memoria())
        tick_mock = MagicMock()
        tick_mock.time = int(datetime.now(timezone.utc).timestamp())
        with patch("MetaTrader5.initialize", return_value=True):
            with patch("MetaTrader5.symbol_info_tick", return_value=tick_mock):
                assert provedor.conectar() is True

    def test_obter_velas_sem_conexao_retorna_none(self):
        provedor = ProvedorDados(_criar_repo_memoria())
        resultado = provedor.obter_velas("EURUSD", 16408, 100)
        assert resultado is None


class TestCacheIncremental:
    def _provedor_conectado(self) -> ProvedorDados:
        provedor = ProvedorDados(_criar_repo_memoria())
        provedor._mt5_conectado = True
        return provedor

    def _gerar_registros(self, n: int, base_ts: int = 1704067200) -> list[dict]:
        return [
            _criar_registro_mt5(
                datetime.fromtimestamp(base_ts + i * 14400, tz=timezone.utc),
                1.1000 + i * 0.0001, 1.1050 + i * 0.0001,
                1.0950 + i * 0.0001, 1.1020 + i * 0.0001,
            )
            for i in range(n)
        ]

    def test_cache_vazio_busca_mt5_e_persiste(self):
        provedor = self._provedor_conectado()
        registros = self._gerar_registros(10)

        with patch("MetaTrader5.copy_rates_from_pos", return_value=registros):
            df = provedor.obter_velas("EURUSD", 16408, 10)

        assert df is not None
        assert len(df) == 10
        assert list(df.columns) == ["tempo", "abertura", "maxima", "minima", "fechamento", "volume"]

    def test_cache_existente_nao_duplica_registros(self):
        provedor = self._provedor_conectado()
        registros = self._gerar_registros(10)

        with patch("MetaTrader5.copy_rates_from_pos", return_value=registros):
            provedor.obter_velas("EURUSD", 16408, 10)

        cursor = provedor._repo._conn.execute("SELECT COUNT(*) FROM velas WHERE simbolo='EURUSD'")
        contagem_inicial = cursor.fetchone()[0]

        registros_novos = self._gerar_registros(5, base_ts=1704067200 + 5 * 14400)
        with patch("MetaTrader5.copy_rates_from", return_value=registros_novos):
            provedor.obter_velas("EURUSD", 16408, 10)

        cursor = provedor._repo._conn.execute("SELECT COUNT(*) FROM velas WHERE simbolo='EURUSD'")
        contagem_final = cursor.fetchone()[0]

        assert contagem_final >= contagem_inicial

    def test_dataframe_retorna_colunas_corretas(self):
        provedor = self._provedor_conectado()
        registros = self._gerar_registros(5)

        with patch("MetaTrader5.copy_rates_from_pos", return_value=registros):
            df = provedor.obter_velas("EURUSD", 16408, 5)

        assert df is not None
        assert str(df["tempo"].dtype).startswith("datetime64") and "UTC" in str(df["tempo"].dtype)
        assert df["abertura"].dtype == float
        assert df["volume"].dtype == int

    def test_simbolo_invalido_retorna_none(self):
        provedor = self._provedor_conectado()
        with patch("MetaTrader5.copy_rates_from_pos", return_value=None):
            resultado = provedor.obter_velas("INVALIDO", 16408, 100)
        assert resultado is None
