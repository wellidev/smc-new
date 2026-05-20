from datetime import datetime, timezone

import pandas as pd

from smc.analisador_smc import (
    CapturaLiquidez,
    calcular_swings,
    detectar_captura_liquidez,
    pool_varrido_por_sweep,
)
from smc.modelos import PoolLiquidez


# --- helpers ---

def _montar_vela(tempo_str: str, abertura: float, maxima: float, minima: float, fechamento: float) -> dict:
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


def _ts(n: int, inicio: str = "2024-01-01") -> list[str]:
    base = pd.Timestamp(inicio, tz="UTC")
    return [(base + pd.Timedelta(hours=4 * i)).isoformat() for i in range(n)]


# ---------------------------------------------------------------------------
# Captura de Liquidez
# ---------------------------------------------------------------------------

class TestCapturaLiquidez:
    """
    Usa periodo_swing=5 para que a janela de detecção do swing (±5 velas)
    não inclua a vela de sweep colocada no índice 22, evitando falso-negativo.
    Com len=25 e periodo=5, swings são detectados em range(5,20); índice 22
    fica fora da janela de qualquer swing candidato.
    """

    def _velas_sweep(self, pavio_pct: float, direcao: str) -> pd.DataFrame:
        ts = _ts(25)
        # Vela neutra base (não forma swing extremo)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(25)]

        if direcao == "BAIXA":
            # Swing high no índice 12, janela [7:18] com periodo=5
            velas[12] = _montar_vela(ts[12], 1.1000, 1.1200, 1.0950, 1.1100)
            # Sweep: wick acima de 1.1200, fecha abaixo
            range_v = 0.0150
            pavio = pavio_pct * range_v
            high = 1.1200 + 0.0010          # 1.1210
            low  = high - range_v            # 1.1060
            close = high - pavio - 0.0001   # varia com pavio_pct
            open_ = close - 0.0010
            velas[22] = _montar_vela(ts[22], open_, high, low, close)
        else:
            # Swing low no índice 12
            velas[12] = _montar_vela(ts[12], 1.1000, 1.1050, 1.0800, 1.0900)
            range_v = 0.0150
            pavio = pavio_pct * range_v
            low   = 1.0800 - 0.0010         # 1.0790
            high  = low + range_v            # 1.0940
            open_ = low + pavio + 0.0001
            close = open_ + 0.0010
            velas[22] = _montar_vela(ts[22], open_, high, low, close)

        return _df(velas)

    def test_sweep_bearish_valido(self):
        df = self._velas_sweep(0.40, "BAIXA")
        capturas = detectar_captura_liquidez(df, periodo_swing=5, limiar_pavio=0.30)
        assert any(c.direcao == "BAIXA" for c in capturas)

    def test_sweep_rejeitado_pavio_insuficiente(self):
        df = self._velas_sweep(0.20, "BAIXA")
        capturas = detectar_captura_liquidez(df, periodo_swing=5, limiar_pavio=0.30)
        assert not any(c.direcao == "BAIXA" for c in capturas)

    def test_sweep_bullish_valido(self):
        df = self._velas_sweep(0.40, "ALTA")
        capturas = detectar_captura_liquidez(df, periodo_swing=5, limiar_pavio=0.30)
        assert any(c.direcao == "ALTA" for c in capturas)

    def test_captura_propaga_simbolo(self):
        df = self._velas_sweep(0.40, "BAIXA")
        capturas = detectar_captura_liquidez(df, periodo_swing=5, limiar_pavio=0.30, simbolo="EURUSD")
        bearish = [c for c in capturas if c.direcao == "BAIXA"]
        assert len(bearish) >= 1
        assert all(c.simbolo == "EURUSD" for c in bearish)


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------

class TestInternos:
    """Testa funções privadas que implementam comportamentos críticos."""

    def test_ultimo_candle_excluido_de_swing(self):
        ts = _ts(15)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(15)]
        # Último candle (índice 14) tem maxima extrema — não deve virar swing
        velas[14] = _montar_vela(ts[14], 1.1000, 1.2000, 1.0990, 1.1010)

        swings_high, _ = calcular_swings(_df(velas), periodo=5)
        assert 14 not in swings_high


# ---------------------------------------------------------------------------
# pool_varrido_por_sweep
# ---------------------------------------------------------------------------

class TestPoolVarridoPorSweep:
    """
    Valida o matching entre pools de liquidez e eventos CapturaLiquidez.

    Regras:
      - EQH/PDH (liquidez acima) → exigem sweep BAIXA (wick varreu máxima).
      - EQL/PDL (liquidez abaixo) → exigem sweep ALTA (wick varreu mínima).
      - Proximidade limitada por ``pool.tolerancia``.
    """

    @staticmethod
    def _pool(tipo: str, preco: float, tolerancia: float = 0.0010) -> PoolLiquidez:
        return PoolLiquidez(
            id="pool-x",
            simbolo="EURUSD",
            tipo=tipo,
            preco=preco,
            tolerancia=tolerancia,
            tempo=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

    @staticmethod
    def _captura(direcao: str, preco: float) -> CapturaLiquidez:
        return CapturaLiquidez(
            simbolo="EURUSD",
            direcao=direcao,
            preco_varredura=preco,
            tempo=datetime(2024, 1, 1, 4, tzinfo=timezone.utc),
            pavio_percentual=0.40,
        )

    def test_eqh_com_captura_bearish_proxima(self):
        pool = self._pool("EQH", 1.1050)
        capturas = [self._captura("BAIXA", 1.1052)]
        assert pool_varrido_por_sweep(pool, capturas) is not None

    def test_eqh_com_captura_bearish_distante(self):
        # Distância 0.0020 > tolerancia 0.0010 → não varrido
        pool = self._pool("EQH", 1.1050, tolerancia=0.0010)
        capturas = [self._captura("BAIXA", 1.1070)]
        assert pool_varrido_por_sweep(pool, capturas) is None

    def test_eql_com_captura_bullish_proxima(self):
        pool = self._pool("EQL", 1.0950)
        capturas = [self._captura("ALTA", 1.0948)]
        assert pool_varrido_por_sweep(pool, capturas) is not None

    def test_eqh_com_captura_bullish_direcao_errada(self):
        # EQH exige sweep BAIXA; captura ALTA não varre o pool
        pool = self._pool("EQH", 1.1050)
        capturas = [self._captura("ALTA", 1.1050)]
        assert pool_varrido_por_sweep(pool, capturas) is None

    def test_pdh_com_captura_bearish_proxima(self):
        pool = self._pool("PDH", 1.1100)
        capturas = [self._captura("BAIXA", 1.1101)]
        assert pool_varrido_por_sweep(pool, capturas) is not None

    def test_pdl_com_captura_bullish_proxima(self):
        pool = self._pool("PDL", 1.0900)
        capturas = [self._captura("ALTA", 1.0905)]
        # 0.0005 está dentro da tolerância padrão (0.0010)
        assert pool_varrido_por_sweep(pool, capturas) is not None

    def test_lista_vazia_de_capturas(self):
        pool = self._pool("EQH", 1.1050)
        assert pool_varrido_por_sweep(pool, []) is None

    def test_retorna_captura_correta(self):
        # Verifica que a CapturaLiquidez retornada é a que fez o match
        pool = self._pool("EQH", 1.1050)
        c1 = self._captura("BAIXA", 1.1200)  # distante — não varre
        c2 = self._captura("BAIXA", 1.1051)  # próxima — varre
        resultado = pool_varrido_por_sweep(pool, [c1, c2])
        assert resultado is c2


# ---------------------------------------------------------------------------
# Janela de capturas (C3 — alinhada com EQH/EQL: 50 velas)
# ---------------------------------------------------------------------------

class TestJanelaCapturas:
    """
    Sweep a mais de 15 candles do fim (mas dentro de 50) deve ser detectado.

    Design da fixture (n=40, periodo=5):
      - Janela antiga: max(5, 40-16) = 24 → varre [24:39]; sweep no índice 20 → PERDIDO
      - Janela nova:   max(5, 40-51) =  5 → varre [5:39];  sweep no índice 20 → ENCONTRADO
      - Candles neutros com maxima=1.0950 < swing (1.1200) evitam swing-highs intermediários
        na janela [9:20] porque a janela de cada índice 9-13 inclui o índice 14 (1.1200).
    """

    def test_sweep_fora_janela_15_dentro_janela_50(self):
        # 40 velas; swing no índice 14, sweep no índice 20
        ts = _ts(40)
        velas = [_montar_vela(ts[i], 1.1000, 1.0950, 1.0900, 1.0930) for i in range(40)]

        # Swing high no índice 14 (ao menos 6 candles antes do sweep → não entra na janela do swing)
        velas[14] = _montar_vela(ts[14], 1.1000, 1.1200, 1.0950, 1.1100)

        # Sweep no índice 20: wick acima do swing (1.1200), fecha abaixo, pavio 40%
        range_v = 0.0150
        high  = 1.1200 + 0.0010           # 1.1210
        low   = high - range_v             # 1.1060
        close = high - 0.40 * range_v - 0.0001  # 1.1149
        open_ = close - 0.0010
        velas[20] = _montar_vela(ts[20], open_, high, low, close)

        df = _df(velas)
        capturas = detectar_captura_liquidez(df, periodo_swing=5, limiar_pavio=0.30)
        assert any(c.direcao == "BAIXA" for c in capturas)
