from datetime import datetime, timezone

import pandas as pd
import pytest

from smc.filtros import (
    calcular_bias_d1,
    calcular_risco_rr,
    verificar_sessao,
    verificar_zona_premium_discount,
)
from smc.analisador_smc import OrderBlock


def _ts(n: int, inicio: str = "2024-01-01") -> list[str]:
    base = pd.Timestamp(inicio, tz="UTC")
    return [(base + pd.Timedelta(days=i)).isoformat() for i in range(n)]


def _vela_d1(tempo_str: str, abertura: float, maxima: float, minima: float, fechamento: float) -> dict:
    return {
        "tempo": pd.Timestamp(tempo_str, tz="UTC"),
        "abertura": abertura,
        "maxima": maxima,
        "minima": minima,
        "fechamento": fechamento,
        "volume": 1000,
    }


def _df(velas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(velas)


def _ob(direcao: str, fundo: float, topo: float) -> OrderBlock:
    return OrderBlock(
        id="ob_t", simbolo="EURUSD", direcao=direcao,
        preco_topo=topo, preco_fundo=fundo,
        tempo=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# verificar_sessao
# ---------------------------------------------------------------------------

class TestVerificarSessao:
    def _tempo(self, hora: int, minuto: int = 0) -> datetime:
        return datetime(2024, 1, 15, hora, minuto, tzinfo=timezone.utc)

    def test_london_valido(self):
        # F1: 09:00 UTC — dentro de London (08:00-11:00)
        assert verificar_sessao(self._tempo(9)) is True

    def test_ny_valido(self):
        # F2: 14:00 UTC — dentro de NY (13:00-17:00)
        assert verificar_sessao(self._tempo(14)) is True

    def test_asia_invalido(self):
        # F3: 05:00 UTC — sessão asiática, fora de London/NY
        assert verificar_sessao(self._tempo(5)) is False

    def test_entre_sessoes_invalido(self):
        # F4: 11:30 UTC — entre London (fim 11:00) e NY (início 13:00)
        assert verificar_sessao(self._tempo(11, 30)) is False

    def test_london_exatamente_inicio(self):
        assert verificar_sessao(self._tempo(8, 0)) is True

    def test_london_exatamente_fim_invalido(self):
        # 11:00 é o fim exclusivo da sessão London
        assert verificar_sessao(self._tempo(11, 0)) is False

    def test_ny_exatamente_inicio(self):
        assert verificar_sessao(self._tempo(13, 0)) is True

    def test_ny_exatamente_fim_invalido(self):
        # 17:00 é o fim exclusivo da sessão NY
        assert verificar_sessao(self._tempo(17, 0)) is False


# ---------------------------------------------------------------------------
# calcular_bias_d1
# ---------------------------------------------------------------------------

class TestCalcularBiasD1:
    """
    Fixtures com zigzag de valores ÚNICOS (sem candles com mesmo high/low).
    Isso garante que apenas os swings estruturais sejam detectados.
    periodo_swing=3 para janelas menores e controle preciso.

    ALTA (HH+HL):
      SL1 idx=4  minima=1.0800
      SH1 idx=9  maxima=1.1200
      SL2 idx=13 minima=1.0880 > SL1 (HL)
      SH2 idx=18 maxima=1.1350 > SH1 (HH)

    BAIXA (LH+LL):
      SH1 idx=4  maxima=1.1200
      SL1 idx=9  minima=1.0850
      SH2 idx=13 maxima=1.1150 < SH1 (LH)
      SL2 idx=18 minima=1.0800 < SL1 (LL)
    """

    def _velas_alta(self) -> pd.DataFrame:
        ts = _ts(20)
        # Cada candle tem valores únicos de high e low
        dados = [
            # (abertura,  maxima,  minima, fechamento)
            (1.1005, 1.1015, 1.0915, 1.1010),  # 0
            (1.1005, 1.1010, 1.0910, 1.1005),  # 1
            (1.1000, 1.1005, 1.0905, 1.1000),  # 2
            (1.0995, 1.1000, 1.0900, 1.0995),  # 3
            (1.0850, 1.0920, 1.0800, 1.0870),  # 4 ← SL1
            (1.0890, 1.0990, 1.0855, 1.0940),  # 5
            (1.0920, 1.1020, 1.0895, 1.0970),  # 6
            (1.0960, 1.1060, 1.0930, 1.1010),  # 7
            (1.1000, 1.1100, 1.0960, 1.1050),  # 8
            (1.1150, 1.1200, 1.0990, 1.1170),  # 9 ← SH1
            (1.1160, 1.1160, 1.0980, 1.1010),  # 10
            (1.1120, 1.1120, 1.0965, 1.0970),  # 11
            (1.1085, 1.1085, 1.0945, 1.0940),  # 12
            (1.0920, 1.1070, 1.0880, 1.0950),  # 13 ← SL2
            (1.0940, 1.1090, 1.0910, 1.1060),  # 14
            (1.0970, 1.1130, 1.0940, 1.1090),  # 15
            (1.1010, 1.1180, 1.0970, 1.1130),  # 16
            (1.1060, 1.1240, 1.1000, 1.1180),  # 17
            (1.1300, 1.1350, 1.1040, 1.1320),  # 18 ← SH2
            (1.1290, 1.1300, 1.0990, 1.1250),  # 19 (n-1, excluído de candidatos)
        ]
        return _df([_vela_d1(ts[i], *d) for i, d in enumerate(dados)])

    def _velas_baixa(self) -> pd.DataFrame:
        ts = _ts(20)
        dados = [
            (1.1085, 1.1085, 1.0985, 1.1000),  # 0
            (1.1090, 1.1090, 1.0990, 1.1005),  # 1
            (1.1095, 1.1095, 1.0995, 1.1010),  # 2
            (1.1100, 1.1100, 1.1000, 1.1015),  # 3
            (1.1160, 1.1200, 1.1080, 1.1170),  # 4 ← SH1
            (1.1140, 1.1175, 1.1040, 1.1050),  # 5
            (1.1100, 1.1155, 1.1005, 1.1010),  # 6
            (1.1060, 1.1125, 1.0960, 1.0970),  # 7
            (1.1020, 1.1090, 1.0920, 1.0930),  # 8
            (1.0870, 1.1050, 1.0850, 1.0890),  # 9 ← SL1
            (1.0890, 1.1060, 1.0880, 1.0910),  # 10
            (1.0920, 1.1070, 1.0900, 1.0940),  # 11
            (1.0950, 1.1080, 1.0920, 1.0960),  # 12
            (1.1050, 1.1150, 1.0960, 1.1080),  # 13 ← SH2
            (1.1050, 1.1130, 1.0940, 1.1020),  # 14
            (1.1020, 1.1100, 1.0910, 1.0990),  # 15
            (1.0990, 1.1070, 1.0880, 1.0960),  # 16
            (1.0960, 1.1040, 1.0845, 1.0930),  # 17
            (1.0820, 1.1010, 1.0800, 1.0850),  # 18 ← SL2
            (1.0840, 1.0990, 1.0810, 1.0870),  # 19 (n-1, excluído)
        ]
        return _df([_vela_d1(ts[i], *d) for i, d in enumerate(dados)])

    def test_bias_alta(self):
        # F5: HH + HL → "ALTA"
        df = self._velas_alta()
        assert calcular_bias_d1(df, periodo_swing=3) == "ALTA"

    def test_bias_baixa(self):
        # F6: LH + LL → "BAIXA"
        df = self._velas_baixa()
        assert calcular_bias_d1(df, periodo_swing=3) == "BAIXA"

    def test_bias_lateral_retorna_none(self):
        # F7: HH + LL (conflitante) → None
        # Usa velas_alta mas troca SL2 por valor MENOR que SL1 → LL em vez de HL
        df = self._velas_alta().copy()
        ts = _ts(20)
        # Substitui índice 13 com minima=1.0780 < SL1=1.0800 → LL + HH = lateral
        df.iloc[13] = _vela_d1(ts[13], 1.0820, 1.1070, 1.0780, 1.0850)
        resultado = calcular_bias_d1(df, periodo_swing=3)
        assert resultado is None

    def test_poucos_swings_retorna_none(self):
        # F8: DataFrame pequeno demais para ter 2 swings confirmados
        ts = _ts(5)
        velas = [_vela_d1(ts[i], 1.1000 + i*0.001, 1.1050 + i*0.001, 1.0950 + i*0.001, 1.1010 + i*0.001) for i in range(5)]
        df = _df(velas)
        assert calcular_bias_d1(df, periodo_swing=3) is None


# ---------------------------------------------------------------------------
# verificar_zona_premium_discount
# ---------------------------------------------------------------------------

class TestVerificarZonaPremiumDiscount:
    def _velas_range(self) -> pd.DataFrame:
        """20 velas D1 com range high=1.1200, low=1.1000 → midpoint=1.1100."""
        ts = _ts(20)
        return _df([_vela_d1(ts[i], 1.1100, 1.1200, 1.1000, 1.1100) for i in range(20)])

    def test_alta_em_desconto(self):
        # F9: ALTA, preço abaixo do midpoint (1.1050 < 1.1100) → True
        df = self._velas_range()
        assert verificar_zona_premium_discount(df, 1.1050, "ALTA") is True

    def test_alta_em_premium(self):
        # F10: ALTA, preço acima do midpoint (1.1150 > 1.1100) → False
        df = self._velas_range()
        assert verificar_zona_premium_discount(df, 1.1150, "ALTA") is False

    def test_baixa_em_premium(self):
        # F11: BAIXA, preço acima do midpoint (1.1150 > 1.1100) → True
        df = self._velas_range()
        assert verificar_zona_premium_discount(df, 1.1150, "BAIXA") is True

    def test_baixa_em_desconto(self):
        df = self._velas_range()
        assert verificar_zona_premium_discount(df, 1.1050, "BAIXA") is False

    def test_df_vazio_retorna_false(self):
        df = _df([_vela_d1("2024-01-01", 1.1000, 1.1050, 1.0950, 1.1010)])
        assert verificar_zona_premium_discount(df, 1.1025, "ALTA") is False


# ---------------------------------------------------------------------------
# calcular_risco_rr
# ---------------------------------------------------------------------------

class TestCalcularRiscoRr:
    def test_alta_rr_1_2(self):
        # F12: entrada=1.1050, ob.fundo=1.1000 → risco=0.0050, tp=1.1150, rr=2.0
        ob = _ob("ALTA", fundo=1.1000, topo=1.1060)
        sl, tp, rr = calcular_risco_rr(1.1050, ob, "ALTA")
        assert sl == pytest.approx(1.1000)
        assert tp == pytest.approx(1.1150)
        assert rr == pytest.approx(2.0)

    def test_baixa_rr_1_2(self):
        # F13: entrada=1.1050, ob.topo=1.1100 → risco=0.0050, tp=1.0950, rr=2.0
        ob = _ob("BAIXA", fundo=1.1040, topo=1.1100)
        sl, tp, rr = calcular_risco_rr(1.1050, ob, "BAIXA")
        assert sl == pytest.approx(1.1100)
        assert tp == pytest.approx(1.0950)
        assert rr == pytest.approx(2.0)
