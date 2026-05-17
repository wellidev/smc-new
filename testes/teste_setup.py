"""Tests for detectar_mss_no_poi, calcular_score_setup, calcular_risco_rr_v2."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from smc.analisador_smc import (
    _calcular_risco_rr_v2,
    calcular_score_setup,
    detectar_mss_no_poi,
)
from smc.modelos import (
    EventoEstrutura,
    LegImpulso,
    PoolLiquidez,
    gerar_id_leg,
    gerar_id_pool,
)


def _ts(n: int) -> list[datetime]:
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return [base + timedelta(minutes=5 * i) for i in range(n)]


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


def _evento(tipo="ChoCH", direcao="ALTA"):
    ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    return EventoEstrutura(
        simbolo="EURUSD",
        tipo=tipo,
        direcao=direcao,
        nivel_rompido=1.1100,
        swing_tempo=ts,
        tempo=ts + timedelta(hours=4),
    )


def _leg(direcao="ALTA", eh_displacement=True):
    ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    return LegImpulso(
        id=gerar_id_leg("EURUSD", ts, ts + timedelta(hours=8)),
        simbolo="EURUSD",
        direcao=direcao,
        tempo_inicio=ts,
        tempo_fim=ts + timedelta(hours=8),
        indice_inicio=5,
        indice_fim=13,
        preco_inicio=1.1000,
        preco_fim=1.1200,
        range_pontos=0.0200,
        atr_multiplo=2.5,
        proporcao_corpo=0.70,
        tem_fvg_interno=True,
        eh_displacement=eh_displacement,
    )


def _pool(tipo="EQH"):
    ts = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
    return PoolLiquidez(
        id=gerar_id_pool("EURUSD", tipo, 1.1050, ts),
        simbolo="EURUSD",
        tipo=tipo,
        preco=1.1050,
        tolerancia=0.0001,
        tempo=ts,
    )


# ---------------------------------------------------------------------------
# detectar_mss_no_poi
# ---------------------------------------------------------------------------

class TestDetectarMssNoPoi:
    def _df_mss_bullish(self) -> pd.DataFrame:
        """
        Creates a bullish MSS scenario:
        - Candles inside POI (poi_fundo=1.1000, poi_topo=1.1060)
        - Bearish candle (retrace) followed by bullish candle closing above bearish open
        """
        ts = _ts(10)
        poi_fundo, poi_topo = 1.1000, 1.1060
        linhas = [_vela(ts[i], 1.1030, 1.1050, 1.1020, 1.1030) for i in range(10)]

        # Bearish candle at idx 3 (inside POI)
        linhas[3] = _vela(ts[3], 1.1040, 1.1055, 1.1020, 1.1025)  # bearish

        # Bullish MSS at idx 4: closes above v3.abertura=1.1040
        linhas[4] = _vela(ts[4], 1.1025, 1.1060, 1.1020, 1.1045)  # close > 1.1040

        return _df(linhas)

    def _df_mss_bearish(self) -> pd.DataFrame:
        ts = _ts(10)
        poi_fundo, poi_topo = 1.1000, 1.1060
        linhas = [_vela(ts[i], 1.1030, 1.1050, 1.1020, 1.1030) for i in range(10)]

        # Bullish candle at idx 3
        linhas[3] = _vela(ts[3], 1.1020, 1.1055, 1.1015, 1.1040)  # bullish

        # Bearish MSS at idx 4: closes below v3.abertura=1.1020
        linhas[4] = _vela(ts[4], 1.1040, 1.1045, 1.1000, 1.1010)  # close < 1.1020

        return _df(linhas)

    def test_mss_bullish_detectado(self):
        df = self._df_mss_bullish()
        result = detectar_mss_no_poi(df, 1.1000, 1.1060, "ALTA", "EURUSD")
        assert result is not None
        assert result.tipo_confirmacao == "MSS"

    def test_mss_bearish_detectado(self):
        df = self._df_mss_bearish()
        result = detectar_mss_no_poi(df, 1.1000, 1.1060, "BAIXA", "EURUSD")
        assert result is not None
        assert result.tipo_confirmacao == "MSS"

    def test_sem_mss_retorna_none(self):
        ts = _ts(10)
        linhas = [_vela(ts[i], 1.1030, 1.1050, 1.1020, 1.1030) for i in range(10)]
        df = _df(linhas)
        result = detectar_mss_no_poi(df, 1.1000, 1.1060, "ALTA", "EURUSD")
        assert result is None

    def test_velas_fora_da_poi(self):
        ts = _ts(10)
        linhas = [_vela(ts[i], 1.1200, 1.1210, 1.1190, 1.1200) for i in range(10)]
        df = _df(linhas)
        result = detectar_mss_no_poi(df, 1.1000, 1.1060, "ALTA", "EURUSD")
        assert result is None

    def test_mss_retorna_sl_tp(self):
        df = self._df_mss_bullish()
        result = detectar_mss_no_poi(df, 1.1000, 1.1060, "ALTA", "EURUSD")
        assert result is not None
        assert result.sl <= result.preco_confirmacao  # SL below entry for ALTA
        assert result.tp > result.preco_confirmacao   # TP above entry for ALTA
        assert result.rr == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# calcular_score_setup
# ---------------------------------------------------------------------------

class TestCalcularScoreSetup:
    def test_score_maximo(self):
        ev = _evento("ChoCH", "ALTA")
        leg = _leg("ALTA", eh_displacement=True)
        pool = _pool("EQH")
        score = calcular_score_setup(
            ev, leg, pool, None, 0.001,
            tem_overlap_ob_fvg=True,
            em_sessao=True,
            zona_ok=True,
            bias_alinhado=True,
        )
        assert score >= 70

    def test_score_minimo_sem_nada(self):
        ev = _evento("BOS", "ALTA")
        pool = _pool("PDH")
        score = calcular_score_setup(ev, None, pool, None, 0.001)
        assert score == 0

    def test_choch_adiciona_20(self):
        ev_choch = _evento("ChoCH", "ALTA")
        ev_bos = _evento("BOS", "ALTA")
        pool = _pool("PDH")
        s1 = calcular_score_setup(ev_choch, None, pool, None, 0.001)
        s2 = calcular_score_setup(ev_bos, None, pool, None, 0.001)
        assert s1 - s2 == 20

    def test_displacement_adiciona_15(self):
        ev = _evento("BOS", "ALTA")
        pool = _pool("PDH")
        leg_disp = _leg("ALTA", eh_displacement=True)
        leg_norm = _leg("ALTA", eh_displacement=False)
        s1 = calcular_score_setup(ev, leg_disp, pool, None, 0.001)
        s2 = calcular_score_setup(ev, leg_norm, pool, None, 0.001)
        assert s1 - s2 == 15

    def test_eqh_adiciona_10_vs_pdh(self):
        ev = _evento("BOS", "ALTA")
        pool_eqh = _pool("EQH")
        pool_pdh = _pool("PDH")
        s1 = calcular_score_setup(ev, None, pool_eqh, None, 0.001)
        s2 = calcular_score_setup(ev, None, pool_pdh, None, 0.001)
        assert s1 - s2 == 10


# ---------------------------------------------------------------------------
# _calcular_risco_rr_v2
# ---------------------------------------------------------------------------

class TestCalcularRiscoRrV2:
    def test_alta_sl_abaixo_entrada(self):
        sl, tp, rr = _calcular_risco_rr_v2(1.1100, 1.1000, 1.1060, "ALTA")
        assert sl == pytest.approx(1.1000)
        assert tp == pytest.approx(1.1100 + 2 * (1.1100 - 1.1000))
        assert rr == pytest.approx(2.0)

    def test_baixa_sl_acima_entrada(self):
        sl, tp, rr = _calcular_risco_rr_v2(1.1050, 1.1000, 1.1100, "BAIXA")
        assert sl == pytest.approx(1.1100)
        assert tp == pytest.approx(1.1050 - 2 * (1.1100 - 1.1050))
        assert rr == pytest.approx(2.0)

    def test_rr_sempre_2(self):
        for direcao, entrada, f, t in [
            ("ALTA", 1.1050, 1.1000, 1.1060),
            ("BAIXA", 1.1030, 1.1000, 1.1060),
        ]:
            _, _, rr = _calcular_risco_rr_v2(entrada, f, t, direcao)
            assert rr == pytest.approx(2.0)
