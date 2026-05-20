"""Tests for extrair_legs, detectar_obs_corpo, extrair_fvgs_no_intervalo."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from smc.analisador_smc import (
    FairValueGap,
    calcular_atr,
    calcular_poi_composta,
    detectar_obs_corpo,
    detectar_eventos_estrutura,
    extrair_fvgs_no_intervalo,
    extrair_legs,
    marcar_fvgs_mitigados,
    marcar_obs_v2_mitigados,
)
from smc.modelos import LegImpulso, OrderBlockV2


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


class TestCalcularPoiComposta:
    """Cenários para `calcular_poi_composta` — interseção real OB∩FVG.

    Os objetos são construídos diretamente (sem DataFrame). Os valores de
    `tempo`, `id`, `leg_id` são placeholders — só importam os preços e os
    flags `mitigado`.
    """

    _T = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def _ob(self, fundo: float, topo: float, mitigado: bool = False,
            direcao: str = "ALTA") -> OrderBlockV2:
        return OrderBlockV2(
            id="ob",
            simbolo="EURUSD",
            direcao=direcao,
            preco_topo=topo,
            preco_fundo=fundo,
            zona_50=(topo + fundo) / 2,
            tempo=self._T,
            leg_id="leg",
            mitigado=mitigado,
            testado=False,
        )

    def _fvg(self, fundo: float, topo: float, mitigado: bool = False,
             direcao: str = "ALTA") -> FairValueGap:
        return FairValueGap(
            id="fvg",
            simbolo="EURUSD",
            direcao=direcao,
            preco_topo=topo,
            preco_fundo=fundo,
            tempo=self._T,
            mitigado=mitigado,
            testado=False,
        )

    def test_intersecao_unica(self):
        obs = [self._ob(1.1000, 1.1060)]
        fvgs = [self._fvg(1.1040, 1.1080)]
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.0)
        assert poi_fundo == pytest.approx(1.1040)
        assert poi_topo == pytest.approx(1.1060)

    def test_sem_intersecao_cai_para_obs(self):
        obs = [self._ob(1.1000, 1.1050)]
        fvgs = [self._fvg(1.1070, 1.1100)]
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.0)
        assert poi_fundo == pytest.approx(1.1000)
        assert poi_topo == pytest.approx(1.1050)

    def test_so_obs_retorna_envelope_dos_obs(self):
        obs = [self._ob(1.1000, 1.1050), self._ob(1.1030, 1.1080)]
        fvgs: list[FairValueGap] = []
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.0)
        assert poi_fundo == pytest.approx(1.1000)
        assert poi_topo == pytest.approx(1.1080)

    def test_so_fvgs_retorna_envelope_dos_fvgs(self):
        obs: list[OrderBlockV2] = []
        fvgs = [self._fvg(1.2000, 1.2030), self._fvg(1.2020, 1.2070)]
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.0)
        assert poi_fundo == pytest.approx(1.2000)
        assert poi_topo == pytest.approx(1.2070)

    def test_tudo_mitigado_usa_fallback(self):
        obs = [self._ob(1.1000, 1.1050, mitigado=True)]
        fvgs = [self._fvg(1.1040, 1.1080, mitigado=True)]
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.2345)
        assert poi_fundo == pytest.approx(1.2345)
        assert poi_topo == pytest.approx(1.2345)

    def test_listas_vazias_usa_fallback(self):
        poi_fundo, poi_topo = calcular_poi_composta([], [], 1.5)
        assert poi_fundo == pytest.approx(1.5)
        assert poi_topo == pytest.approx(1.5)

    def test_multiplas_intersecoes_retorna_envelope(self):
        # Par 1: OB [1.1000-1.1030] ∩ FVG [1.1020-1.1040] = [1.1020-1.1030]
        # Par 2: OB [1.1060-1.1090] ∩ FVG [1.1080-1.1100] = [1.1080-1.1090]
        obs = [self._ob(1.1000, 1.1030), self._ob(1.1060, 1.1090)]
        fvgs = [self._fvg(1.1020, 1.1040), self._fvg(1.1080, 1.1100)]
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.0)
        assert poi_fundo == pytest.approx(1.1020)
        assert poi_topo == pytest.approx(1.1090)

    def test_fronteira_coincidente_nao_e_intersecao(self):
        # OB.topo == FVG.fundo → inf == sup; estrito inf < sup falha.
        # Deve cair para o fallback dos OBs.
        obs = [self._ob(1.1000, 1.1050)]
        fvgs = [self._fvg(1.1050, 1.1080)]
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.0)
        assert poi_fundo == pytest.approx(1.1000)
        assert poi_topo == pytest.approx(1.1050)

    def test_ignora_obs_mitigados_em_intersecao(self):
        # OB ativo não interseciona; OB mitigado intersecionaria, mas é ignorado.
        # Resultado: fallback para envelope do OB ativo.
        obs = [
            self._ob(1.1000, 1.1020, mitigado=False),
            self._ob(1.1040, 1.1080, mitigado=True),
        ]
        fvgs = [self._fvg(1.1050, 1.1090)]
        poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, 1.0)
        assert poi_fundo == pytest.approx(1.1000)
        assert poi_topo == pytest.approx(1.1020)


class TestMarcarObsV2Mitigados:
    """OB v2 mitigation at 50% (zona_50) using wick (minima/maxima)."""

    _T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _T1 = datetime(2024, 1, 1, 4, tzinfo=timezone.utc)

    def _ob(self, fundo: float, topo: float, direcao: str = "ALTA") -> OrderBlockV2:
        return OrderBlockV2(
            id="ob",
            simbolo="EURUSD",
            direcao=direcao,
            preco_topo=topo,
            preco_fundo=fundo,
            zona_50=(topo + fundo) / 2,
            tempo=self._T0,
            leg_id="leg",
        )

    def _velas(self, minima: float, maxima: float) -> pd.DataFrame:
        return pd.DataFrame([{
            "tempo": self._T1,
            "abertura": 1.1050,
            "maxima": maxima,
            "minima": minima,
            "fechamento": 1.1050,
            "volume": 100,
        }])

    def test_alta_wick_atinge_zona50_mitiga(self):
        # OB ALTA [1.100-1.110], zona_50=1.105. wick min=1.103 <= 1.105 → mitigado
        ob = self._ob(1.1000, 1.1100)
        marcar_obs_v2_mitigados([ob], self._velas(minima=1.1030, maxima=1.1120))
        assert ob.mitigado is True

    def test_alta_wick_acima_zona50_nao_mitiga(self):
        # min=1.106 > zona_50=1.105 → não mitigado
        ob = self._ob(1.1000, 1.1100)
        marcar_obs_v2_mitigados([ob], self._velas(minima=1.1060, maxima=1.1120))
        assert ob.mitigado is False

    def test_baixa_wick_atinge_zona50_mitiga(self):
        # OB BAIXA [1.100-1.110], zona_50=1.105. maxima=1.107 >= 1.105 → mitigado
        ob = self._ob(1.1000, 1.1100, direcao="BAIXA")
        marcar_obs_v2_mitigados([ob], self._velas(minima=1.1040, maxima=1.1070))
        assert ob.mitigado is True

    def test_baixa_wick_abaixo_zona50_nao_mitiga(self):
        # maxima=1.103 < zona_50=1.105 → não mitigado
        ob = self._ob(1.1000, 1.1100, direcao="BAIXA")
        marcar_obs_v2_mitigados([ob], self._velas(minima=1.0980, maxima=1.1030))
        assert ob.mitigado is False

    def test_vela_anterior_ao_ob_nao_mitiga(self):
        # Vela com tempo < ob.tempo é ignorada pelo filtro
        ob = self._ob(1.1000, 1.1100)
        velas_antes = pd.DataFrame([{
            "tempo": self._T0 - timedelta(hours=4),
            "abertura": 1.1090,
            "maxima": 1.1120,
            "minima": 1.0900,  # bem abaixo de zona_50=1.105
            "fechamento": 1.0950,
            "volume": 100,
        }])
        marcar_obs_v2_mitigados([ob], velas_antes)
        assert ob.mitigado is False


# ---------------------------------------------------------------------------
# marcar_fvgs_mitigados
# ---------------------------------------------------------------------------

class TestMarcarFvgsMitigados:
    """
    FVG mitigation usa close (não wick) a 50% do gap — canônico ICT.
    Wick atingindo meio_gap mas close permanecendo do lado correto = não mitigado.
    """

    _T0 = datetime(2024, 1, 2, tzinfo=timezone.utc)

    def _fvg(self, fundo: float, topo: float, direcao: str = "ALTA") -> FairValueGap:
        return FairValueGap(
            id="fvg-test",
            simbolo="EURUSD",
            direcao=direcao,
            preco_topo=topo,
            preco_fundo=fundo,
            tempo=self._T0,
        )

    def _vela(self, fechamento: float, minima: float | None = None, maxima: float | None = None) -> pd.DataFrame:
        return pd.DataFrame([{
            "tempo": self._T0 + timedelta(hours=4),
            "abertura": fechamento,
            "maxima": maxima if maxima is not None else fechamento + 0.0010,
            "minima": minima if minima is not None else fechamento - 0.0010,
            "fechamento": fechamento,
            "volume": 100,
        }])

    def test_alta_close_atinge_meio_gap_mitiga(self):
        # FVG ALTA [1.1000–1.1100], meio_gap=1.1050; close=1.1050 → mitigado
        fvg = self._fvg(1.1000, 1.1100, "ALTA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1050))
        assert fvg.mitigado is True

    def test_alta_close_abaixo_meio_gap_mitiga(self):
        # close=1.1040 < meio_gap=1.1050 → mitigado
        fvg = self._fvg(1.1000, 1.1100, "ALTA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1040))
        assert fvg.mitigado is True

    def test_alta_wick_atinge_mas_close_acima_nao_mitiga(self):
        # Wick desce até 1.1040 (abaixo de meio_gap=1.1050) mas close=1.1060 → não mitigado
        fvg = self._fvg(1.1000, 1.1100, "ALTA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1060, minima=1.1040))
        assert fvg.mitigado is False

    def test_baixa_close_atinge_meio_gap_mitiga(self):
        # FVG BAIXA [1.1000–1.1100], meio_gap=1.1050; close=1.1050 → mitigado
        fvg = self._fvg(1.1000, 1.1100, "BAIXA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1050))
        assert fvg.mitigado is True

    def test_baixa_close_acima_meio_gap_mitiga(self):
        # close=1.1060 > meio_gap=1.1050 → mitigado
        fvg = self._fvg(1.1000, 1.1100, "BAIXA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1060))
        assert fvg.mitigado is True

    def test_baixa_wick_atinge_mas_close_abaixo_nao_mitiga(self):
        # Wick sobe até 1.1060 (acima de meio_gap=1.1050) mas close=1.1040 → não mitigado
        fvg = self._fvg(1.1000, 1.1100, "BAIXA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1040, maxima=1.1060))
        assert fvg.mitigado is False

    def test_vela_anterior_ao_fvg_nao_mitiga(self):
        # Vela com tempo < fvg.tempo é ignorada
        fvg = self._fvg(1.1000, 1.1100, "ALTA")
        vela_antes = pd.DataFrame([{
            "tempo": self._T0 - timedelta(hours=4),
            "abertura": 1.1040,
            "maxima": 1.1040,
            "minima": 1.0900,
            "fechamento": 1.0900,  # bem abaixo de meio_gap
            "volume": 100,
        }])
        marcar_fvgs_mitigados([fvg], vela_antes)
        assert fvg.mitigado is False


# ---------------------------------------------------------------------------
# testado — OBs e FVGs
# ---------------------------------------------------------------------------

class TestMarcarZonasTestadas:
    """
    Zona testada = wick entrou na zona sem mitigar.
    Zona virgem = testado=False após verificação.
    """

    _T0 = datetime(2024, 1, 2, tzinfo=timezone.utc)

    def _ob(self, fundo: float, topo: float, direcao: str = "ALTA") -> OrderBlockV2:
        return OrderBlockV2(
            id="ob-test",
            simbolo="EURUSD",
            direcao=direcao,
            preco_topo=topo,
            preco_fundo=fundo,
            zona_50=(fundo + topo) / 2,
            tempo=self._T0,
            leg_id="leg-x",
        )

    def _fvg(self, fundo: float, topo: float, direcao: str = "ALTA") -> FairValueGap:
        return FairValueGap(
            id="fvg-test",
            simbolo="EURUSD",
            direcao=direcao,
            preco_topo=topo,
            preco_fundo=fundo,
            tempo=self._T0,
        )

    def _vela(self, fechamento: float, minima: float | None = None, maxima: float | None = None) -> pd.DataFrame:
        return pd.DataFrame([{
            "tempo": self._T0 + timedelta(hours=4),
            "abertura": fechamento,
            "maxima": maxima if maxima is not None else fechamento + 0.0005,
            "minima": minima if minima is not None else fechamento - 0.0005,
            "fechamento": fechamento,
            "volume": 100,
        }])

    # --- OB ALTA ---

    def test_ob_alta_wick_entra_na_zona_sem_mitigar_testado(self):
        # OB ALTA [1.1000–1.1100], zona_50=1.1050
        # minima=1.1060 <= preco_topo=1.1100, mas > zona_50=1.1050 → testado, não mitigado
        ob = self._ob(1.1000, 1.1100)
        marcar_obs_v2_mitigados([ob], self._vela(fechamento=1.1070, minima=1.1060))
        assert ob.testado is True
        assert ob.mitigado is False

    def test_ob_alta_wick_nao_entra_virgem(self):
        # minima=1.1110 > preco_topo=1.1100 → zona não tocada → testado=False
        ob = self._ob(1.1000, 1.1100)
        marcar_obs_v2_mitigados([ob], self._vela(fechamento=1.1120, minima=1.1110))
        assert ob.testado is False
        assert ob.mitigado is False

    def test_ob_alta_mitigado_nao_marca_testado(self):
        # minima=1.1040 <= zona_50=1.1050 → mitigado antes de checar testado
        ob = self._ob(1.1000, 1.1100)
        marcar_obs_v2_mitigados([ob], self._vela(fechamento=1.1060, minima=1.1040))
        assert ob.mitigado is True

    # --- OB BAIXA ---

    def test_ob_baixa_wick_entra_na_zona_sem_mitigar_testado(self):
        # OB BAIXA [1.1000–1.1100], zona_50=1.1050
        # maxima=1.1040 >= preco_fundo=1.1000, mas < zona_50=1.1050 → testado, não mitigado
        ob = self._ob(1.1000, 1.1100, direcao="BAIXA")
        marcar_obs_v2_mitigados([ob], self._vela(fechamento=1.1030, maxima=1.1040))
        assert ob.testado is True
        assert ob.mitigado is False

    # --- FVG ALTA ---

    def test_fvg_alta_wick_entra_no_gap_sem_mitigar_testado(self):
        # FVG ALTA [1.1000–1.1100], meio_gap=1.1050
        # minima=1.1080 < preco_topo=1.1100, close=1.1085 > meio_gap → testado, não mitigado
        fvg = self._fvg(1.1000, 1.1100, "ALTA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1085, minima=1.1080))
        assert fvg.testado is True
        assert fvg.mitigado is False

    def test_fvg_alta_wick_nao_entra_virgem(self):
        # minima=1.1110 > preco_topo=1.1100 → não entrou → testado=False
        fvg = self._fvg(1.1000, 1.1100, "ALTA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1120, minima=1.1110))
        assert fvg.testado is False
        assert fvg.mitigado is False

    # --- FVG BAIXA ---

    def test_fvg_baixa_wick_entra_no_gap_sem_mitigar_testado(self):
        # FVG BAIXA [1.1000–1.1100], meio_gap=1.1050
        # maxima=1.1020 > preco_fundo=1.1000, close=1.1015 < meio_gap → testado, não mitigado
        fvg = self._fvg(1.1000, 1.1100, "BAIXA")
        marcar_fvgs_mitigados([fvg], self._vela(fechamento=1.1015, maxima=1.1020))
        assert fvg.testado is True
        assert fvg.mitigado is False
