# Spec: analisador_smc.py

## Responsabilidade
Núcleo da lógica SMC. Recebe DataFrames de velas, retorna estruturas de dados tipadas com as zonas e sinais detectados.

## Dataclasses

```python
@dataclass
class OrderBlock:
    id: str             # SHA1(simbolo + str(tempo))
    simbolo: str
    direcao: str        # "ALTA" | "BAIXA"
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False

@dataclass
class FairValueGap:
    id: str             # SHA1(simbolo + str(tempo))
    simbolo: str
    direcao: str        # "ALTA" | "BAIXA"
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False

@dataclass
class CapturaLiquidez:
    simbolo: str
    direcao: str        # "ALTA" (varreu mínima) | "BAIXA" (varreu máxima)
    preco_varredura: float
    tempo: datetime
    pavio_percentual: float

@dataclass
class QuebraEstrutura:
    simbolo: str
    direcao: str        # "ALTA" (rompeu swing high) | "BAIXA" (rompeu swing low)
    nivel_rompido: float
    tempo: datetime
```

## Funções Públicas

### `detectar_captura_liquidez(velas_h4, periodo_swing, limiar_pavio) -> list[CapturaLiquidez]`

**Identificação de swing highs/lows:**
- `maxima[i]` é swing high se `maxima[i] == max(maxima[i-N : i+N+1])`
- `minima[i]` é swing low se `minima[i] == min(minima[i-N : i+N+1])`

**Critério Captura Bearish (varreu máxima):**
```
range     = high - low
pavio_sup = high - max(open, close)
válido    = (high > swing_high_anterior)
          AND (close < swing_high_anterior)
          AND (pavio_sup / range >= limiar_pavio)
```

**Critério Captura Bullish (varreu mínima):**
```
pavio_inf = min(open, close) - low
válido    = (low < swing_low_anterior)
          AND (close > swing_low_anterior)
          AND (pavio_inf / range >= limiar_pavio)
```

- Janela de relevância: últimas 3 velas H4
- Proteção contra divisão por zero: ignora velas com `range == 0`

---

### `detectar_quebra_estrutura(velas, simbolo, periodo_swing) -> list[QuebraEstrutura]`

**Distinção BOS vs Captura:**
```
Captura: wick rompe o nível + fecha do lado oposto  → manipulação
BOS:     close rompe o nível (sem exigir wick)       → confirmação estrutural
```

**BOS de Alta:** `close[i] > swing_high` (fechamento acima, não apenas wick)
**BOS de Baixa:** `close[i] < swing_low`

- Janela de relevância: últimas 5 velas
- Usa mesma lógica de swing da `detectar_captura_liquidez`

---

### `mapear_zonas_interesse(velas_h4, simbolo) -> tuple[list[OrderBlock], list[FairValueGap]]`

**Algoritmo Order Block:**
- Varrer velas da mais antiga para mais recente
- **OB Bullish:** última vela bearish (`close < open`) antes de impulso bullish
  - Impulso: próximas 3 velas com fechamentos crescentes OU próxima vela engolfa (`close - open > 1.5 * corpo_ob`)
- **OB Bearish:** última vela bullish (`close > open`) antes de impulso bearish
- Zona: `preco_topo = high`, `preco_fundo = low`
- **Mitigação:** alguma vela posterior fechou dentro da zona (`fundo < close < topo`)

**Algoritmo FVG:**
- Para cada trio `[vela[i], vela[i+1], vela[i+2]]`:
  - **FVG Bullish:** `vela[i].maxima < vela[i+2].minima`
  - **FVG Bearish:** `vela[i].minima > vela[i+2].maxima`
- **Mitigação:** vela posterior negociou pelo gap inteiro (`minima <= fundo_fvg AND maxima >= topo_fvg`)
- Retorna apenas FVGs não mitigados

---

### `verificar_confluencia(captura, quebra_estrutura, order_blocks, fvgs, preco_atual_m15) -> bool`

**Retorna `True` se TODAS as condições forem satisfeitas:**
1. `captura` não é None
2. `quebra_estrutura` não é None e na **mesma direção** que a captura
3. `preco_atual_m15` está dentro de algum OB não mitigado na direção correta
4. Esse OB tem pelo menos 1 FVG não mitigado sobreposto

**Critério de sobreposição FVG ↔ OB:**
```
sobreposição = max(ob.preco_fundo, fvg.preco_fundo) < min(ob.preco_topo, fvg.preco_topo)
```

**Lógica direcional:**
```
Sinal ALTA:  captura="ALTA" + bos="ALTA" + ob="ALTA" + preço dentro do OB bullish
Sinal BAIXA: captura="BAIXA" + bos="BAIXA" + ob="BAIXA" + preço dentro do OB bearish
```

## Cenários de Teste

| # | Cenário | Entrada | Saída esperada |
|---|---------|---------|----------------|
| 1 | Sweep bearish válido | pavio_sup = 40% do range, high > swing high, close < swing high | `CapturaLiquidez(direcao="BAIXA")` |
| 2 | Sweep rejeitado | pavio_sup = 20% do range | `[]` |
| 3 | OB Bullish simples | Vela bearish + 3 velas bullish subsequentes | `OrderBlock(direcao="ALTA")` |
| 4 | OB mitigado | OB + vela posterior fecha dentro da zona | `ob.mitigado = True` |
| 5 | FVG Bearish | Trio: `low[i] > high[i+2]` | `FairValueGap(direcao="BAIXA")` |
| 6 | BOS de Alta | `close > swing_high` | `QuebraEstrutura(direcao="ALTA")` |
| 7 | BOS de Baixa | `close < swing_low` | `QuebraEstrutura(direcao="BAIXA")` |
| 8 | BOS rejeitado (wick apenas) | Wick > swing high, close abaixo | `[]` |
| 9 | Confluência completa | captura BAIXA + BOS BAIXA + OB bearish + FVG sobreposto | `True` |
| 10 | Sem BOS | captura + OB + FVG, sem BOS | `False` |
| 11 | BOS direção errada | captura BAIXA + BOS ALTA | `False` |
| 12 | OB mitigado | captura + BOS + OB mitigado | `False` |
