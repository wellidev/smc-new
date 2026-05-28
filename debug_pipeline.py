"""Runner do diagnóstico de pipeline SMC.

Uso:
    python debug_pipeline.py EURUSD
    python debug_pipeline.py AUDUSD GBPUSD EURAUD
"""

import sys
import time

sys.path.insert(0, "src")

from smc.analisador_smc import calcular_atr
from smc.configuracoes import (
    ATR_PERIODO,
    CAMINHO_BANCO,
    TIMEFRAME_CONTEXTO_MACRO, TIMEFRAME_ESTRUTURAL, TIMEFRAME_GATILHO,
    VELAS_TIMEFRAME_CONTEXTO_MACRO, VELAS_TIMEFRAME_ESTRUTURAL,
)
from smc.diagnostico import auditar_pipeline, auditar_setups_ativos
from smc.provedor_dados import ProvedorDados
from smc.repositorio import Repositorio


def _conectar(provedor: ProvedorDados) -> bool:
    for tentativa in range(1, 4):
        if provedor.conectar():
            return True
        print(f"  tentativa {tentativa}/3 falhou, aguardando...")
        time.sleep(2)
    return False


def main() -> None:
    simbolos = sys.argv[1:] if len(sys.argv) > 1 else ["EURUSD"]

    repo = Repositorio(CAMINHO_BANCO)

    provedor = ProvedorDados(repo)
    print("Conectando ao MT5...")
    if not _conectar(provedor):
        print("Falha ao conectar ao MT5. Verifique .env e se o MT5 esta aberto.")
        sys.exit(1)
    print("Conectado\n")

    for simbolo in simbolos:
        velas_d1 = provedor.obter_velas(simbolo, TIMEFRAME_CONTEXTO_MACRO, VELAS_TIMEFRAME_CONTEXTO_MACRO)
        velas_h4 = provedor.obter_velas(simbolo, TIMEFRAME_ESTRUTURAL, VELAS_TIMEFRAME_ESTRUTURAL)
        velas_m5 = provedor.obter_velas(simbolo, TIMEFRAME_GATILHO, 200)

        if velas_h4 is None or len(velas_h4) < 20:
            print(f"[{simbolo}] Dados H4 insuficientes - pulando")
            continue
        if velas_m5 is None or len(velas_m5) == 0:
            print(f"[{simbolo}] Dados M5 insuficientes - pulando")
            continue

        # Estagio 1: detectar candidatos a setup
        auditar_pipeline(simbolo, velas_h4, velas_m5, velas_d1, repo=repo)

        # Estagio 2: setups ativos no banco -> MSS M5
        atr = calcular_atr(velas_h4, ATR_PERIODO)
        if atr > 0:
            auditar_setups_ativos(simbolo, velas_m5, atr, repo)

    provedor.desconectar()


if __name__ == "__main__":
    main()
