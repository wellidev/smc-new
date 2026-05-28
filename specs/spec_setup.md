# Spec: SetupSMC e Confirmação LTF

## Responsabilidade
Implementar o pipeline de 2 estágios: (1) detectar e persistir SetupSMC após sweep de pool + evento de estrutura, (2) aguardar MSS no M5 dentro da POI para disparar sinal. Substitui o pipeline de confluência síncrona (verificar_confluencia + _encontrar_confluencias).

## Onde vive
- Detecção MSS: `src/smc/analisador_smc.py` — função pública `detectar_mss_no_poi`
- Score: `src/smc/analisador_smc.py` — função pública `calcular_score_setup`
- Orchestração: `src/smc/principal.py` — `_detectar_e_registrar_setups`, `_verificar_confirmacoes`

## Dependências
- `SetupSMC`, `ConfirmacaoEntrada`, `PoolLiquidez`, `EventoEstrutura`, `LegImpulso` de `modelos.py`
- `Repositorio` de `repositorio.py`

---

## Feature Flag

```python
EXIGIR_CONFIRMACAO_LTF: bool = True  # em configuracoes.py
```

Quando `False`: o setup dispara sinal imediatamente após scoring (sem aguardar MSS no M5). Permite rollout gradual.

---

## Algoritmo — Estágio 1: Detectar Setup

Executado em `_detectar_e_registrar_setups(simbolo, velas_h4, velas_d1, repo)`:

```
atr = calcular_atr(velas_h4)
pools = detectar_eqh_eql(velas_h4, simbolo, atr)
      + detectar_pdh_pdl(velas_d1, simbolo, atr)  # [] se velas_d1 is None

eventos = detectar_eventos_estrutura(velas_h4, simbolo, PERIODO_SWING)
legs    = extrair_legs(velas_h4, eventos, simbolo, PERIODO_SWING, atr)

# Detectar capturas reais de liquidez (sweeps canônicos) ANTES do loop de pools:
# wick rompe swing high/low + close do lado oposto + pavio ≥ LIMIAR_PAVIO (0.30).
capturas = detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO, simbolo)

para cada pool em pools:
    captura = pool_varrido_por_sweep(pool, capturas)
    se captura is None: continue
        # pool_varrido_por_sweep(pool, capturas) -> CapturaLiquidez | None
        # EQH/PDH (liquidez acima): exige captura BAIXA com preco_varredura
        #   dentro de ±pool.tolerancia.
        # EQL/PDL (liquidez abaixo): exige captura ALTA com preco_varredura
        #   dentro de ±pool.tolerancia.
        # Retorna a CapturaLiquidez que varrreu o pool, ou None.
        # Procurar evento estrutural APÓS O SWEEP (captura.tempo), não
        # após a criação do pool (pool.tempo) — causalidade temporal correta.
        eventos_pos = [e for e in eventos if e.tempo > captura.tempo and e.direcao == direcao_reversal]
        se não há eventos_pos: continue

        evento = eventos_pos[0]  ← PRIMEIRO evento após o sweep (causalidade: leg mais próxima)
        leg = leg associada ao evento (se existir)

        # Calcular POI
        se leg:
            obs  = detectar_obs_corpo(velas_h4, leg, simbolo)
            fvgs = extrair_fvgs_no_intervalo(velas_h4, leg.indice_inicio, leg.indice_fim, simbolo)
            _marcar_obs_mitigados(obs, velas_h4.iloc[:-1])
            _marcar_fvgs_mitigados(fvgs, velas_h4.iloc[:-1])
            poi_fundo, poi_topo = calcular_poi_composta(obs, fvgs, evento.nivel_rompido)
        else:
            poi_fundo = poi_topo = evento.nivel_rompido

        score = calcular_score_setup(evento, leg, pool, velas_d1, atr)
        se score < SCORE_MINIMO_SETUP: continue  # ignora setup fraco

        setup_id = SHA1(simbolo|pool.id|evento.tempo.isoformat())[:20]
        se repo.setup_ja_existe(setup_id): continue

        setup = SetupSMC(
            id=setup_id, simbolo=simbolo, direcao=evento.direcao,
            pool_id=pool.id, evento_tipo=evento.tipo, evento_tempo=evento.tempo,
            evento_nivel=evento.nivel_rompido, leg_id=leg.id if leg else None,
            poi_fundo=poi_fundo, poi_topo=poi_topo, score=score
        )
        repo.persistir_setup(setup)
```

---

## Algoritmo — Estágio 2: Verificar Confirmação

Executado em `_verificar_confirmacoes(simbolo, velas_m5, velas_d1, preco_atual, repo, notificador)`:

```
setups_ativos = repo.carregar_setups_ativos(simbolo, cutoff=now - IDADE_MAX_SETUP_HORAS)

para cada setup em setups_ativos:
    se EXIGIR_CONFIRMACAO_LTF:
        confirmacao = detectar_mss_no_poi(velas_m5, setup.poi_fundo, setup.poi_topo,
                                           setup.direcao, simbolo)
        se não confirmacao: continue
    else:
        # Sem LTF: usa preco_atual como confirmacao sintética se dentro da POI
        se not (setup.poi_fundo <= preco_atual <= setup.poi_topo): continue
        confirmacao = ConfirmacaoEntrada(tipo_confirmacao="DIRETO", preco_confirmacao=preco_atual, ...)

    # Calcular filtros contextuais (informativos)
    ctx = _calcular_contexto_v2(setup, confirmacao, velas_d1)

    mensagem = _construir_mensagem_v2(simbolo, setup, confirmacao, ctx)
    repo.persistir_confirmacao(...)   # INSERT OR IGNORE — dedup via confirmacoes.id (PK)
    repo.desativar_setup(setup.id)    # ativo=0 — exclui da fila em ciclos futuros
    notificador.enviar(mensagem)
```

---

## `detectar_mss_no_poi(velas_m5, poi_fundo, poi_topo, direcao, simbolo, cutoff=None) -> ConfirmacaoEntrada | None`

MSS (Market Structure Shift) canônico no M5 dentro da POI — ChoCH de LTF
confirmando rejeição da zona. Equivalente a um *Change of Character* clássico:
o preço forma micro-estrutura interna e a primeira vela que fecha além do
último pivô define o shift.

**Algoritmo:**
```
1. (Opcional) Se cutoff for fornecido, descartar velas com tempo <= cutoff.
2. Filtrar velas_m5 cujo intervalo [minima, maxima] cruza a POI
   (maxima >= poi_fundo AND minima <= poi_topo) → velas_poi.
3. Se len(velas_poi) < 3: return None  (mínimo para calcular_swings com periodo=1).
4. swings_high, swings_low = calcular_swings(velas_poi, periodo=1).
5. Para direcao="ALTA" (setup de compra):
   a. Para cada sl_idx em sorted(swings_low):
      - Selecionar o PRIMEIRO swing HIGH com idx > sl_idx → (sh_idx, sh_price).
        Se não houver, próximo sl_idx.
      - Para j em (sh_idx + 1, len(velas_poi) - 1]:
          se velas_poi.iloc[j].fechamento > sh_price:
              preco = sh_price   ← entrada no nível rompido (limite), não no close
              tempo = vela j
              retornar ConfirmacaoEntrada(tipo="MSS", ...)
6. Para direcao="BAIXA" (setup de venda): simétrico — swing HIGH primeiro,
   depois swing LOW posterior, depois fechamento abaixo do swing LOW.
   preco = sl_price  ← entrada no nível rompido (limite), não no close
7. SL/TP via _calcular_risco_rr_v2(preco, sl_ref, direcao, atr, tp_ref):
   - Para ALTA: sl_ref = swings_low[sl_idx] — o swing low que iniciou a sequência MSS
     (ponto de invalidação estrutural: abaixo dele a tese de compra falha)
   - Para BAIXA: sl_ref = swings_high[sh_idx] — o swing high que iniciou a sequência MSS
     (ponto de invalidação estrutural: acima dele a tese de venda falha)
   - tp_ref: evento_nivel (nível do ChoCH/BOS H4 que gerou o setup), injetado pelo caller
   se retornar None (risco <= 0), pular essa candidata.
8. Se nada encontrado: return None.
```

**Requisitos mínimos:** 3 candles dentro da POI (necessário para que
`calcular_swings(..., periodo=1)` consiga identificar pelo menos um swing).
Na prática, a sequência completa LOW→HIGH→confirmação exige ~6 candles para
produzir resultado, mas o guard `< 3` serve apenas de fast-path.

---

## `calcular_score_setup(evento, leg, pool, tem_overlap_ob_fvg, em_sessao, zona_ok, bias_alinhado) -> int`

Score composicional 0–100:

| Condição | Pontos |
|----------|--------|
| `evento.tipo == "ChoCH"` | +20 |
| `evento.tipo == "BOS"` | +10 |
| D1 alinhado com a direção | +15 |
| `leg is not None and leg.eh_displacement` | +15 |
| Pool do tipo "PDH" ou "PDL" (D1 institucional) | +15 |
| Pool do tipo "EQH" ou "EQL" (intraday) | +10 |
| OB∩FVG sobreposição existe na POI | +10 |
| Sessão London/NY no momento do evento | +10 |
| Zona premium/discount (D1 range) alinhada | +10 |
| Zona virgem: nenhum OB/FVG da POI foi testado antes | +5 |
| Total máximo (ChoCH) | 100 |
| Total máximo (BOS) | 90 |

`SCORE_MINIMO_SETUP = 40` em `configuracoes.py` — setups abaixo disso são descartados silenciosamente.

---

## `_calcular_risco_rr_v2(preco_entrada, sl_ref, direcao, atr=0.0, tp_ref=None) -> tuple[float, float, float] | None`

```python
buffer = 0.1 * atr
se direcao == "ALTA":
    sl = sl_ref - buffer        # sl_ref = swing low estrutural do MSS (M5) ou poi_fundo no modo DIRETO
    risco = preco_entrada - sl
    se risco <= 0: return None
    tp_candidato = tp_ref se (tp_ref is not None and tp_ref > preco_entrada) else None
    se tp_candidato and (tp_candidato - preco_entrada) / risco >= 1.5:
        tp = tp_candidato       # alvo estrutural: nível ChoCH/BOS H4
    else:
        tp = preco_entrada + 2.0 * risco   # fallback mecânico
else:
    sl = sl_ref + buffer        # sl_ref = swing high estrutural do MSS (M5) ou poi_topo no modo DIRETO
    risco = sl - preco_entrada
    se risco <= 0: return None
    tp_candidato = tp_ref se (tp_ref is not None and tp_ref < preco_entrada) else None
    se tp_candidato and (preco_entrada - tp_candidato) / risco >= 1.5:
        tp = tp_candidato
    else:
        tp = preco_entrada - 2.0 * risco

rr = abs(tp - preco_entrada) / risco
return sl, tp, round(rr, 2)
```

Parâmetros:
- `sl_ref`: nível de invalidação estrutural. No modo MSS: swing low (ALTA) ou swing high (BAIXA) da micro-estrutura M5. No modo DIRETO: poi_fundo (ALTA) ou poi_topo (BAIXA).
- `tp_ref`: alvo estrutural opcional — `evento_nivel` (nível do ChoCH/BOS H4). Priorizado quando dá RR ≥ 1.5; caso contrário usa extensão mecânica 2× risco.
- `atr`: H4 ATR de Wilder (padrão 0.0). Buffer `0.1×ATR` afasta o SL do swing/POI para absorver ruído imediato.

---

## Schemas SQLite (novos — adicionados ao Repositorio)

```sql
CREATE TABLE IF NOT EXISTS setups (
    id              TEXT PRIMARY KEY,
    simbolo         TEXT NOT NULL,
    direcao         TEXT NOT NULL,
    pool_id         TEXT NOT NULL,
    evento_tipo     TEXT NOT NULL,     -- 'ChoCH' | 'BOS'
    evento_tempo    TEXT NOT NULL,
    evento_nivel    REAL NOT NULL,
    leg_id          TEXT,              -- NULL se não displacement
    poi_fundo       REAL NOT NULL,
    poi_topo        REAL NOT NULL,
    score           INTEGER NOT NULL,
    ativo           INTEGER NOT NULL DEFAULT 1,
    criado_em       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmacoes (
    id                  TEXT PRIMARY KEY,
    setup_id            TEXT NOT NULL,
    simbolo             TEXT NOT NULL,
    tipo_confirmacao    TEXT NOT NULL,  -- 'MSS' | 'DIRETO'
    preco_confirmacao   REAL NOT NULL,
    tempo               TEXT NOT NULL,
    sl                  REAL NOT NULL,
    tp                  REAL NOT NULL,
    rr                  REAL NOT NULL
);
```

---

## Configurações adicionais em `configuracoes.py`

```python
EXIGIR_CONFIRMACAO_LTF: bool = True
SCORE_MINIMO_SETUP: int = 40
IDADE_MAX_SETUP_HORAS: int = 60  # 15 velas H4 × 4h
```

---

## Cenários de Teste

| # | Cenário | Entrada | Saída esperada |
|---|---------|---------|----------------|
| 1 | `detectar_mss_no_poi` — MSS bullish | velas M5 com retrace + fechamento acima | `ConfirmacaoEntrada(tipo="MSS")` |
| 2 | `detectar_mss_no_poi` — sem MSS | apenas retrace, sem fechamento de confirmação | `None` |
| 3 | `detectar_mss_no_poi` — velas fora da POI | M5 não toca a zona | `None` |
| 4 | `calcular_score_setup` — score ChoCH+displacement | ChoCH + leg displacement + EQH | ≥ 45 |
| 5 | `calcular_score_setup` — score mínimo | BOS simples sem leg | < 40 |
| 6 | `calcular_risco_rr` — ALTA | entrada=1.1100, poi_fundo=1.1050 | `sl=1.1050, tp=1.1200, rr=2.0` |
| 7 | `calcular_risco_rr` — BAIXA | entrada=1.1100, poi_topo=1.1150 | `sl=1.1150, tp=1.1000, rr=2.0` |
| 8 | Setup deduplicado | mesmo setup detectado 2 ciclos | 1 sinal disparado |
| 9 | Setup expirado | `criado_em` > IDADE_MAX_SETUP_HORAS | não retornado por `carregar_setups_ativos` |
| 10 | Feature flag False | `EXIGIR_CONFIRMACAO_LTF=False`, preço na POI | sinal disparado sem MSS |
