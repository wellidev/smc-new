import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from smc.modelos import (
    ConfirmacaoEntrada,
    EventoEstrutura,
    LegImpulso,
    OrderBlockV2,
    PoolLiquidez,
    SwingPoint,
    gerar_id_confirmacao,
    gerar_id_leg,
    gerar_id_pool,
)

logger = logging.getLogger(__name__)


@dataclass
class OrderBlock:
    id: str
    simbolo: str
    direcao: str
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False
    testado: bool = False


@dataclass
class FairValueGap:
    id: str
    simbolo: str
    direcao: str
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False
    testado: bool = False


@dataclass
class CapturaLiquidez:
    simbolo: str
    direcao: str
    preco_varredura: float
    tempo: datetime
    pavio_percentual: float


def detectar_captura_liquidez(
    velas_h4: pd.DataFrame,
    periodo_swing: int,
    limiar_pavio: float,
    simbolo: str = "",
) -> list[CapturaLiquidez]:
    capturas: list[CapturaLiquidez] = []
    swings_high, swings_low = calcular_swings(velas_h4, periodo_swing)
    swings_bearish_vistos: set[float] = set()
    swings_bullish_vistos: set[float] = set()

    janela_inicio = max(periodo_swing, len(velas_h4) - 15 - 1)
    for i in range(janela_inicio, len(velas_h4)):
        vela = velas_h4.iloc[i]
        range_vela = vela["maxima"] - vela["minima"]
        if range_vela == 0:
            continue

        res_high = _ultimo_swing_anterior(swings_high, i)
        res_low = _ultimo_swing_anterior(swings_low, i)
        swing_high = res_high[1] if res_high is not None else None
        swing_low = res_low[1] if res_low is not None else None

        if swing_high is not None and swing_high not in swings_bearish_vistos:
            pavio_sup = vela["maxima"] - max(vela["abertura"], vela["fechamento"])
            if (vela["maxima"] > swing_high > vela["fechamento"]
                    and pavio_sup / range_vela >= limiar_pavio):
                swings_bearish_vistos.add(swing_high)
                capturas.append(CapturaLiquidez(
                    simbolo=simbolo,
                    direcao="BAIXA",
                    preco_varredura=float(vela["maxima"]),
                    tempo=_tempo_da_vela(vela),
                    pavio_percentual=round(pavio_sup / range_vela, 4),
                ))
                logger.debug("Captura bearish detectada @ %.5f", vela["maxima"])

        if swing_low is not None and swing_low not in swings_bullish_vistos:
            pavio_inf = min(vela["abertura"], vela["fechamento"]) - vela["minima"]
            if (vela["minima"] < swing_low < vela["fechamento"]
                    and pavio_inf / range_vela >= limiar_pavio):
                swings_bullish_vistos.add(swing_low)
                capturas.append(CapturaLiquidez(
                    simbolo=simbolo,
                    direcao="ALTA",
                    preco_varredura=float(vela["minima"]),
                    tempo=_tempo_da_vela(vela),
                    pavio_percentual=round(pavio_inf / range_vela, 4),
                ))
                logger.debug("Captura bullish detectada @ %.5f", vela["minima"])

    return capturas


def mapear_zonas_interesse(
    velas_h4: pd.DataFrame,
    simbolo: str,
) -> tuple[list[OrderBlock], list[FairValueGap]]:
    order_blocks = _detectar_order_blocks(velas_h4, simbolo)
    fvgs = _detectar_fvgs(velas_h4, simbolo)

    # Exclui a última vela (ainda aberta) da marcação — MT5 retorna o tick atual
    # como "fechamento" da vela em andamento, o que causaria mitigação prematura.
    velas_fechadas = velas_h4.iloc[:-1]
    _marcar_obs_mitigados(order_blocks, velas_fechadas)
    _marcar_fvgs_mitigados(fvgs, velas_fechadas)
    _marcar_obs_testados(order_blocks, velas_fechadas)
    _marcar_fvgs_testados(fvgs, velas_fechadas)

    obs_ativos = [ob for ob in order_blocks if not ob.mitigado]
    fvgs_ativos = [fvg for fvg in fvgs if not fvg.mitigado]

    obs_virgens = sum(1 for ob in obs_ativos if not ob.testado)
    fvgs_virgens = sum(1 for fvg in fvgs_ativos if not fvg.testado)
    logger.debug(
        "OBs ativos: %d (%d virgens) | FVGs ativos: %d (%d virgens)",
        len(obs_ativos), obs_virgens, len(fvgs_ativos), fvgs_virgens,
    )
    return obs_ativos, fvgs_ativos


def calcular_swings(
    velas: pd.DataFrame,
    periodo: int,
) -> tuple[dict[int, float], dict[int, float]]:
    swings_high: dict[int, float] = {}
    swings_low: dict[int, float] = {}
    n = len(velas)

    for i in range(periodo, n - 1):
        # Para as últimas `periodo` velas usa apenas as barras disponíveis à direita
        lado_dir = min(periodo, n - 1 - i)
        janela_max = velas["maxima"].iloc[i - periodo: i + lado_dir + 1]
        janela_min = velas["minima"].iloc[i - periodo: i + lado_dir + 1]
        if velas["maxima"].iloc[i] == janela_max.max():
            swings_high[i] = float(velas["maxima"].iloc[i])
        if velas["minima"].iloc[i] == janela_min.min():
            swings_low[i] = float(velas["minima"].iloc[i])

    return swings_high, swings_low


def _ultimo_swing_anterior(swings: dict[int, float], indice_atual: int) -> tuple[int, float] | None:
    indices_anteriores = [idx for idx in swings if idx < indice_atual]
    if not indices_anteriores:
        return None
    idx = max(indices_anteriores)
    return idx, swings[idx]


def _detectar_order_blocks(velas: pd.DataFrame, simbolo: str) -> list[OrderBlock]:
    obs: list[OrderBlock] = []

    inicio = max(0, len(velas) - 100)
    for i in range(inicio, len(velas) - 3):
        vela = velas.iloc[i]
        corpo = float(vela["fechamento"]) - float(vela["abertura"])
        e_bearish = corpo < 0
        e_bullish = corpo > 0

        proximas = velas.iloc[i + 1: i + 4]

        if e_bearish:
            proximo_nao_bearish = float(proximas.iloc[0]["fechamento"]) >= float(proximas.iloc[0]["abertura"])
            corpos_proximas = proximas["fechamento"] - proximas["abertura"]
            impulso_bullish = (corpos_proximas > 0).all()
            fechamentos_crescentes = (proximas["fechamento"].diff().dropna() > 0).all()
            corpo_ob = abs(corpo)
            proximo_corpo = float(proximas.iloc[0]["fechamento"]) - float(proximas.iloc[0]["abertura"])
            engolfo = proximo_corpo > 1.5 * corpo_ob if corpo_ob > 0 else False

            if proximo_nao_bearish and (impulso_bullish or fechamentos_crescentes or engolfo):
                obs.append(OrderBlock(
                    id=_gerar_id(simbolo, vela),
                    simbolo=simbolo,
                    direcao="ALTA",
                    preco_topo=float(vela["maxima"]),
                    preco_fundo=float(vela["minima"]),
                    tempo=_tempo_da_vela(vela),
                ))

        if e_bullish:
            proximo_nao_bullish = float(proximas.iloc[0]["fechamento"]) <= float(proximas.iloc[0]["abertura"])
            corpos_proximas = proximas["fechamento"] - proximas["abertura"]
            impulso_bearish = (corpos_proximas < 0).all()
            fechamentos_decrescentes = (proximas["fechamento"].diff().dropna() < 0).all()
            corpo_ob = abs(corpo)
            proximo_corpo = float(proximas.iloc[0]["abertura"]) - float(proximas.iloc[0]["fechamento"])
            engolfo = proximo_corpo > 1.5 * corpo_ob if corpo_ob > 0 else False

            if proximo_nao_bullish and (impulso_bearish or fechamentos_decrescentes or engolfo):
                obs.append(OrderBlock(
                    id=_gerar_id(simbolo, vela),
                    simbolo=simbolo,
                    direcao="BAIXA",
                    preco_topo=float(vela["maxima"]),
                    preco_fundo=float(vela["minima"]),
                    tempo=_tempo_da_vela(vela),
                ))

    return obs


def _detectar_fvgs(velas: pd.DataFrame, simbolo: str) -> list[FairValueGap]:
    fvgs: list[FairValueGap] = []

    inicio = max(0, len(velas) - 100)
    for i in range(inicio, len(velas) - 2):
        v0 = velas.iloc[i]
        v2 = velas.iloc[i + 2]
        tempo_fvg = _tempo_da_vela(velas.iloc[i + 2])

        if float(v0["maxima"]) < float(v2["minima"]):
            fvgs.append(FairValueGap(
                id=_gerar_id(simbolo, velas.iloc[i + 1]),
                simbolo=simbolo,
                direcao="ALTA",
                preco_topo=float(v2["minima"]),
                preco_fundo=float(v0["maxima"]),
                tempo=tempo_fvg,
            ))

        elif float(v0["minima"]) > float(v2["maxima"]):
            fvgs.append(FairValueGap(
                id=_gerar_id(simbolo, velas.iloc[i + 1]),
                simbolo=simbolo,
                direcao="BAIXA",
                preco_topo=float(v0["minima"]),
                preco_fundo=float(v2["maxima"]),
                tempo=tempo_fvg,
            ))

    return fvgs


def _marcar_obs_mitigados(obs: list[OrderBlock], velas: pd.DataFrame) -> None:
    for ob in obs:
        velas_pos = velas[velas["tempo"] > ob.tempo]
        for _, v in velas_pos.iterrows():
            close = float(v["fechamento"])
            # Mitigado apenas quando o fechamento perfura além da zona:
            # OB ALTA (demanda): close abaixo do fundo indica que a zona foi rompida para baixo.
            # OB BAIXA (oferta): close acima do topo indica que a zona foi rompida para cima.
            # Um simples toque ou teste (close dentro da zona) NÃO mitiga — é sinal de entrada válido.
            if ob.direcao == "ALTA" and close < ob.preco_fundo:
                ob.mitigado = True
                break
            elif ob.direcao == "BAIXA" and close > ob.preco_topo:
                ob.mitigado = True
                break


def _marcar_obs_testados(obs: list[OrderBlock], velas: pd.DataFrame) -> None:
    for ob in obs:
        if ob.mitigado:
            continue
        velas_pos = velas[velas["tempo"] > ob.tempo]
        for _, v in velas_pos.iterrows():
            if ob.direcao == "ALTA":
                if float(v["abertura"]) >= ob.preco_topo and float(v["minima"]) <= ob.preco_topo:
                    ob.testado = True
                    break
            else:
                if float(v["abertura"]) <= ob.preco_fundo and float(v["maxima"]) >= ob.preco_fundo:
                    ob.testado = True
                    break


def _marcar_fvgs_testados(fvgs: list[FairValueGap], velas: pd.DataFrame) -> None:
    for fvg in fvgs:
        if fvg.mitigado:
            continue
        velas_pos = velas[velas["tempo"] > fvg.tempo]
        for _, v in velas_pos.iterrows():
            if fvg.direcao == "ALTA":
                if float(v["abertura"]) >= fvg.preco_topo and float(v["minima"]) <= fvg.preco_topo:
                    fvg.testado = True
                    break
            else:
                if float(v["abertura"]) <= fvg.preco_fundo and float(v["maxima"]) >= fvg.preco_fundo:
                    fvg.testado = True
                    break


def _marcar_fvgs_mitigados(fvgs: list[FairValueGap], velas: pd.DataFrame) -> None:
    for fvg in fvgs:
        gap = fvg.preco_topo - fvg.preco_fundo
        meio_gap = fvg.preco_fundo + gap * 0.5
        velas_pos = velas[velas["tempo"] > fvg.tempo]
        for _, v in velas_pos.iterrows():
            # Mitigado quando o preço alcança ao menos 50% do gap
            if fvg.direcao == "ALTA" and float(v["minima"]) <= meio_gap:
                fvg.mitigado = True
                break
            if fvg.direcao == "BAIXA" and float(v["maxima"]) >= meio_gap:
                fvg.mitigado = True
                break


def _gerar_id(simbolo: str, vela: pd.Series) -> str:
    chave = f"{simbolo}{_tempo_da_vela(vela).isoformat()}"
    return hashlib.sha1(chave.encode()).hexdigest()[:16]


def _tempo_da_vela(vela: pd.Series) -> datetime:
    tempo = vela["tempo"]
    if isinstance(tempo, pd.Timestamp):
        return tempo.to_pydatetime()
    if isinstance(tempo, datetime):
        return tempo
    return datetime.fromisoformat(str(tempo))


# ---------------------------------------------------------------------------
# Phase 1 — Foundation: symmetric swings + ATR
# ---------------------------------------------------------------------------

def calcular_swings_confirmados(velas: pd.DataFrame, periodo: int) -> list[SwingPoint]:
    """Symmetric window: requires `periodo` confirmed candles on BOTH sides."""
    result: list[SwingPoint] = []
    n = len(velas)
    # Upper bound exclusive: i + periodo must not include the last (open) candle
    for i in range(periodo, n - 1 - periodo):
        janela_max = velas["maxima"].iloc[i - periodo: i + periodo + 1]
        janela_min = velas["minima"].iloc[i - periodo: i + periodo + 1]
        if float(velas["maxima"].iloc[i]) == janela_max.max():
            result.append(SwingPoint(
                indice=i,
                preco=float(velas["maxima"].iloc[i]),
                tipo="HIGH",
                tempo=_tempo_da_vela(velas.iloc[i]),
            ))
        if float(velas["minima"].iloc[i]) == janela_min.min():
            result.append(SwingPoint(
                indice=i,
                preco=float(velas["minima"].iloc[i]),
                tipo="LOW",
                tempo=_tempo_da_vela(velas.iloc[i]),
            ))
    return sorted(result, key=lambda s: s.indice)


def calcular_atr(velas: pd.DataFrame, periodo: int = 14) -> float:
    """Wilder ATR on closed candles."""
    velas_f = velas.iloc[:-1]
    n = len(velas_f)
    if n < periodo + 1:
        return 0.0

    trs: list[float] = []
    for i in range(1, n):
        high = float(velas_f["maxima"].iloc[i])
        low = float(velas_f["minima"].iloc[i])
        prev_close = float(velas_f["fechamento"].iloc[i - 1])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr = sum(trs[:periodo]) / periodo
    for i in range(periodo, len(trs)):
        atr = (atr * (periodo - 1) + trs[i]) / periodo
    return atr


# ---------------------------------------------------------------------------
# Phase 2 — Structure state machine
# ---------------------------------------------------------------------------

def detectar_eventos_estrutura(
    velas: pd.DataFrame,
    simbolo: str,
    periodo_swing: int,
) -> list[EventoEstrutura]:
    """Detect ChoCH and BOS using an HH/HL/LH/LL state machine."""
    swings = calcular_swings_confirmados(velas, periodo_swing)
    if not swings:
        return []

    eventos: list[EventoEstrutura] = []
    tendencia = "INDEFINIDA"
    ultimo_high: SwingPoint | None = None
    ultimo_low: SwingPoint | None = None

    for swing in sorted(swings, key=lambda s: s.indice):
        if swing.tipo == "HIGH":
            if ultimo_high is None:
                ultimo_high = swing
                continue

            if tendencia == "INDEFINIDA":
                if swing.preco > ultimo_high.preco:
                    tendencia = "ALTA"
                    eventos.append(_evento_estrutura(simbolo, "BOS", "ALTA", ultimo_high, swing, velas))
                ultimo_high = swing

            elif tendencia == "ALTA":
                if swing.preco > ultimo_high.preco:
                    eventos.append(_evento_estrutura(simbolo, "BOS", "ALTA", ultimo_high, swing, velas))
                # LH in bullish trend: update reference silently
                ultimo_high = swing

            else:  # BAIXA
                if swing.preco > ultimo_high.preco:
                    eventos.append(_evento_estrutura(simbolo, "ChoCH", "ALTA", ultimo_high, swing, velas))
                    tendencia = "ALTA"
                ultimo_high = swing

        else:  # LOW
            if ultimo_low is None:
                ultimo_low = swing
                continue

            if tendencia == "INDEFINIDA":
                if swing.preco < ultimo_low.preco:
                    tendencia = "BAIXA"
                    eventos.append(_evento_estrutura(simbolo, "BOS", "BAIXA", ultimo_low, swing, velas))
                ultimo_low = swing

            elif tendencia == "BAIXA":
                if swing.preco < ultimo_low.preco:
                    eventos.append(_evento_estrutura(simbolo, "BOS", "BAIXA", ultimo_low, swing, velas))
                ultimo_low = swing

            else:  # ALTA
                if swing.preco < ultimo_low.preco:
                    eventos.append(_evento_estrutura(simbolo, "ChoCH", "BAIXA", ultimo_low, swing, velas))
                    tendencia = "BAIXA"
                ultimo_low = swing

    return eventos


def _evento_estrutura(
    simbolo: str,
    tipo: str,
    direcao: str,
    ref: SwingPoint,
    novo: SwingPoint,
    velas: pd.DataFrame,
) -> EventoEstrutura:
    desloc = _tem_fvg_adjacente(novo.indice, direcao, velas)
    return EventoEstrutura(
        simbolo=simbolo,
        tipo=tipo,
        direcao=direcao,
        nivel_rompido=ref.preco,
        swing_tempo=ref.tempo,
        tempo=novo.tempo,
        deslocamento=desloc,
    )


def _tem_fvg_adjacente(idx: int, direcao: str, velas: pd.DataFrame) -> bool:
    """True if candle `idx` leaves a gap with its neighbors (displacement proxy)."""
    n = len(velas)
    if idx < 1 or idx + 1 >= n:
        return False
    if direcao == "ALTA":
        return float(velas["maxima"].iloc[idx - 1]) < float(velas["minima"].iloc[idx + 1])
    return float(velas["minima"].iloc[idx - 1]) > float(velas["maxima"].iloc[idx + 1])


# ---------------------------------------------------------------------------
# Phase 3 — Legs + linked POI
# ---------------------------------------------------------------------------

def extrair_legs(
    velas: pd.DataFrame,
    eventos: list[EventoEstrutura],
    simbolo: str,
    periodo_swing: int,
    atr: float,
) -> list[LegImpulso]:
    """Extract impulse legs linked to structure events."""
    swings = calcular_swings_confirmados(velas, periodo_swing)
    swings_ord = sorted(swings, key=lambda s: s.indice)
    swing_por_tempo: dict[datetime, SwingPoint] = {s.tempo: s for s in swings_ord}

    legs: list[LegImpulso] = []
    processados: set[tuple[int, int]] = set()

    for evento in sorted(eventos, key=lambda e: e.tempo):
        ending = swing_por_tempo.get(evento.tempo)
        if ending is None:
            continue

        tipo_oposto = "LOW" if evento.direcao == "ALTA" else "HIGH"
        starting: SwingPoint | None = None
        for s in reversed(swings_ord):
            if s.tipo == tipo_oposto and s.indice < ending.indice:
                starting = s
                break

        if starting is None:
            continue

        key = (starting.indice, ending.indice)
        if key in processados:
            continue
        processados.add(key)

        i0, i1 = starting.indice, ending.indice
        velas_leg = velas.iloc[i0: i1 + 1]
        n_velas = len(velas_leg)

        range_pontos = abs(ending.preco - starting.preco)

        if evento.direcao == "ALTA":
            corp_dir = sum(
                1 for j in range(n_velas)
                if float(velas_leg["fechamento"].iloc[j]) > float(velas_leg["abertura"].iloc[j])
            )
        else:
            corp_dir = sum(
                1 for j in range(n_velas)
                if float(velas_leg["fechamento"].iloc[j]) < float(velas_leg["abertura"].iloc[j])
            )

        proporcao_corpo = corp_dir / n_velas if n_velas > 0 else 0.0
        fvgs_int = extrair_fvgs_no_intervalo(velas, i0, i1, simbolo)
        tem_fvg = len(fvgs_int) > 0
        atr_multiplo = range_pontos / atr if atr > 0 else 0.0
        eh_displacement = atr_multiplo >= 2.0 and proporcao_corpo >= 0.60 and tem_fvg

        leg_id = gerar_id_leg(simbolo, starting.tempo, ending.tempo)
        legs.append(LegImpulso(
            id=leg_id,
            simbolo=simbolo,
            direcao=evento.direcao,
            tempo_inicio=starting.tempo,
            tempo_fim=ending.tempo,
            indice_inicio=i0,
            indice_fim=i1,
            preco_inicio=starting.preco,
            preco_fim=ending.preco,
            range_pontos=round(range_pontos, 6),
            atr_multiplo=round(atr_multiplo, 3),
            proporcao_corpo=round(proporcao_corpo, 3),
            tem_fvg_interno=tem_fvg,
            eh_displacement=eh_displacement,
        ))

    return legs


def detectar_obs_corpo(
    velas: pd.DataFrame,
    leg: LegImpulso,
    simbolo: str,
) -> list[OrderBlockV2]:
    """Order Blocks using candle body (open/close), linked to the given leg."""
    obs: list[OrderBlockV2] = []
    velas_leg = velas.iloc[leg.indice_inicio: leg.indice_fim + 1]
    n = len(velas_leg)
    if n < 2:
        return obs

    if leg.direcao == "ALTA":
        for j in range(n - 1):
            vela = velas_leg.iloc[j]
            if float(vela["fechamento"]) >= float(vela["abertura"]):
                continue
            proximo = velas_leg.iloc[j + 1]
            if float(proximo["fechamento"]) < float(proximo["abertura"]):
                continue
            topo = max(float(vela["abertura"]), float(vela["fechamento"]))
            fundo = min(float(vela["abertura"]), float(vela["fechamento"]))
            obs.append(OrderBlockV2(
                id=_gerar_id_ob_v2(simbolo, leg.id, _tempo_da_vela(vela)),
                simbolo=simbolo,
                direcao="ALTA",
                preco_topo=topo,
                preco_fundo=fundo,
                zona_50=(topo + fundo) / 2,
                tempo=_tempo_da_vela(vela),
                leg_id=leg.id,
            ))
    else:
        for j in range(n - 1):
            vela = velas_leg.iloc[j]
            if float(vela["fechamento"]) <= float(vela["abertura"]):
                continue
            proximo = velas_leg.iloc[j + 1]
            if float(proximo["fechamento"]) > float(proximo["abertura"]):
                continue
            topo = max(float(vela["abertura"]), float(vela["fechamento"]))
            fundo = min(float(vela["abertura"]), float(vela["fechamento"]))
            obs.append(OrderBlockV2(
                id=_gerar_id_ob_v2(simbolo, leg.id, _tempo_da_vela(vela)),
                simbolo=simbolo,
                direcao="BAIXA",
                preco_topo=topo,
                preco_fundo=fundo,
                zona_50=(topo + fundo) / 2,
                tempo=_tempo_da_vela(vela),
                leg_id=leg.id,
            ))

    return obs


def extrair_fvgs_no_intervalo(
    velas: pd.DataFrame,
    indice_inicio: int,
    indice_fim: int,
    simbolo: str,
) -> list[FairValueGap]:
    """FVGs created within the candle interval [indice_inicio, indice_fim]."""
    fvgs: list[FairValueGap] = []
    fim = min(indice_fim - 1, len(velas) - 3)
    for i in range(indice_inicio, fim + 1):
        v0 = velas.iloc[i]
        v2 = velas.iloc[i + 2]
        tempo_fvg = _tempo_da_vela(velas.iloc[i + 1])
        if float(v0["maxima"]) < float(v2["minima"]):
            fvgs.append(FairValueGap(
                id=_gerar_id(simbolo, velas.iloc[i + 1]),
                simbolo=simbolo,
                direcao="ALTA",
                preco_topo=float(v2["minima"]),
                preco_fundo=float(v0["maxima"]),
                tempo=tempo_fvg,
            ))
        elif float(v0["minima"]) > float(v2["maxima"]):
            fvgs.append(FairValueGap(
                id=_gerar_id(simbolo, velas.iloc[i + 1]),
                simbolo=simbolo,
                direcao="BAIXA",
                preco_topo=float(v0["minima"]),
                preco_fundo=float(v2["maxima"]),
                tempo=tempo_fvg,
            ))
    return fvgs


def calcular_poi_composta(
    obs: list[OrderBlockV2],
    fvgs: list[FairValueGap],
    fallback_nivel: float,
) -> tuple[float, float]:
    """Composite POI envelope from active OBs + FVGs of the same leg."""
    ativos = [(o.preco_fundo, o.preco_topo) for o in obs if not o.mitigado]
    ativos += [(f.preco_fundo, f.preco_topo) for f in fvgs if not f.mitigado]
    if not ativos:
        return fallback_nivel, fallback_nivel
    poi_fundo = min(p[0] for p in ativos)
    poi_topo = max(p[1] for p in ativos)
    return poi_fundo, poi_topo


def _marcar_obs_v2_mitigados(obs: list[OrderBlockV2], velas: pd.DataFrame) -> None:
    for ob in obs:
        for _, v in velas[velas["tempo"] > ob.tempo].iterrows():
            close = float(v["fechamento"])
            if ob.direcao == "ALTA" and close < ob.preco_fundo:
                ob.mitigado = True
                break
            elif ob.direcao == "BAIXA" and close > ob.preco_topo:
                ob.mitigado = True
                break


# ---------------------------------------------------------------------------
# Phase 4 — Liquidity pools
# ---------------------------------------------------------------------------

def detectar_eqh_eql(
    velas: pd.DataFrame,
    simbolo: str,
    atr: float,
) -> list[PoolLiquidez]:
    """Detect Equal Highs/Lows: clusters of wicks within 0.1×ATR."""
    if atr <= 0:
        return []

    tolerancia = 0.1 * atr
    pools: list[PoolLiquidez] = []
    n = len(velas)
    janela = min(50, n - 1)
    velas_f = velas.iloc[n - 1 - janela: n - 1]  # last 50 closed candles

    def _construir_pools(valores: list[tuple[float, datetime]], tipo: str) -> list[PoolLiquidez]:
        resultado: list[PoolLiquidez] = []
        vistos: set[int] = set()
        for i, (preco_i, tempo_i) in enumerate(valores):
            cluster = [preco_j for preco_j, _ in valores[i + 1:] if abs(preco_j - preco_i) <= tolerancia]
            if not cluster:
                continue
            todos = [preco_i] + cluster
            centro = sum(todos) / len(todos)
            chave = round(centro / (tolerancia * 2 + 1e-10))
            if chave in vistos:
                continue
            vistos.add(chave)
            # Quantiza o preço ao bucket da tolerância para estabilizar o pool_id
            # entre ciclos: variações sub-tolerância no centro (por mudança de membros
            # do cluster) não devem gerar um hash diferente.
            preco_bucket = round(centro / tolerancia) * tolerancia
            resultado.append(PoolLiquidez(
                id=gerar_id_pool(simbolo, tipo, preco_bucket, tempo_i),
                simbolo=simbolo,
                tipo=tipo,
                preco=centro,
                tolerancia=tolerancia,
                tempo=tempo_i,
            ))
        return resultado

    highs = [(float(velas_f["maxima"].iloc[i]), _tempo_da_vela(velas_f.iloc[i]))
             for i in range(len(velas_f))]
    lows = [(float(velas_f["minima"].iloc[i]), _tempo_da_vela(velas_f.iloc[i]))
            for i in range(len(velas_f))]

    pools.extend(_construir_pools(highs, "EQH"))
    pools.extend(_construir_pools(lows, "EQL"))
    return pools


def detectar_pdh_pdl(
    velas_d1: pd.DataFrame,
    simbolo: str,
    atr: float,
) -> list[PoolLiquidez]:
    """Detect Previous Day High/Low from D1 data."""
    if velas_d1 is None or len(velas_d1) < 2:
        return []

    tolerancia = max(0.1 * atr, 1e-6)
    dia = velas_d1.iloc[-2]
    tempo = _tempo_da_vela(dia)
    return [
        PoolLiquidez(
            id=gerar_id_pool(simbolo, "PDH", float(dia["maxima"]), tempo),
            simbolo=simbolo,
            tipo="PDH",
            preco=float(dia["maxima"]),
            tolerancia=tolerancia,
            tempo=tempo,
        ),
        PoolLiquidez(
            id=gerar_id_pool(simbolo, "PDL", float(dia["minima"]), tempo),
            simbolo=simbolo,
            tipo="PDL",
            preco=float(dia["minima"]),
            tolerancia=tolerancia,
            tempo=tempo,
        ),
    ]


# ---------------------------------------------------------------------------
# Phase 5 — LTF confirmation (MSS on M5)
# ---------------------------------------------------------------------------

def detectar_mss_no_poi(
    velas_m5: pd.DataFrame,
    poi_fundo: float,
    poi_topo: float,
    direcao: str,
    simbolo: str,
) -> ConfirmacaoEntrada | None:
    """Detect Market Structure Shift on M5 within the POI zone."""
    velas_poi = velas_m5[
        (velas_m5["maxima"] >= poi_fundo) & (velas_m5["minima"] <= poi_topo)
    ].reset_index(drop=True)

    if len(velas_poi) < 2:
        return None

    for i in range(1, len(velas_poi)):
        v_prev = velas_poi.iloc[i - 1]
        v_curr = velas_poi.iloc[i]

        if direcao == "ALTA":
            prev_bearish = float(v_prev["fechamento"]) < float(v_prev["abertura"])
            curr_breaks_high = float(v_curr["fechamento"]) > float(v_prev["abertura"])
            if prev_bearish and curr_breaks_high:
                preco = float(v_curr["fechamento"])
                sl, tp, rr = _calcular_risco_rr_v2(preco, poi_fundo, poi_topo, direcao)
                tempo = _tempo_da_vela(v_curr)
                return ConfirmacaoEntrada(
                    id=gerar_id_confirmacao(simbolo, "", tempo),
                    setup_id="",
                    simbolo=simbolo,
                    tipo_confirmacao="MSS",
                    preco_confirmacao=preco,
                    tempo=tempo,
                    sl=sl,
                    tp=tp,
                    rr=rr,
                )
        else:
            prev_bullish = float(v_prev["fechamento"]) > float(v_prev["abertura"])
            curr_breaks_low = float(v_curr["fechamento"]) < float(v_prev["abertura"])
            if prev_bullish and curr_breaks_low:
                preco = float(v_curr["fechamento"])
                sl, tp, rr = _calcular_risco_rr_v2(preco, poi_fundo, poi_topo, direcao)
                tempo = _tempo_da_vela(v_curr)
                return ConfirmacaoEntrada(
                    id=gerar_id_confirmacao(simbolo, "", tempo),
                    setup_id="",
                    simbolo=simbolo,
                    tipo_confirmacao="MSS",
                    preco_confirmacao=preco,
                    tempo=tempo,
                    sl=sl,
                    tp=tp,
                    rr=rr,
                )

    return None


def calcular_score_setup(
    evento: EventoEstrutura,
    leg: LegImpulso | None,
    pool: PoolLiquidez,
    velas_d1: pd.DataFrame | None,
    atr: float,
    tem_overlap_ob_fvg: bool = False,
    em_sessao: bool = False,
    zona_ok: bool = False,
    bias_alinhado: bool = False,
) -> int:
    score = 0
    if evento.tipo == "ChoCH":
        score += 20
    if bias_alinhado:
        score += 15
    if leg is not None and leg.eh_displacement:
        score += 15
    if pool.tipo in ("EQH", "EQL"):
        score += 10
    if tem_overlap_ob_fvg:
        score += 10
    if em_sessao:
        score += 10
    if zona_ok:
        score += 10
    return score


def _calcular_risco_rr_v2(
    preco_entrada: float,
    poi_fundo: float,
    poi_topo: float,
    direcao: str,
) -> tuple[float, float, float]:
    if direcao == "ALTA":
        sl = poi_fundo
        risco = preco_entrada - sl
        tp = preco_entrada + 2.0 * risco if risco > 0 else preco_entrada
    else:
        sl = poi_topo
        risco = sl - preco_entrada
        tp = preco_entrada - 2.0 * risco if risco > 0 else preco_entrada
    return sl, tp, 2.0


def _gerar_id_ob_v2(simbolo: str, leg_id: str, tempo: datetime) -> str:
    chave = f"{simbolo}|{leg_id}|{tempo.isoformat()}"
    return hashlib.sha1(chave.encode()).hexdigest()[:16]
