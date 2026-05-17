import hashlib
import logging.handlers
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from smc.analisador_smc import (
    calcular_atr,
    calcular_poi_composta,
    calcular_score_setup,
    detectar_eventos_estrutura,
    detectar_mss_no_poi,
    detectar_obs_corpo,
    detectar_pdh_pdl,
    detectar_eqh_eql,
    extrair_fvgs_no_intervalo,
    extrair_legs,
    _marcar_obs_v2_mitigados,
    _marcar_fvgs_mitigados,
)
from smc.configuracoes import (
    ATR_PERIODO,
    ATIVOS_MONITORADOS,
    CAMINHO_BANCO,
    EXIGIR_CONFIRMACAO_LTF,
    IDADE_MAX_SETUP_HORAS,
    INTERVALO_VARREDURA_SEGUNDOS,
    PERIODO_SWING,
    PERIODO_SWING_D1,
    SCORE_MINIMO_SETUP,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    TIMEFRAME_D1,
    TIMEFRAME_ESTRUTURAL,
    TIMEFRAME_GATILHO,
    VELAS_D1_HISTORICO,
    VELAS_HISTORICO,
)
from smc.modelos import (
    ConfirmacaoEntrada,
    gerar_id_confirmacao,
    gerar_id_setup,
)
from smc.filtros import calcular_bias_d1_v2, verificar_sessao, verificar_zona_premium_discount_v2
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
        logger.warning("Dados insuficientes para %s no M15.", simbolo)
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
    _verificar_confirmacoes(simbolo, velas_m5, velas_d1, preco_atual, repo, notificador)


MENSAGEM_SETUP = (
    "🔔 <b>SETUP SMC — {simbolo}</b> [{score}pts]\n"
    "📊 Tipo: {evento_tipo} | Direção: {direcao}\n"
    "🎯 POI: {poi_fundo:.5f} – {poi_topo:.5f}\n"
    "💡 Pool: {pool_tipo} @ {pool_preco:.5f}\n"
    "📈 Displacement: {displacement}\n"
    "💰 SL: {sl:.5f} | TP: {tp:.5f} | R:R 1:{rr:.1f}\n"
    "🔍 Sessão: {check_sessao} | Bias D1: {check_bias} | Zona: {check_zona}\n"
    "⏰ {timestamp}"
)


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

    eventos = detectar_eventos_estrutura(velas_h4, simbolo, PERIODO_SWING)
    if not eventos:
        return

    legs = extrair_legs(velas_h4, eventos, simbolo, PERIODO_SWING, atr)
    leg_por_evento: dict[datetime, Any] = {leg.tempo_fim: leg for leg in legs}

    pools = detectar_eqh_eql(velas_h4, simbolo, atr)
    if velas_d1 is not None:
        pools += detectar_pdh_pdl(velas_d1, simbolo, atr)

    if not pools:
        return

    preco_atual = float(velas_m5.iloc[-1]["fechamento"])

    for pool in pools:
        varrido = (
            (pool.tipo in ("EQH", "PDH") and preco_atual > pool.preco - pool.tolerancia)
            or (pool.tipo in ("EQL", "PDL") and preco_atual < pool.preco + pool.tolerancia)
        )
        if not varrido:
            continue

        eventos_pos = [e for e in eventos if e.tempo > pool.tempo]
        if not eventos_pos:
            continue

        evento = eventos_pos[0]
        leg = leg_por_evento.get(evento.tempo)

        velas_fechadas = velas_h4.iloc[:-1]
        if leg is not None:
            obs_leg = detectar_obs_corpo(velas_h4, leg, simbolo)
            fvgs_leg = extrair_fvgs_no_intervalo(velas_h4, leg.indice_inicio, leg.indice_fim, simbolo)
            _marcar_obs_v2_mitigados(obs_leg, velas_fechadas)
            _marcar_fvgs_mitigados(fvgs_leg, velas_fechadas)
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

        bias_d1 = calcular_bias_d1_v2(velas_d1, simbolo, PERIODO_SWING_D1) if velas_d1 is not None else None
        bias_alinhado = bias_d1 == evento.direcao
        em_sessao = verificar_sessao(datetime.now(timezone.utc))
        zona_ok = (
            verificar_zona_premium_discount_v2(velas_d1, preco_atual, evento.direcao, PERIODO_SWING_D1)
            if velas_d1 is not None else False
        )

        score = calcular_score_setup(
            evento=evento,
            leg=leg,
            pool=pool,
            velas_d1=velas_d1,
            atr=atr,
            tem_overlap_ob_fvg=tem_overlap,
            em_sessao=em_sessao,
            zona_ok=zona_ok,
            bias_alinhado=bias_alinhado,
        )

        if score < SCORE_MINIMO_SETUP:
            logger.debug("Setup %s ignorado: score=%d < %d", simbolo, score, SCORE_MINIMO_SETUP)
            continue

        setup_id = gerar_id_setup(simbolo, pool.id, evento.tempo)
        if repo.setup_ja_existe(setup_id):
            continue

        repo.persistir_setup(
            id_setup=setup_id,
            simbolo=simbolo,
            direcao=evento.direcao,
            pool_id=pool.id,
            evento_tipo=evento.tipo,
            evento_tempo=evento.tempo,
            evento_nivel=evento.nivel_rompido,
            leg_id=leg.id if leg else None,
            poi_fundo=poi_fundo,
            poi_topo=poi_topo,
            score=score,
        )
        logger.info("Setup SMC registrado: %s %s score=%d POI=[%.5f-%.5f]",
                    simbolo, evento.direcao, score, poi_fundo, poi_topo)


def _verificar_confirmacoes(
        simbolo: str,
        velas_m5: pd.DataFrame,
        velas_d1: pd.DataFrame | None,
        preco_atual: float,
        repo: Repositorio,
        notificador: Notificador,
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=IDADE_MAX_SETUP_HORAS)
    setups = repo.carregar_setups_ativos(simbolo, cutoff)

    for row in setups:
        (setup_id, _, direcao, pool_id, evento_tipo, evento_tempo_str,
         evento_nivel, leg_id, poi_fundo, poi_topo, score) = row

        if EXIGIR_CONFIRMACAO_LTF:
            confirmacao = detectar_mss_no_poi(velas_m5, poi_fundo, poi_topo, direcao, simbolo)
            if confirmacao is None:
                continue
        else:
            if not (poi_fundo <= preco_atual <= poi_topo):
                continue
            from smc.modelos import gerar_id_confirmacao as _gid
            sl, tp, rr = _calcular_risco_rr_direto(preco_atual, poi_fundo, poi_topo, direcao)
            confirmacao = ConfirmacaoEntrada(
                id=_gid(simbolo, setup_id, datetime.now(timezone.utc)),
                setup_id=setup_id,
                simbolo=simbolo,
                tipo_confirmacao="DIRETO",
                preco_confirmacao=preco_atual,
                tempo=datetime.now(timezone.utc),
                sl=sl,
                tp=tp,
                rr=rr,
            )

        id_sinal = hashlib.sha1(
            f"{setup_id}|{confirmacao.tempo.isoformat()}".encode()
        ).hexdigest()[:20]

        if repo.sinal_ja_disparado(id_sinal):
            continue

        bias_d1 = calcular_bias_d1_v2(velas_d1, simbolo, PERIODO_SWING_D1) if velas_d1 is not None else None
        em_sessao = verificar_sessao(datetime.now(timezone.utc))
        zona_ok = (
            verificar_zona_premium_discount_v2(velas_d1, preco_atual, direcao, PERIODO_SWING_D1)
            if velas_d1 is not None else False
        )

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

        mensagem = MENSAGEM_SETUP.format(
            simbolo=simbolo,
            score=score,
            evento_tipo=evento_tipo,
            direcao=direcao,
            poi_fundo=poi_fundo,
            poi_topo=poi_topo,
            pool_tipo=pool_id[:3],
            pool_preco=poi_fundo,
            displacement=displacement,
            sl=confirmacao.sl,
            tp=confirmacao.tp,
            rr=confirmacao.rr,
            check_sessao=check_sessao,
            check_bias=check_bias,
            check_zona=check_zona,
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

        class _FakeZona:
            def __init__(self, id_: str, t: float, f: float):
                self.id = id_
                self.preco_topo = t
                self.preco_fundo = f

        repo.persistir_sinal(
            id_sinal,
            simbolo,
            _FakeZona(setup_id, poi_topo, poi_fundo),
            _FakeZona(setup_id + "_fvg", poi_topo, poi_fundo),
            direcao,
        )

        repo.desativar_setup(setup_id)
        logger.info("Sinal v2 disparado: %s %s score=%d", simbolo, direcao, score)

        enviado = notificador.enviar_alerta(mensagem)
        if not enviado:
            logger.error("Falha ao enviar alerta v2 para %s.", simbolo)


def _calcular_risco_rr_direto(
        preco: float, poi_fundo: float, poi_topo: float, direcao: str
) -> tuple[float, float, float]:
    if direcao == "ALTA":
        sl = poi_fundo
        risco = preco - sl
        tp = preco + 2.0 * risco if risco > 0 else preco
    else:
        sl = poi_topo
        risco = sl - preco
        tp = preco - 2.0 * risco if risco > 0 else preco
    return sl, tp, 2.0


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
