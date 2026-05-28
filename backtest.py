"""Engine de backtesting walk-forward com janela deslizante.

Replica exatamente o comportamento do sistema ao vivo:
- Janela deslizante de VELAS_TIMEFRAME_ESTRUTURAL candles H4 (padrão: 500)
- Passo de 6 candles H4 (≈ 24h) para cobrir todo o histórico
- `velas_h4.iloc[:-1]` como referência de mitigação (excluindo candle aberta)
- Confirmação MSS buscada no M5 dentro de IDADE_MAX_SETUP_HORAS
- Resultado avaliado por TP/SL nas velas M5 seguintes

Uso:
    python backtest.py
    python backtest.py --simbolos EURUSD XAUUSD GBPUSD
    python backtest.py --score-min 60
    python backtest.py --saida resultados_bt.csv
"""

import argparse
import csv
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from smc.analisador_smc import (
    calcular_atr,
    calcular_poi_composta,
    calcular_score_setup,
    detectar_captura_liquidez,
    detectar_eventos_estrutura,
    detectar_mss_no_poi,
    detectar_obs_corpo,
    detectar_eqh_eql,
    detectar_pdh_pdl,
    extrair_fvgs_no_intervalo,
    extrair_legs,
    marcar_fvgs_mitigados,
    marcar_obs_v2_mitigados,
    pool_varrido_por_sweep,
)
from smc.configuracoes import (
    ATR_PERIODO,
    ATIVOS_MONITORADOS,
    CAMINHO_BANCO,
    IDADE_MAX_SETUP_HORAS,
    LIMIAR_PAVIO,
    PERIODO_SWING,
    PERIODO_SWING_D1,
    PERIODO_SWING_ESTRUTURA,
    TIMEFRAME_CONTEXTO_MACRO,
    TIMEFRAME_ESTRUTURAL,
    TIMEFRAME_GATILHO,
    VELAS_TIMEFRAME_CONTEXTO_MACRO,
    VELAS_TIMEFRAME_ESTRUTURAL,
)
from smc.filtros import calcular_bias_d1_v2, verificar_sessao, verificar_zona_premium_discount_v2
from smc.modelos import gerar_id_setup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_SQL_VELAS = """
    SELECT tempo, abertura, maxima, minima, fechamento, volume
    FROM velas WHERE simbolo = ? AND timeframe = ?
    ORDER BY tempo
"""
_COLUNAS = ["tempo", "abertura", "maxima", "minima", "fechamento", "volume"]

# Passo do walk-forward: a cada 6 H4 (≈ 24h) o sistema verifica novos setups.
_STEP_H4 = 6


# ---------------------------------------------------------------------------
# Carregamento
# ---------------------------------------------------------------------------

def _carregar_velas(conn: sqlite3.Connection, simbolo: str, timeframe: int) -> pd.DataFrame | None:
    rows = conn.execute(_SQL_VELAS, (simbolo, timeframe)).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=_COLUNAS)
    df["tempo"] = pd.to_datetime(df["tempo"], utc=True)
    for col in ("abertura", "maxima", "minima", "fechamento"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(int)
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Detecção na janela — replica _detectar_e_registrar_setups do sistema ao vivo
# ---------------------------------------------------------------------------

def _detectar_setups_na_janela(
    simbolo: str,
    velas_h4: pd.DataFrame,
    velas_d1: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Replica exatamente o pipeline ao vivo sobre a janela de 500 candles."""
    atr = calcular_atr(velas_h4, ATR_PERIODO)
    if atr <= 0:
        return []

    eventos = detectar_eventos_estrutura(velas_h4, simbolo, PERIODO_SWING_ESTRUTURA)
    if not eventos:
        return []

    legs = extrair_legs(velas_h4, eventos, simbolo, PERIODO_SWING_ESTRUTURA, atr)
    leg_por_evento: dict[datetime, Any] = {leg.tempo_fim: leg for leg in legs}

    pools = detectar_eqh_eql(velas_h4, simbolo, atr)
    if velas_d1 is not None:
        pools += detectar_pdh_pdl(velas_d1, simbolo, atr)
    if not pools:
        return []

    preco_atual = float(velas_h4.iloc[-1]["fechamento"])
    capturas = detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO, simbolo)

    # Pré-computar filtros contextuais uma vez por janela (como o backport do fix P1).
    bias_d1 = (
        calcular_bias_d1_v2(velas_d1, simbolo, PERIODO_SWING_D1)
        if velas_d1 is not None else None
    )
    ts_agora = velas_h4.iloc[-1]["tempo"].to_pydatetime()
    if ts_agora.tzinfo is None:
        ts_agora = ts_agora.replace(tzinfo=timezone.utc)
    em_sessao = verificar_sessao(ts_agora)
    zona_por_direcao: dict[str, bool] = {}
    if velas_d1 is not None:
        for _dir in ("ALTA", "BAIXA"):
            zona_por_direcao[_dir] = verificar_zona_premium_discount_v2(
                velas_d1, preco_atual, _dir, PERIODO_SWING_D1
            )

    setups: list[dict[str, Any]] = []

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

        evento = eventos_pos[-1]
        leg = leg_por_evento.get(evento.tempo)

        if leg is not None:
            obs_leg = detectar_obs_corpo(velas_h4, leg, simbolo)
            fvgs_leg = extrair_fvgs_no_intervalo(
                velas_h4, leg.indice_inicio, leg.indice_fim, simbolo
            )
            # Mitigação checada apenas contra candles anteriores ao evento (ChoCH/BOS),
            # pois retestes pós-evento são zonas de entrada válidas, não invalidações.
            ts_evento = pd.Timestamp(evento.tempo)
            velas_pre_evento = velas_h4[velas_h4["tempo"] < ts_evento]
            marcar_obs_v2_mitigados(obs_leg, velas_pre_evento)
            marcar_fvgs_mitigados(fvgs_leg, velas_pre_evento)
            obs_val = [o for o in obs_leg if not o.mitigado and o.direcao == evento.direcao]
            fvgs_val = [f for f in fvgs_leg if not f.mitigado and f.direcao == evento.direcao]
            poi_fundo, poi_topo = calcular_poi_composta(obs_val, fvgs_val, evento.nivel_rompido)
            tem_overlap = any(
                max(o.preco_fundo, f.preco_fundo) <= min(o.preco_topo, f.preco_topo)
                for o in obs_val for f in fvgs_val
            )
            zona_virgem = (
                not any(o.testado for o in obs_val)
                and not any(f.testado for f in fvgs_val)
            )
        else:
            poi_fundo = poi_topo = evento.nivel_rompido
            tem_overlap = False
            zona_virgem = False

        if poi_fundo >= poi_topo:
            continue

        bias_alinhado = bias_d1 == evento.direcao
        zona_ok = zona_por_direcao.get(evento.direcao, False)

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

        ancora = leg.id if leg is not None else evento.tipo
        setup_id = gerar_id_setup(simbolo, ancora, evento.tempo)

        setups.append({
            "setup_id": setup_id,
            "simbolo": simbolo,
            "direcao": evento.direcao,
            "evento_tipo": evento.tipo,
            "evento_tempo": evento.tempo,
            "evento_nivel": evento.nivel_rompido,
            "pool_tipo": pool.tipo,
            "leg_id": leg.id if leg else None,
            "poi_fundo": poi_fundo,
            "poi_topo": poi_topo,
            "score": score,
            "atr": atr,
        })

    return setups


# ---------------------------------------------------------------------------
# Confirmação MSS
# ---------------------------------------------------------------------------

def _buscar_confirmacao(
    setup: dict[str, Any],
    velas_m5: pd.DataFrame,
) -> dict[str, Any] | None:
    evento_tempo = setup["evento_tempo"]
    if evento_tempo.tzinfo is None:
        evento_tempo = evento_tempo.replace(tzinfo=timezone.utc)
    expiry = evento_tempo + timedelta(hours=IDADE_MAX_SETUP_HORAS)

    janela = velas_m5[
        (velas_m5["tempo"] > pd.Timestamp(evento_tempo))
        & (velas_m5["tempo"] <= pd.Timestamp(expiry))
    ].reset_index(drop=True)

    if len(janela) == 0:
        return None

    atr_m5 = calcular_atr(velas_m5, ATR_PERIODO)
    conf = detectar_mss_no_poi(
        janela,
        setup["poi_fundo"],
        setup["poi_topo"],
        setup["direcao"],
        setup["simbolo"],
        cutoff=None,
        atr_m5=atr_m5,
        tp_ref=setup["evento_nivel"],
    )
    if conf is None:
        return None

    return {
        "direcao": setup["direcao"],
        "preco_entrada": conf.preco_confirmacao,
        "tempo_entrada": conf.tempo,
        "sl": conf.sl,
        "tp": conf.tp,
        "rr": conf.rr,
    }


# ---------------------------------------------------------------------------
# Avaliação de resultado — vetorizado
# ---------------------------------------------------------------------------

def _avaliar_resultado(
    conf: dict[str, Any],
    velas_m5: pd.DataFrame,
) -> tuple[str, datetime | None, float | None]:
    tempo_entrada = conf["tempo_entrada"]
    if tempo_entrada.tzinfo is None:
        tempo_entrada = tempo_entrada.replace(tzinfo=timezone.utc)

    pos = velas_m5[velas_m5["tempo"] > pd.Timestamp(tempo_entrada)].reset_index(drop=True)
    if len(pos) == 0:
        return "ABERTO", None, None

    direcao = conf["direcao"]
    sl, tp = conf["sl"], conf["tp"]
    max_h = pos["maxima"].values
    min_l = pos["minima"].values

    tp_hits = max_h >= tp if direcao == "ALTA" else min_l <= tp
    sl_hits = min_l <= sl if direcao == "ALTA" else max_h >= sl

    tp_idx = int(tp_hits.argmax()) if tp_hits.any() else len(pos)
    sl_idx = int(sl_hits.argmax()) if sl_hits.any() else len(pos)

    if tp_idx == len(pos) and sl_idx == len(pos):
        return "ABERTO", None, None

    if tp_idx < sl_idx:
        resultado, hit_row = "WIN", pos.iloc[tp_idx]
    elif sl_idx < tp_idx:
        resultado, hit_row = "LOSS", pos.iloc[sl_idx]
    else:
        resultado, hit_row = "AMBIGUO", pos.iloc[tp_idx]

    hit_dt = hit_row["tempo"].to_pydatetime()
    duracao = (hit_dt - tempo_entrada).total_seconds() / 3600
    return resultado, hit_dt, round(duracao, 1)


# ---------------------------------------------------------------------------
# Loop por símbolo (walk-forward com janela deslizante)
# ---------------------------------------------------------------------------

def _rodar_simbolo(
    conn: sqlite3.Connection,
    simbolo: str,
    score_min: int,
) -> list[dict[str, Any]]:
    velas_h4_full = _carregar_velas(conn, simbolo, TIMEFRAME_ESTRUTURAL)
    velas_m5_full = _carregar_velas(conn, simbolo, TIMEFRAME_GATILHO)
    velas_d1_full = _carregar_velas(conn, simbolo, TIMEFRAME_CONTEXTO_MACRO)

    if velas_h4_full is None or len(velas_h4_full) < VELAS_TIMEFRAME_ESTRUTURAL + _STEP_H4:
        logger.warning("%s: H4 insuficiente (%d candles), pulando.",
                       simbolo, len(velas_h4_full) if velas_h4_full is not None else 0)
        return []

    setups_vistos: set[str] = set()
    todos_setups: list[dict[str, Any]] = []
    n = len(velas_h4_full)

    # Alinha o início do walk-forward com o início dos dados M5, para garantir
    # que o evento_tempo tenha M5 disponível para confirmação e avaliação.
    i_inicio = VELAS_TIMEFRAME_ESTRUTURAL
    if velas_m5_full is not None and len(velas_m5_full) > 0:
        ts_m5_inicio = velas_m5_full.iloc[0]["tempo"]
        mask = velas_h4_full["tempo"] >= ts_m5_inicio
        if mask.any():
            i_inicio = max(VELAS_TIMEFRAME_ESTRUTURAL, int(mask.idxmax()))
        logger.debug(
            "%s: M5 inicia em %s — walk-forward começa no índice H4 %d/%d",
            simbolo, ts_m5_inicio.strftime("%Y-%m-%d"), i_inicio, n,
        )

    # Walk-forward: janela deslizante, passo de 6 candles (≈ 24h)
    for i in range(i_inicio, n, _STEP_H4):
        janela_h4 = velas_h4_full.iloc[i - VELAS_TIMEFRAME_ESTRUTURAL: i].reset_index(drop=True)
        ts_fim = janela_h4.iloc[-1]["tempo"]

        janela_d1 = None
        if velas_d1_full is not None:
            janela_d1 = (
                velas_d1_full[velas_d1_full["tempo"] <= ts_fim]
                .tail(VELAS_TIMEFRAME_CONTEXTO_MACRO)
                .reset_index(drop=True)
            )
            if len(janela_d1) == 0:
                janela_d1 = None

        setups = _detectar_setups_na_janela(simbolo, janela_h4, janela_d1)

        for s in setups:
            if s["setup_id"] not in setups_vistos:
                setups_vistos.add(s["setup_id"])
                todos_setups.append(s)

    # Avaliar cada setup único encontrado
    resultados: list[dict[str, Any]] = []

    for setup in todos_setups:
        row: dict[str, Any] = {
            "simbolo": simbolo,
            "direcao": setup["direcao"],
            "evento_tipo": setup["evento_tipo"],
            "pool_tipo": setup["pool_tipo"],
            "score": setup["score"],
            "evento_tempo": setup["evento_tempo"].strftime("%Y-%m-%d %H:%M"),
            "poi_fundo": round(setup["poi_fundo"], 5),
            "poi_topo": round(setup["poi_topo"], 5),
            "confirmado": False,
            "tempo_entrada": "",
            "preco_entrada": "",
            "sl": "",
            "tp": "",
            "rr": "",
            "resultado": f"FILTRADO_SCORE<{score_min}" if setup["score"] < score_min else "SEM_CONFIRMACAO",
            "duracao_horas": "",
        }

        if setup["score"] < score_min or velas_m5_full is None:
            resultados.append(row)
            continue

        conf = _buscar_confirmacao(setup, velas_m5_full)
        if conf is None:
            resultados.append(row)
            continue

        row["confirmado"] = True
        t_ent = conf["tempo_entrada"]
        row["tempo_entrada"] = t_ent.strftime("%Y-%m-%d %H:%M") if hasattr(t_ent, "strftime") else str(t_ent)
        row["preco_entrada"] = round(conf["preco_entrada"], 5)
        row["sl"] = round(conf["sl"], 5)
        row["tp"] = round(conf["tp"], 5)
        row["rr"] = round(conf["rr"], 2)

        resultado, _, duracao = _avaliar_resultado(conf, velas_m5_full)
        row["resultado"] = resultado
        row["duracao_horas"] = duracao or ""

        resultados.append(row)

    return resultados


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

def _imprimir_relatorio(resultados: list[dict[str, Any]], score_min: int) -> None:
    confirmados = [r for r in resultados if r["confirmado"]]
    avaliados = [r for r in confirmados if r["resultado"] in ("WIN", "LOSS", "AMBIGUO")]
    wins = [r for r in avaliados if r["resultado"] == "WIN"]
    losses = [r for r in avaliados if r["resultado"] == "LOSS"]
    abertos = [r for r in confirmados if r["resultado"] == "ABERTO"]

    print()
    print("=" * 62)
    print(f"  BACKTEST — score ≥ {score_min}")
    print("=" * 62)
    print(f"  Setups únicos detectados : {len(resultados)}")
    print(f"  Sem confirmação MSS      : {len([r for r in resultados if r['resultado'] == 'SEM_CONFIRMACAO'])}")
    print(f"  Confirmados              : {len(confirmados)}")
    print(f"  Avaliados (W+L)          : {len(avaliados)}")
    print(f"  Abertos (sem dados M5)   : {len(abertos)}")
    print()

    if avaliados:
        wr = len(wins) / len(avaliados) * 100
        rr_wins = [float(r["rr"]) for r in wins]
        dur_wins = [r["duracao_horas"] for r in wins if r["duracao_horas"] != ""]
        dur_loss = [r["duracao_horas"] for r in losses if r["duracao_horas"] != ""]
        print(f"  Win rate geral           : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
        if rr_wins:
            print(f"  RR médio (wins)          : {sum(rr_wins)/len(rr_wins):.2f}")
        if dur_wins:
            print(f"  Duração média WIN        : {sum(dur_wins)/len(dur_wins):.1f}h")
        if dur_loss:
            print(f"  Duração média LOSS       : {sum(dur_loss)/len(dur_loss):.1f}h")

    print()
    print("  Win rate por faixa de score:")
    for lo, hi in [(40, 49), (50, 59), (60, 69), (70, 79), (80, 100)]:
        grupo = [r for r in avaliados if lo <= r["score"] <= hi]
        if not grupo:
            continue
        w = sum(1 for r in grupo if r["resultado"] == "WIN")
        print(f"    {lo}-{hi} pts : {w:3}/{len(grupo):3} = {w/len(grupo)*100:.0f}%")

    print()
    print("  Win rate por direção:")
    for direcao in ("ALTA", "BAIXA"):
        grupo = [r for r in avaliados if r["direcao"] == direcao]
        if not grupo:
            continue
        w = sum(1 for r in grupo if r["resultado"] == "WIN")
        print(f"    {direcao:6} : {w:3}/{len(grupo):3} = {w/len(grupo)*100:.0f}%")

    print()
    print("  Win rate por tipo de evento:")
    for tipo in ("ChoCH", "BOS"):
        grupo = [r for r in avaliados if r["evento_tipo"] == tipo]
        if not grupo:
            continue
        w = sum(1 for r in grupo if r["resultado"] == "WIN")
        print(f"    {tipo:5} : {w:3}/{len(grupo):3} = {w/len(grupo)*100:.0f}%")

    from collections import defaultdict
    por_simbolo: dict[str, list[bool]] = defaultdict(list)
    for r in avaliados:
        por_simbolo[r["simbolo"]].append(r["resultado"] == "WIN")
    ranking = sorted(
        [(s, sum(v), len(v)) for s, v in por_simbolo.items()],
        key=lambda x: -x[1] / x[2],
    )
    if ranking:
        print()
        print("  Top 5 símbolos (win rate):")
        for sim, w_n, tot in ranking[:5]:
            print(f"    {sim:10} : {w_n}/{tot} = {w_n/tot*100:.0f}%")
        if len(ranking) > 5:
            print("  Bottom 5 símbolos:")
            for sim, w_n, tot in ranking[-5:]:
                print(f"    {sim:10} : {w_n}/{tot} = {w_n/tot*100:.0f}%")

    print("=" * 62)
    print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest SMC walk-forward com janela deslizante.")
    parser.add_argument("--simbolos", nargs="+", help="Subset de símbolos (padrão: todos)")
    parser.add_argument("--score-min", type=int, default=40, help="Score mínimo (padrão: 40)")
    parser.add_argument("--saida", default="resultados_backtest.csv", help="Arquivo CSV de saída")
    args = parser.parse_args()

    simbolos = args.simbolos or ATIVOS_MONITORADOS

    conn = sqlite3.connect(CAMINHO_BANCO)
    todos: list[dict[str, Any]] = []

    for i, simbolo in enumerate(simbolos, 1):
        logger.info("[%d/%d] %s...", i, len(simbolos), simbolo)
        resultados = _rodar_simbolo(conn, simbolo, args.score_min)
        todos.extend(resultados)
        confirmados = sum(1 for r in resultados if r["confirmado"])
        avaliados = [r for r in resultados if r["resultado"] in ("WIN", "LOSS")]
        wins = sum(1 for r in avaliados if r["resultado"] == "WIN")
        wr = f"{wins}/{len(avaliados)} = {wins/len(avaliados)*100:.0f}%" if avaliados else "—"
        logger.info("  → %d setups | %d confirmados | %s", len(resultados), confirmados, wr)

    conn.close()

    if not todos:
        logger.warning("Nenhum resultado gerado. Verifique os dados históricos.")
        sys.exit(0)

    campos = [
        "simbolo", "direcao", "evento_tipo", "pool_tipo", "score",
        "evento_tempo", "poi_fundo", "poi_topo",
        "confirmado", "tempo_entrada", "preco_entrada", "sl", "tp", "rr",
        "resultado", "duracao_horas",
    ]
    with open(args.saida, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(todos)

    logger.info("Resultados salvos em %s (%d linhas)", args.saida, len(todos))
    _imprimir_relatorio(todos, args.score_min)


if __name__ == "__main__":
    main()
