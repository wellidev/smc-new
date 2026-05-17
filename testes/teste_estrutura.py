"""Tests for calcular_swings_confirmados, calcular_atr, detectar_eventos_estrutura."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from smc.analisador_smc import (
    calcular_atr,
    calcular_swings_confirmados,
    detectar_eventos_estrutura,
)
from smc.modelos import SwingPoint


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


def _df(linhas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(linhas)


# ---------------------------------------------------------------------------
# calcular_swings_confirmados
# ---------------------------------------------------------------------------

class TestCalcularSwingsConfirmados:
    def _df_plano_com_pico(self, n=20, periodo=2) -> pd.DataFrame:
        """Flat candles with one clear HIGH at index 5 and one clear LOW at index 10."""
        ts = _ts(n)
        linhas = [_vela(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(n)]
        # Clear HIGH at index 5
        linhas[5] = _vela(ts[5], 1.1000, 1.1100, 1.0990, 1.1000)
        # Clear LOW at index 10
        linhas[10] = _vela(ts[10], 1.1000, 1.1010, 1.0900, 1.1000)
        return _df(linhas)

    def test_swing_no_meio_confirmado(self):
        df = self._df_plano_com_pico()
        swings = calcular_swings_confirmados(df, periodo=2)
        highs = [s for s in swings if s.tipo == "HIGH"]
        lows = [s for s in swings if s.tipo == "LOW"]
        assert any(s.indice == 5 for s in highs)
        assert any(s.indice == 10 for s in lows)

    def test_ultimo_candle_nunca_swing(self):
        ts = _ts(15)
        linhas = [_vela(ts[i], 1.1000, 1.1000 + i * 0.001, 1.0990, 1.1000) for i in range(15)]
        df = _df(linhas)
        swings = calcular_swings_confirmados(df, periodo=2)
        assert all(s.indice != 14 for s in swings)

    def test_borda_direita_rejeitada(self):
        """Candle near the right edge (within periodo of end) must NOT be confirmed."""
        ts = _ts(20)
        linhas = [_vela(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(20)]
        # Place an extreme HIGH at index 17 (3 from end=19, periodo=2 → index 17 would need idx+2=19 which is the open candle)
        linhas[17] = _vela(ts[17], 1.1000, 1.1500, 1.0990, 1.1000)
        df = _df(linhas)
        swings = calcular_swings_confirmados(df, periodo=2)
        assert all(s.indice != 17 for s in swings if s.tipo == "HIGH")

    def test_retorna_lista_de_swingpoints(self):
        df = self._df_plano_com_pico()
        swings = calcular_swings_confirmados(df, periodo=2)
        assert all(isinstance(s, SwingPoint) for s in swings)

    def test_ordenado_por_indice(self):
        df = self._df_plano_com_pico()
        swings = calcular_swings_confirmados(df, periodo=2)
        indices = [s.indice for s in swings]
        assert indices == sorted(indices)

    def test_sem_velas_suficientes(self):
        ts = _ts(5)
        linhas = [_vela(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(5)]
        df = _df(linhas)
        swings = calcular_swings_confirmados(df, periodo=3)
        assert swings == []


# ---------------------------------------------------------------------------
# calcular_atr
# ---------------------------------------------------------------------------

class TestCalcularAtr:
    def _df_range_constante(self, n=30, range_val=0.0010) -> pd.DataFrame:
        ts = _ts(n)
        linhas = [_vela(ts[i], 1.1000, 1.1000 + range_val, 1.1000, 1.1000) for i in range(n)]
        return _df(linhas)

    def test_range_constante_atr_aproximado(self):
        df = self._df_range_constante(30, range_val=0.0010)
        atr = calcular_atr(df, periodo=14)
        assert atr == pytest.approx(0.0010, rel=0.01)

    def test_velas_insuficientes_retorna_zero(self):
        ts = _ts(5)
        linhas = [_vela(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(5)]
        df = _df(linhas)
        assert calcular_atr(df, periodo=14) == 0.0

    def test_atr_positivo_com_dados_validos(self):
        df = self._df_range_constante(30)
        assert calcular_atr(df, periodo=14) > 0.0

    def test_exclui_vela_aberta(self):
        """Adding an extreme open candle at the end must not affect ATR."""
        df = self._df_range_constante(30, range_val=0.0010)
        df2 = df.copy()
        df2.iloc[-1, df2.columns.get_loc("maxima")] = 99.9
        atr1 = calcular_atr(df, periodo=14)
        atr2 = calcular_atr(df2, periodo=14)
        assert atr1 == pytest.approx(atr2, rel=0.001)


# ---------------------------------------------------------------------------
# detectar_eventos_estrutura
# ---------------------------------------------------------------------------

def _df_uptrend_then_reversal(periodo: int = 2) -> pd.DataFrame:
    """
    Creates a sequence: uptrend (HH/HL) followed by a lower low → ChoCH de BAIXA.
    Structure (with periodo=2, need symmetric windows):

    n=30 candles:
    - indices 3-5: clear LOW L1 (min) around idx 4
    - indices 6-8: clear HIGH H1 around idx 7
    - indices 9-11: clear LOW L2 > L1 around idx 10 (HL)
    - indices 12-14: clear HIGH H2 > H1 around idx 13 (HH → BOS ALTA)
    - indices 15-17: clear LOW L3 < L2 around idx 16 (LL → ChoCH BAIXA)
    - rest: neutral candles
    """
    n = 30
    ts = _ts(n)
    base = 1.1000
    step = 0.0010

    linhas = [_vela(ts[i], base, base + step, base - step, base) for i in range(n)]

    # L1 at idx 4: clear minimum
    l1 = base - 0.0040
    linhas[4] = _vela(ts[4], base, base + step, l1, base)

    # H1 at idx 7: clear maximum
    h1 = base + 0.0040
    linhas[7] = _vela(ts[7], base, h1, base - step, base)

    # L2 at idx 10: higher low (HL)
    l2 = base - 0.0020
    linhas[10] = _vela(ts[10], base, base + step, l2, base)

    # H2 at idx 13: higher high (HH) → BOS ALTA
    h2 = base + 0.0080
    linhas[13] = _vela(ts[13], base, h2, base - step, base)

    # L3 at idx 16: lower low (breaks HL=L2) → ChoCH BAIXA
    l3 = l2 - 0.0010  # l3 < l2
    linhas[16] = _vela(ts[16], base, base + step, l3, base)

    return _df(linhas)


def _df_downtrend_then_reversal(periodo: int = 2) -> pd.DataFrame:
    """
    Downtrend (LH/LL) followed by a higher high → ChoCH de ALTA.
    """
    n = 30
    ts = _ts(n)
    base = 1.1000
    step = 0.0010

    linhas = [_vela(ts[i], base, base + step, base - step, base) for i in range(n)]

    # H1 at idx 4: initial high
    h1 = base + 0.0040
    linhas[4] = _vela(ts[4], base, h1, base - step, base)

    # L1 at idx 7: clear low
    l1 = base - 0.0040
    linhas[7] = _vela(ts[7], base, base + step, l1, base)

    # H2 at idx 10: lower high (LH)
    h2 = h1 - 0.0020
    linhas[10] = _vela(ts[10], base, h2, base - step, base)

    # L2 at idx 13: lower low (LL) → BOS BAIXA
    l2 = l1 - 0.0040
    linhas[13] = _vela(ts[13], base, base + step, l2, base)

    # H3 at idx 16: higher high (breaks LH=H2) → ChoCH ALTA
    h3 = h2 + 0.0010  # h3 > h2
    linhas[16] = _vela(ts[16], base, h3, base - step, base)

    return _df(linhas)


class TestDetectarEventosEstrutura:
    def test_choch_baixa_em_uptrend(self):
        df = _df_uptrend_then_reversal()
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        tipos = [(e.tipo, e.direcao) for e in eventos]
        assert ("ChoCH", "BAIXA") in tipos

    def test_bos_alta_em_uptrend(self):
        df = _df_uptrend_then_reversal()
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        tipos = [(e.tipo, e.direcao) for e in eventos]
        assert ("BOS", "ALTA") in tipos

    def test_choch_alta_em_downtrend(self):
        df = _df_downtrend_then_reversal()
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        tipos = [(e.tipo, e.direcao) for e in eventos]
        assert ("ChoCH", "ALTA") in tipos

    def test_bos_baixa_em_downtrend(self):
        df = _df_downtrend_then_reversal()
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        tipos = [(e.tipo, e.direcao) for e in eventos]
        assert ("BOS", "BAIXA") in tipos

    def test_sem_swings_retorna_vazio(self):
        n = 10
        ts = _ts(n)
        linhas = [_vela(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(n)]
        df = _df(linhas)
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        assert eventos == []

    def test_evento_contem_nivel_rompido(self):
        df = _df_uptrend_then_reversal()
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        for ev in eventos:
            assert ev.nivel_rompido > 0

    def test_simbolo_propagado(self):
        df = _df_uptrend_then_reversal()
        eventos = detectar_eventos_estrutura(df, "GBPUSD", periodo_swing=2)
        assert all(e.simbolo == "GBPUSD" for e in eventos)

    def test_tempo_evento_posterior_ao_swing_rompido(self):
        df = _df_uptrend_then_reversal()
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        for ev in eventos:
            assert ev.tempo > ev.swing_tempo
