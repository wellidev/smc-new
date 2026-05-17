"""Tests for extrair_legs, detectar_obs_corpo, extrair_fvgs_no_intervalo."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from smc.analisador_smc import (
    calcular_atr,
    detectar_obs_corpo,
    detectar_eventos_estrutura,
    extrair_fvgs_no_intervalo,
    extrair_legs,
)
from smc.modelos import LegImpulso


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


def _df_impulso_bullish(n=30) -> pd.DataFrame:
    """
    Creates a bullish displacement leg:
    - Low at idx 4, High at idx 13, preceded by clear opposite swings.
    - Most candles in the leg are bullish with gaps → displacement.
    """
    ts = _ts(n)
    base = 1.1000
    linhas = [_vela(ts[i], base, base + 0.0010, base - 0.0010, base) for i in range(n)]

    # Previous structure: HIGH at idx 2 (for reference)
    linhas[2] = _vela(ts[2], base, base + 0.0050, base - 0.0010, base)

    # Low at idx 4 (clear LOW, start of bullish leg)
    linhas[4] = _vela(ts[4], base, base + 0.0010, base - 0.0080, base - 0.0070)

    # Bullish impulse candles from idx 5 to 12 with gaps (FVG between some)
    prices = [base - 0.0070 + i * 0.0020 for i in range(8)]
    for j, i in enumerate(range(5, 13)):
        op = prices[j]
        cl = prices[j] + 0.0018
        hi = cl + 0.0002
        lo = op - 0.0001
        linhas[i] = _vela(ts[i], op, hi, lo, cl)

    # One bearish candle at idx 6 to make it an OB candidate (reset)
    linhas[6] = _vela(ts[6], prices[2] + 0.0018, prices[2] + 0.0020, prices[2] - 0.0002,
                      prices[2] - 0.0001)

    # High (HH) at idx 13: breaks prior high → BOS/ChoCH ALTA
    h_new = base + 0.0100
    linhas[13] = _vela(ts[13], base + 0.0080, h_new, base + 0.0078, h_new - 0.0002)

    return _df(linhas)


class TestExtrairLegs:
    def test_leg_alta_detectada(self):
        df = _df_impulso_bullish()
        atr = calcular_atr(df)
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        legs = extrair_legs(df, eventos, "EURUSD", 2, atr if atr > 0 else 0.001)
        assert len(legs) > 0
        alta_legs = [l for l in legs if l.direcao == "ALTA"]
        assert len(alta_legs) > 0

    def test_leg_tem_campos_obrigatorios(self):
        df = _df_impulso_bullish()
        atr = calcular_atr(df) or 0.001
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        legs = extrair_legs(df, eventos, "EURUSD", 2, atr)
        for leg in legs:
            assert isinstance(leg, LegImpulso)
            assert leg.range_pontos >= 0
            assert 0.0 <= leg.proporcao_corpo <= 1.0
            assert leg.tempo_fim > leg.tempo_inicio

    def test_leg_id_deterministico(self):
        df = _df_impulso_bullish()
        atr = calcular_atr(df) or 0.001
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        legs1 = extrair_legs(df, eventos, "EURUSD", 2, atr)
        legs2 = extrair_legs(df, eventos, "EURUSD", 2, atr)
        ids1 = {l.id for l in legs1}
        ids2 = {l.id for l in legs2}
        assert ids1 == ids2

    def test_sem_eventos_retorna_vazio(self):
        n = 20
        ts = _ts(n)
        linhas = [_vela(ts[i], 1.1, 1.101, 1.099, 1.1) for i in range(n)]
        df = _df(linhas)
        legs = extrair_legs(df, [], "EURUSD", 2, 0.001)
        assert legs == []

    def test_deduplicacao_mesma_leg(self):
        df = _df_impulso_bullish()
        atr = calcular_atr(df) or 0.001
        eventos = detectar_eventos_estrutura(df, "EURUSD", periodo_swing=2)
        # Duplicate events with same leg boundaries
        legs = extrair_legs(df, eventos + eventos, "EURUSD", 2, atr)
        ids = [l.id for l in legs]
        assert len(ids) == len(set(ids))


class TestDetectarObsCorpo:
    def _df_ob_bullish(self) -> tuple[pd.DataFrame, LegImpulso]:
        """Leg com uma vela bearish seguida de bullish — OB bullish simples."""
        n = 20
        ts = _ts(n)
        base = 1.1000
        linhas = [_vela(ts[i], base, base + 0.0010, base - 0.0010, base) for i in range(n)]

        # Leg from idx 3 to idx 10
        # Last bearish at idx 5 followed by bullish at idx 6
        linhas[5] = _vela(ts[5], base + 0.0030, base + 0.0040, base + 0.0010, base + 0.0015)  # bearish
        linhas[6] = _vela(ts[6], base + 0.0015, base + 0.0060, base + 0.0010, base + 0.0055)  # bullish

        df = _df(linhas)
        from smc.modelos import gerar_id_leg
        leg = LegImpulso(
            id=gerar_id_leg("EURUSD", ts[3], ts[10]),
            simbolo="EURUSD",
            direcao="ALTA",
            tempo_inicio=ts[3],
            tempo_fim=ts[10],
            indice_inicio=3,
            indice_fim=10,
            preco_inicio=base,
            preco_fim=base + 0.0080,
            range_pontos=0.0080,
            atr_multiplo=2.0,
            proporcao_corpo=0.7,
            tem_fvg_interno=True,
            eh_displacement=True,
        )
        return df, leg

    def test_ob_bullish_zona_usa_corpo(self):
        df, leg = self._df_ob_bullish()
        obs = detectar_obs_corpo(df, leg, "EURUSD")
        assert any(o.direcao == "ALTA" for o in obs)
        for ob in obs:
            # Zone must be body (open/close), not range (high/low)
            # preco_topo must be <= maxima, preco_fundo >= minima
            vela = df[df["tempo"] == ob.tempo].iloc[0]
            corpo_topo = max(float(vela["abertura"]), float(vela["fechamento"]))
            corpo_fundo = min(float(vela["abertura"]), float(vela["fechamento"]))
            assert ob.preco_topo == pytest.approx(corpo_topo)
            assert ob.preco_fundo == pytest.approx(corpo_fundo)

    def test_zona_50_calculada(self):
        df, leg = self._df_ob_bullish()
        obs = detectar_obs_corpo(df, leg, "EURUSD")
        for ob in obs:
            assert ob.zona_50 == pytest.approx((ob.preco_topo + ob.preco_fundo) / 2)

    def test_ob_tem_leg_id(self):
        df, leg = self._df_ob_bullish()
        obs = detectar_obs_corpo(df, leg, "EURUSD")
        for ob in obs:
            assert ob.leg_id == leg.id

    def test_leg_muito_curta_retorna_vazio(self):
        n = 20
        ts = _ts(n)
        linhas = [_vela(ts[i], 1.1, 1.101, 1.099, 1.1) for i in range(n)]
        df = _df(linhas)
        from smc.modelos import gerar_id_leg
        leg = LegImpulso(
            id=gerar_id_leg("EURUSD", ts[5], ts[5]),
            simbolo="EURUSD",
            direcao="ALTA",
            tempo_inicio=ts[5],
            tempo_fim=ts[5],
            indice_inicio=5,
            indice_fim=5,
            preco_inicio=1.1,
            preco_fim=1.1,
            range_pontos=0.0,
            atr_multiplo=0.0,
            proporcao_corpo=0.0,
            tem_fvg_interno=False,
            eh_displacement=False,
        )
        obs = detectar_obs_corpo(df, leg, "EURUSD")
        assert obs == []


class TestExtrairFvgsNoIntervalo:
    def _df_com_fvg(self) -> pd.DataFrame:
        """Creates a bullish FVG between candles 5 and 7."""
        n = 20
        ts = _ts(n)
        base = 1.1000
        linhas = [_vela(ts[i], base, base + 0.0010, base - 0.0010, base) for i in range(n)]

        # FVG bullish: v5.maxima < v7.minima
        linhas[5] = _vela(ts[5], base, base + 0.0020, base - 0.0010, base)
        linhas[7] = _vela(ts[7], base, base + 0.0010, base + 0.0030, base)  # minima > v5.maxima

        return _df(linhas)

    def test_fvg_dentro_do_intervalo(self):
        df = self._df_com_fvg()
        fvgs = extrair_fvgs_no_intervalo(df, 4, 12, "EURUSD")
        assert any(f.direcao == "ALTA" for f in fvgs)

    def test_fvg_fora_do_intervalo_nao_retornado(self):
        df = self._df_com_fvg()
        # FVG is at index 5-7, interval 8-15 should not contain it
        fvgs = extrair_fvgs_no_intervalo(df, 8, 15, "EURUSD")
        assert len(fvgs) == 0

    def test_intervalo_curto_sem_crash(self):
        n = 10
        ts = _ts(n)
        linhas = [_vela(ts[i], 1.1, 1.101, 1.099, 1.1) for i in range(n)]
        df = _df(linhas)
        fvgs = extrair_fvgs_no_intervalo(df, 3, 4, "EURUSD")
        assert isinstance(fvgs, list)
