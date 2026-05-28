"""Auditoria ponto a ponto do pipeline de geração de setups.

Uso:
    python debug_pipeline.py EURUSD
    python debug_pipeline.py AUDUSD GBPUSD EURAUD
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pandas as pd

from smc.analisador_smc import (
    _calcular_risco_rr_v2,
    calcular_atr,
    calcular_poi_composta,
    calcular_score_setup,
    calcular_swings,
    calcular_swings_confirmados,
    detectar_captura_liquidez,
    detectar_eventos_estrutura,
    detectar_eqh_eql,
    detectar_obs_corpo,
    detectar_pdh_pdl,
    extrair_fvgs_no_intervalo,
    extrair_legs,
    marcar_fvgs_mitigados,
    marcar_obs_v2_mitigados,
    pool_varrido_por_sweep,
)
from smc.configuracoes import (
    ATR_PERIODO,
    EXIGIR_CONFIRMACAO_LTF,
    IDADE_MAX_SETUP_HORAS,
    LIMIAR_PAVIO,
    PERIODO_SWING,
    PERIODO_SWING_D1,
    PERIODO_SWING_ESTRUTURA,
    SCORE_MINIMO_SETUP,
)
from smc.filtros import (
    calcular_bias_d1_v2,
    calcular_swings_confirmados as _swings_conf_filtros,
    verificar_sessao,
)

if TYPE_CHECKING:
    from smc.repositorio import Repositorio

_SEP  = "─" * 68
_SEP2 = "═" * 68
_BOX  = "│"


def _pips(p1: float, p2: float, simbolo: str) -> int:
    factor = 100 if "JPY" in simbolo else 10000
    return round(abs(p1 - p2) * factor)


def _fmt(price: float, simbolo: str) -> str:
    decimais = 3 if "JPY" in simbolo else 5
    return f"{price:.{decimais}f}"


def _t(dt: object) -> str:
    if hasattr(dt, "strftime"):
        return dt.strftime("%m-%d %H:%M")  # type: ignore[union-attr]
    return str(dt)


def _yn(v: bool) -> str:
    return "✓" if v else "✗"


# ---------------------------------------------------------------------------
# Helpers de sub-seções reutilizáveis
# ---------------------------------------------------------------------------

def _print_ctx_bias_d1(simbolo: str, velas_d1: pd.DataFrame | None, preco_atual: float) -> tuple[str | None, bool, bool]:
    """Imprime a seção CTX e retorna (bias_d1, zona_alta, zona_baixa)."""
    print(f"\n[CTX] CONTEXTO DIRECIONAL")
    print(f"  Preço atual (M5 close) : {_fmt(preco_atual, simbolo)}")

    bias_d1: str | None = None
    zona_alta = zona_baixa = False

    if velas_d1 is None:
        print("  Bias D1                : — (sem dados D1)")
        print("  Premium/Discount       : — (sem dados D1)")
    else:
        # Bias D1 — mostrar qual BOS D1 o definiu
        eventos_d1 = detectar_eventos_estrutura(velas_d1, simbolo, PERIODO_SWING_D1)
        bias_d1 = calcular_bias_d1_v2(velas_d1, simbolo, PERIODO_SWING_D1)
        if bias_d1 is None:
            ultimo_d1 = eventos_d1[-1] if eventos_d1 else None
            if ultimo_d1:
                print(f"  Bias D1                : Neutro (último evento={ultimo_d1.tipo} {ultimo_d1.direcao} — ChoCH não confirma bias)")
            else:
                print(f"  Bias D1                : Neutro (sem eventos D1)")
        else:
            bos_d1 = next((e for e in reversed(eventos_d1) if e.tipo == "BOS"), None)
            if bos_d1:
                print(f"  Bias D1                : {bias_d1}")
                print(f"    derivado de: BOS {bos_d1.direcao}  nível={_fmt(bos_d1.nivel_rompido, simbolo)}  {_t(bos_d1.tempo)}")
            else:
                print(f"  Bias D1                : {bias_d1}  (BOS D1 não localizado)")

        # Premium/Discount — mostrar midpoint explícito
        swings_d1 = calcular_swings_confirmados(velas_d1, PERIODO_SWING_D1)
        highs_d1 = [s for s in swings_d1 if s.tipo == "HIGH"]
        lows_d1  = [s for s in swings_d1 if s.tipo == "LOW"]
        if highs_d1 and lows_d1:
            last_h = max(highs_d1, key=lambda s: s.indice)
            last_l = max(lows_d1,  key=lambda s: s.indice)
            midpoint = (last_h.preco + last_l.preco) / 2.0
            zona_alta  = preco_atual < midpoint
            zona_baixa = preco_atual > midpoint
            pos = "DESCONTO (abaixo do midpoint)" if zona_alta else "PREMIUM (acima do midpoint)"
            print(f"  Premium/Discount")
            print(f"    último swing HIGH D1  : {_fmt(last_h.preco, simbolo)}  {_t(last_h.tempo)}")
            print(f"    último swing LOW  D1  : {_fmt(last_l.preco, simbolo)}  {_t(last_l.tempo)}")
            print(f"    equilíbrio (midpoint) : {_fmt(midpoint, simbolo)}")
            print(f"    preço atual           : {_fmt(preco_atual, simbolo)}  → {pos}")
            print(f"    zona desconto (ALTA)  : {_yn(zona_alta)}")
            print(f"    zona premium (BAIXA)  : {_yn(zona_baixa)}")
        else:
            print(f"  Premium/Discount       : — (swings D1 insuficientes)")

    em_sessao = verificar_sessao(datetime.now(timezone.utc))
    print(f"  Sessão London/NY       : {_yn(em_sessao)}  ({datetime.now(timezone.utc).strftime('%H:%M UTC')})")
    return bias_d1, zona_alta, zona_baixa


def _print_clusters_eqh_eql(simbolo: str, velas_h4: pd.DataFrame, atr: float) -> None:
    """Mostra os wicks membros de cada cluster EQH/EQL."""
    tolerancia = 0.1 * atr
    n = len(velas_h4)
    janela = min(50, n - 1)
    velas_f = velas_h4.iloc[n - 1 - janela: n - 1]

    highs = [(float(velas_f["maxima"].iloc[i]), velas_f["tempo"].iloc[i]) for i in range(len(velas_f))]
    lows  = [(float(velas_f["minima"].iloc[i]),  velas_f["tempo"].iloc[i]) for i in range(len(velas_f))]

    def _clusters(valores: list[tuple[float, object]], tipo: str) -> None:
        vistos: set[int] = set()
        for i, (preco_i, tempo_i) in enumerate(valores):
            membros = [(preco_j, tempo_j) for preco_j, tempo_j in valores[i + 1:] if abs(preco_j - preco_i) <= tolerancia]
            if not membros:
                continue
            todos_precos = [preco_i] + [p for p, _ in membros]
            centro = sum(todos_precos) / len(todos_precos)
            chave = round(centro / (tolerancia * 2 + 1e-10))
            if chave in vistos:
                continue
            vistos.add(chave)
            campo = "maxima" if tipo == "EQH" else "minima"
            print(f"  {tipo}  @ {_fmt(centro, simbolo)}  tolerância=±{_pips(centro + tolerancia, centro, simbolo)} pips  centro de {len(todos_precos)} wicks:")
            todos = [(preco_i, tempo_i)] + membros
            for p, t in todos:
                print(f"    {campo}={_fmt(p, simbolo)}  {_t(t)}")

    _clusters(highs, "EQH")
    _clusters(lows,  "EQL")


# ---------------------------------------------------------------------------
# Função principal: Estágio 1
# ---------------------------------------------------------------------------

def auditar_pipeline(
    simbolo: str,
    velas_h4: pd.DataFrame,
    velas_m5: pd.DataFrame,
    velas_d1: pd.DataFrame | None,
    repo: Repositorio | None = None,
) -> None:
    """Auditoria completa do Estágio 1 (detecção de setups)."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{_SEP2}")
    print(f"  AUDITORIA PIPELINE — {simbolo}   {now_str}")
    print(f"  H4={len(velas_h4)} velas | M5={len(velas_m5)} velas | D1={len(velas_d1) if velas_d1 is not None else '—'} velas")
    print(_SEP2)

    # ── G1: ATR ─────────────────────────────────────────────────────────────
    print("\n[G1] ATR H4")
    atr = calcular_atr(velas_h4, ATR_PERIODO)
    if atr <= 0:
        print(f"  ✗ ATR inválido ({atr:.5f}) — pipeline abortado")
        return
    print(f"  ATR = {_fmt(atr, simbolo)}  ({_pips(atr, 0, simbolo)} pips)")

    # ── G2: Swings confirmados H4 + eventos de estrutura ────────────────────
    print(f"\n[G2] SWINGS CONFIRMADOS H4  (periodo={PERIODO_SWING_ESTRUTURA})")
    swings_h4 = calcular_swings_confirmados(velas_h4, PERIODO_SWING_ESTRUTURA)
    if not swings_h4:
        print("  Nenhum swing confirmado")
    else:
        for s in swings_h4[-10:]:
            print(f"  {s.tipo:<4}  idx={s.indice:<4}  {_fmt(s.preco, simbolo)}  {_t(s.tempo)}")
        if len(swings_h4) > 10:
            print(f"  ... (+{len(swings_h4) - 10} anteriores omitidos)")

    print(f"\n[G2] EVENTOS ESTRUTURA H4")
    eventos = detectar_eventos_estrutura(velas_h4, simbolo, PERIODO_SWING_ESTRUTURA)
    legs    = extrair_legs(velas_h4, eventos, simbolo, PERIODO_SWING_ESTRUTURA, atr)
    leg_por_evento = {leg.tempo_fim: leg for leg in legs}

    if not eventos:
        print("  ✗ Sem eventos de estrutura — pipeline abortado")
        return
    for ev in eventos[-8:]:
        leg_ok = " [leg✓]" if leg_por_evento.get(ev.tempo) else ""
        print(f"  {ev.tipo:<5} {ev.direcao:<5}  nível={_fmt(ev.nivel_rompido, simbolo)}  {_t(ev.tempo)}{leg_ok}")
    if len(eventos) > 8:
        print(f"  ... (+{len(eventos) - 8} anteriores omitidos)")
    print(f"  Total: {len(eventos)} eventos, {len(legs)} legs")

    # ── G3: Pools ───────────────────────────────────────────────────────────
    print(f"\n[G3] POOLS DE LIQUIDEZ")
    pools_pdh_pdl = detectar_pdh_pdl(velas_d1, simbolo, atr) if velas_d1 is not None else []
    for p in pools_pdh_pdl:
        tol = _pips(p.preco + p.tolerancia, p.preco, simbolo)
        print(f"  {p.tipo}  @ {_fmt(p.preco, simbolo)}  tolerância=±{tol} pips  (D1 anterior: {_t(p.tempo)})")

    _print_clusters_eqh_eql(simbolo, velas_h4, atr)

    pools = pools_pdh_pdl + detectar_eqh_eql(velas_h4, simbolo, atr)
    if not pools:
        print("  ✗ Sem pools — pipeline abortado")
        return

    # ── CTX: Contexto direcional ─────────────────────────────────────────────
    preco_atual = float(velas_m5.iloc[-1]["fechamento"])
    bias_d1, zona_alta, zona_baixa = _print_ctx_bias_d1(simbolo, velas_d1, preco_atual)
    zona_por_direcao = {"ALTA": zona_alta, "BAIXA": zona_baixa}

    # ── G4: Sweeps ──────────────────────────────────────────────────────────
    print(f"\n[G4] SWEEPS DETECTADOS H4  (limiar_pavio={LIMIAR_PAVIO:.0%})")
    capturas = detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO, simbolo)
    if not capturas:
        print("  Nenhuma captura detectada — pools não serão ativados")
    for c in capturas[-10:]:
        print(f"  {c.direcao:<5} @ {_fmt(c.preco_varredura, simbolo)}  pavio={c.pavio_percentual:.0%}  {_t(c.tempo)}")

    # ── G5: Pool→Sweep matching ──────────────────────────────────────────────
    print(f"\n[G5] POOL → SWEEP MATCHING")
    pools_varridos: list = []
    for p in pools:
        captura = pool_varrido_por_sweep(p, capturas)
        tol = _pips(p.preco + p.tolerancia, p.preco, simbolo)
        if captura is None:
            dir_esp = "BAIXA" if p.tipo in ("EQH", "PDH") else "ALTA"
            print(f"  {p.tipo}@{_fmt(p.preco, simbolo)}  → ✗  sem captura {dir_esp} dentro de ±{tol} pips")
        else:
            diff = _pips(captura.preco_varredura, p.preco, simbolo)
            print(f"  {p.tipo}@{_fmt(p.preco, simbolo)}  → ✓  {captura.direcao}@{_fmt(captura.preco_varredura, simbolo)}  diff={diff}pip  {_t(captura.tempo)}")
            pools_varridos.append((p, captura))

    if not pools_varridos:
        print("\n  Nenhum pool varrido — nenhum setup candidato.")
        return

    # ── Por cada pool varrido ────────────────────────────────────────────────
    em_sessao = verificar_sessao(datetime.now(timezone.utc))

    for pool, captura in pools_varridos:
        direcao_reversal = "BAIXA" if pool.tipo in ("EQH", "PDH") else "ALTA"
        print(f"\n{'┌' + _SEP}")
        print(f"{_BOX} CANDIDATO: {pool.tipo}@{_fmt(pool.preco, simbolo)} → setup {direcao_reversal}")
        print(f"{_BOX}{_SEP}")

        # G6: eventos pós-sweep
        eventos_pos = [e for e in eventos if e.tempo > captura.tempo and e.direcao == direcao_reversal]
        print(f"{_BOX} [G6] Eventos {direcao_reversal} após sweep({_t(captura.tempo)}): {len(eventos_pos)}")
        if not eventos_pos:
            print(f"{_BOX}   ✗ Nenhum evento {direcao_reversal} pós-sweep — candidato descartado")
            print(f"└{_SEP}")
            continue

        for i, ev in enumerate(eventos_pos[:5]):
            mark = " ← SELECIONADO" if i == 0 else ""
            print(f"{_BOX}   {i+1}. {ev.tipo:<5} {ev.direcao}  nível={_fmt(ev.nivel_rompido, simbolo)}  {_t(ev.tempo)}{mark}")
        if len(eventos_pos) > 5:
            print(f"{_BOX}   ... (+{len(eventos_pos) - 5} omitidos)")

        evento = eventos_pos[0]
        leg = leg_por_evento.get(evento.tempo)

        # Leg details
        if leg is not None:
            leg_range = _pips(leg.preco_fim, leg.preco_inicio, simbolo)
            n_velas = leg.indice_fim - leg.indice_inicio
            print(f"{_BOX}   Leg: {leg.direcao}  início=idx{leg.indice_inicio}@{_fmt(leg.preco_inicio, simbolo)}  fim=idx{leg.indice_fim}@{_fmt(leg.preco_fim, simbolo)}")
            print(f"{_BOX}        range={leg_range}pip  {n_velas} velas H4  displacement={_yn(leg.eh_displacement)}")
        else:
            print(f"{_BOX}   Leg: não vinculada (POI = fallback em nível_rompido)")

        # G7: OBs, FVGs, POI
        velas_fechadas = velas_h4.iloc[:-1]
        obs_val: list = []
        fvgs_val: list = []

        if leg is not None:
            obs_leg  = detectar_obs_corpo(velas_h4, leg, simbolo)
            fvgs_leg = extrair_fvgs_no_intervalo(velas_h4, leg.indice_inicio, leg.indice_fim, simbolo)
            marcar_obs_v2_mitigados(obs_leg, velas_fechadas)
            marcar_fvgs_mitigados(fvgs_leg, velas_fechadas)
            obs_val  = [o for o in obs_leg  if not o.mitigado and o.direcao == evento.direcao]
            fvgs_val = [f for f in fvgs_leg if not f.mitigado and f.direcao == evento.direcao]

            print(f"{_BOX} [G7] OBs  total={len(obs_leg)}  válidos={len(obs_val)}  mitigados={len(obs_leg)-len(obs_val)}")
            for ob in obs_leg:
                status = "MITIGADO" if ob.mitigado else "válido  "
                print(f"{_BOX}   {_yn(not ob.mitigado)} [{_fmt(ob.preco_fundo, simbolo)}–{_fmt(ob.preco_topo, simbolo)}]  50%={_fmt(ob.zona_50, simbolo)}  {status}  {_t(ob.tempo)}")

            print(f"{_BOX} [G7] FVGs total={len(fvgs_leg)}  válidos={len(fvgs_val)}  mitigados={len(fvgs_leg)-len(fvgs_val)}")
            for fvg in fvgs_leg:
                status = "MITIGADO" if fvg.mitigado else "válido  "
                print(f"{_BOX}   {_yn(not fvg.mitigado)} [{_fmt(fvg.preco_fundo, simbolo)}–{_fmt(fvg.preco_topo, simbolo)}]  {status}  {_t(fvg.tempo)}")

            poi_fundo, poi_topo = calcular_poi_composta(obs_val, fvgs_val, evento.nivel_rompido)
            tem_overlap = any(
                max(o.preco_fundo, f.preco_fundo) <= min(o.preco_topo, f.preco_topo)
                for o in obs_val for f in fvgs_val
            )
            if poi_fundo == poi_topo:
                derivacao = "fallback (sem OB/FVG válidos)"
            elif tem_overlap:
                derivacao = "interseção OB∩FVG"
            elif obs_val:
                derivacao = "envelope OBs válidos"
            else:
                derivacao = "envelope FVGs válidos"
            zona_virgem = not any(o.testado for o in obs_val) and not any(f.testado for f in fvgs_val)
        else:
            poi_fundo = poi_topo = evento.nivel_rompido
            tem_overlap = False
            derivacao = "fallback (sem leg)"
            zona_virgem = False

        poi_pips = _pips(poi_topo, poi_fundo, simbolo)
        print(f"{_BOX} [G7] POI → [{_fmt(poi_fundo, simbolo)}–{_fmt(poi_topo, simbolo)}]  {poi_pips}pip  via {derivacao}")
        print(f"{_BOX}      overlap={_yn(tem_overlap)}  virgem={_yn(zona_virgem)}")

        if poi_fundo >= poi_topo:
            print(f"{_BOX}   ✗ POI degenerada — candidato descartado")
            print(f"└{_SEP}")
            continue

        # G8: Score
        bias_alinhado = bias_d1 == evento.direcao
        zona_ok       = zona_por_direcao.get(evento.direcao, False)
        score = calcular_score_setup(
            evento=evento, leg=leg, pool=pool,
            tem_overlap_ob_fvg=tem_overlap, em_sessao=em_sessao,
            zona_ok=zona_ok, bias_alinhado=bias_alinhado, zona_virgem=zona_virgem,
        )
        ev_pts   = 20 if evento.tipo == "ChoCH" else 10
        pool_pts = 15 if pool.tipo in ("PDH", "PDL") else 10
        bias_pts = 15 if bias_alinhado else 0
        disp_pts = 15 if (leg and leg.eh_displacement) else 0
        ovlp_pts = 10 if tem_overlap else 0
        sess_pts = 10 if em_sessao else 0
        zona_pts = 10 if zona_ok else 0
        virg_pts =  5 if zona_virgem else 0

        print(f"{_BOX} [G8] SCORE  (mínimo={SCORE_MINIMO_SETUP})")
        print(f"{_BOX}   {evento.tipo:<5}                  {ev_pts:+3d}")
        print(f"{_BOX}   Pool {pool.tipo:<4}                {pool_pts:+3d}")
        print(f"{_BOX}   Bias D1 {_yn(bias_alinhado)} ({bias_d1 or 'Neutro':<5})       {bias_pts:+3d}")
        print(f"{_BOX}   Displacement {_yn(leg.eh_displacement if leg else False)}          {disp_pts:+3d}")
        print(f"{_BOX}   Overlap OB∩FVG {_yn(tem_overlap)}        {ovlp_pts:+3d}")
        print(f"{_BOX}   Sessão London/NY {_yn(em_sessao)}      {sess_pts:+3d}")
        print(f"{_BOX}   Zona prem/desc {_yn(zona_ok)}        {zona_pts:+3d}")
        print(f"{_BOX}   Virgem {_yn(zona_virgem)}                {virg_pts:+3d}")
        print(f"{_BOX}   {'─' * 30}")
        decisao = "✓ passa" if score >= SCORE_MINIMO_SETUP else "✗ bloqueado"
        print(f"{_BOX}   TOTAL: {score}  {decisao}")

        if score < SCORE_MINIMO_SETUP:
            print(f"└{_SEP}")
            continue

        # G9/G10: deduplicação (requer repo)
        if repo is not None:
            cutoff_zona = datetime.now(timezone.utc) - timedelta(hours=IDADE_MAX_SETUP_HORAS)
            zona_coberta = repo.setup_ativo_na_zona(simbolo, evento.direcao, poi_fundo, poi_topo, cutoff_zona)
            print(f"{_BOX} [G9]  Zona POI já coberta? {'✗ SIM — bloqueado' if zona_coberta else '✓ NÃO'}")
            if zona_coberta:
                print(f"└{_SEP}")
                continue

            from smc.analisador_smc import gerar_id_setup
            ancora = leg.id if leg is not None else evento.tipo
            setup_id = gerar_id_setup(simbolo, ancora, evento.tempo)
            ja_existe = repo.setup_ja_existe(setup_id)
            print(f"{_BOX} [G10] Setup id={setup_id[:12]}... já existe? {'✗ SIM — ignorado' if ja_existe else '✓ NÃO'}")
            if ja_existe:
                print(f"└{_SEP}")
                continue
        else:
            print(f"{_BOX} [G9/G10] (requer repo — passe repo= para verificar deduplicação)")

        print(f"{_BOX} → Setup válido para registro")
        print(f"└{_SEP}")


# ---------------------------------------------------------------------------
# Estágio 2: Setups ativos no banco → MSS M5
# ---------------------------------------------------------------------------

def auditar_setups_ativos(
    simbolo: str,
    velas_m5: pd.DataFrame,
    atr: float,
    repo: Repositorio,
) -> None:
    """Lê todos os setups ativos no banco e audita o MSS M5 de cada um."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=IDADE_MAX_SETUP_HORAS)
    setups = repo.carregar_setups_ativos(simbolo, cutoff)

    print(f"\n{_SEP2}")
    print(f"  ESTÁGIO 2 — SETUPS ATIVOS NO BANCO: {simbolo}  ({len(setups)} setups)")
    print(_SEP2)

    if not setups:
        print("  Nenhum setup ativo.")
        return

    for row in setups:
        (setup_id, _, direcao, pool_id, pool_tipo, pool_preco,
         evento_tipo, evento_tempo_str,
         evento_nivel, leg_id, poi_fundo, poi_topo, score) = row

        evento_tempo = datetime.fromisoformat(evento_tempo_str)
        if evento_tempo.tzinfo is None:
            evento_tempo = evento_tempo.replace(tzinfo=timezone.utc)

        print(f"\n  Setup id={setup_id[:16]}...")
        print(f"  {direcao}  {evento_tipo}  score={score}")
        print(f"  Pool: {pool_tipo}@{_fmt(pool_preco, simbolo)}")
        print(f"  POI : [{_fmt(poi_fundo, simbolo)}–{_fmt(poi_topo, simbolo)}]  {_pips(poi_topo, poi_fundo, simbolo)} pips")
        print(f"  Evento tempo : {_t(evento_tempo)}  nível={_fmt(evento_nivel, simbolo)}")
        if not EXIGIR_CONFIRMACAO_LTF:
            print("  EXIGIR_CONFIRMACAO_LTF=False — MSS M5 não exigido")
            continue

        auditar_mss_para_setup(
            simbolo=simbolo,
            velas_m5=velas_m5,
            atr=atr,
            poi_fundo=poi_fundo,
            poi_topo=poi_topo,
            direcao=direcao,
            cutoff=evento_tempo,
            tp_ref=evento_nivel,
        )


# ---------------------------------------------------------------------------
# MSS M5 para um setup específico
# ---------------------------------------------------------------------------

def auditar_mss_para_setup(
    simbolo: str,
    velas_m5: pd.DataFrame,
    atr: float,
    poi_fundo: float,
    poi_topo: float,
    direcao: str,
    cutoff: datetime | None = None,
    tp_ref: float | None = None,
) -> None:
    """Audita o MSS M5 para um setup com os valores passados."""
    print(f"\n  [MSS M5] {direcao}  POI=[{_fmt(poi_fundo, simbolo)}–{_fmt(poi_topo, simbolo)}]")

    df = velas_m5
    if cutoff is not None:
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        ts = pd.to_datetime(df["tempo"], utc=True)
        df = df[ts > pd.Timestamp(cutoff)]
    print(f"  Velas M5 {'após cutoff ' + _t(cutoff) if cutoff else 'total'}: {len(df)}")

    velas_poi = df[(df["maxima"] >= poi_fundo) & (df["minima"] <= poi_topo)].reset_index(drop=True)
    print(f"  Velas dentro da POI: {len(velas_poi)}")

    if len(velas_poi) < 3:
        print(f"  ✗ Menos de 3 velas na POI — MSS não detectável")
        return

    for i in range(len(velas_poi)):
        row = velas_poi.iloc[i]
        seta = "▲" if float(row["fechamento"]) >= float(row["abertura"]) else "▼"
        print(f"    [{i}] {_t(row['tempo'])}  O={_fmt(row['abertura'],simbolo)} "
              f"H={_fmt(row['maxima'],simbolo)} L={_fmt(row['minima'],simbolo)} "
              f"C={_fmt(row['fechamento'],simbolo)} {seta}")

    swings_high, swings_low = calcular_swings(velas_poi, periodo=1)
    print(f"  Swings (periodo=1): {len(swings_high)} highs, {len(swings_low)} lows")
    for idx, p in sorted(swings_high.items()):
        print(f"    HIGH[{idx}] = {_fmt(p, simbolo)}")
    for idx, p in sorted(swings_low.items()):
        print(f"    LOW [{idx}] = {_fmt(p, simbolo)}")

    print(f"  Buscando sequência MSS {direcao}:")
    encontrou = False

    if direcao == "ALTA":
        for sl_idx in sorted(swings_low.keys()):
            sl_ref = swings_low[sl_idx]
            sh_cands = {i: p for i, p in swings_high.items() if i > sl_idx}
            if not sh_cands:
                print(f"    LOW[{sl_idx}]={_fmt(sl_ref, simbolo)} → sem swing HIGH posterior")
                continue
            sh_idx = min(sh_cands.keys())
            sh_price = sh_cands[sh_idx]
            print(f"    LOW[{sl_idx}]={_fmt(sl_ref, simbolo)} → HIGH[{sh_idx}]={_fmt(sh_price, simbolo)} → buscando close>{_fmt(sh_price, simbolo)}")
            for j in range(sh_idx + 1, len(velas_poi)):
                v = velas_poi.iloc[j]
                close = float(v["fechamento"])
                if close > sh_price:
                    rr = _calcular_risco_rr_v2(sh_price, sl_ref, direcao, atr, tp_ref)
                    if rr:
                        sl_v, tp_v, rr_v = rr
                        print(f"      ✓ [{j}] close={_fmt(close, simbolo)} confirma MSS")
                        print(f"        entrada={_fmt(sh_price, simbolo)}  SL={_fmt(sl_v, simbolo)} ({_pips(sh_price,sl_v,simbolo)}pip)"
                              f"  TP={_fmt(tp_v, simbolo)} ({_pips(tp_v,sh_price,simbolo)}pip)  RR={rr_v}")
                    else:
                        print(f"      ✓ [{j}] confirma MSS mas risco≤0 — ignorado")
                    encontrou = True
                    break
            if encontrou:
                break
    else:
        for sh_idx in sorted(swings_high.keys()):
            sl_ref = swings_high[sh_idx]
            sl_cands = {i: p for i, p in swings_low.items() if i > sh_idx}
            if not sl_cands:
                print(f"    HIGH[{sh_idx}]={_fmt(sl_ref, simbolo)} → sem swing LOW posterior")
                continue
            sl_idx_k = min(sl_cands.keys())
            sl_price = sl_cands[sl_idx_k]
            print(f"    HIGH[{sh_idx}]={_fmt(sl_ref, simbolo)} → LOW[{sl_idx_k}]={_fmt(sl_price, simbolo)} → buscando close<{_fmt(sl_price, simbolo)}")
            for j in range(sl_idx_k + 1, len(velas_poi)):
                v = velas_poi.iloc[j]
                close = float(v["fechamento"])
                if close < sl_price:
                    rr = _calcular_risco_rr_v2(sl_price, sl_ref, direcao, atr, tp_ref)
                    if rr:
                        sl_v, tp_v, rr_v = rr
                        print(f"      ✓ [{j}] close={_fmt(close, simbolo)} confirma MSS")
                        print(f"        entrada={_fmt(sl_price, simbolo)}  SL={_fmt(sl_v, simbolo)} ({_pips(sl_price,sl_v,simbolo)}pip)"
                              f"  TP={_fmt(tp_v, simbolo)} ({_pips(tp_v,sl_price,simbolo)}pip)  RR={rr_v}")
                    else:
                        print(f"      ✓ [{j}] confirma MSS mas risco≤0 — ignorado")
                    encontrou = True
                    break
            if encontrou:
                break

    if not encontrou:
        print(f"    ✗ Nenhuma sequência MSS {direcao} encontrada")
