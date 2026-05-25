"""Coleta dados históricos do MT5 e persiste no banco para uso em backtesting.

Uso:
    python buscar_historico.py              # busca padrão (M5=20k, H4=2k, D1=500)
    python buscar_historico.py --m5 5000   # sobrescreve quantidade de M5
"""

import argparse
import logging
import sys

from smc.configuracoes import (
    ATIVOS_MONITORADOS,
    CAMINHO_BANCO,
    TIMEFRAME_D1,
    TIMEFRAME_ESTRUTURAL,
    TIMEFRAME_GATILHO,
)
from smc.provedor_dados import ProvedorDados
from smc.repositorio import Repositorio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_NOMES_TF = {
    TIMEFRAME_GATILHO: "M5",
    TIMEFRAME_ESTRUTURAL: "H4",
    TIMEFRAME_D1: "D1",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Coleta histórico do MT5 para backtesting.")
    parser.add_argument("--m5", type=int, default=20_000, help="Candles M5 (padrão: 20000 ≈ 3 meses)")
    parser.add_argument("--h4", type=int, default=2_000, help="Candles H4 (padrão: 2000 ≈ 16 meses)")
    parser.add_argument("--d1", type=int, default=500, help="Candles D1 (padrão: 500 ≈ 2 anos)")
    parser.add_argument("--simbolos", nargs="+", help="Subset de símbolos (padrão: todos)")
    args = parser.parse_args()

    quantidades = {
        TIMEFRAME_GATILHO: args.m5,
        TIMEFRAME_ESTRUTURAL: args.h4,
        TIMEFRAME_D1: args.d1,
    }

    simbolos = args.simbolos or ATIVOS_MONITORADOS
    total_chamadas = len(simbolos) * len(quantidades)

    repo = Repositorio(CAMINHO_BANCO)
    provedor = ProvedorDados(repo)

    if not provedor.conectar():
        logger.critical("Não foi possível conectar ao MT5. Verifique se está aberto.")
        repo.fechar()
        sys.exit(1)

    logger.info(
        "Iniciando coleta: %d símbolos × %d timeframes = %d chamadas MT5",
        len(simbolos), len(quantidades), total_chamadas,
    )
    for tf, qtd in quantidades.items():
        logger.info("  %s: %d candles", _NOMES_TF[tf], qtd)

    feito = 0
    erros = 0

    for simbolo in simbolos:
        for timeframe, quantidade in quantidades.items():
            feito += 1
            n = provedor.buscar_historico_bulk(simbolo, timeframe, quantidade)
            nome_tf = _NOMES_TF[timeframe]
            if n > 0:
                logger.info("[%d/%d] %s %s: %d candles armazenados", feito, total_chamadas, simbolo, nome_tf, n)
            else:
                logger.warning("[%d/%d] %s %s: sem dados", feito, total_chamadas, simbolo, nome_tf)
                erros += 1

    provedor.desconectar()
    repo.fechar()

    logger.info("Coleta concluída. %d/%d OK, %d erros.", total_chamadas - erros, total_chamadas, erros)


if __name__ == "__main__":
    main()
