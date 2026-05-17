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

para cada pool em pools:
    se pool foi varrido (preco_atual_m5 cruzou pool.preco):
        # Procurar evento estrutural APÓS a varredura do pool
        eventos_pos = [e for e in eventos if e.tempo > pool.tempo]
        se não há eventos_pos: continue

        evento = primeiro evento_pos
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

    id_sinal = SHA1(setup.id|confirmacao.tempo.isoformat())[:20]
    se repo.sinal_ja_disparado(id_sinal): continue

    # Calcular filtros contextuais (informativos)
    ctx = _calcular_contexto_v2(setup, confirmacao, velas_d1)

    mensagem = _construir_mensagem_v2(simbolo, setup, confirmacao, ctx)
    repo.persistir_sinal(id_sinal, simbolo, setup_id=setup.id, ...)
    repo.desativar_setup(setup.id)
    notificador.enviar(mensagem)
```

---

## `detectar_mss_no_poi(velas_m5, poi_fundo, poi_topo, direcao, simbolo) -> ConfirmacaoEntrada | None`

MSS (Market Structure Shift) no M5 dentro da POI — ChoCH de LTF confirmando rejeição.

**Algoritmo:**
```
1. Filtrar velas_m5 que têm mínima/máxima dentro da POI (tocam a zona)
2. Para direcao="ALTA" (setup de alta):
   — Dentro da POI, encontrar sequência: vela bearish (retrace) seguida por fechamento
     acima da máxima da vela bearish anterior → "CHoCH bullish no M5"
   — preco_confirmacao = fechamento da vela que confirmou o MSS
3. Para direcao="BAIXA" (setup de baixa):
   — Sequência: vela bullish seguida por fechamento abaixo da mínima → "CHoCH bearish no M5"
4. Se encontrado:
   — sl, tp, rr = calcular_risco_rr(preco_confirmacao, poi_fundo, poi_topo, direcao)
   — return ConfirmacaoEntrada(tipo_confirmacao="MSS", ...)
5. Se não encontrado: return None
```

---

## `calcular_score_setup(evento, leg, pool, velas_d1, atr) -> int`

Score composicional 0–100:

| Condição | Pontos |
|----------|--------|
| `evento.tipo == "ChoCH"` (vs BOS) | +20 |
| D1 alinhado com a direção | +15 |
| `leg is not None and leg.eh_displacement` | +15 |
| Pool do tipo "EQH" ou "EQL" (vs PDH/PDL) | +10 |
| OB∩FVG sobreposição existe na POI | +10 |
| Sessão London/NY no momento do evento | +10 |
| Zona premium/discount (D1 range) alinhada | +10 |
| Total máximo | 90 |

`SCORE_MINIMO_SETUP = 40` em `configuracoes.py` — setups abaixo disso são descartados silenciosamente.

---

## `calcular_risco_rr(preco_entrada, poi_fundo, poi_topo, direcao) -> tuple[float, float, float]`

```python
se direcao == "ALTA":
    sl = poi_fundo        # abaixo da zona de demanda
    risco = preco_entrada - sl
    tp = preco_entrada + 2 * risco
else:
    sl = poi_topo         # acima da zona de oferta
    risco = sl - preco_entrada
    tp = preco_entrada - 2 * risco

rr = 2.0
return sl, tp, rr
```

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
