from datetime import datetime

import pandas as pd

from smc.analisador_smc import calcular_swings, calcular_swings_confirmados, detectar_eventos_estrutura
from smc.configuracoes import (
    SESSAO_LONDON_FIM,
    SESSAO_LONDON_INICIO,
    SESSAO_NY_FIM,
    SESSAO_NY_INICIO,
)


def verificar_sessao(tempo: datetime) -> bool:
    hora = tempo.hour + tempo.minute / 60.0
    return (SESSAO_LONDON_INICIO <= hora < SESSAO_LONDON_FIM) or (SESSAO_NY_INICIO <= hora < SESSAO_NY_FIM)


def calcular_bias_d1(velas_d1: pd.DataFrame, periodo_swing: int) -> str | None:
    swings_high, swings_low = calcular_swings(velas_d1, periodo_swing)

    highs = sorted(swings_high.items())
    lows = sorted(swings_low.items())

    if len(highs) < 2 or len(lows) < 2:
        return None

    sh1, sh2 = highs[-2][1], highs[-1][1]
    sl1, sl2 = lows[-2][1], lows[-1][1]

    if sh2 > sh1 and sl2 > sl1:
        return "ALTA"
    if sh2 < sh1 and sl2 < sl1:
        return "BAIXA"
    return None


def verificar_zona_premium_discount(
    velas_d1: pd.DataFrame,
    preco_atual: float,
    direcao: str,
) -> bool:
    if len(velas_d1) < 2:
        return False
    janela = velas_d1.tail(20)
    range_high = float(janela["maxima"].max())
    range_low = float(janela["minima"].min())
    midpoint = (range_high + range_low) / 2.0
    if direcao == "ALTA":
        return preco_atual < midpoint
    return preco_atual > midpoint


def calcular_risco_rr(
    preco_entrada: float,
    ob,
    direcao: str,
) -> tuple[float, float, float]:
    if direcao == "ALTA":
        sl = ob.preco_fundo
        risco = preco_entrada - sl
        tp = preco_entrada + 2.0 * risco
    else:
        sl = ob.preco_topo
        risco = sl - preco_entrada
        tp = preco_entrada - 2.0 * risco
    return sl, tp, 2.0


def calcular_bias_d1_v2(velas_d1: pd.DataFrame, simbolo: str, periodo_swing: int) -> str | None:
    eventos = detectar_eventos_estrutura(velas_d1, simbolo, periodo_swing)
    if not eventos:
        return None
    ultimo = eventos[-1]
    # ChoCH = reversão potencial; bias só confirmado após BOS subsequente na mesma direção
    return ultimo.direcao if ultimo.tipo == "BOS" else None


def verificar_zona_premium_discount_v2(
    velas_d1: pd.DataFrame,
    preco_atual: float,
    direcao: str,
    periodo_swing: int,
) -> bool:
    swings = calcular_swings_confirmados(velas_d1, periodo_swing)
    highs = [s for s in swings if s.tipo == "HIGH"]
    lows = [s for s in swings if s.tipo == "LOW"]
    if not highs or not lows:
        return False
    last_high = max(highs, key=lambda s: s.indice).preco
    last_low = max(lows, key=lambda s: s.indice).preco
    equilibrium = (last_high + last_low) / 2.0
    if direcao == "ALTA":
        return preco_atual < equilibrium
    return preco_atual > equilibrium
