from datetime import datetime, timezone

import pandas as pd
import pytest

from smc.analisador_smc import (
    CapturaLiquidez,
    FairValueGap,
    OrderBlock,
    QuebraEstrutura,
    detectar_captura_liquidez,
    detectar_quebra_estrutura,
    mapear_zonas_interesse,
    verificar_confluencia,
)

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


# ---------------------------------------------------------------------------
# Order Blocks
# ---------------------------------------------------------------------------

class TestOrderBlocks:
    """
    OB bullish: última vela bearish antes de impulso bullish de 3 velas.
    Para evitar mitigação involuntária, as velas padrão têm fechamento bem
    abaixo do fundo da zona do OB.
    Fixture: len=8, OB em índice 3, impulso em 4-6, vela[7] abaixo da zona.
    """

    def _velas_ob_bullish(self, com_mitigacao: bool = False) -> pd.DataFrame:
        ts = _ts(8)
        # Padrão: preços bem abaixo da zona do OB para não mitigar
        velas = [_montar_vela(ts[i], 1.0850, 1.0900, 1.0800, 1.0870) for i in range(8)]

        # OB bullish: vela bearish, zona [1.0980, 1.1060]
        velas[3] = _montar_vela(ts[3], 1.1020, 1.1060, 1.0980, 1.0990)

        # Impulso bullish com closes acima do topo do OB (1.1060) → não mitiga
        velas[4] = _montar_vela(ts[4], 1.0990, 1.1120, 1.0985, 1.1100)
        velas[5] = _montar_vela(ts[5], 1.1100, 1.1180, 1.1090, 1.1170)
        velas[6] = _montar_vela(ts[6], 1.1170, 1.1230, 1.1160, 1.1220)

        if com_mitigacao:
            # Fecha dentro da zona [1.0980, 1.1060] → mitiga o OB
            velas[7] = _montar_vela(ts[7], 1.1020, 1.1055, 1.0975, 1.1020)

        return _df(velas)

    def test_ob_bullish_detectado(self):
        df = self._velas_ob_bullish()
        obs, _ = mapear_zonas_interesse(df, "EURUSD")
        bullish = [ob for ob in obs if ob.direcao == "ALTA"]
        assert len(bullish) >= 1

    def test_ob_mitigado_excluido(self):
        df = self._velas_ob_bullish(com_mitigacao=True)
        obs, _ = mapear_zonas_interesse(df, "EURUSD")
        # OB bullish em [1.0980, 1.1060] deve estar mitigado e ausente dos resultados
        bullish_na_zona = [
            ob for ob in obs
            if ob.direcao == "ALTA" and abs(ob.preco_fundo - 1.0980) < 0.0001
        ]
        assert len(bullish_na_zona) == 0

    def test_obs_retornados_nao_sao_mitigados(self):
        df = self._velas_ob_bullish()
        obs, _ = mapear_zonas_interesse(df, "EURUSD")
        for ob in obs:
            assert not ob.mitigado


# ---------------------------------------------------------------------------
# Fair Value Gaps
# ---------------------------------------------------------------------------

class TestFairValueGap:
    """
    FVG bearish: v0.minima > v2.maxima.
    FVG bullish: v0.maxima < v2.minima.
    Velas subsequentes propositalmente fora do intervalo do gap para
    não acionar mitigação.
    """

    def test_fvg_bearish_detectado(self):
        ts = _ts(5)
        # Candle padrão abaixo do gap
        velas = [_montar_vela(ts[i], 1.0950, 1.1000, 1.0920, 1.0970) for i in range(5)]

        # FVG bearish: v[0].minima=1.1040 > v[2].maxima=1.1020
        velas[0] = _montar_vela(ts[0], 1.1060, 1.1080, 1.1040, 1.1070)  # minima=1.1040
        velas[1] = _montar_vela(ts[1], 1.1070, 1.1090, 1.1050, 1.1080)  # vela central
        velas[2] = _montar_vela(ts[2], 1.0990, 1.1020, 1.0960, 1.1000)  # maxima=1.1020

        # Velas 3-4 têm maxima=1.1000 < 1.1040 (topo do FVG) → não mitiga
        _, fvgs = mapear_zonas_interesse(_df(velas), "EURUSD")
        bearish = [f for f in fvgs if f.direcao == "BAIXA"]
        assert len(bearish) >= 1

    def test_fvg_bullish_detectado(self):
        ts = _ts(5)
        # Candle padrão acima do gap
        velas = [_montar_vela(ts[i], 1.1030, 1.1080, 1.1025, 1.1060) for i in range(5)]

        # FVG bullish: v[0].maxima=1.1010 < v[2].minima=1.1020
        velas[0] = _montar_vela(ts[0], 1.0990, 1.1010, 1.0970, 1.1000)  # maxima=1.1010
        velas[1] = _montar_vela(ts[1], 1.1000, 1.1015, 1.0995, 1.1010)  # vela central
        velas[2] = _montar_vela(ts[2], 1.1015, 1.1060, 1.1020, 1.1050)  # minima=1.1020

        # Velas 3-4 têm minima=1.1025 > 1.1010 (fundo do FVG) → não mitiga
        _, fvgs = mapear_zonas_interesse(_df(velas), "EURUSD")
        bullish = [f for f in fvgs if f.direcao == "ALTA"]
        assert len(bullish) >= 1

    def test_fvg_mitigado_excluido(self):
        ts = _ts(5)
        velas = [_montar_vela(ts[i], 1.0950, 1.1000, 1.0920, 1.0970) for i in range(5)]
        # FVG bearish: gap [1.1020, 1.1040]
        velas[0] = _montar_vela(ts[0], 1.1060, 1.1080, 1.1040, 1.1070)
        velas[1] = _montar_vela(ts[1], 1.1070, 1.1090, 1.1050, 1.1080)
        velas[2] = _montar_vela(ts[2], 1.0990, 1.1020, 1.0960, 1.1000)
        # Vela que mitiga: negocia por todo o gap (minima ≤ 1.1020 AND maxima ≥ 1.1040)
        velas[3] = _montar_vela(ts[3], 1.1050, 1.1060, 1.1010, 1.1045)
        velas[4] = _montar_vela(ts[4], 1.0950, 1.1000, 1.0920, 1.0970)

        _, fvgs = mapear_zonas_interesse(_df(velas), "EURUSD")
        bearish_gap = [
            f for f in fvgs
            if f.direcao == "BAIXA" and abs(f.preco_fundo - 1.1020) < 0.0001
        ]
        assert len(bearish_gap) == 0


# ---------------------------------------------------------------------------
# Quebra de Estrutura (BOS)
# ---------------------------------------------------------------------------

class TestQuebraEstrutura:
    """
    Usa periodo_swing=5 pela mesma razão das capturas: janela [7:18] para
    o swing em índice 12 não inclui vela[22], que porta o fechamento de teste.
    """

    def _velas_bos(self, close_final: float, tipo: str) -> pd.DataFrame:
        ts = _ts(25)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(25)]

        if tipo == "alta":
            # Swing high em 12 (maxima=1.1200)
            velas[12] = _montar_vela(ts[12], 1.1000, 1.1200, 1.0950, 1.1100)
        else:
            # Swing low em 12 (minima=1.0800)
            velas[12] = _montar_vela(ts[12], 1.1000, 1.1050, 1.0800, 1.0900)

        # Vela de BOS no índice 22 — dentro da janela de varredura [19:25]
        if tipo == "alta":
            velas[22] = _montar_vela(ts[22], 1.1200, 1.1300, 1.1190, close_final)
        else:
            velas[22] = _montar_vela(ts[22], 1.0810, 1.0850, 1.0750, close_final)

        return _df(velas)

    def test_bos_de_alta(self):
        df = self._velas_bos(1.1250, "alta")  # close=1.1250 > swing_high=1.1200
        quebras = detectar_quebra_estrutura(df, "EURUSD", periodo_swing=5)
        assert any(q.direcao == "ALTA" for q in quebras)

    def test_bos_de_baixa(self):
        df = self._velas_bos(1.0750, "baixa")  # close=1.0750 < swing_low=1.0800
        quebras = detectar_quebra_estrutura(df, "EURUSD", periodo_swing=5)
        assert any(q.direcao == "BAIXA" for q in quebras)

    def test_bos_rejeitado_apenas_wick(self):
        ts = _ts(25)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(25)]
        velas[12] = _montar_vela(ts[12], 1.1000, 1.1200, 1.0950, 1.1100)
        # Wick ultrapassa 1.1200, mas close=1.1150 < 1.1200 → NÃO é BOS
        velas[22] = _montar_vela(ts[22], 1.1100, 1.1250, 1.1090, 1.1150)

        quebras = detectar_quebra_estrutura(_df(velas), "EURUSD", periodo_swing=5)
        bos_acima_swing = [
            q for q in quebras
            if q.direcao == "ALTA" and q.nivel_rompido >= 1.1200
        ]
        assert len(bos_acima_swing) == 0


# ---------------------------------------------------------------------------
# Verificação de Confluência
# ---------------------------------------------------------------------------

class TestVerificarConfluencia:
    def _captura(self, direcao: str) -> CapturaLiquidez:
        return CapturaLiquidez(
            simbolo="EURUSD", direcao=direcao,
            preco_varredura=1.1200,
            tempo=datetime(2024, 1, 1, tzinfo=timezone.utc),
            pavio_percentual=0.40,
        )

    def _bos(self, direcao: str) -> QuebraEstrutura:
        return QuebraEstrutura(
            simbolo="EURUSD", direcao=direcao,
            nivel_rompido=1.1100,
            tempo=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )

    def _ob(self, direcao: str, mitigado: bool = False) -> OrderBlock:
        return OrderBlock(
            id="ob_test", simbolo="EURUSD", direcao=direcao,
            preco_topo=1.1050, preco_fundo=1.1000,
            tempo=datetime(2024, 1, 1, tzinfo=timezone.utc),
            mitigado=mitigado,
        )

    def _fvg(self, direcao: str, mitigado: bool = False) -> FairValueGap:
        return FairValueGap(
            id="fvg_test", simbolo="EURUSD", direcao=direcao,
            preco_topo=1.1048, preco_fundo=1.1002,
            tempo=datetime(2024, 1, 1, tzinfo=timezone.utc),
            mitigado=mitigado,
        )

    def test_confluencia_completa_baixa(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m15=1.1025,
        ) is True

    def test_confluencia_completa_alta(self):
        assert verificar_confluencia(
            self._captura("ALTA"), self._bos("ALTA"),
            [self._ob("ALTA")], [self._fvg("ALTA")],
            preco_atual_m15=1.1025,
        ) is True

    def test_sem_captura(self):
        assert verificar_confluencia(
            None, self._bos("BAIXA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m15=1.1025,
        ) is False

    def test_sem_bos(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), None,
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m15=1.1025,
        ) is False

    def test_bos_direcao_errada(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("ALTA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m15=1.1025,
        ) is False

    def test_ob_mitigado(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [self._ob("BAIXA", mitigado=True)], [self._fvg("BAIXA")],
            preco_atual_m15=1.1025,
        ) is False

    def test_preco_fora_do_ob(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m15=1.1200,  # fora da zona [1.1000-1.1050]
        ) is False

    def test_sem_fvg_sobreposto(self):
        fvg_longe = FairValueGap(
            id="fvg_longe", simbolo="EURUSD", direcao="BAIXA",
            preco_topo=1.1150, preco_fundo=1.1100,
            tempo=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [self._ob("BAIXA")], [fvg_longe],
            preco_atual_m15=1.1025,
        ) is False
