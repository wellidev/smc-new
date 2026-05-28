from datetime import datetime, timezone

import pandas as pd
import pytest

from smc.filtros import (
    calcular_bias_d1,
    calcular_bias_d1_v2,
    calcular_bias_h4,
    calcular_risco_rr,
    verificar_sessao,
    verificar_zona_premium_discount,
    verificar_zona_premium_discount_v2,
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


# ---------------------------------------------------------------------------
# calcular_bias_d1_v2
# ---------------------------------------------------------------------------

class TestCalcularBiasD1V2:
    """
    Fixtures com 30 D1 candles (periodo_swing=2).

    ChoCH = sinal de reversão potencial; bias incerto até BOS confirmador.
    BOS   = estrutura confirmada na direção; bias definido.

    Fixtures:
      _velas_choch_alta:          downtrend → ChoCH ALTA (sem BOS posterior)
      _velas_choch_baixa:         uptrend  → ChoCH BAIXA (sem BOS posterior)
      _velas_bos_alta:            uptrend puro com dois BOS ALTA
      _velas_bos_baixa:           downtrend puro com dois BOS BAIXA
      _velas_choch_alta_confirmado: downtrend → ChoCH ALTA → BOS ALTA
    """

    def _velas_choch_alta(self) -> pd.DataFrame:
        """H1→L1→H2(LH)→L2(BOS BAIXA)→H3(ChoCH ALTA). Último evento: ChoCH ALTA."""
        n = 30
        ts = _ts(n)
        base, step = 1.1000, 0.0010
        linhas = [_vela_d1(ts[i], base, base + step, base - step, base) for i in range(n)]
        linhas[4]  = _vela_d1(ts[4],  base, base + 0.0040, base - step, base)   # H1
        linhas[7]  = _vela_d1(ts[7],  base, base + step,   base - 0.0040, base) # L1
        linhas[10] = _vela_d1(ts[10], base, base + 0.0020, base - step, base)   # H2 < H1 (LH)
        linhas[13] = _vela_d1(ts[13], base, base + step,   base - 0.0080, base) # L2 < L1 (BOS BAIXA)
        linhas[16] = _vela_d1(ts[16], base, base + 0.0030, base - step, base)   # H3 > H2 (ChoCH ALTA)
        return _df(linhas)

    def _velas_choch_baixa(self) -> pd.DataFrame:
        """L1→H1→L2(HL)→H2(BOS ALTA)→L3(ChoCH BAIXA). Último evento: ChoCH BAIXA."""
        n = 30
        ts = _ts(n)
        base, step = 1.1000, 0.0010
        linhas = [_vela_d1(ts[i], base, base + step, base - step, base) for i in range(n)]
        linhas[4]  = _vela_d1(ts[4],  base, base + step,   base - 0.0040, base) # L1
        linhas[7]  = _vela_d1(ts[7],  base, base + 0.0040, base - step, base)   # H1
        linhas[10] = _vela_d1(ts[10], base, base + step,   base - 0.0020, base) # L2 > L1 (HL)
        linhas[13] = _vela_d1(ts[13], base, base + 0.0080, base - step, base)   # H2 > H1 (BOS ALTA)
        linhas[16] = _vela_d1(ts[16], base, base + step,   base - 0.0030, base) # L3 < L2 (ChoCH BAIXA)
        return _df(linhas)

    def _velas_bos_alta(self) -> pd.DataFrame:
        """H1(idx=8)→H2(idx=18): dois BOS ALTA consecutivos. Último evento: BOS ALTA."""
        n = 30
        ts = _ts(n)
        base, step = 1.1000, 0.0010
        linhas = [_vela_d1(ts[i], base, base + step, base - step, base) for i in range(n)]
        linhas[8]  = _vela_d1(ts[8],  base, base + 0.0040, base - step, base) # H1 → BOS ALTA
        linhas[18] = _vela_d1(ts[18], base, base + 0.0080, base - step, base) # H2 → BOS ALTA (último)
        return _df(linhas)

    def _velas_bos_baixa(self) -> pd.DataFrame:
        """L1(idx=8)→L2(idx=18): dois BOS BAIXA consecutivos. Último evento: BOS BAIXA."""
        n = 30
        ts = _ts(n)
        base, step = 1.1000, 0.0010
        linhas = [_vela_d1(ts[i], base, base + step, base - step, base) for i in range(n)]
        linhas[8]  = _vela_d1(ts[8],  base, base + step, base - 0.0040, base) # L1 → BOS BAIXA
        linhas[18] = _vela_d1(ts[18], base, base + step, base - 0.0080, base) # L2 → BOS BAIXA (último)
        return _df(linhas)

    def _velas_choch_alta_confirmado(self) -> pd.DataFrame:
        """ChoCH ALTA (idx=16) seguido de BOS ALTA (idx=22). Último evento: BOS ALTA."""
        n = 30
        ts = _ts(n)
        base, step = 1.1000, 0.0010
        linhas = [_vela_d1(ts[i], base, base + step, base - step, base) for i in range(n)]
        linhas[4]  = _vela_d1(ts[4],  base, base + 0.0040, base - step, base)   # H1
        linhas[7]  = _vela_d1(ts[7],  base, base + step,   base - 0.0040, base) # L1
        linhas[10] = _vela_d1(ts[10], base, base + 0.0020, base - step, base)   # H2 < H1 (LH)
        linhas[13] = _vela_d1(ts[13], base, base + step,   base - 0.0080, base) # L2 < L1 (BOS BAIXA)
        linhas[16] = _vela_d1(ts[16], base, base + 0.0030, base - step, base)   # H3 > H2 (ChoCH ALTA)
        linhas[22] = _vela_d1(ts[22], base, base + 0.0060, base - step, base)   # H4 > H3 (BOS ALTA)
        return _df(linhas)

    def test_choch_sem_bias_h4_retorna_none(self):
        # F21: ChoCH ALTA sem bias_h4 fornecido → retorna None (D1 em transição sem referência H4)
        assert calcular_bias_d1_v2(self._velas_choch_alta(), "EURUSD", periodo_swing=2) is None

    def test_choch_baixa_sem_bias_h4_retorna_none(self):
        # F22: ChoCH BAIXA sem bias_h4 fornecido → retorna None
        assert calcular_bias_d1_v2(self._velas_choch_baixa(), "EURUSD", periodo_swing=2) is None

    def test_choch_herda_bias_h4_alta(self):
        # F26: ChoCH ALTA + bias_h4="ALTA" → herda "ALTA" do fluxo H4
        assert calcular_bias_d1_v2(self._velas_choch_alta(), "EURUSD", periodo_swing=2, bias_h4="ALTA") == "ALTA"

    def test_choch_herda_bias_h4_contrario(self):
        # F27: ChoCH ALTA + bias_h4="BAIXA" → retorna "BAIXA" (H4 ainda bearish durante transição D1)
        assert calcular_bias_d1_v2(self._velas_choch_alta(), "EURUSD", periodo_swing=2, bias_h4="BAIXA") == "BAIXA"

    def test_bos_alta_retorna_alta(self):
        # F23: BOS ALTA como último evento → "ALTA"
        assert calcular_bias_d1_v2(self._velas_bos_alta(), "EURUSD", periodo_swing=2) == "ALTA"

    def test_bos_baixa_retorna_baixa(self):
        # F24: BOS BAIXA como último evento → "BAIXA"
        assert calcular_bias_d1_v2(self._velas_bos_baixa(), "EURUSD", periodo_swing=2) == "BAIXA"

    def test_choch_confirmado_por_bos_retorna_direcao(self):
        # F25: ChoCH ALTA seguido de BOS ALTA → bias confirmado → "ALTA"
        assert calcular_bias_d1_v2(self._velas_choch_alta_confirmado(), "EURUSD", periodo_swing=2) == "ALTA"

    def test_sem_eventos_retorna_none(self):
        # F16: mercado flat → sem swings → sem eventos → None
        ts = _ts(10)
        linhas = [_vela_d1(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(10)]
        assert calcular_bias_d1_v2(_df(linhas), "EURUSD", periodo_swing=2) is None


# ---------------------------------------------------------------------------
# calcular_bias_h4
# ---------------------------------------------------------------------------

class TestCalcularBiasH4:
    """calcular_bias_h4 retorna direção para qualquer evento (BOS ou ChoCH)."""

    def _velas_bos_alta(self) -> pd.DataFrame:
        ts = _ts(20)
        base = 1.1000
        step = 0.0050
        n = len(ts)
        linhas = [_vela_d1(ts[i], base, base + step, base - step, base) for i in range(n)]
        linhas[4]  = _vela_d1(ts[4],  base, base + 0.0040, base - step, base)
        linhas[7]  = _vela_d1(ts[7],  base, base + step,   base - 0.0040, base)
        linhas[10] = _vela_d1(ts[10], base, base + 0.0060, base - step, base)
        linhas[13] = _vela_d1(ts[13], base, base + step,   base - 0.0020, base)
        return _df(linhas)

    def _velas_choch_alta(self) -> pd.DataFrame:
        ts = _ts(20)
        base = 1.1000
        step = 0.0050
        n = len(ts)
        linhas = [_vela_d1(ts[i], base, base + step, base - step, base) for i in range(n)]
        linhas[4]  = _vela_d1(ts[4],  base, base + step,   base - 0.0040, base)
        linhas[7]  = _vela_d1(ts[7],  base, base + 0.0040, base - step, base)
        linhas[10] = _vela_d1(ts[10], base, base + step,   base - 0.0060, base)
        linhas[13] = _vela_d1(ts[13], base, base + 0.0030, base - step, base)
        return _df(linhas)

    def test_bos_retorna_direcao(self):
        # H4 BOS → retorna a direção confirmada
        assert calcular_bias_h4(self._velas_bos_alta(), "EURUSD", periodo_swing=2) == "ALTA"

    def test_choch_retorna_direcao(self):
        # H4 ChoCH → retorna imediatamente (sem aguardar BOS)
        resultado = calcular_bias_h4(self._velas_choch_alta(), "EURUSD", periodo_swing=2)
        assert resultado in ("ALTA", "BAIXA")  # direcao do ChoCH

    def test_sem_eventos_retorna_none(self):
        ts = _ts(10)
        linhas = [_vela_d1(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(10)]
        assert calcular_bias_h4(_df(linhas), "EURUSD", periodo_swing=2) is None


# ---------------------------------------------------------------------------
# verificar_zona_premium_discount_v2
# ---------------------------------------------------------------------------

class TestVerificarZonaPremiumDiscountV2:
    """
    9 D1 candles, periodo_swing=2:
    - Swing HIGH at idx 2: maxima=1.120 (highest in window 0-4)
    - Swing LOW at idx 5: minima=1.070 (lowest in window 3-7)
    equilibrium = (1.120 + 1.070) / 2 = 1.095
    """

    def _velas_com_swings(self) -> pd.DataFrame:
        ts = _ts(9)
        data = [
            (1.100, 1.105, 1.095, 1.100),  # 0
            (1.103, 1.108, 1.097, 1.103),  # 1
            (1.115, 1.120, 1.105, 1.115),  # 2 ← HIGH
            (1.103, 1.108, 1.097, 1.103),  # 3
            (1.100, 1.105, 1.090, 1.100),  # 4
            (1.075, 1.085, 1.070, 1.075),  # 5 ← LOW
            (1.080, 1.090, 1.075, 1.085),  # 6
            (1.085, 1.095, 1.080, 1.090),  # 7
            (1.090, 1.100, 1.085, 1.095),  # 8
        ]
        return _df([_vela_d1(ts[i], *d) for i, d in enumerate(data)])

    def test_alta_em_desconto(self):
        # F17: ALTA, preco=1.080 < equilibrium=1.095 → True
        assert verificar_zona_premium_discount_v2(self._velas_com_swings(), 1.080, "ALTA", 2) is True

    def test_alta_em_premium(self):
        # F18: ALTA, preco=1.110 > equilibrium=1.095 → False
        assert verificar_zona_premium_discount_v2(self._velas_com_swings(), 1.110, "ALTA", 2) is False

    def test_baixa_em_premium(self):
        # F19: BAIXA, preco=1.110 > equilibrium=1.095 → True
        assert verificar_zona_premium_discount_v2(self._velas_com_swings(), 1.110, "BAIXA", 2) is True

    def test_sem_swings_retorna_false(self):
        # F20: flat 5-candle market → no confirmed swings → False
        ts = _ts(5)
        linhas = [_vela_d1(ts[i], 1.1000, 1.1010, 1.0990, 1.1000) for i in range(5)]
        assert verificar_zona_premium_discount_v2(_df(linhas), 1.1000, "ALTA", 2) is False

    def test_usa_swing_mais_recente_nao_extremo(self):
        """
        26 candles, periodo_swing=3.
        SH1 idx=5:  maxima=1.200 (older, absolute highest)
        SL1 idx=11: minima=1.070 (older, absolute lowest)
        SH2 idx=17: maxima=1.165 (recent, lower than SH1)
        SL2 idx=21: minima=1.085 (recent, higher than SL1)

        New equilibrium = (1.165 + 1.085) / 2 = 1.125  →  preco=1.130 > 1.125 → BAIXA=True
        Old equilibrium = (1.200 + 1.070) / 2 = 1.135  →  preco=1.130 < 1.135 → BAIXA=False
        """
        ts = _ts(26)
        # baseline: maxima=1.100+i*0.0005, minima=1.090+i*0.0005 (slowly ascending)
        linhas = [
            _vela_d1(ts[i], 1.1000, 1.1000 + i * 0.0005, 1.0900 + i * 0.0005, 1.1000)
            for i in range(26)
        ]
        linhas[5]  = _vela_d1(ts[5],  1.1800, 1.2000, 1.0925, 1.1800)  # SH1: max=1.200
        linhas[11] = _vela_d1(ts[11], 1.0800, 1.1055, 1.0700, 1.0800)  # SL1: min=1.070
        linhas[17] = _vela_d1(ts[17], 1.1500, 1.1650, 1.0985, 1.1500)  # SH2: max=1.165
        linhas[21] = _vela_d1(ts[21], 1.0900, 1.1105, 1.0850, 1.0900)  # SL2: min=1.085
        df = _df(linhas)
        assert verificar_zona_premium_discount_v2(df, 1.130, "BAIXA", periodo_swing=3) is True
