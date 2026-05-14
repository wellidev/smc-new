import hashlib
import logging.handlers
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

import pandas as pd

from smc.analisador_smc import (
    CapturaLiquidez,
    FairValueGap,
    OrderBlock,
    QuebraEstrutura,
    _zonas_sobrepoem,
    detectar_captura_liquidez,
    detectar_quebra_estrutura,
    mapear_zonas_interesse,
    verificar_confluencia,
)
from smc.configuracoes import (
    ATIVOS_MONITORADOS,
    CAMINHO_BANCO,
    IDADE_MAX_EVENTO_H4,
    INTERVALO_VARREDURA_SEGUNDOS,
    LIMIAR_PAVIO,
    MENSAGEM_ALERTA,
    PERIODO_SWING,
    PERIODO_SWING_D1,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    TIMEFRAME_D1,
    TIMEFRAME_ESTRUTURAL,
    TIMEFRAME_GATILHO,
    VELAS_D1_HISTORICO,
    VELAS_HISTORICO,
)
from smc.filtros import calcular_bias_d1, calcular_risco_rr, verificar_sessao, verificar_zona_premium_discount
from smc.notificador import Notificador
from smc.provedor_dados import ProvedorDados
from smc.repositorio import Repositorio

_NIVEL_LOG = os.getenv("SMC_LOG_LEVEL", "INFO")
_nivel = getattr(logging, _NIVEL_LOG, logging.INFO)
_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_handlers: list[logging.Handler] = [_console_handler]

if _nivel == logging.DEBUG:
    _caminho_log = os.getenv("SMC_LOG_FILE", os.path.join("logs", "smc_debug.log"))
    os.makedirs(os.path.dirname(_caminho_log) or ".", exist_ok=True)
    _file_handler = logging.handlers.TimedRotatingFileHandler(
        _caminho_log, when="midnight", backupCount=30, encoding="utf-8"
    )
    _file_handler.setLevel(logging.DEBUG)
    _handlers.append(_file_handler)

for _h in _handlers:
    _h.setFormatter(_fmt)

logging.basicConfig(level=_nivel, handlers=_handlers)
logger = logging.getLogger(__name__)


class Confluencia(NamedTuple):
    captura: CapturaLiquidez
    bos: QuebraEstrutura
    ob: OrderBlock
    fvg: FairValueGap
    overlap_fundo: float
    overlap_topo: float


def _gerar_id_sinal(simbolo: str, ob_id: str, fvg_id: str) -> str:
    chave = f"{simbolo}{ob_id}{fvg_id}"
    return hashlib.sha1(chave.encode()).hexdigest()[:20]


def _gerar_id_evento(simbolo: str, tipo: str, tempo: datetime, preco: float) -> str:
    chave = f"{simbolo}|{tipo}|{tempo.isoformat()}|{preco}"
    return hashlib.sha1(chave.encode()).hexdigest()[:20]


def _registrar_novos_eventos(
    capturas: list[CapturaLiquidez],
    quebras: list[QuebraEstrutura],
    repo: Repositorio,
    simbolo: str,
) -> None:
    for c in capturas:
        id_ev = _gerar_id_evento(simbolo, "CAPTURA", c.tempo, c.preco_varredura)
        if not repo.evento_ja_detectado(id_ev) and not repo.captura_ja_registrada_para_vela(simbolo, c.direcao, c.tempo):
            repo.registrar_captura(id_ev, simbolo, c.direcao, c.tempo, c.preco_varredura, c.pavio_percentual)

    for b in quebras:
        id_ev = _gerar_id_evento(simbolo, "BOS", b.tempo, b.nivel_rompido)
        if not repo.evento_ja_detectado(id_ev):
            repo.registrar_bos(id_ev, simbolo, b.direcao, b.nivel_rompido, b.swing_tempo, b.tempo, b.deslocamento)


def _carregar_eventos_ativos(
    repo: Repositorio,
    simbolo: str,
    cutoff: datetime,
) -> tuple[list[CapturaLiquidez], list[QuebraEstrutura]]:
    capturas = [
        CapturaLiquidez(
            simbolo=row[0],
            direcao=row[1],
            preco_varredura=row[2],
            tempo=datetime.fromisoformat(row[3]).replace(tzinfo=timezone.utc),
            pavio_percentual=row[4] or 0.0,
        )
        for row in repo.carregar_capturas_ativas(simbolo, cutoff)
    ]
    quebras = [
        QuebraEstrutura(
            simbolo=row[0],
            direcao=row[1],
            nivel_rompido=row[2],
            swing_tempo=datetime.fromisoformat(row[3]).replace(tzinfo=timezone.utc),
            tempo=datetime.fromisoformat(row[4]).replace(tzinfo=timezone.utc),
            deslocamento=bool(row[5]),
        )
        for row in repo.carregar_quebras_ativas(simbolo, cutoff)
    ]
    return capturas, quebras


def _obter_dados_mercado(
        simbolo: str, provedor: ProvedorDados
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None] | None:
    velas_h4 = provedor.obter_velas(simbolo, TIMEFRAME_ESTRUTURAL, VELAS_HISTORICO)
    if velas_h4 is None or len(velas_h4) < 20:
        logger.warning("Dados insuficientes para %s no H4.", simbolo)
        return None

    velas_m15 = provedor.obter_velas(simbolo, TIMEFRAME_GATILHO, 50)
    if velas_m15 is None or len(velas_m15) == 0:
        logger.warning("Dados insuficientes para %s no M15.", simbolo)
        return None

    velas_d1 = provedor.obter_velas(simbolo, TIMEFRAME_D1, VELAS_D1_HISTORICO)
    if velas_d1 is None:
        logger.debug("D1 indisponível para %s — filtros contextuais desativados.", simbolo)

    return velas_h4, velas_m15, velas_d1


def _detectar_estrutura_h4(
        velas_h4: pd.DataFrame, simbolo: str
) -> tuple[list[CapturaLiquidez], list[QuebraEstrutura], list[OrderBlock], list[FairValueGap]]:
    capturas = detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO, simbolo)
    quebras = detectar_quebra_estrutura(velas_h4, simbolo, PERIODO_SWING)
    obs, fvgs = mapear_zonas_interesse(velas_h4, simbolo)
    return capturas, quebras, obs, fvgs


def _encontrar_confluencias(
        capturas: list[CapturaLiquidez],
        quebras: list[QuebraEstrutura],
        obs: list[OrderBlock],
        fvgs: list[FairValueGap],
        preco_atual: float,
) -> list[Confluencia]:
    resultado: list[Confluencia] = []
    for captura in capturas:
        for bos in quebras:
            if not verificar_confluencia(captura, bos, obs, fvgs, preco_atual):
                continue
            for ob in obs:
                if ob.mitigado:
                    continue
                if ob.direcao != captura.direcao:
                    continue
                for fvg in fvgs:
                    if fvg.mitigado:
                        continue
                    if fvg.direcao != captura.direcao:
                        continue
                    if not _zonas_sobrepoem(ob, fvg):
                        continue
                    if not (ob.preco_fundo <= preco_atual <= ob.preco_topo):
                        continue
                    overlap_fundo = max(ob.preco_fundo, fvg.preco_fundo)
                    overlap_topo = min(ob.preco_topo, fvg.preco_topo)
                    resultado.append(Confluencia(captura, bos, ob, fvg, overlap_fundo, overlap_topo))
    return resultado


def _calcular_contexto(
        conf: Confluencia, preco_atual: float, velas_d1: pd.DataFrame | None
) -> dict[str, Any]:
    em_sessao = verificar_sessao(conf.captura.tempo)
    bias_d1 = calcular_bias_d1(velas_d1, PERIODO_SWING_D1) if velas_d1 is not None else None
    zona_ok = (
        verificar_zona_premium_discount(velas_d1, preco_atual, conf.captura.direcao)
        if velas_d1 is not None else False
    )
    sl, tp, rr = calcular_risco_rr(preco_atual, conf.ob, conf.captura.direcao)

    check_sessao = "✅ London/NY" if em_sessao else "⚠️ Fora de sessão"
    check_bias = (
        "✅ Alinhado" if bias_d1 == conf.captura.direcao
        else "⚠️ Neutro" if bias_d1 is None
        else "⚠️ Contra D1"
    )
    check_zona = "✅ Desconto" if (conf.captura.direcao == "ALTA" and zona_ok) else (
        "✅ Premium" if (conf.captura.direcao == "BAIXA" and zona_ok) else "⚠️ Sem confluência"
    )
    bos_qualidade = "Forte 💪" if conf.bos.deslocamento else "Normal"

    return {
        "check_sessao": check_sessao,
        "check_bias": check_bias,
        "check_zona": check_zona,
        "bos_qualidade": bos_qualidade,
        "sl": sl,
        "tp": tp,
        "rr": rr,
    }


def _construir_mensagem(simbolo: str, conf: Confluencia, ctx: dict[str, Any]) -> str:
    return MENSAGEM_ALERTA.format(
        simbolo=simbolo,
        direcao_captura=conf.captura.direcao,
        preco_varredura=conf.captura.preco_varredura,
        bos_qualidade=ctx["bos_qualidade"],
        direcao_bos=conf.bos.direcao,
        nivel_bos=conf.bos.nivel_rompido,
        ob_qualidade="⚠️ Testado" if conf.ob.testado else "Virgem",
        ob_fundo=conf.ob.preco_fundo,
        ob_topo=conf.ob.preco_topo,
        fvg_qualidade="⚠️ Testado" if conf.fvg.testado else "Virgem",
        fvg_fundo=conf.fvg.preco_fundo,
        fvg_topo=conf.fvg.preco_topo,
        overlap_fundo=conf.overlap_fundo,
        overlap_topo=conf.overlap_topo,
        sl=ctx["sl"],
        tp=ctx["tp"],
        rr=ctx["rr"],
        check_sessao=ctx["check_sessao"],
        check_bias=ctx["check_bias"],
        check_zona=ctx["check_zona"],
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def _processar_confluencia(
        simbolo: str,
        conf: Confluencia,
        preco_atual: float,
        velas_d1: pd.DataFrame | None,
        repo: Repositorio,
        notificador: Notificador,
) -> None:
    id_sinal = _gerar_id_sinal(simbolo, conf.ob.id, conf.fvg.id)
    if repo.sinal_ja_disparado(id_sinal):
        logger.debug("Sinal %s já disparado anteriormente.", id_sinal)
        return

    ctx = _calcular_contexto(conf, preco_atual, velas_d1)
    mensagem = _construir_mensagem(simbolo, conf, ctx)

    repo.persistir_sinal(id_sinal, simbolo, conf.ob, conf.fvg, conf.captura.direcao)
    logger.info("Novo sinal SMC para %s — %s. Enviando alerta...", simbolo, conf.captura.direcao)
    enviado = notificador.enviar_alerta(mensagem)
    if not enviado:
        logger.error("Falha ao enviar alerta para %s. Sinal persistido no banco.", simbolo)


def _processar_simbolo(
        simbolo: str,
        provedor: ProvedorDados,
        notificador: Notificador,
        repo: Repositorio,
) -> None:
    dados = _obter_dados_mercado(simbolo, provedor)
    if dados is None:
        return
    velas_h4, velas_m15, velas_d1 = dados
    preco_atual = float(velas_m15.iloc[-1]["fechamento"])
    capturas_raw, quebras_raw, obs, fvgs = _detectar_estrutura_h4(velas_h4, simbolo)
    _registrar_novos_eventos(capturas_raw, quebras_raw, repo, simbolo)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=IDADE_MAX_EVENTO_H4 * 4)
    capturas, quebras = _carregar_eventos_ativos(repo, simbolo, cutoff)
    logger.info(
        "%s | capturas=%d | BOS=%d | OBs=%d | FVGs=%d | preço=%.5f",
        simbolo, len(capturas), len(quebras), len(obs), len(fvgs), preco_atual,
    )
    for conf in _encontrar_confluencias(capturas, quebras, obs, fvgs, preco_atual):
        _processar_confluencia(simbolo, conf, preco_atual, velas_d1, repo, notificador)


def _conectar_mt5_com_retry(provedor: ProvedorDados, tentativas: int = 3) -> bool:
    for tentativa in range(1, tentativas + 1):
        if provedor.conectar():
            return True
        logger.warning("Tentativa %d/%d de conexão com MT5 falhou.", tentativa, tentativas)
        if tentativa < tentativas:
            time.sleep(5)
    return False


def main() -> None:
    repo = Repositorio(CAMINHO_BANCO)
    provedor = ProvedorDados(repo)
    notificador = Notificador(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

    if not _conectar_mt5_com_retry(provedor):
        logger.critical("Não foi possível conectar ao MT5 após 3 tentativas. Encerrando.")
        repo.fechar()
        return

    logger.info("Sistema SMC iniciado. Monitorando %d ativos.", len(ATIVOS_MONITORADOS))

    try:
        while True:
            logger.info("--- Início do ciclo de varredura ---")
            for simbolo in ATIVOS_MONITORADOS:
                try:
                    _processar_simbolo(simbolo, provedor, notificador, repo)
                except Exception:
                    logger.exception("Erro não tratado ao processar %s.", simbolo)

            logger.info("--- Ciclo concluído. Aguardando %ds ---", INTERVALO_VARREDURA_SEGUNDOS)
            time.sleep(INTERVALO_VARREDURA_SEGUNDOS)

    except KeyboardInterrupt:
        logger.info("Encerramento solicitado pelo usuário.")
    finally:
        provedor.desconectar()
        repo.fechar()
        logger.info("Sistema SMC encerrado.")
