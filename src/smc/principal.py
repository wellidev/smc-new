import logging.handlers
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from smc.analisador_smc import (
    _calcular_risco_rr_v2,
    calcular_atr,
    calcular_atr_adaptativo,
    calcular_poi_composta,
    calcular_score_setup,
    detectar_captura_liquidez,
    detectar_eventos_estrutura,
    detectar_mss_no_poi,
    detectar_obs_corpo,
    detectar_pdh_pdl,
    detectar_eqh_eql,
    extrair_fvgs_no_intervalo,
    extrair_legs,
    marcar_obs_v2_mitigados,
    marcar_fvgs_mitigados,
    pool_varrido_por_sweep,
)
from smc.configuracoes import (
    ATR_PERIODO,
    ATR_SMA_PERIODO,
    ATIVOS_MONITORADOS,
    CAMINHO_BANCO,
    EXIGIR_CONFIRMACAO_LTF,
    IDADE_MAX_SETUP_HORAS,
    INTERVALO_VARREDURA_SEGUNDOS,
    LIMIAR_PAVIO,
    PERIODO_SWING,
    PERIODO_SWING_ESTRUTURA,
    PERIODO_SWING_D1,
    SCORE_MINIMO_SETUP,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    TIMEFRAME_D1,
    TIMEFRAME_ESTRUTURAL,
    TIMEFRAME_GATILHO,
    TIMEFRAME_GATILHO_MINUTOS,
    VELAS_D1_HISTORICO,
    VELAS_HISTORICO,
)
from smc.modelos import (
    ConfirmacaoEntrada,
    gerar_id_confirmacao,
    gerar_id_setup,
)
from smc.filtros import calcular_bias_d1_v2, calcular_bias_h4, verificar_sessao, verificar_zona_premium_discount_v2
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


def _obter_dados_mercado(
        simbolo: str, provedor: ProvedorDados
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None] | None:
    velas_h4 = provedor.obter_velas(simbolo, TIMEFRAME_ESTRUTURAL, VELAS_HISTORICO)
    if velas_h4 is None or len(velas_h4) < 20:
        logger.warning("Dados insuficientes para %s no H4.", simbolo)
        return None

    velas_m5 = provedor.obter_velas(simbolo, TIMEFRAME_GATILHO, 50)
    if velas_m5 is None or len(velas_m5) == 0:
        logger.warning("Dados insuficientes para %s no M5.", simbolo)
        return None

    velas_d1 = provedor.obter_velas(simbolo, TIMEFRAME_D1, VELAS_D1_HISTORICO)
    if velas_d1 is None:
        logger.debug("D1 indisponível para %s — filtros contextuais desativados.", simbolo)

    return velas_h4, velas_m5, velas_d1


def _processar_simbolo(
        simbolo: str,
        provedor: ProvedorDados,
        notificador: Notificador,
        repo: Repositorio,
) -> None:
    dados = _obter_dados_mercado(simbolo, provedor)
    if dados is None:
        return
    velas_h4, velas_m5, velas_d1 = dados
    preco_atual = float(velas_m5.iloc[-1]["fechamento"])
    _detectar_e_registrar_setups(simbolo, velas_h4, velas_m5, velas_d1, repo)
    _verificar_confirmacoes(simbolo, velas_h4, velas_m5, velas_d1, preco_atual, repo, notificador)


MENSAGEM_SETUP = (
    "🔔 <b>SETUP SMC — {simbolo}</b>  [{score}/100] {score_emoji}\n\n"
    "📊 {evento_tipo} {direcao}  |  {pool_tipo} @ {pool_preco:.5f}\n"
    "🎯 POI: {poi_fundo:.5f} – {poi_topo:.5f}\n"
    "📍 Entrada: {entrada:.5f}  |  SL: {sl:.5f} (-{sl_pips}p)  |  TP: {tp:.5f} (+{tp_pips}p)\n"
    "📐 R:R 1:{rr:.1f}  |  Displacement: {displacement}\n\n"
    "{check_sessao}\n"
    "{checks_aviso}"
    "⏳ Válido até: {expiration_str}{expired_flag}\n"
    "⏰ {timestamp}"
)


def _score_emoji(score: int) -> str:
    if score >= 70:
        return "🟢"
    if score >= 55:
        return "🟡"
    return "🔴"


def _pips(p1: float, p2: float, simbolo: str) -> int:
    factor = 100 if "JPY" in simbolo else 10000
    return round(abs(p1 - p2) * factor)


def _checks_aviso(check_bias: str, check_zona: str) -> str:
    avisos = []
    if check_bias.startswith("⚠️"):
        avisos.append(f"Bias D1: {check_bias.lstrip('⚠️').strip()}")
    if check_zona.startswith("⚠️"):
        avisos.append(f"Zona: {check_zona.lstrip('⚠️').strip()}")
    return f"⚠️ {' | '.join(avisos)}\n\n" if avisos else ""


def _detectar_e_registrar_setups(
        simbolo: str,
        velas_h4: pd.DataFrame,
        velas_m5: pd.DataFrame,
        velas_d1: pd.DataFrame | None,
        repo: Repositorio,
) -> None:
    atr = calcular_atr(velas_h4, ATR_PERIODO)
    if atr <= 0:
        return

    eventos = detectar_eventos_estrutura(velas_h4, simbolo, PERIODO_SWING_ESTRUTURA)
    if not eventos:
        return

    atr_disp = calcular_atr_adaptativo(velas_h4, ATR_PERIODO, ATR_SMA_PERIODO)
    legs = extrair_legs(velas_h4, eventos, simbolo, PERIODO_SWING_ESTRUTURA, atr_disp)
    leg_por_evento: dict[datetime, Any] = {leg.tempo_fim: leg for leg in legs}

    pools = detectar_eqh_eql(velas_h4, simbolo, atr)
    if velas_d1 is not None:
        pools += detectar_pdh_pdl(velas_d1, simbolo, atr)

    if not pools:
        return

    preco_atual = float(velas_m5.iloc[-1]["fechamento"])
    capturas = detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO, simbolo)
    _bias_h4_setup = calcular_bias_h4(velas_h4, simbolo, PERIODO_SWING_ESTRUTURA)
    bias_d1 = calcular_bias_d1_v2(velas_d1, simbolo, PERIODO_SWING_D1, bias_h4=_bias_h4_setup) if velas_d1 is not None else None
    em_sessao = verificar_sessao(datetime.now(timezone.utc))
    zona_por_direcao: dict[str, bool] = {}
    if velas_d1 is not None:
        for _dir in ("ALTA", "BAIXA"):
            zona_por_direcao[_dir] = verificar_zona_premium_discount_v2(velas_d1, preco_atual, _dir, PERIODO_SWING_D1)

    for pool in pools:
        captura = pool_varrido_por_sweep(pool, capturas)
        if captura is None:
            continue

        direcao_reversal = "BAIXA" if pool.tipo in ("EQH", "PDH") else "ALTA"
        eventos_pos = [
            e for e in eventos
            if e.tempo > captura.tempo and e.direcao == direcao_reversal
        ]
        if not eventos_pos:
            continue

        evento = eventos_pos[0]
        leg = leg_por_evento.get(evento.tempo)

        velas_fechadas = velas_h4.iloc[:-1]
        if leg is not None:
            obs_leg = detectar_obs_corpo(velas_h4, leg, simbolo)
            fvgs_leg = extrair_fvgs_no_intervalo(velas_h4, leg.indice_inicio, leg.indice_fim, simbolo)
            marcar_obs_v2_mitigados(obs_leg, velas_fechadas)
            marcar_fvgs_mitigados(fvgs_leg, velas_fechadas)
            obs_val = [o for o in obs_leg if not o.mitigado and o.direcao == evento.direcao]
            fvgs_val = [f for f in fvgs_leg if not f.mitigado and f.direcao == evento.direcao]
            poi_fundo, poi_topo = calcular_poi_composta(obs_val, fvgs_val, evento.nivel_rompido)
            tem_overlap = any(
                max(o.preco_fundo, f.preco_fundo) <= min(o.preco_topo, f.preco_topo)
                for o in obs_val for f in fvgs_val
            )
        else:
            poi_fundo = poi_topo = evento.nivel_rompido
            tem_overlap = False

        bias_alinhado = bias_d1 == evento.direcao
        zona_ok = zona_por_direcao.get(evento.direcao, False)

        zona_virgem = (
            not any(o.testado for o in obs_val)
            and not any(f.testado for f in fvgs_val)
        ) if leg is not None else False
        score = calcular_score_setup(
            evento=evento,
            leg=leg,
            pool=pool,
            tem_overlap_ob_fvg=tem_overlap,
            em_sessao=em_sessao,
            zona_ok=zona_ok,
            bias_alinhado=bias_alinhado,
            zona_virgem=zona_virgem,
        )

        if score < SCORE_MINIMO_SETUP:
            logger.debug("Setup %s ignorado: score=%d < %d", simbolo, score, SCORE_MINIMO_SETUP)
            continue

        if poi_fundo >= poi_topo:
            logger.debug("Setup %s ignorado: POI sem range (%.5f–%.5f)", simbolo, poi_fundo, poi_topo)
            continue

        cutoff_zona = datetime.now(timezone.utc) - timedelta(hours=IDADE_MAX_SETUP_HORAS)
        if repo.setup_ativo_na_zona(simbolo, evento.direcao, poi_fundo, poi_topo, cutoff_zona):
            logger.debug("Setup %s ignorado: zona POI já coberta por setup ativo", simbolo)
            continue

        ancora = leg.id if leg is not None else evento.tipo
        setup_id = gerar_id_setup(simbolo, ancora, evento.tempo)
        if repo.setup_ja_existe(setup_id):
            continue

        repo.persistir_setup(
            id_setup=setup_id,
            simbolo=simbolo,
            direcao=evento.direcao,
            pool_id=pool.id,
            pool_tipo=pool.tipo,
            pool_preco=pool.preco,
            evento_tipo=evento.tipo,
            evento_tempo=evento.tempo,
            evento_nivel=evento.nivel_rompido,
            leg_id=leg.id if leg else None,
            poi_fundo=poi_fundo,
            poi_topo=poi_topo,
            score=score,
        )
        logger.debug("Setup %s id=%s registrado: %s score=%d POI=[%.5f-%.5f]",
                    simbolo, setup_id, evento.direcao, score, poi_fundo, poi_topo)


def _verificar_confirmacoes(
        simbolo: str,
        velas_h4: pd.DataFrame,
        velas_m5: pd.DataFrame,
        velas_d1: pd.DataFrame | None,
        preco_atual: float,
        repo: Repositorio,
        notificador: Notificador,
) -> None:
    atr = calcular_atr(velas_h4, ATR_PERIODO)
    atr_m5 = calcular_atr(velas_m5, ATR_PERIODO)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=IDADE_MAX_SETUP_HORAS)
    setups = repo.carregar_setups_ativos(simbolo, cutoff)
    _bias_h4 = calcular_bias_h4(velas_h4, simbolo, PERIODO_SWING_ESTRUTURA)
    bias_d1 = calcular_bias_d1_v2(velas_d1, simbolo, PERIODO_SWING_D1, bias_h4=_bias_h4) if velas_d1 is not None else None
    em_sessao = verificar_sessao(datetime.now(timezone.utc))
    zona_por_direcao: dict[str, bool] = {}
    if velas_d1 is not None:
        for _dir in ("ALTA", "BAIXA"):
            zona_por_direcao[_dir] = verificar_zona_premium_discount_v2(velas_d1, preco_atual, _dir, PERIODO_SWING_D1)

    for row in setups:
        (setup_id, _, direcao, pool_id, pool_tipo, pool_preco,
         evento_tipo, evento_tempo_str,
         evento_nivel, leg_id, poi_fundo, poi_topo, score) = row

        if EXIGIR_CONFIRMACAO_LTF:
            evento_tempo = datetime.fromisoformat(evento_tempo_str)
            confirmacao = detectar_mss_no_poi(velas_m5, poi_fundo, poi_topo, direcao, simbolo,
                                              cutoff=evento_tempo, atr_m5=atr_m5, tp_ref=evento_nivel)
            if confirmacao is None:
                logger.debug(
                    "Setup %s id=%s aguardando: MSS no M5 (POI=[%.5f–%.5f] dir=%s)",
                    simbolo, setup_id, poi_fundo, poi_topo, direcao,
                )
                continue
            confirmacao.setup_id = setup_id
            confirmacao.id = gerar_id_confirmacao(simbolo, setup_id, confirmacao.tempo)
            confirmacao.expiration_time = confirmacao.tempo + timedelta(minutes=1 * TIMEFRAME_GATILHO_MINUTOS)
        else:
            if not (poi_fundo <= preco_atual <= poi_topo):
                continue
            sl_ref = poi_fundo if direcao == "ALTA" else poi_topo
            rr_result = _calcular_risco_rr_v2(preco_atual, sl_ref, direcao, atr_m5, tp_ref=evento_nivel)
            if rr_result is None:
                logger.debug("Setup %s id=%s ignorado: risco nulo (poi degenerado)", simbolo, setup_id)
                continue
            sl, tp, rr = rr_result
            _agora = datetime.now(timezone.utc)
            confirmacao = ConfirmacaoEntrada(
                id=gerar_id_confirmacao(simbolo, setup_id, _agora),
                setup_id=setup_id,
                simbolo=simbolo,
                tipo_confirmacao="DIRETO",
                preco_confirmacao=preco_atual,
                tempo=_agora,
                sl=sl,
                tp=tp,
                rr=rr,
                expiration_time=_agora + timedelta(minutes=1 * TIMEFRAME_GATILHO_MINUTOS),
            )

        zona_ok = zona_por_direcao.get(direcao, False)

        check_sessao = "✅ London/NY" if em_sessao else "⚠️ Fora de sessão"
        check_bias = (
            "✅ Alinhado" if bias_d1 == direcao
            else "⚠️ Neutro" if bias_d1 is None
            else "⚠️ Contra D1"
        )
        check_zona = (
            ("✅ Desconto" if direcao == "ALTA" else "✅ Premium") if zona_ok
            else "⚠️ Sem confluência"
        )
        displacement = "✅ Sim" if leg_id else "—"

        entrada = confirmacao.preco_confirmacao
        mensagem = MENSAGEM_SETUP.format(
            simbolo=simbolo,
            score=score,
            score_emoji=_score_emoji(score),
            evento_tipo=evento_tipo,
            direcao=direcao,
            poi_fundo=poi_fundo,
            poi_topo=poi_topo,
            pool_tipo=pool_tipo,
            pool_preco=pool_preco,
            entrada=entrada,
            sl=confirmacao.sl,
            sl_pips=_pips(entrada, confirmacao.sl, simbolo),
            tp=confirmacao.tp,
            tp_pips=_pips(confirmacao.tp, entrada, simbolo),
            rr=confirmacao.rr,
            displacement=displacement,
            check_sessao=check_sessao,
            checks_aviso=_checks_aviso(check_bias, check_zona),
            expiration_str=(
                confirmacao.expiration_time.strftime("%H:%M UTC")
                if confirmacao.expiration_time else "—"
            ),
            expired_flag=(
                " ❌ EXPIRADO"
                if confirmacao.expiration_time and datetime.now(timezone.utc) > confirmacao.expiration_time
                else ""
            ),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

        repo.persistir_confirmacao(
            id_conf=confirmacao.id,
            setup_id=setup_id,
            simbolo=simbolo,
            tipo=confirmacao.tipo_confirmacao,
            preco=confirmacao.preco_confirmacao,
            tempo=confirmacao.tempo,
            sl=confirmacao.sl,
            tp=confirmacao.tp,
            rr=confirmacao.rr,
        )

        enviado = notificador.enviar_alerta(mensagem)
        if enviado:
            repo.desativar_setup(setup_id)
            logger.info("Sinal v2 disparado: %s %s score=%d", simbolo, direcao, score)
        else:
            logger.error("Falha ao enviar alerta v2 para %s — setup mantido ativo para retry.", simbolo)


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

    _ciclo = 0
    try:
        while True:
            _ciclo += 1
            logger.debug("--- Início do ciclo de varredura ---")
            for simbolo in ATIVOS_MONITORADOS:
                try:
                    _processar_simbolo(simbolo, provedor, notificador, repo)
                except Exception:
                    logger.exception("Erro não tratado ao processar %s.", simbolo)

            if _ciclo % 60 == 0:
                cutoff_housekeeping = datetime.now(timezone.utc) - timedelta(hours=IDADE_MAX_SETUP_HORAS * 2)
                removidos = repo.limpar_setups_antigos(cutoff_housekeeping)
                if removidos:
                    logger.info("Housekeeping: %d setups antigos removidos.", removidos)

            logger.debug("--- Ciclo concluído. Aguardando %ds ---", INTERVALO_VARREDURA_SEGUNDOS)
            time.sleep(INTERVALO_VARREDURA_SEGUNDOS)

    except KeyboardInterrupt:
        logger.info("Encerramento solicitado pelo usuário.")
    finally:
        provedor.desconectar()
        repo.fechar()
        logger.info("Sistema SMC encerrado.")
