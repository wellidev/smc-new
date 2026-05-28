import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

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
    janela_candles: int | None = 50,
) -> list[CapturaLiquidez]:
    capturas: list[CapturaLiquidez] = []
    swings_high, swings_low = calcular_swings(velas_h4, periodo_swing)
    swings_bearish_vistos: set[float] = set()
    swings_bullish_vistos: set[float] = set()

    if janela_candles is None:
        janela_inicio = periodo_swing
    else:
        janela_inicio = max(periodo_swing, len(velas_h4) - janela_candles - 1)
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


def pool_varrido_por_sweep(
    pool: PoolLiquidez, capturas: list[CapturaLiquidez]
) -> CapturaLiquidez | None:
    """Retorna a CapturaLiquidez que varreu o nível do pool, ou None.

    Pools EQH/PDH (liquidez acima) exigem captura BAIXA próxima ao nível.
    Pools EQL/PDL (liquidez abaixo) exigem captura ALTA.
    Proximidade limitada por ``pool.tolerancia``.
    O caller usa ``captura.tempo`` como âncora temporal para filtrar eventos
    estruturais posteriores ao sweep (não à criação do pool).
    """
    direcao_sweep = "BAIXA" if pool.tipo in ("EQH", "PDH") else "ALTA"
    for c in capturas:
        if c.direcao == direcao_sweep and abs(c.preco_varredura - pool.preco) <= pool.tolerancia:
            return c
    return None


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


def _marcar_fvgs_mitigados(fvgs: list[FairValueGap], velas: pd.DataFrame) -> None:
    for fvg in fvgs:
        gap = fvg.preco_topo - fvg.preco_fundo
        meio_gap = fvg.preco_fundo + gap * 0.5
        post = velas[velas["tempo"] > fvg.tempo]
        if post.empty:
            continue
        if fvg.direcao == "ALTA":
            mit_mask = post["fechamento"] <= meio_gap
            fvg.mitigado = bool(mit_mask.any())
            if fvg.mitigado:
                n = int(mit_mask.values.argmax())
                fvg.testado = bool((post["minima"].values[:n] < fvg.preco_topo).any())
            else:
                fvg.testado = bool((post["minima"] < fvg.preco_topo).any())
        else:
            mit_mask = post["fechamento"] >= meio_gap
            fvg.mitigado = bool(mit_mask.any())
            if fvg.mitigado:
                n = int(mit_mask.values.argmax())
                fvg.testado = bool((post["maxima"].values[:n] > fvg.preco_fundo).any())
            else:
                fvg.testado = bool((post["maxima"] > fvg.preco_fundo).any())


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
        eh_displacement = atr_multiplo >= 1.5 and proporcao_corpo >= 0.50 and tem_fvg

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
    """POI Composta como a interseção geométrica real de OBs e FVGs ativos.

    Prioridade (veja spec_analisador_smc.md → calcular_poi_composta):
      1. Se existir qualquer interseção OB∩FVG válida (inf < sup, estrita) → retorna o
         envelope de todas as interseções válidas.
      2. Caso contrário, se existirem OBs ativos → retorna o envelope dos OBs.
      3. Caso contrário, se existirem apenas FVGs ativos → retorna o envelope dos FVGs.
      4. Fallback: (fallback_nivel, fallback_nivel).
    """
    obs_ativos = [o for o in obs if not o.mitigado]
    fvgs_ativos = [f for f in fvgs if not f.mitigado]

    intersecoes: list[tuple[float, float]] = []
    for ob in obs_ativos:
        for fvg in fvgs_ativos:
            inf = max(ob.preco_fundo, fvg.preco_fundo)
            sup = min(ob.preco_topo, fvg.preco_topo)
            if inf < sup:
                intersecoes.append((inf, sup))

    if intersecoes:
        poi_fundo = min(z[0] for z in intersecoes)
        poi_topo = max(z[1] for z in intersecoes)
        return poi_fundo, poi_topo

    if obs_ativos:
        poi_fundo = min(o.preco_fundo for o in obs_ativos)
        poi_topo = max(o.preco_topo for o in obs_ativos)
        return poi_fundo, poi_topo

    if fvgs_ativos:
        poi_fundo = min(f.preco_fundo for f in fvgs_ativos)
        poi_topo = max(f.preco_topo for f in fvgs_ativos)
        return poi_fundo, poi_topo

    return fallback_nivel, fallback_nivel


def _marcar_obs_v2_mitigados(obs: list[OrderBlockV2], velas: pd.DataFrame) -> None:
    for ob in obs:
        post = velas[velas["tempo"] > ob.tempo]
        if post.empty:
            continue
        if ob.direcao == "ALTA":
            mit_mask = post["minima"] <= ob.zona_50
            ob.mitigado = bool(mit_mask.any())
            if ob.mitigado:
                n = int(mit_mask.values.argmax())
                ob.testado = bool((post["minima"].values[:n] <= ob.preco_topo).any())
            else:
                ob.testado = bool((post["minima"] <= ob.preco_topo).any())
        else:
            mit_mask = post["maxima"] >= ob.zona_50
            ob.mitigado = bool(mit_mask.any())
            if ob.mitigado:
                n = int(mit_mask.values.argmax())
                ob.testado = bool((post["maxima"].values[:n] >= ob.preco_fundo).any())
            else:
                ob.testado = bool((post["maxima"] >= ob.preco_fundo).any())


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
    cutoff: datetime | None = None,
    atr: float = 0.0,
    tp_ref: float | None = None,
) -> ConfirmacaoEntrada | None:
    """Detect Market Structure Shift (ChoCH LTF) on M5 within the POI zone.

    Canonical sequence:
      - ALTA: micro-swing LOW dentro da POI, depois micro-swing HIGH, e por
        fim uma vela fecha acima do swing HIGH (ChoCH bullish).
      - BAIXA: micro-swing HIGH, depois micro-swing LOW, e por fim uma vela
        fecha abaixo do swing LOW (ChoCH bearish).

    Requer no mínimo 5 velas dentro da POI para que `calcular_swings` com
    `periodo=2` consiga identificar micro-swings.
    """
    df = velas_m5
    if cutoff is not None:
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        ts = pd.to_datetime(df["tempo"], utc=True)
        df = df[ts > pd.Timestamp(cutoff)]

    velas_poi = df[
        (df["maxima"] >= poi_fundo) & (df["minima"] <= poi_topo)
    ].reset_index(drop=True)

    if len(velas_poi) < 3:
        return None

    swings_high, swings_low = calcular_swings(velas_poi, periodo=1)

    if direcao == "ALTA":
        # Para cada swing LOW em ordem cronológica, procurar o primeiro swing
        # HIGH posterior e então uma vela que feche acima desse swing HIGH.
        for sl_idx in sorted(swings_low.keys()):
            sl_ref = swings_low[sl_idx]  # invalidação: abaixo deste low o MSS falhou
            sh_candidates = {idx: p for idx, p in swings_high.items() if idx > sl_idx}
            if not sh_candidates:
                continue
            sh_idx = min(sh_candidates.keys())
            sh_price = sh_candidates[sh_idx]
            for j in range(sh_idx + 1, len(velas_poi)):
                v = velas_poi.iloc[j]
                if float(v["fechamento"]) > sh_price:
                    preco = sh_price  # entrada no nível rompido (limite), não no close da vela
                    rr_result = _calcular_risco_rr_v2(preco, sl_ref, direcao, atr, tp_ref)
                    if rr_result is None:
                        continue
                    sl, tp, rr = rr_result
                    tempo = _tempo_da_vela(v)
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
    else:  # BAIXA — simétrico
        for sh_idx in sorted(swings_high.keys()):
            sl_ref = swings_high[sh_idx]  # invalidação: acima deste high o MSS falhou
            sl_candidates = {idx: p for idx, p in swings_low.items() if idx > sh_idx}
            if not sl_candidates:
                continue
            sl_idx = min(sl_candidates.keys())
            sl_price = sl_candidates[sl_idx]
            for j in range(sl_idx + 1, len(velas_poi)):
                v = velas_poi.iloc[j]
                if float(v["fechamento"]) < sl_price:
                    preco = sl_price  # entrada no nível rompido (limite), não no close da vela
                    rr_result = _calcular_risco_rr_v2(preco, sl_ref, direcao, atr, tp_ref)
                    if rr_result is None:
                        continue
                    sl, tp, rr = rr_result
                    tempo = _tempo_da_vela(v)
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
    tem_overlap_ob_fvg: bool = False,
    em_sessao: bool = False,
    zona_ok: bool = False,
    bias_alinhado: bool = False,
    zona_virgem: bool = False,
) -> int:
    score = 0
    if evento.tipo == "ChoCH":
        score += 20
    elif evento.tipo == "BOS":
        score += 10
    if bias_alinhado:
        score += 15
    if leg is not None and leg.eh_displacement:
        score += 15
    if pool.tipo in ("PDH", "PDL"):
        score += 15
    elif pool.tipo in ("EQH", "EQL"):
        score += 10
    if tem_overlap_ob_fvg:
        score += 10
    if em_sessao:
        score += 10
    if zona_ok:
        score += 10
    if zona_virgem:
        score += 5
    return score


def _calcular_risco_rr_v2(
    preco_entrada: float,
    sl_ref: float,
    direcao: str,
    atr: float = 0.0,
    tp_ref: float | None = None,
) -> tuple[float, float, float] | None:
    buffer = 0.1 * atr
    if direcao == "ALTA":
        sl = sl_ref - buffer
        risco = preco_entrada - sl
        if risco <= 0:
            return None
        tp_candidato = tp_ref if (tp_ref is not None and tp_ref > preco_entrada) else None
        if tp_candidato is not None and (tp_candidato - preco_entrada) / risco >= 1.5:
            tp = tp_candidato
        else:
            tp = preco_entrada + 2.0 * risco
    else:
        sl = sl_ref + buffer
        risco = sl - preco_entrada
        if risco <= 0:
            return None
        tp_candidato = tp_ref if (tp_ref is not None and tp_ref < preco_entrada) else None
        if tp_candidato is not None and (preco_entrada - tp_candidato) / risco >= 1.5:
            tp = tp_candidato
        else:
            tp = preco_entrada - 2.0 * risco
    rr = abs(tp - preco_entrada) / risco
    return sl, tp, round(rr, 2)


def _gerar_id_ob_v2(simbolo: str, leg_id: str, tempo: datetime) -> str:
    chave = f"{simbolo}|{leg_id}|{tempo.isoformat()}"
    return hashlib.sha1(chave.encode()).hexdigest()[:16]


# Aliases públicos para funções de marcação usadas externamente
marcar_obs_v2_mitigados = _marcar_obs_v2_mitigados
marcar_fvgs_mitigados = _marcar_fvgs_mitigados
