# Spec: Legs e POI Vinculado

## Responsabilidade
Extrair legs de impulso a partir de eventos de estrutura e identificar os POIs (Order Blocks e FVGs) que pertencem a cada leg. Garante causalidade: cada POI só é relevante dentro do contexto da leg que o criou.

## Onde vive
`src/smc/analisador_smc.py` — funções públicas `extrair_legs`, `detectar_obs_corpo`, `extrair_fvgs_no_intervalo`.

## Dependências
- `EventoEstrutura`, `LegImpulso`, `OrderBlockV2` de `modelos.py`
- `calcular_atr` de `analisador_smc.py`

---

## Funções

### `extrair_legs(velas, eventos, simbolo, periodo_swing, atr) -> list[LegImpulso]`

Para cada `EventoEstrutura` em `eventos`, extrai a leg de impulso que CAUSOU aquele evento.

**Algoritmo:**

```
Para cada evento em eventos (ordenado por tempo):
    1. Encontrar o swing que foi rompido (pelo evento.swing_tempo)
    2. Encontrar o swing oposto imediatamente anterior ao swing rompido
       — este é o ponto de INÍCIO da leg
    3. A leg vai de indice_inicio (swing oposto) até indice_fim (candle do evento)
    4. Calcular métricas da leg:
       range_pontos = abs(preco_fim - preco_inicio)
       n_velas = indice_fim - indice_inicio + 1
       corpo_direcional = velas no intervalo com corpo na direção / n_velas
       fvgs_internos = extrair_fvgs_no_intervalo(velas, inicio, fim, simbolo)
       tem_fvg_interno = len(fvgs_internos) > 0
       atr_multiplo = range_pontos / atr (0.0 se atr == 0)
       eh_displacement = (atr_multiplo >= 2.0 AND corpo_direcional >= 0.60 AND tem_fvg_interno)
```

**Direção da leg:**
- Evento de ALTA (quebrou high): leg é bullish — vai de low anterior ao high quebrado
- Evento de BAIXA (quebrou low): leg é bearish — vai de high anterior ao low quebrado

**Deduplicação:** Se dois eventos quebram o mesmo nível, apenas uma leg é extraída (a do evento mais recente).

---

### `detectar_obs_corpo(velas, leg, simbolo) -> list[OrderBlockV2]`

Detecta Order Blocks dentro de uma leg, usando CORPO (open/close) em vez de range (high/low).

**Algoritmo para leg BULLISH (direcao="ALTA"):**
```
Iterar velas[leg.indice_inicio : leg.indice_fim]
OB = última vela bearish (close < open) antes de sequência bullish dentro da leg
  — "última": a vela bearish imediatamente seguida por vela não-bearish
  — Zona: preco_topo = max(abertura, fechamento), preco_fundo = min(abertura, fechamento)
  — zona_50 = (preco_topo + preco_fundo) / 2
```

**Algoritmo para leg BEARISH (direcao="BAIXA"):**
```
OB = última vela bullish (close > open) antes de sequência bearish dentro da leg
  — Zona corpo: preco_topo = max(abertura, fechamento), preco_fundo = min(abertura, fechamento)
```

**Mitigação (aplicada sobre velas_fechadas = velas_h4.iloc[:-1]):**
- ALTA: `close < ob.preco_fundo` → mitigado
- BAIXA: `close > ob.preco_topo` → mitigado

**ID:** `SHA1(simbolo|leg.tempo_inicio.isoformat()|str(indice))[:16]`

**leg_id:** igual ao ID da LegImpulso associada (calculado pela assinatura da leg)

---

### `extrair_fvgs_no_intervalo(velas, indice_inicio, indice_fim, simbolo) -> list[FairValueGap]`

Extrai FVGs criados DENTRO do intervalo `[indice_inicio, indice_fim]`.

**Algoritmo:**
```
Para i em range(indice_inicio, indice_fim - 1):
    trio = velas[i], velas[i+1], velas[i+2]
    FVG Bullish: maxima[i] < minima[i+2]  → FairValueGap(direcao="ALTA", ...)
    FVG Bearish: minima[i] > maxima[i+2]  → FairValueGap(direcao="BAIXA", ...)
```

Retorna FVGs SEM verificar mitigação — a mitigação é responsabilidade do chamador (usa `velas_fechadas`).

---

## Critério de POI Composto

A zona de entrada do `SetupSMC` é calculada como:

```
obs_validos = [ob for ob in obs_corpo if not ob.mitigado and ob.direcao == direcao]
fvgs_validos = [fvg for fvg in fvgs_leg if not fvg.mitigado and fvg.direcao == direcao]

# Zona = envelope de todos os POIs ativos da leg
poi_fundo = min(poi.preco_fundo for poi in obs_validos + fvgs_validos)
poi_topo  = max(poi.preco_topo  for poi in obs_validos + fvgs_validos)
```

Se não há POIs ativos: `poi_fundo = poi_topo = evento.nivel_rompido` (fallback ao nível de estrutura).

---

## Cenários de Teste

| # | Cenário | Entrada | Saída esperada |
|---|---------|---------|----------------|
| 1 | `extrair_legs` — leg bullish simples | evento BOS de Alta | `LegImpulso(direcao="ALTA")` |
| 2 | `extrair_legs` — displacement | range ≥ 2×ATR, corpo ≥ 60%, FVG interno | `eh_displacement=True` |
| 3 | `extrair_legs` — não displacement | range < 2×ATR | `eh_displacement=False` |
| 4 | `detectar_obs_corpo` — zona usa corpo | vela bearish com sombras longas | `preco_topo = max(abertura, fechamento)` (não `maxima`) |
| 5 | `detectar_obs_corpo` — última vela bearish | duas velas bearish consecutivas | somente a segunda (mais próxima do impulso) |
| 6 | `detectar_obs_corpo` — mitigação | close abaixo do corpo OB ALTA | `ob.mitigado = True` |
| 7 | `extrair_fvgs_no_intervalo` — bullish | gap entre vela[i].maxima e vela[i+2].minima | `FairValueGap(direcao="ALTA")` |
| 8 | `extrair_fvgs_no_intervalo` — fora do intervalo | FVG em candle antes do indice_inicio | não retornado |
| 9 | `detectar_obs_corpo` — leg de 2 velas | leg muito curta | sem crash, retorna `[]` |
| 10 | `extrair_legs` — deduplicação | dois ChoCHs do mesmo nível | 1 leg retornada |
