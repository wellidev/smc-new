import hashlib
import logging
import os
import time
from datetime import datetime, timezone

from smc.configuracoes import (
    ATIVOS_MONITORADOS,
    CAMINHO_BANCO,
    INTERVALO_VARREDURA_SEGUNDOS,
    LIMIAR_PAVIO,
    MENSAGEM_ALERTA,
    PERIODO_SWING,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    TIMEFRAME_ESTRUTURAL,
    TIMEFRAME_GATILHO,
    VELAS_HISTORICO,
)
from smc.analisador_smc import (
    _zonas_sobrepoem,
    detectar_captura_liquidez,
    detectar_quebra_estrutura,
    mapear_zonas_interesse,
    verificar_confluencia,
)
from smc.notificador import Notificador
from smc.provedor_dados import ProvedorDados
from smc.repositorio import Repositorio

_NIVEL_LOG = os.getenv("SMC_LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, _NIVEL_LOG, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _gerar_id_sinal(simbolo: str, ob_id: str, fvg_id: str) -> str:
    chave = f"{simbolo}{ob_id}{fvg_id}"
    return hashlib.sha1(chave.encode()).hexdigest()[:20]


def _processar_simbolo(
    simbolo: str,
    provedor: ProvedorDados,
    notificador: Notificador,
    repo: Repositorio,
) -> None:
    velas_h4 = provedor.obter_velas(simbolo, TIMEFRAME_ESTRUTURAL, VELAS_HISTORICO)
    if velas_h4 is None or len(velas_h4) < 20:
        logger.warning("Dados insuficientes para %s no H4.", simbolo)
        return

    velas_m15 = provedor.obter_velas(simbolo, TIMEFRAME_GATILHO, 50)
    if velas_m15 is None or len(velas_m15) == 0:
        logger.warning("Dados insuficientes para %s no M15.", simbolo)
        return

    capturas = detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO, simbolo)
    quebras = detectar_quebra_estrutura(velas_h4, simbolo, PERIODO_SWING)
    obs, fvgs = mapear_zonas_interesse(velas_h4, simbolo)

    preco_atual = float(velas_m15.iloc[-1]["fechamento"])

    logger.info(
        "%s | capturas=%d | BOS=%d | OBs=%d | FVGs=%d | preço=%.5f",
        simbolo, len(capturas), len(quebras), len(obs), len(fvgs), preco_atual,
    )

    for captura in capturas:
        for bos in quebras:
            if not verificar_confluencia(captura, bos, obs, fvgs, preco_atual):
                continue

            for ob in obs:
                if ob.direcao != captura.direcao:
                    continue
                if ob.tempo >= captura.tempo:
                    continue
                for fvg in fvgs:
                    if fvg.direcao != captura.direcao:
                        continue
                    if fvg.tempo >= captura.tempo:
                        continue
                    if not _zonas_sobrepoem(ob, fvg):
                        continue
                    overlap_fundo = max(ob.preco_fundo, fvg.preco_fundo)
                    overlap_topo  = min(ob.preco_topo,  fvg.preco_topo)
                    if not (overlap_fundo <= preco_atual <= overlap_topo):
                        continue

                    id_sinal = _gerar_id_sinal(simbolo, ob.id, fvg.id)
                    if repo.sinal_ja_disparado(id_sinal):
                        logger.debug("Sinal %s já disparado anteriormente.", id_sinal)
                        continue

                    mensagem = MENSAGEM_ALERTA.format(
                        simbolo=simbolo,
                        direcao_captura=captura.direcao,
                        preco_varredura=captura.preco_varredura,
                        direcao_bos=bos.direcao,
                        nivel_bos=bos.nivel_rompido,
                        ob_fundo=ob.preco_fundo,
                        ob_topo=ob.preco_topo,
                        fvg_fundo=fvg.preco_fundo,
                        fvg_topo=fvg.preco_topo,
                        overlap_fundo=overlap_fundo,
                        overlap_topo=overlap_topo,
                        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    )

                    repo.persistir_sinal(id_sinal, simbolo, ob, fvg, captura.direcao)
                    logger.info("Novo sinal SMC para %s — %s. Enviando alerta...", simbolo, captura.direcao)
                    enviado = notificador.enviar_alerta(mensagem)
                    if not enviado:
                        logger.error("Falha ao enviar alerta para %s. Sinal persistido no banco.", simbolo)


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
