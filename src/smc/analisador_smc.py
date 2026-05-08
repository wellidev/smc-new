import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

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


@dataclass
class FairValueGap:
    id: str
    simbolo: str
    direcao: str
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False


@dataclass
class CapturaLiquidez:
    simbolo: str
    direcao: str
    preco_varredura: float
    tempo: datetime
    pavio_percentual: float


@dataclass
class QuebraEstrutura:
    simbolo: str
    direcao: str
    nivel_rompido: float
    tempo: datetime


def detectar_captura_liquidez(
    velas_h4: pd.DataFrame,
    periodo_swing: int,
    limiar_pavio: float,
) -> list[CapturaLiquidez]:
    capturas: list[CapturaLiquidez] = []
    swings_high, swings_low = _calcular_swings(velas_h4, periodo_swing)

    janela_inicio = max(periodo_swing, len(velas_h4) - 3 - 1)
    for i in range(janela_inicio, len(velas_h4)):
        vela = velas_h4.iloc[i]
        range_vela = vela["maxima"] - vela["minima"]
        if range_vela == 0:
            continue

        swing_high = _ultimo_swing_anterior(swings_high, i)
        swing_low = _ultimo_swing_anterior(swings_low, i)

        if swing_high is not None:
            pavio_sup = vela["maxima"] - max(vela["abertura"], vela["fechamento"])
            if (vela["maxima"] > swing_high
                    and vela["fechamento"] < swing_high
                    and pavio_sup / range_vela >= limiar_pavio):
                capturas.append(CapturaLiquidez(
                    simbolo=str(velas_h4.get("simbolo", pd.Series([""])).iloc[0]) if "simbolo" in velas_h4 else "",
                    direcao="BAIXA",
                    preco_varredura=float(vela["maxima"]),
                    tempo=_tempo_da_vela(vela),
                    pavio_percentual=round(pavio_sup / range_vela, 4),
                ))
                logger.debug("Captura bearish detectada @ %.5f", vela["maxima"])

        if swing_low is not None:
            pavio_inf = min(vela["abertura"], vela["fechamento"]) - vela["minima"]
            if (vela["minima"] < swing_low
                    and vela["fechamento"] > swing_low
                    and pavio_inf / range_vela >= limiar_pavio):
                capturas.append(CapturaLiquidez(
                    simbolo=str(velas_h4.get("simbolo", pd.Series([""])).iloc[0]) if "simbolo" in velas_h4 else "",
                    direcao="ALTA",
                    preco_varredura=float(vela["minima"]),
                    tempo=_tempo_da_vela(vela),
                    pavio_percentual=round(pavio_inf / range_vela, 4),
                ))
                logger.debug("Captura bullish detectada @ %.5f", vela["minima"])

    return capturas


def detectar_quebra_estrutura(
    velas: pd.DataFrame,
    simbolo: str,
    periodo_swing: int,
) -> list[QuebraEstrutura]:
    quebras: list[QuebraEstrutura] = []
    swings_high, swings_low = _calcular_swings(velas, periodo_swing)

    janela_inicio = max(periodo_swing, len(velas) - 5 - 1)
    for i in range(janela_inicio, len(velas)):
        vela = velas.iloc[i]
        fechamento = float(vela["fechamento"])

        swing_high = _ultimo_swing_anterior(swings_high, i)
        if swing_high is not None and fechamento > swing_high:
            quebras.append(QuebraEstrutura(
                simbolo=simbolo,
                direcao="ALTA",
                nivel_rompido=float(swing_high),
                tempo=_tempo_da_vela(vela),
            ))
            logger.debug("BOS de Alta @ %.5f (nível: %.5f)", fechamento, swing_high)

        swing_low = _ultimo_swing_anterior(swings_low, i)
        if swing_low is not None and fechamento < swing_low:
            quebras.append(QuebraEstrutura(
                simbolo=simbolo,
                direcao="BAIXA",
                nivel_rompido=float(swing_low),
                tempo=_tempo_da_vela(vela),
            ))
            logger.debug("BOS de Baixa @ %.5f (nível: %.5f)", fechamento, swing_low)

    return quebras


def mapear_zonas_interesse(
    velas_h4: pd.DataFrame,
    simbolo: str,
) -> tuple[list[OrderBlock], list[FairValueGap]]:
    order_blocks = _detectar_order_blocks(velas_h4, simbolo)
    fvgs = _detectar_fvgs(velas_h4, simbolo)

    _marcar_obs_mitigados(order_blocks, velas_h4)
    _marcar_fvgs_mitigados(fvgs, velas_h4)

    obs_ativos = [ob for ob in order_blocks if not ob.mitigado]
    fvgs_ativos = [fvg for fvg in fvgs if not fvg.mitigado]

    logger.debug("OBs ativos: %d | FVGs ativos: %d", len(obs_ativos), len(fvgs_ativos))
    return obs_ativos, fvgs_ativos


def verificar_confluencia(
    captura: CapturaLiquidez | None,
    quebra_estrutura: QuebraEstrutura | None,
    order_blocks: list[OrderBlock],
    fvgs: list[FairValueGap],
    preco_atual_m15: float,
) -> bool:
    if captura is None or quebra_estrutura is None:
        return False

    if captura.direcao != quebra_estrutura.direcao:
        return False

    direcao = captura.direcao
    for ob in order_blocks:
        if ob.mitigado or ob.direcao != direcao:
            continue
        if not (ob.preco_fundo <= preco_atual_m15 <= ob.preco_topo):
            continue
        for fvg in fvgs:
            if fvg.mitigado or fvg.direcao != direcao:
                continue
            if _zonas_sobrepoem(ob, fvg):
                logger.info(
                    "Confluência SMC detectada: captura=%s BOS=%s OB=[%.5f-%.5f] FVG=[%.5f-%.5f]",
                    captura.direcao, quebra_estrutura.direcao,
                    ob.preco_fundo, ob.preco_topo,
                    fvg.preco_fundo, fvg.preco_topo,
                )
                return True

    return False


def _calcular_swings(
    velas: pd.DataFrame,
    periodo: int,
) -> tuple[dict[int, float], dict[int, float]]:
    swings_high: dict[int, float] = {}
    swings_low: dict[int, float] = {}

    for i in range(periodo, len(velas) - periodo):
        janela_max = velas["maxima"].iloc[i - periodo: i + periodo + 1]
        janela_min = velas["minima"].iloc[i - periodo: i + periodo + 1]
        if velas["maxima"].iloc[i] == janela_max.max():
            swings_high[i] = float(velas["maxima"].iloc[i])
        if velas["minima"].iloc[i] == janela_min.min():
            swings_low[i] = float(velas["minima"].iloc[i])

    return swings_high, swings_low


def _ultimo_swing_anterior(swings: dict[int, float], indice_atual: int) -> float | None:
    indices_anteriores = [idx for idx in swings if idx < indice_atual]
    if not indices_anteriores:
        return None
    return swings[max(indices_anteriores)]


def _detectar_order_blocks(velas: pd.DataFrame, simbolo: str) -> list[OrderBlock]:
    obs: list[OrderBlock] = []

    for i in range(len(velas) - 4):
        vela = velas.iloc[i]
        corpo = float(vela["fechamento"]) - float(vela["abertura"])
        e_bearish = corpo < 0
        e_bullish = corpo > 0

        proximas = velas.iloc[i + 1: i + 4]

        if e_bearish:
            corpos_proximas = proximas["fechamento"] - proximas["abertura"]
            impulso_bullish = (corpos_proximas > 0).all()
            fechamentos_crescentes = proximas["fechamento"].is_monotonic_increasing
            corpo_ob = abs(corpo)
            proximo_corpo = float(proximas.iloc[0]["fechamento"]) - float(proximas.iloc[0]["abertura"])
            engolfo = proximo_corpo > 1.5 * corpo_ob if corpo_ob > 0 else False

            if impulso_bullish or fechamentos_crescentes or engolfo:
                obs.append(OrderBlock(
                    id=_gerar_id(simbolo, vela),
                    simbolo=simbolo,
                    direcao="ALTA",
                    preco_topo=float(vela["maxima"]),
                    preco_fundo=float(vela["minima"]),
                    tempo=_tempo_da_vela(vela),
                ))

        if e_bullish:
            corpos_proximas = proximas["fechamento"] - proximas["abertura"]
            impulso_bearish = (corpos_proximas < 0).all()
            fechamentos_decrescentes = proximas["fechamento"].is_monotonic_decreasing
            corpo_ob = abs(corpo)
            proximo_corpo = float(proximas.iloc[0]["abertura"]) - float(proximas.iloc[0]["fechamento"])
            engolfo = proximo_corpo > 1.5 * corpo_ob if corpo_ob > 0 else False

            if impulso_bearish or fechamentos_decrescentes or engolfo:
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

    for i in range(len(velas) - 2):
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


def _marcar_obs_mitigados(obs: list[OrderBlock], velas: pd.DataFrame) -> None:
    for ob in obs:
        velas_pos = velas[velas["tempo"] > ob.tempo]
        for _, v in velas_pos.iterrows():
            if ob.preco_fundo < float(v["fechamento"]) < ob.preco_topo:
                ob.mitigado = True
                break


def _marcar_fvgs_mitigados(fvgs: list[FairValueGap], velas: pd.DataFrame) -> None:
    for fvg in fvgs:
        velas_pos = velas[velas["tempo"] > fvg.tempo]
        for _, v in velas_pos.iterrows():
            if float(v["minima"]) <= fvg.preco_fundo and float(v["maxima"]) >= fvg.preco_topo:
                fvg.mitigado = True
                break


def _zonas_sobrepoem(ob: OrderBlock, fvg: FairValueGap) -> bool:
    return max(ob.preco_fundo, fvg.preco_fundo) < min(ob.preco_topo, fvg.preco_topo)


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
