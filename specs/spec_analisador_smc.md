# Spec: analisador_smc.py

## Responsabilidade
Núcleo da lógica SMC v2. Implementa o pipeline completo: captura de liquidez → estrutura H4 → legs + POI → pools → MSS no LTF. Todas as funções são puras (sem side-effects, sem estado global).

Este spec é uma visão geral do módulo. Algoritmos detalhados estão em:
- `specs/spec_estrutura.md` — state machine ChoCH/BOS
- `specs/spec_legs.md`      — legs, OBs v2, FVGs, POI composta
- `specs/spec_pools.md`     — EQH/EQL/PDH/PDL
- `specs/spec_setup.md`     — MSS, score, risco/retorno

---

## Dataclasses (legado — usados por `detectar_captura_liquidez`)

```python
@dataclass
class OrderBlock:          # legado; funções v2 usam OrderBlockV2 de modelos.py
    id: str                # SHA1(simbolo + str(tempo))[:16]
    simbolo: str
    direcao: str           # "ALTA" | "BAIXA"
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False
    testado: bool = False

@dataclass
class FairValueGap:        # legado; funções v2 usam FairValueGap de modelos.py
    id: str
    simbolo: str
    direcao: str           # "ALTA" | "BAIXA"
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False
    testado: bool = False

@dataclass
class CapturaLiquidez:     # ainda em uso no pipeline v2
    simbolo: str
    direcao: str           # "ALTA" (varreu mínima) | "BAIXA" (varreu máxima)
    preco_varredura: float
    tempo: datetime
    pavio_percentual: float
```

`QuebraEstrutura` foi removido na Fase 7 (substituído por `EventoEstrutura` de `modelos.py`).

---

## Funções Públicas

### Utilitários

| Função | Retorno | Descrição |
|--------|---------|-----------|
| `calcular_swings(velas, periodo)` | `tuple[dict, dict]` | Swings confirmados com janela assimétrica (borda-direita inclusa). Usado por `detectar_captura_liquidez`. |
| `calcular_swings_confirmados(velas, periodo)` | `list[SwingPoint]` | Swings com janela simétrica estrita. Usado pela state machine e `extrair_legs`. Ver `spec_estrutura.md`. |
| `calcular_atr(velas, periodo=14)` | `float` | ATR de Wilder sobre velas fechadas. `0.0` se `len < periodo+1`. Ver `spec_estrutura.md`. |

---

### Captura de Liquidez

#### `detectar_captura_liquidez(velas_h4, periodo_swing, limiar_pavio, simbolo) -> list[CapturaLiquidez]`

Detecta sweeps canônicos de swing highs/lows.

**Critério Captura Bearish (varreu máxima / EQH/PDH):**
```
range     = high - low
pavio_sup = high - max(open, close)
válido    = high > swing_high_anterior
          AND close < swing_high_anterior
          AND pavio_sup / range >= limiar_pavio  (padrão: 0.30)
```

**Critério Captura Bullish (varreu mínima / EQL/PDL):**
```
pavio_inf = min(open, close) - low
válido    = low < swing_low_anterior
          AND close > swing_low_anterior
          AND pavio_inf / range >= limiar_pavio
```

- Janela: últimas 50 velas H4 (~8 dias) — alinhada com a janela de detecção de EQH/EQL
- Deduplicação: 1 captura por swing high/low por direção por ciclo
- Proteção: ignora velas com `range == 0`

---

#### `pool_varrido_por_sweep(pool, capturas) -> CapturaLiquidez | None`

Retorna a `CapturaLiquidez` que varreu o nível do pool, ou `None`.

```
EQH/PDH (liquidez acima) → exige captura BAIXA próxima ao nível (± pool.tolerancia)
EQL/PDL (liquidez abaixo) → exige captura ALTA próxima ao nível
```

Usado em `_detectar_e_registrar_setups` para obter `captura.tempo` — o filtro de eventos usa `evento.tempo > captura.tempo` (evento posterior ao sweep, não à criação do pool).

---

### Pipeline v2 — Estrutura

#### `detectar_eventos_estrutura(velas, simbolo, periodo_swing) -> list[EventoEstrutura]`
State machine HH/HL/LH/LL → ChoCH/BOS. Ver `spec_estrutura.md`.

---

### Pipeline v2 — Legs e POI

#### `extrair_legs(velas, eventos, simbolo, periodo_swing, atr) -> list[LegImpulso]`
Extrai a leg de impulso que causou cada evento. Ver `spec_legs.md`.

#### `detectar_obs_corpo(velas, leg, simbolo) -> list[OrderBlockV2]`
Order Blocks usando corpo (open/close), vinculados à leg. Ver `spec_legs.md`.

#### `extrair_fvgs_no_intervalo(velas, i0, i1, simbolo) -> list[FairValueGap]`
FVGs dentro do intervalo de índices. Retorna sem marcar mitigação. Ver `spec_legs.md`.

#### `calcular_poi_composta(obs, fvgs, fallback_nivel) -> tuple[float, float]`
POI composta com prioridade: interseção OB∩FVG → envelope OBs → envelope FVGs → fallback. Ver `spec_legs.md` e `spec_analisador_smc.md §Cenários de Teste`.

---

### Pipeline v2 — Pools de Liquidez

#### `detectar_eqh_eql(velas, simbolo, atr) -> list[PoolLiquidez]`
Equal Highs/Lows: clusters ≥ 2 wicks dentro de `0.1×ATR`, janela 50 velas. Ver `spec_pools.md`.

#### `detectar_pdh_pdl(velas_d1, simbolo, atr) -> list[PoolLiquidez]`
Previous Day High/Low do dia anterior (D1). Ver `spec_pools.md`.

---

### Pipeline v2 — Mitigação

#### `_marcar_obs_v2_mitigados(obs, velas_fechadas)` (privada, usada pelo pipeline)
- ALTA: `minima <= ob.zona_50` → `ob.mitigado = True` (wick atinge 50% do corpo)
- BAIXA: `maxima >= ob.zona_50` → `ob.mitigado = True`
- ALTA: `minima <= ob.preco_topo` (sem mitigar) → `ob.testado = True` (wick entrou na zona)
- BAIXA: `maxima >= ob.preco_fundo` (sem mitigar) → `ob.testado = True`

#### `_marcar_fvgs_mitigados(fvgs, velas_fechadas)` (privada, usada pelo pipeline)
- ALTA: `fechamento <= meio_gap` → `fvg.mitigado = True` (close a 50% do gap — wick não mitiga)
- BAIXA: `fechamento >= meio_gap` → `fvg.mitigado = True`
- ALTA: `minima < fvg.preco_topo` (sem mitigar) → `fvg.testado = True` (wick entrou no gap)
- BAIXA: `maxima > fvg.preco_fundo` (sem mitigar) → `fvg.testado = True`
- `meio_gap = preco_fundo + 0.5 × (preco_topo - preco_fundo)`

**Racional:** FVGs mitigados por wick seriam prematuramente invalidados por ruído de mercado. Exigir close garante comprometimento real do preço ao nível de equilíbrio do gap. O campo `testado` distingue zonas virgens (nunca tocadas) de zonas usadas — zonas virgens recebem bônus no score.

---

### Pipeline v2 — MSS e Score

#### `detectar_mss_no_poi(velas_m5, poi_fundo, poi_topo, direcao, simbolo) -> ConfirmacaoEntrada | None`
ChoCH no M5 dentro da POI. Ver `spec_setup.md`.

#### `calcular_score_setup(evento, leg, pool, ...) -> int`
Score composicional 0–100. Ver `spec_setup.md`.

---

## Cenários de Teste

| # | Função | Cenário | Saída esperada |
|---|--------|---------|----------------|
| 1 | `detectar_captura_liquidez` | Pavio sup ≥ 30%, high > swing, close < swing | `CapturaLiquidez(direcao="BAIXA")` |
| 2 | `detectar_captura_liquidez` | Pavio sup = 20% do range | `[]` |
| 3 | `calcular_swings` | Índice `len-1` tem maxima extrema | Índice não está em `swings_high` |
| 4 | `calcular_swings` | Importação pública | `from smc.analisador_smc import calcular_swings` não lança `ImportError` |
| 5 | `pool_varrido_por_sweep` | EQH + captura BAIXA próxima | retorna `CapturaLiquidez` (não None) |
| 6 | `pool_varrido_por_sweep` | EQH + captura BAIXA distante | retorna `None` |
| 7 | `pool_varrido_por_sweep` | EQH + captura ALTA (direção errada) | retorna `None` |
| 8 | `pool_varrido_por_sweep` | Lista vazia de capturas | retorna `None` |
| 9 | `calcular_poi_composta` | OB [1.1000–1.1060] + FVG [1.1040–1.1080] | `(1.1040, 1.1060)` |
| 10 | `calcular_poi_composta` | OB [1.1000–1.1050] + FVG [1.1070–1.1100] (sem interseção) | `(1.1000, 1.1050)` |
| 11 | `calcular_poi_composta` | Só OBs | envelope dos OBs |
| 12 | `calcular_poi_composta` | Só FVGs | envelope dos FVGs |
| 13 | `calcular_poi_composta` | Tudo vazio | `(fallback, fallback)` |
| 14 | `calcular_poi_composta` | Fronteira coincidente (OB.topo == FVG.fundo) | não conta como interseção |
| 15 | `_marcar_fvgs_mitigados` | Close atinge 50% do gap | `fvg.mitigado = True` |
| 16 | `_marcar_fvgs_mitigados` | Wick atinge 50% mas close permanece acima | `fvg.mitigado = False` |
| 17 | `_marcar_obs_v2_mitigados` | Wick (minima) atinge zona_50 do OB ALTA | `ob.mitigado = True` |
| 18 | `_marcar_obs_v2_mitigados` | Wick entra na zona (minima <= preco_topo) sem mitigar | `ob.testado = True, ob.mitigado = False` |
| 19 | `_marcar_fvgs_mitigados` | Wick entra no gap (minima < preco_topo) sem close mitigando | `fvg.testado = True, fvg.mitigado = False` |
| 20 | `detectar_captura_liquidez` | Sweep 20 candles antes do fim (>15, ≤50) | captura detectada |
