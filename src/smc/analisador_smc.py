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


@dataclass
class QuebraEstrutura:
    simbolo: str
    direcao: str
    nivel_rompido: float
    swing_tempo: datetime
    tempo: datetime
    deslocamento: bool = False


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


def detectar_quebra_estrutura(
    velas: pd.DataFrame,
    simbolo: str,
    periodo_swing: int,
) -> list[QuebraEstrutura]:
    quebras: list[QuebraEstrutura] = []
    swings_high, swings_low = calcular_swings(velas, periodo_swing)
    swings_alta_vistos: set[float] = set()
    swings_baixa_vistos: set[float] = set()

    janela_inicio = max(periodo_swing, len(velas) - 15 - 1)
    for i in range(janela_inicio, len(velas)):
        vela = velas.iloc[i]
        fechamento = float(vela["fechamento"])

        tem_proximo = i + 1 < len(velas)

        res_high = _ultimo_swing_anterior(swings_high, i)
        if res_high is not None:
            swing_idx_h, swing_high = res_high
            if fechamento > swing_high and swing_high not in swings_alta_vistos:
                swings_alta_vistos.add(swing_high)
                desloc = (
                    tem_proximo
                    and float(velas.iloc[i - 1]["maxima"]) < float(velas.iloc[i + 1]["minima"])
                ) if i > 0 else False
                quebras.append(QuebraEstrutura(
                    simbolo=simbolo,
                    direcao="ALTA",
                    nivel_rompido=float(swing_high),
                    swing_tempo=_tempo_da_vela(velas.iloc[swing_idx_h]),
                    tempo=_tempo_da_vela(vela),
                    deslocamento=desloc,
                ))
                logger.debug("BOS de Alta @ %.5f (nível: %.5f, deslocamento=%s)", fechamento, swing_high, desloc)

        res_low = _ultimo_swing_anterior(swings_low, i)
        if res_low is not None:
            swing_idx_l, swing_low = res_low
            if fechamento < swing_low and swing_low not in swings_baixa_vistos:
                swings_baixa_vistos.add(swing_low)
                desloc = (
                    tem_proximo
                    and float(velas.iloc[i - 1]["minima"]) > float(velas.iloc[i + 1]["maxima"])
                ) if i > 0 else False
                quebras.append(QuebraEstrutura(
                    simbolo=simbolo,
                    direcao="BAIXA",
                    nivel_rompido=float(swing_low),
                    swing_tempo=_tempo_da_vela(velas.iloc[swing_idx_l]),
                    tempo=_tempo_da_vela(vela),
                    deslocamento=desloc,
                ))
                logger.debug("BOS de Baixa @ %.5f (nível: %.5f, deslocamento=%s)", fechamento, swing_low, desloc)

    return quebras


def mapear_zonas_interesse(
    velas_h4: pd.DataFrame,
    simbolo: str,
) -> tuple[list[OrderBlock], list[FairValueGap]]:
    order_blocks = _detectar_order_blocks(velas_h4, simbolo)
    fvgs = _detectar_fvgs(velas_h4, simbolo)

    _marcar_obs_mitigados(order_blocks, velas_h4)
    _marcar_fvgs_mitigados(fvgs, velas_h4)
    _marcar_obs_testados(order_blocks, velas_h4)
    _marcar_fvgs_testados(fvgs, velas_h4)

    obs_ativos = [ob for ob in order_blocks if not ob.mitigado]
    fvgs_ativos = [fvg for fvg in fvgs if not fvg.mitigado]

    obs_virgens = sum(1 for ob in obs_ativos if not ob.testado)
    fvgs_virgens = sum(1 for fvg in fvgs_ativos if not fvg.testado)
    logger.debug(
        "OBs ativos: %d (%d virgens) | FVGs ativos: %d (%d virgens)",
        len(obs_ativos), obs_virgens, len(fvgs_ativos), fvgs_virgens,
    )
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
        logger.debug(
            "Confluência rejeitada: direções divergem (captura=%s, BOS=%s)",
            captura.direcao, quebra_estrutura.direcao,
        )
        return False

    if quebra_estrutura.tempo <= captura.tempo:
        logger.debug(
            "Confluência rejeitada: BOS (%s) não é posterior à captura (%s)",
            quebra_estrutura.tempo, captura.tempo,
        )
        return False

    if quebra_estrutura.swing_tempo <= captura.tempo:
        logger.debug(
            "Confluência rejeitada: swing do BOS (%s) não é posterior à captura (%s)",
            quebra_estrutura.swing_tempo, captura.tempo,
        )
        return False

    direcao = captura.direcao
    obs_validos = [o for o in order_blocks if not o.mitigado and o.direcao == direcao and o.tempo < captura.tempo]
    fvgs_validos = [f for f in fvgs if not f.mitigado and f.direcao == direcao and f.tempo < captura.tempo]

    for ob in obs_validos:
        for fvg in fvgs_validos:
            if not _zonas_sobrepoem(ob, fvg):
                continue
            overlap_fundo = max(ob.preco_fundo, fvg.preco_fundo)
            overlap_topo  = min(ob.preco_topo,  fvg.preco_topo)
            if overlap_fundo <= preco_atual_m15 <= overlap_topo:
                logger.info(
                    "Confluência SMC detectada: captura=%s BOS=%s OB=[%.5f-%.5f]%s FVG=[%.5f-%.5f]%s "
                    "overlap=[%.5f-%.5f] preço=%.5f",
                    captura.direcao, quebra_estrutura.direcao,
                    ob.preco_fundo, ob.preco_topo,
                    " [TESTADO]" if ob.testado else "",
                    fvg.preco_fundo, fvg.preco_topo,
                    " [TESTADO]" if fvg.testado else "",
                    overlap_fundo, overlap_topo, preco_atual_m15,
                )
                return True

    logger.debug(
        "Confluência rejeitada: nenhum OB %s com FVG sobreposto contendo preço=%.5f na zona de sobreposição "
        "(OBs válidos=%d, FVGs válidos=%d)",
        direcao, preco_atual_m15, len(obs_validos), len(fvgs_validos),
    )
    return False


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
            low = float(v["minima"])
            high = float(v["maxima"])
            close = float(v["fechamento"])
            if ob.direcao == "ALTA":
                # Close dentro da zona OU wick retorna à zona vindo de cima
                inside = ob.preco_fundo < close < ob.preco_topo
                wick_retorno = high >= ob.preco_topo and low <= ob.preco_topo and close <= ob.preco_topo
                if inside or wick_retorno:
                    ob.mitigado = True
                    break
            else:
                inside = ob.preco_fundo < close < ob.preco_topo
                wick_retorno = low <= ob.preco_fundo and high >= ob.preco_fundo and close >= ob.preco_fundo
                if inside or wick_retorno:
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
