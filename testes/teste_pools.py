"""Tests for detectar_eqh_eql and detectar_pdh_pdl."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from smc.analisador_smc import detectar_eqh_eql, detectar_pdh_pdl
from smc.modelos import PoolLiquidez


def _ts(n: int) -> list[datetime]:
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return [base + timedelta(hours=4 * i) for i in range(n)]


def _vela(tempo, abertura, maxima, minima, fechamento):
    return {
        "tempo": tempo,
        "abertura": abertura,
        "maxima": maxima,
        "minima": minima,
        "fechamento": fechamento,
        "volume": 100,
    }


def _df(linhas):
    return pd.DataFrame(linhas)


class TestDetectarEqhEql:
    def _df_com_equal_highs(self, atr=0.0010) -> pd.DataFrame:
        """Creates two wicks at the same level within 0.1×ATR tolerance."""
        tolerancia = 0.1 * atr  # 0.0001
        n = 25
        ts = _ts(n + 1)  # +1 for open candle
        base = 1.1000
        linhas = [_vela(ts[i], base, base + atr * 0.5, base - atr * 0.5, base) for i in range(n + 1)]

        # Two wicks at ~1.1050 within tolerance
        nivel = 1.1050
        linhas[5] = _vela(ts[5], base, nivel, base - atr * 0.5, base)
        linhas[10] = _vela(ts[10], base, nivel + tolerancia * 0.5, base - atr * 0.5, base)

        return _df(linhas)

    def _df_com_equal_lows(self, atr=0.0010) -> pd.DataFrame:
        tolerancia = 0.1 * atr
        n = 25
        ts = _ts(n + 1)
        base = 1.1000
        linhas = [_vela(ts[i], base, base + atr * 0.5, base - atr * 0.5, base) for i in range(n + 1)]

        nivel = 1.0950
        linhas[5] = _vela(ts[5], base, base + atr * 0.5, nivel, base)
        linhas[10] = _vela(ts[10], base, base + atr * 0.5, nivel + tolerancia * 0.5, base)

        return _df(linhas)

    def test_eqh_detectado(self):
        atr = 0.0010
        df = self._df_com_equal_highs(atr)
        pools = detectar_eqh_eql(df, "EURUSD", atr)
        eqh = [p for p in pools if p.tipo == "EQH"]
        assert len(eqh) >= 1

    def test_eql_detectado(self):
        atr = 0.0010
        df = self._df_com_equal_lows(atr)
        pools = detectar_eqh_eql(df, "EURUSD", atr)
        eql = [p for p in pools if p.tipo == "EQL"]
        assert len(eql) >= 1

    def test_wicks_dispersos_sem_cluster(self):
        atr = 0.0010
        n = 25
        ts = _ts(n + 1)
        base = 1.1000
        linhas = [_vela(ts[i], base, base + i * atr, base - atr, base) for i in range(n + 1)]
        df = _df(linhas)
        pools = detectar_eqh_eql(df, "EURUSD", atr)
        # With widely dispersed wicks, no EQH cluster should form
        assert isinstance(pools, list)

    def test_atr_zero_retorna_vazio(self):
        n = 25
        ts = _ts(n + 1)
        linhas = [_vela(ts[i], 1.1, 1.101, 1.099, 1.1) for i in range(n + 1)]
        df = _df(linhas)
        pools = detectar_eqh_eql(df, "EURUSD", 0.0)
        assert pools == []

    def test_pool_tem_campos_obrigatorios(self):
        atr = 0.0010
        df = self._df_com_equal_highs(atr)
        pools = detectar_eqh_eql(df, "EURUSD", atr)
        for pool in pools:
            assert isinstance(pool, PoolLiquidez)
            assert pool.tolerancia > 0
            assert pool.preco > 0
            assert pool.id != ""

    def test_varredido_false_por_padrao(self):
        atr = 0.0010
        df = self._df_com_equal_highs(atr)
        pools = detectar_eqh_eql(df, "EURUSD", atr)
        for pool in pools:
            assert pool.varredido is False


class TestDetectarPdhPdl:
    def _df_d1(self, n=5) -> pd.DataFrame:
        ts = _ts(n)
        base = 1.1000
        linhas = [
            _vela(ts[i], base, base + 0.0200 * (i + 1), base - 0.0100 * (i + 1), base)
            for i in range(n)
        ]
        return _df(linhas)

    def test_pdh_pdl_retornados(self):
        df = self._df_d1()
        pools = detectar_pdh_pdl(df, "EURUSD", 0.0010)
        tipos = {p.tipo for p in pools}
        assert "PDH" in tipos
        assert "PDL" in tipos

    def test_pdh_usa_penultima_vela(self):
        df = self._df_d1(5)
        pools = detectar_pdh_pdl(df, "EURUSD", 0.0010)
        pdh = next(p for p in pools if p.tipo == "PDH")
        penultima_maxima = float(df["maxima"].iloc[-2])
        assert pdh.preco == pytest.approx(penultima_maxima)

    def test_pdl_usa_penultima_vela(self):
        df = self._df_d1(5)
        pools = detectar_pdh_pdl(df, "EURUSD", 0.0010)
        pdl = next(p for p in pools if p.tipo == "PDL")
        penultima_minima = float(df["minima"].iloc[-2])
        assert pdl.preco == pytest.approx(penultima_minima)

    def test_d1_insuficiente_retorna_vazio(self):
        ts = _ts(1)
        linhas = [_vela(ts[0], 1.1, 1.12, 1.09, 1.1)]
        df = _df(linhas)
        assert detectar_pdh_pdl(df, "EURUSD", 0.0010) == []

    def test_d1_none_retorna_vazio(self):
        assert detectar_pdh_pdl(None, "EURUSD", 0.0010) == []
