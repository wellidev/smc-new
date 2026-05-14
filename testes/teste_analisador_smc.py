from datetime import datetime, timezone

import pandas as pd

from smc.analisador_smc import (
    CapturaLiquidez,
    FairValueGap,
    OrderBlock,
    QuebraEstrutura,
    calcular_swings,
    _marcar_fvgs_testados,
    _marcar_obs_testados,
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

    def test_captura_propaga_simbolo(self):
        df = self._velas_sweep(0.40, "BAIXA")
        capturas = detectar_captura_liquidez(df, periodo_swing=5, limiar_pavio=0.30, simbolo="EURUSD")
        bearish = [c for c in capturas if c.direcao == "BAIXA"]
        assert len(bearish) >= 1
        assert all(c.simbolo == "EURUSD" for c in bearish)


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

    def test_ob_fora_do_horizonte_ignorado(self):
        ts = _ts(200)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(200)]
        # OB bullish no índice 50 — fora do horizonte de 100 velas (inicio=100 para len=200)
        velas[50] = _montar_vela(ts[50], 1.1020, 1.1060, 1.0980, 1.0990)  # bearish
        velas[51] = _montar_vela(ts[51], 1.0990, 1.1120, 1.0985, 1.1100)  # bullish
        velas[52] = _montar_vela(ts[52], 1.1100, 1.1180, 1.1090, 1.1170)  # bullish
        velas[53] = _montar_vela(ts[53], 1.1170, 1.1230, 1.1160, 1.1220)  # bullish

        obs, _ = mapear_zonas_interesse(_df(velas), "EURUSD")
        na_zona_antiga = [ob for ob in obs if ob.direcao == "ALTA" and abs(ob.preco_fundo - 1.0980) < 0.0001]
        assert len(na_zona_antiga) == 0

    def test_ob_bullish_rejeitado_impulso_plano(self):
        ts = _ts(8)
        velas = [_montar_vela(ts[i], 1.0850, 1.0900, 1.0800, 1.0870) for i in range(8)]
        # OB candidate: bearish, corpo=0.0090 → 1.5*corpo=0.0135
        velas[3] = _montar_vela(ts[3], 1.1060, 1.1080, 1.0980, 1.0970)
        # Impulso: corpo[4]=0.0010 (sem engolfo), close[4]==close[5] (não strictly increasing)
        velas[4] = _montar_vela(ts[4], 1.1000, 1.1120, 1.0990, 1.1010)
        velas[5] = _montar_vela(ts[5], 1.1010, 1.1130, 1.1005, 1.1010)  # mesmo close
        velas[6] = _montar_vela(ts[6], 1.1010, 1.1220, 1.1000, 1.1080)

        obs, _ = mapear_zonas_interesse(_df(velas), "EURUSD")
        bullish_na_zona = [ob for ob in obs if ob.direcao == "ALTA" and abs(ob.preco_fundo - 1.0980) < 0.0001]
        assert len(bullish_na_zona) == 0

    def test_ob_ultima_vela_bearish_antes_do_impulso(self):
        # Cenário 19: duas velas bearish consecutivas antes do impulso bullish.
        # Apenas a última (índice 4) deve gerar OB; a primeira (índice 3) é rejeitada
        # porque seu candle seguinte ainda é bearish.
        ts = _ts(9)
        velas = [_montar_vela(ts[i], 1.0850, 1.0900, 1.0800, 1.0870) for i in range(9)]
        # Primeira vela bearish — NÃO deve virar OB
        velas[3] = _montar_vela(ts[3], 1.1060, 1.1080, 1.0940, 1.0980)
        # Segunda vela bearish (última antes do impulso) — deve virar OB, zona [1.0920, 1.1000]
        velas[4] = _montar_vela(ts[4], 1.1000, 1.1000, 1.0920, 1.0940)
        # Impulso bullish: closes estritamente crescentes
        velas[5] = _montar_vela(ts[5], 1.0950, 1.1120, 1.0945, 1.1100)
        velas[6] = _montar_vela(ts[6], 1.1100, 1.1180, 1.1090, 1.1170)
        velas[7] = _montar_vela(ts[7], 1.1170, 1.1230, 1.1160, 1.1220)

        obs, _ = mapear_zonas_interesse(_df(velas), "EURUSD")
        bullish = [ob for ob in obs if ob.direcao == "ALTA"]
        # OB da primeira vela bearish (preco_fundo≈1.0940) não deve aparecer
        ob_primeira = [ob for ob in bullish if abs(ob.preco_fundo - 1.0940) < 0.0001]
        assert len(ob_primeira) == 0
        # OB da segunda vela bearish (preco_fundo≈1.0920) deve aparecer
        ob_ultima = [ob for ob in bullish if abs(ob.preco_fundo - 1.0920) < 0.0001]
        assert len(ob_ultima) == 1


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

    def test_fvg_fora_do_horizonte_ignorado(self):
        ts = _ts(200)
        velas = [_montar_vela(ts[i], 1.0950, 1.1000, 1.0920, 1.0970) for i in range(200)]
        # FVG bearish no índice 50 — fora do horizonte de 100 velas (inicio=100 para len=200)
        velas[50] = _montar_vela(ts[50], 1.1060, 1.1080, 1.1040, 1.1070)  # minima=1.1040
        velas[51] = _montar_vela(ts[51], 1.1070, 1.1090, 1.1050, 1.1080)
        velas[52] = _montar_vela(ts[52], 1.0990, 1.1020, 1.0960, 1.1000)  # maxima=1.1020

        _, fvgs = mapear_zonas_interesse(_df(velas), "EURUSD")
        fvg_antigo = [f for f in fvgs if f.direcao == "BAIXA" and abs(f.preco_topo - 1.1040) < 0.0001]
        assert len(fvg_antigo) == 0

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

    def test_bos_deduplica_mesmo_swing(self):
        ts = _ts(25)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(25)]
        # Swing high em 12
        velas[12] = _montar_vela(ts[12], 1.1000, 1.1200, 1.0950, 1.1100)
        # Três candles consecutivos na janela fechando acima do swing high 1.1200
        velas[20] = _montar_vela(ts[20], 1.1200, 1.1280, 1.1190, 1.1250)
        velas[21] = _montar_vela(ts[21], 1.1250, 1.1310, 1.1240, 1.1280)
        velas[22] = _montar_vela(ts[22], 1.1280, 1.1340, 1.1270, 1.1310)

        quebras = detectar_quebra_estrutura(_df(velas), "EURUSD", periodo_swing=5)
        bos_no_nivel = [q for q in quebras if q.direcao == "ALTA" and abs(q.nivel_rompido - 1.1200) < 0.0001]
        assert len(bos_no_nivel) == 1

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
            swing_tempo=datetime(2024, 1, 1, 12, tzinfo=timezone.utc),  # após captura (00:00)
            tempo=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )

    def _ob(self, direcao: str, mitigado: bool = False) -> OrderBlock:
        return OrderBlock(
            id="ob_test", simbolo="EURUSD", direcao=direcao,
            preco_topo=1.1050, preco_fundo=1.1000,
            tempo=datetime(2023, 12, 31, tzinfo=timezone.utc),  # antes da captura
            mitigado=mitigado,
        )

    def _fvg(self, direcao: str, mitigado: bool = False) -> FairValueGap:
        return FairValueGap(
            id="fvg_test", simbolo="EURUSD", direcao=direcao,
            preco_topo=1.1048, preco_fundo=1.1002,
            tempo=datetime(2023, 12, 31, tzinfo=timezone.utc),  # antes da captura
            mitigado=mitigado,
        )

    def test_confluencia_completa_baixa(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m5=1.1025,
        ) is True

    def test_confluencia_completa_alta(self):
        assert verificar_confluencia(
            self._captura("ALTA"), self._bos("ALTA"),
            [self._ob("ALTA")], [self._fvg("ALTA")],
            preco_atual_m5=1.1025,
        ) is True

    def test_sem_captura(self):
        assert verificar_confluencia(
            None, self._bos("BAIXA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m5=1.1025,
        ) is False

    def test_sem_bos(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), None,
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m5=1.1025,
        ) is False

    def test_bos_direcao_errada(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("ALTA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m5=1.1025,
        ) is False

    def test_ob_mitigado(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [self._ob("BAIXA", mitigado=True)], [self._fvg("BAIXA")],
            preco_atual_m5=1.1025,
        ) is False

    def test_preco_fora_do_ob(self):
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m5=1.1200,  # fora da zona [1.1000-1.1050]
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
            preco_atual_m5=1.1025,
        ) is False

    def test_preco_em_ob_mas_fora_da_sobreposicao(self):
        # Cenário 22 (revisado): preço dentro do OB é suficiente para confluência,
        # mesmo que esteja fora da zona de sobreposição OB∩FVG.
        # OB [1.1000, 1.1060], FVG [1.1035, 1.1080], preço=1.1010 → dentro do OB
        ob = OrderBlock(
            id="ob_p", simbolo="EURUSD", direcao="BAIXA",
            preco_topo=1.1060, preco_fundo=1.1000,
            tempo=datetime(2023, 12, 31, tzinfo=timezone.utc),
            mitigado=False,
        )
        fvg = FairValueGap(
            id="fvg_p", simbolo="EURUSD", direcao="BAIXA",
            preco_topo=1.1080, preco_fundo=1.1035,
            tempo=datetime(2023, 12, 31, tzinfo=timezone.utc),
            mitigado=False,
        )
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [ob], [fvg],
            preco_atual_m5=1.1010,
        ) is True

    def test_ob_pos_captura_aceito(self):
        # Cenário 20 (revisado): OB formado depois da captura é válido —
        # OBs do impulso pós-captura são os POIs mais relevantes para retorno.
        ob_tardio = OrderBlock(
            id="ob_tardio", simbolo="EURUSD", direcao="BAIXA",
            preco_topo=1.1050, preco_fundo=1.1000,
            tempo=datetime(2024, 1, 2, tzinfo=timezone.utc),  # após captura (01-01)
            mitigado=False,
        )
        assert verificar_confluencia(
            self._captura("BAIXA"), self._bos("BAIXA"),
            [ob_tardio], [self._fvg("BAIXA")],
            preco_atual_m5=1.1025,
        ) is True

    def test_bos_com_swing_pre_captura_aceito(self):
        # Cenário 21 (revisado): swing_tempo anterior à captura ainda é válido —
        # o BOS deve ser posterior à captura, mas o swing rompido pode ser pré-existente.
        bos_swing_antigo = QuebraEstrutura(
            simbolo="EURUSD", direcao="BAIXA",
            nivel_rompido=1.1100,
            swing_tempo=datetime(2023, 12, 30, tzinfo=timezone.utc),  # antes da captura
            tempo=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert verificar_confluencia(
            self._captura("BAIXA"), bos_swing_antigo,
            [self._ob("BAIXA")], [self._fvg("BAIXA")],
            preco_atual_m5=1.1025,
        ) is True


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
# OB Testado
# ---------------------------------------------------------------------------

class TestObTestado:
    """
    OB ALTA zona [1.0980, 1.1060].
    Impulso: candles 4-6 abrem a partir de ~1.0990 (dentro/abaixo do topo).
    Retrace: candle que abre ACIMA do topo (1.1060) e cujo wick entra na zona.
    """

    def _velas_ob_alta_com_retrace(self, incluir_retrace: bool) -> pd.DataFrame:
        ts = _ts(9)
        velas = [_montar_vela(ts[i], 1.0850, 1.0900, 1.0800, 1.0870) for i in range(9)]
        # OB ALTA: bearish em [1.0980, 1.1060]
        velas[3] = _montar_vela(ts[3], 1.1020, 1.1060, 1.0980, 1.0990)
        # Impulso bullish — abrem próximos ao close da vela OB (≈1.0990 < topo=1.1060)
        velas[4] = _montar_vela(ts[4], 1.0990, 1.1120, 1.0985, 1.1100)
        velas[5] = _montar_vela(ts[5], 1.1100, 1.1180, 1.1090, 1.1170)
        velas[6] = _montar_vela(ts[6], 1.1170, 1.1230, 1.1160, 1.1220)
        if incluir_retrace:
            # Retrace: abre ACIMA do topo (1.1200 > 1.1060), wick entra na zona (minima=1.1040 <= 1.1060)
            velas[7] = _montar_vela(ts[7], 1.1200, 1.1210, 1.1040, 1.1150)
        else:
            velas[7] = _montar_vela(ts[7], 1.1220, 1.1280, 1.1200, 1.1250)
        return _df(velas)

    def test_ob_virgem_sem_retrace(self):
        # Cenário 23: nenhum candle retorna à zona → testado = False
        df = self._velas_ob_alta_com_retrace(incluir_retrace=False)
        obs, _ = mapear_zonas_interesse(df, "EURUSD")
        ob = next((o for o in obs if o.direcao == "ALTA"), None)
        assert ob is not None
        assert ob.testado is False

    def test_ob_testado_com_retrace(self):
        # Cenário 24: candle abre acima do topo e wick entra na zona → testado = True
        df = self._velas_ob_alta_com_retrace(incluir_retrace=True)
        obs, _ = mapear_zonas_interesse(df, "EURUSD")
        ob = next((o for o in obs if o.direcao == "ALTA"), None)
        assert ob is not None
        assert ob.testado is True

    def test_impulso_nao_marca_como_testado(self):
        # Cenário 25: candles do impulso abrem abaixo do topo → não contam como retrace
        ts = _ts(8)
        velas = [_montar_vela(ts[i], 1.0850, 1.0900, 1.0800, 1.0870) for i in range(8)]
        velas[3] = _montar_vela(ts[3], 1.1020, 1.1060, 1.0980, 1.0990)
        # Impulso: todos abrem abaixo de ob.preco_topo=1.1060
        velas[4] = _montar_vela(ts[4], 1.0990, 1.1120, 1.0985, 1.1100)
        velas[5] = _montar_vela(ts[5], 1.1100, 1.1180, 1.1090, 1.1170)
        velas[6] = _montar_vela(ts[6], 1.1170, 1.1230, 1.1160, 1.1220)

        ob_alvo = OrderBlock(
            id="ob_t", simbolo="EURUSD", direcao="ALTA",
            preco_topo=1.1060, preco_fundo=1.0980,
            tempo=pd.Timestamp(ts[3], tz="UTC").to_pydatetime(),
        )
        _marcar_obs_testados([ob_alvo], _df(velas))
        assert ob_alvo.testado is False

    def test_ob_mitigado_ignorado_por_testados(self):
        # Cenário 26: OB mitigado não deve ser marcado como testado
        ts = _ts(8)
        velas = [_montar_vela(ts[i], 1.0850, 1.0900, 1.0800, 1.0870) for i in range(8)]
        velas[3] = _montar_vela(ts[3], 1.1020, 1.1060, 1.0980, 1.0990)
        # Retrace que abriria como teste
        velas[7] = _montar_vela(ts[7], 1.1200, 1.1210, 1.1040, 1.1150)

        ob_mitigado = OrderBlock(
            id="ob_m", simbolo="EURUSD", direcao="ALTA",
            preco_topo=1.1060, preco_fundo=1.0980,
            tempo=pd.Timestamp(ts[3], tz="UTC").to_pydatetime(),
            mitigado=True,
        )
        _marcar_obs_testados([ob_mitigado], _df(velas))
        assert ob_mitigado.testado is False


# ---------------------------------------------------------------------------
# FVG Testado
# ---------------------------------------------------------------------------

class TestFvgTestado:
    """
    FVG ALTA zona [1.1010, 1.1020] (fundo=v0.maxima, topo=v2.minima).
    Retrace: candle que abre ACIMA do topo (1.1020) e cujo wick entra no gap.
    FVG BAIXA zona [1.1020, 1.1040] (fundo=v2.maxima, topo=v0.minima).
    Retrace: candle que abre ABAIXO do fundo (1.1020) e cujo wick entra no gap.
    """

    def _fvg_alta(self, mitigado: bool = False) -> FairValueGap:
        return FairValueGap(
            id="fvg_a", simbolo="EURUSD", direcao="ALTA",
            preco_topo=1.1020, preco_fundo=1.1010,
            tempo=pd.Timestamp("2024-01-01 08:00:00", tz="UTC").to_pydatetime(),
            mitigado=mitigado,
        )

    def _velas_pos(self, abertura: float, maxima: float, minima: float, fechamento: float) -> pd.DataFrame:
        ts = [
            pd.Timestamp("2024-01-01 04:00:00", tz="UTC"),  # antes do FVG (ignorada)
            pd.Timestamp("2024-01-01 12:00:00", tz="UTC"),  # posterior ao FVG
        ]
        return pd.DataFrame([
            {"tempo": ts[0], "abertura": 1.1000, "maxima": 1.1005, "minima": 1.0995, "fechamento": 1.1002, "volume": 100},
            {"tempo": ts[1], "abertura": abertura, "maxima": maxima, "minima": minima, "fechamento": fechamento, "volume": 100},
        ])

    def test_fvg_virgem_sem_retrace(self):
        # Cenário 27: nenhum candle toca o FVG → testado = False
        fvg = self._fvg_alta()
        # Candle posterior fica bem acima do gap (minima=1.1025 > topo=1.1020)
        df = self._velas_pos(1.1030, 1.1050, 1.1025, 1.1040)
        _marcar_fvgs_testados([fvg], df)
        assert fvg.testado is False

    def test_fvg_testado_com_retrace(self):
        # Cenário 28: candle abre acima do topo e wick entra no gap → testado = True
        fvg = self._fvg_alta()
        # abertura=1.1025 >= topo=1.1020 ✓; minima=1.1015 <= topo=1.1020 ✓
        df = self._velas_pos(1.1025, 1.1030, 1.1015, 1.1022)
        _marcar_fvgs_testados([fvg], df)
        assert fvg.testado is True

    def test_fvg_mitigado_ignorado(self):
        # Cenário 29: FVG mitigado não deve ser marcado como testado
        fvg = self._fvg_alta(mitigado=True)
        df = self._velas_pos(1.1025, 1.1030, 1.1015, 1.1022)
        _marcar_fvgs_testados([fvg], df)
        assert fvg.testado is False

    def test_fvg_baixa_testado(self):
        # Cenário 30: FVG BAIXA — candle abre abaixo do fundo e wick entra no gap
        fvg = FairValueGap(
            id="fvg_b", simbolo="EURUSD", direcao="BAIXA",
            preco_topo=1.1040, preco_fundo=1.1020,
            tempo=pd.Timestamp("2024-01-01 04:00:00", tz="UTC").to_pydatetime(),
        )
        # abertura=1.1015 <= fundo=1.1020 ✓; maxima=1.1025 >= fundo=1.1020 ✓
        df = self._velas_pos(1.1015, 1.1025, 1.1010, 1.1018)
        _marcar_fvgs_testados([fvg], df)
        assert fvg.testado is True


# ---------------------------------------------------------------------------
# Displacement BOS
# ---------------------------------------------------------------------------

class TestDisplacementBos:
    """
    Cenários 31-34: campo deslocamento em QuebraEstrutura.
    Usa periodo_swing=5 e fixture similar aos testes de BOS.
    """

    def _velas_bos_com_gap(self, bullish: bool, criar_gap: bool) -> pd.DataFrame:
        ts = _ts(27)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(27)]

        if bullish:
            # Swing high em índice 12
            velas[12] = _montar_vela(ts[12], 1.1000, 1.1200, 1.0950, 1.1100)
            # BOS candle em índice 22 (close > 1.1200)
            velas[22] = _montar_vela(ts[22], 1.1200, 1.1350, 1.1210, 1.1300)
            if criar_gap:
                # Candle anterior (21): maxima=1.1150, candle posterior (23): minima=1.1180
                # → high[21]=1.1150 < low[23]=1.1180 → gap bullish
                velas[21] = _montar_vela(ts[21], 1.1100, 1.1150, 1.1080, 1.1130)
                velas[23] = _montar_vela(ts[23], 1.1300, 1.1380, 1.1180, 1.1350)
            else:
                # Sem gap: candles adjacentes se sobrepõem
                velas[21] = _montar_vela(ts[21], 1.1100, 1.1250, 1.1080, 1.1200)
                velas[23] = _montar_vela(ts[23], 1.1300, 1.1380, 1.1200, 1.1350)
        else:
            # Swing low em índice 12
            velas[12] = _montar_vela(ts[12], 1.1000, 1.1050, 1.0800, 1.0900)
            # BOS candle em índice 22 (close < 1.0800)
            velas[22] = _montar_vela(ts[22], 1.0800, 1.0840, 1.0650, 1.0700)
            if criar_gap:
                # gap bearish: low[21]=1.0870 > high[23]=1.0840
                velas[21] = _montar_vela(ts[21], 1.0900, 1.0950, 1.0870, 1.0890)
                velas[23] = _montar_vela(ts[23], 1.0700, 1.0840, 1.0620, 1.0680)
            else:
                # Sem gap: candles adjacentes se sobrepõem
                velas[21] = _montar_vela(ts[21], 1.0900, 1.0950, 1.0820, 1.0860)
                velas[23] = _montar_vela(ts[23], 1.0700, 1.0860, 1.0620, 1.0680)

        return _df(velas)

    def test_bos_com_displacement(self):
        # Cenário 31: BOS Bullish onde high[i-1] < low[i+1] → deslocamento=True
        df = self._velas_bos_com_gap(bullish=True, criar_gap=True)
        quebras = detectar_quebra_estrutura(df, "EURUSD", periodo_swing=5)
        bos_alta = [q for q in quebras if q.direcao == "ALTA"]
        assert len(bos_alta) >= 1
        assert any(q.deslocamento is True for q in bos_alta)

    def test_bos_sem_displacement(self):
        # Cenário 32: BOS sem gap entre candles adjacentes → deslocamento=False
        df = self._velas_bos_com_gap(bullish=True, criar_gap=False)
        quebras = detectar_quebra_estrutura(df, "EURUSD", periodo_swing=5)
        bos_alta = [q for q in quebras if q.direcao == "ALTA"]
        assert len(bos_alta) >= 1
        assert all(q.deslocamento is False for q in bos_alta)

    def test_bos_ultima_posicao_sem_crash(self):
        # Cenário 33: BOS próximo ao fim do DataFrame (sem i+1) → deslocamento=False sem crash
        ts = _ts(16)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(16)]
        velas[7] = _montar_vela(ts[7], 1.1000, 1.1200, 1.0950, 1.1100)  # swing high
        # BOS na penúltima vela (índice 14 = len-2), sem candle posterior
        velas[14] = _montar_vela(ts[14], 1.1200, 1.1350, 1.1190, 1.1300)

        quebras = detectar_quebra_estrutura(_df(velas), "EURUSD", periodo_swing=5)
        bos_alta = [q for q in quebras if q.direcao == "ALTA"]
        assert len(bos_alta) >= 1
        assert all(q.deslocamento is False for q in bos_alta)

    def test_calcular_swings_importavel_publicamente(self):
        # Cenário 34: calcular_swings deve ser acessível via import público
        from smc.analisador_smc import calcular_swings  # noqa: F401
        ts = _ts(15)
        velas = [_montar_vela(ts[i], 1.1000, 1.1050, 1.0950, 1.1010) for i in range(15)]
        highs, lows = calcular_swings(_df(velas), periodo=3)
        assert isinstance(highs, dict)
        assert isinstance(lows, dict)
