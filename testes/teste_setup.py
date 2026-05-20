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
        Bullish MSS canônico (9 candles, todos dentro da POI [1.1000, 1.1060]):
          - c2: micro-swing LOW  (minima=1.1010)
          - c4: micro-swing HIGH (maxima=1.1055)
          - c7: confirmação      (close=1.1058 > swing HIGH 1.1055)
        """
        ts = _ts(9)
        linhas = [
            _vela(ts[0], 1.1040, 1.1050, 1.1035, 1.1040),
            _vela(ts[1], 1.1040, 1.1050, 1.1035, 1.1040),
            _vela(ts[2], 1.1035, 1.1045, 1.1010, 1.1015),  # swing LOW
            _vela(ts[3], 1.1020, 1.1035, 1.1015, 1.1030),
            _vela(ts[4], 1.1030, 1.1055, 1.1025, 1.1050),  # swing HIGH
            _vela(ts[5], 1.1045, 1.1050, 1.1040, 1.1045),
            _vela(ts[6], 1.1045, 1.1050, 1.1040, 1.1045),
            _vela(ts[7], 1.1050, 1.1060, 1.1048, 1.1058),  # confirmação MSS
            _vela(ts[8], 1.1055, 1.1060, 1.1050, 1.1055),  # vela ainda aberta
        ]
        return _df(linhas)

    def _df_mss_bearish(self) -> pd.DataFrame:
        """
        Bearish MSS canônico (9 candles, todos dentro da POI [1.1000, 1.1060]):
          - c2: micro-swing HIGH (maxima=1.1055)
          - c4: micro-swing LOW  (minima=1.1005)
          - c7: confirmação      (close=1.0998 < swing LOW 1.1005)
        """
        ts = _ts(9)
        linhas = [
            _vela(ts[0], 1.1040, 1.1050, 1.1035, 1.1040),
            _vela(ts[1], 1.1040, 1.1050, 1.1035, 1.1040),
            _vela(ts[2], 1.1045, 1.1055, 1.1040, 1.1050),  # swing HIGH
            _vela(ts[3], 1.1045, 1.1050, 1.1038, 1.1040),
            _vela(ts[4], 1.1035, 1.1040, 1.1005, 1.1010),  # swing LOW
            _vela(ts[5], 1.1015, 1.1025, 1.1010, 1.1020),
            _vela(ts[6], 1.1020, 1.1030, 1.1015, 1.1025),
            _vela(ts[7], 1.1020, 1.1025, 1.0995, 1.0998),  # confirmação MSS
            _vela(ts[8], 1.1000, 1.1005, 1.0995, 1.1000),  # vela ainda aberta
        ]
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
            ev, leg, pool,
            tem_overlap_ob_fvg=True,
            em_sessao=True,
            zona_ok=True,
            bias_alinhado=True,
        )
        assert score >= 70

    def test_score_minimo_bos_pdh(self):
        ev = _evento("BOS", "ALTA")
        pool = _pool("PDH")
        score = calcular_score_setup(ev, None, pool)
        assert score == 15  # BOS(0) + PDH(+15), sem outros critérios

    def test_choch_adiciona_20(self):
        ev_choch = _evento("ChoCH", "ALTA")
        ev_bos = _evento("BOS", "ALTA")
        pool = _pool("PDH")
        s1 = calcular_score_setup(ev_choch, None, pool)
        s2 = calcular_score_setup(ev_bos, None, pool)
        assert s1 - s2 == 20

    def test_displacement_adiciona_15(self):
        ev = _evento("BOS", "ALTA")
        pool = _pool("PDH")
        leg_disp = _leg("ALTA", eh_displacement=True)
        leg_norm = _leg("ALTA", eh_displacement=False)
        s1 = calcular_score_setup(ev, leg_disp, pool)
        s2 = calcular_score_setup(ev, leg_norm, pool)
        assert s1 - s2 == 15

    def test_pdh_adiciona_15_eqh_adiciona_10(self):
        ev = _evento("BOS", "ALTA")
        pool_eqh = _pool("EQH")
        pool_pdh = _pool("PDH")
        s_eqh = calcular_score_setup(ev, None, pool_eqh)
        s_pdh = calcular_score_setup(ev, None, pool_pdh)
        assert s_eqh == 10
        assert s_pdh == 15
        assert s_pdh - s_eqh == 5  # PDH institucional pondera mais

    def test_zona_virgem_adiciona_5(self):
        ev = _evento("BOS", "ALTA")
        pool = _pool("PDH")
        s_virgem = calcular_score_setup(ev, None, pool, zona_virgem=True)
        s_testada = calcular_score_setup(ev, None, pool, zona_virgem=False)
        assert s_virgem - s_testada == 5

    def test_score_maximo_absoluto_100(self):
        # Todas as condições verdadeiras + PDH + virgem → máximo = 100
        ev = _evento("ChoCH", "ALTA")
        leg = _leg("ALTA", eh_displacement=True)
        pool = _pool("PDH")
        score = calcular_score_setup(
            ev, leg, pool,
            tem_overlap_ob_fvg=True,
            em_sessao=True,
            zona_ok=True,
            bias_alinhado=True,
            zona_virgem=True,
        )
        assert score == 100


# ---------------------------------------------------------------------------
# _calcular_risco_rr_v2
# ---------------------------------------------------------------------------

class TestCalcularRiscoRrV2:
    def test_alta_sl_abaixo_entrada(self):
        result = _calcular_risco_rr_v2(1.1100, 1.1000, 1.1060, "ALTA")
        assert result is not None
        sl, tp, rr = result
        assert sl == pytest.approx(1.1000)
        assert tp == pytest.approx(1.1100 + 2 * (1.1100 - 1.1000))
        assert rr == pytest.approx(2.0)

    def test_baixa_sl_acima_entrada(self):
        result = _calcular_risco_rr_v2(1.1050, 1.1000, 1.1100, "BAIXA")
        assert result is not None
        sl, tp, rr = result
        assert sl == pytest.approx(1.1100)
        assert tp == pytest.approx(1.1050 - 2 * (1.1100 - 1.1050))
        assert rr == pytest.approx(2.0)

    def test_rr_sempre_2(self):
        for direcao, entrada, f, t in [
            ("ALTA", 1.1050, 1.1000, 1.1060),
            ("BAIXA", 1.1030, 1.1000, 1.1060),
        ]:
            result = _calcular_risco_rr_v2(entrada, f, t, direcao)
            assert result is not None
            _, _, rr = result
            assert rr == pytest.approx(2.0)

    def test_risco_nulo_retorna_none(self):
        # ALTA: preco_entrada <= poi_fundo → risco <= 0
        result = _calcular_risco_rr_v2(1.1000, 1.1000, 1.1060, "ALTA")
        assert result is None

    def test_risco_negativo_retorna_none(self):
        # BAIXA: preco_entrada >= poi_topo → risco <= 0
        result = _calcular_risco_rr_v2(1.1100, 1.1000, 1.1100, "BAIXA")
        assert result is None

    def test_alta_sl_com_buffer_atr(self):
        # atr=0.0010 → buffer=0.0001 → sl = poi_fundo - 0.0001
        atr = 0.0010
        result = _calcular_risco_rr_v2(1.1100, 1.1000, 1.1060, "ALTA", atr=atr)
        assert result is not None
        sl, tp, rr = result
        assert sl == pytest.approx(1.1000 - 0.1 * atr)
        risco = 1.1100 - sl
        assert tp == pytest.approx(1.1100 + 2.0 * risco)
        assert rr == pytest.approx(2.0)

    def test_baixa_sl_com_buffer_atr(self):
        # atr=0.0010 → buffer=0.0001 → sl = poi_topo + 0.0001
        atr = 0.0010
        result = _calcular_risco_rr_v2(1.1050, 1.1000, 1.1100, "BAIXA", atr=atr)
        assert result is not None
        sl, tp, rr = result
        assert sl == pytest.approx(1.1100 + 0.1 * atr)
        risco = sl - 1.1050
        assert tp == pytest.approx(1.1050 - 2.0 * risco)
        assert rr == pytest.approx(2.0)
