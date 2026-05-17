# Spec: Pools de Liquidez

## Responsabilidade
Identificar alvos de liquidez não preenchida — EQH/EQL (clusters de wicks) e PDH/PDL (máxima/mínima do dia anterior). Esses são os gatilhos primários para sweeps que iniciam setups SMC.

## Onde vive
`src/smc/analisador_smc.py` — funções públicas `detectar_eqh_eql`, `detectar_pdh_pdl`.

## Dependências
- `PoolLiquidez` de `modelos.py`
- `calcular_atr` de `analisador_smc.py`

---

## Funções

### `detectar_eqh_eql(velas, simbolo, atr) -> list[PoolLiquidez]`

Detecta Equal Highs (EQH) e Equal Lows (EQL): ≥ 2 wicks que tocam o mesmo nível, dentro de `tolerancia = 0.1 × ATR`.

**Algoritmo EQH:**
```
candidatos = wicks superiores (maxima de cada vela)
Para cada maxima[i]:
    cluster = [maxima[j] for j in range(i+1, min(i+15, len)) 
               if abs(maxima[j] - maxima[i]) <= tolerancia]
    se len(cluster) >= 1:  # i + pelo menos 1 outro = cluster ≥ 2
        preco_centro = mean([maxima[i]] + cluster)
        yield PoolLiquidez(tipo="EQH", preco=preco_centro, tolerancia=tolerancia, ...)
```

**Algoritmo EQL (análogo para minima):**
```
candidatos = wicks inferiores (minima de cada vela)
cluster = [minima[j] for j in range(i+1, min(i+15, len))
           if abs(minima[j] - minima[i]) <= tolerancia]
```

**Janela:** últimas 50 velas H4 (~8 dias) — alvos mais recentes são mais relevantes.

**Deduplicação:** Se dois clusters têm centros dentro de `2 × tolerancia` um do outro, manter apenas o mais recente.

**Varredura:** `varredido=True` quando uma vela fecha além do `preco` em `tolerancia/2`:
- EQH varredido: `close > preco + tolerancia/2`
- EQL varredido: `close < preco - tolerancia/2`

**ID:** `SHA1(simbolo|tipo|str(round(preco, 5))|tempo.isoformat())[:16]`

---

### `detectar_pdh_pdl(velas_d1, simbolo, atr) -> list[PoolLiquidez]`

Detecta Previous Day High/Low (PDH/PDL) a partir de dados D1.

**Algoritmo:**
```
se len(velas_d1) < 2: return []

dia_anterior = velas_d1.iloc[-2]  # penúltimo (o último ainda está aberto)
pdh = PoolLiquidez(
    tipo="PDH",
    preco=dia_anterior["maxima"],
    tolerancia=0.1 × atr,
    tempo=dia_anterior["tempo"],
    ...
)
pdl = PoolLiquidez(
    tipo="PDL",
    preco=dia_anterior["minima"],
    tolerancia=0.1 × atr,
    tempo=dia_anterior["tempo"],
    ...
)
return [pdh, pdl]
```

**Nota:** PDH/PDL são sempre retornados (nunca `varredido=True` nesta função) — a detecção de varredura é feita no loop principal ao comparar `preco_atual_m5` com `pool.preco`.

---

## Integração com Captura de Liquidez

`detectar_captura_liquidez` (legado) detecta sweeps de swing highs/lows genéricos. O novo fluxo usa pools como alvos específicos:

```
pools = detectar_eqh_eql(velas_h4, simbolo, atr) + detectar_pdh_pdl(velas_d1, simbolo, atr)
para cada pool:
    se preco_atual_m5 cruzou pool.preco (dentro de tolerancia):
        pool.varredido = True
        iniciar busca por evento de estrutura posterior → SetupSMC
```

A varredura de um pool é condição necessária (mas não suficiente) para gerar um setup.

---

## Cenários de Teste

| # | Cenário | Entrada | Saída esperada |
|---|---------|---------|----------------|
| 1 | EQH simples | 3 wicks em ±0.05% um do outro | `PoolLiquidez(tipo="EQH")` |
| 2 | EQL simples | 2 mínimas em ±0.05% | `PoolLiquidez(tipo="EQL")` |
| 3 | Sem cluster | wicks dispersos | `[]` |
| 4 | PDH/PDL normais | D1 com ≥ 2 velas | `[PDH, PDL]` |
| 5 | D1 insuficiente | `len(velas_d1) < 2` | `[]` |
| 6 | Deduplicação EQH | dois clusters sobrepostos | apenas 1 pool |
| 7 | Varredura EQH | preço fecha acima do centro + tolerancia/2 | `pool.varredido = True` |
| 8 | Janela correta | EQH formado há 51 velas | não retornado (fora da janela de 50) |
