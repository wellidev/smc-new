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
    mitigado: bool = False   # True se a zona foi consumida/invalidada. O preço entrou fundo o suficiente para "queimar" a zona. Ela deixa de existir como nível relevante.
    testado: bool = False   # True se a zona foi tocada ao menos uma vez, mas sobreviveu. O preço recuou até ela (abriu do lado oposto, wick entrou), Smart Money defendeu a zona, e o preço voltou.

@dataclass
class FairValueGap:
    id: str             # SHA1(simbolo + str(tempo))
    simbolo: str
    direcao: str        # "ALTA" | "BAIXA"
    preco_topo: float
    preco_fundo: float
    tempo: datetime
    mitigado: bool = False   # True se a zona foi consumida/invalidada. O preço entrou fundo o suficiente para "queimar" a zona. Ela deixa de existir como nível relevante.
    testado: bool = False   # True se a zona foi tocada ao menos uma vez, mas sobreviveu. O preço recuou até ela (abriu do lado oposto, wick entrou), Smart Money defendeu a zona, e o preço voltou.

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
    swing_tempo: datetime   # quando o swing rompido foi formado
    tempo: datetime         # quando o fechamento rompeu o nível
    deslocamento: bool = False  # True se BOS candle criou FVG com candle i-1 (gap de alta energia)
```

## Funções Públicas

### `calcular_swings(velas, periodo) -> tuple[dict[int, float], dict[int, float]]`

Retorna `(swings_high, swings_low)` — dicionários `{índice: valor}` com os swing highs/lows confirmados.
- `maxima[i]` é swing high se `maxima[i] == max(maxima[i-N : i+lado_dir+1])` onde `lado_dir = min(N, len-1-i)`
- `minima[i]` é swing low se `minima[i] == min(minima[i-N : i+lado_dir+1])`
- O candle de índice `len-1` (candle aberto no MT5) é **excluído** dos candidatos
- Função pública — pode ser importada por módulos externos (ex: `filtros.py` para bias D1)

---

### `detectar_captura_liquidez(velas_h4, periodo_swing, limiar_pavio, simbolo="") -> list[CapturaLiquidez]`

**Identificação de swing highs/lows (`calcular_swings`):**
- `maxima[i]` é swing high se `maxima[i] == max(maxima[i-N : i+lado_dir+1])` onde `lado_dir = min(N, len-1-i)`
- `minima[i]` é swing low se `minima[i] == min(minima[i-N : i+lado_dir+1])`
- O candle de índice `len-1` (candle aberto no MT5) é **excluído** dos candidatos; todo swing tem ao menos 1 barra de confirmação à direita
- Candles próximos da borda direita usam janela assimétrica (menos confirmação à direita — trade-off intencional para cobrir estrutura recente)

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

- Janela de relevância: últimas 15 velas H4 (~2,5 dias)
- Proteção contra divisão por zero: ignora velas com `range == 0`
- Deduplicação: cada swing só gera uma captura por direção por ciclo (evita N capturas para o mesmo nível)
- `simbolo` propagado para cada `CapturaLiquidez` gerada (parâmetro opcional, padrão `""`)

---

### `detectar_quebra_estrutura(velas, simbolo, periodo_swing) -> list[QuebraEstrutura]`

**Distinção BOS vs Captura:**
```
Captura: wick rompe o nível + fecha do lado oposto  → manipulação
BOS:     close rompe o nível (sem exigir wick)       → confirmação estrutural
```

**BOS de Alta:** `close[i] > swing_high` (fechamento acima, não apenas wick)
**BOS de Baixa:** `close[i] < swing_low`

- Janela de relevância: últimas 15 velas H4 (~2,5 dias) — idêntica à captura para garantir cobertura temporal compatível
- Usa `calcular_swings` (função pública, ver abaixo)
- Deduplicação: cada nível de swing só gera um BOS por direção por ciclo
- `swing_tempo` registra o tempo da vela que formou o swing rompido (necessário para validação causal em `verificar_confluencia`)

**Critério de Displacement (campo `deslocamento`):**
```
BOS Bullish: high[i-1] < low[i+1]  → vela i deixou gap bullish (FVG) entre candle anterior e próximo
BOS Bearish: low[i-1]  > high[i+1] → vela i deixou gap bearish
```
Requer `i + 1 < len(velas)` — se não existir candle à direita, `deslocamento = False`. Displacement indica BOS de alta energia (institucional); ausência não invalida o BOS.

---

### `mapear_zonas_interesse(velas_h4, simbolo) -> tuple[list[OrderBlock], list[FairValueGap]]`

**Algoritmo Order Block:**
- Horizonte: últimas 100 velas H4 (~17 dias); OBs de candles mais antigos são ignorados
- **OB Bullish:** última vela bearish (`close < open`) antes do impulso bullish
  - "Última vela bearish": a vela bearish candidata deve ser imediatamente seguida por uma vela não-bearish (`close >= open`); sequências de velas bearish consecutivas só geram OB para a **última** delas (a mais próxima do impulso)
  - Impulso: fechamentos **estritamente** crescentes (`diff > 0` nos 3 próximos candles)
    OU próxima vela engolfa (`close - open > 1.5 * corpo_ob`)
- **OB Bearish:** última vela bullish (`close > open`) antes do impulso bearish
  - "Última vela bullish": a vela candidata deve ser imediatamente seguida por uma vela não-bullish (`close <= open`)
  - Impulso: fechamentos **estritamente** decrescentes OU engolfo bearish
- Zona: `preco_topo = high`, `preco_fundo = low`
- **Mitigação direction-aware:**
  - OB Bullish: `fundo < close < topo` OU wick retorna à zona vindo de cima (`high >= topo AND low <= topo AND close <= topo`)
  - OB Bearish: `fundo < close < topo` OU wick retorna à zona vindo de baixo (`low <= fundo AND high >= fundo AND close >= fundo`)
- **Qualidade: Virgem vs. Testado (`_marcar_obs_testados`, chamada após mitigação):**
  - OB Bullish testado: candle posterior com `abertura >= topo AND minima <= topo` (abriu acima da zona e wick entrou nela — retrace de cima)
  - OB Bearish testado: candle posterior com `abertura <= fundo AND maxima >= fundo` (abriu abaixo da zona e wick entrou nela — retrace de baixo)
  - O critério "abertura do lado oposto" exclui os candles do próprio impulso (que abrem de dentro ou próximos ao OB)
  - OBs mitigados são ignorados pelo marcador de testado
  - Sinal ainda é gerado para OBs testados, mas a mensagem Telegram indica "⚠️ Testado" vs. "Virgem"

**Algoritmo FVG:**
- Horizonte: últimas 100 velas H4 (~17 dias); FVGs de candles mais antigos são ignorados
- Para cada trio `[vela[i], vela[i+1], vela[i+2]]`:
  - **FVG Bullish:** `vela[i].maxima < vela[i+2].minima`
  - **FVG Bearish:** `vela[i].minima > vela[i+2].maxima`
- **Mitigação:** vela posterior alcança ao menos 50% do gap via wick
  - FVG Bullish: `minima <= preco_fundo + 0.5 * gap`
  - FVG Bearish: `maxima >= preco_fundo + 0.5 * gap`
- **Qualidade: Virgem vs. Testado (`_marcar_fvgs_testados`, chamada após mitigação):**
  - FVG Bullish testado: candle posterior com `abertura >= preco_topo AND minima <= preco_topo` (abriu acima do gap e wick entrou nele)
  - FVG Bearish testado: candle posterior com `abertura <= preco_fundo AND maxima >= preco_fundo` (abriu abaixo do gap e wick entrou nele)
  - FVGs mitigados são ignorados pelo marcador de testado
  - Sinal ainda é gerado para FVGs testados; a mensagem Telegram indica "⚠️ Testado" vs. "Virgem"
- Retorna apenas FVGs não mitigados

---

### `verificar_confluencia(captura, quebra_estrutura, order_blocks, fvgs, preco_atual_m5) -> bool`

**Retorna `True` se TODAS as condições forem satisfeitas:**
1. `captura` não é None
2. `quebra_estrutura` não é None e na **mesma direção** que a captura
3. `quebra_estrutura.tempo > captura.tempo` — BOS deve ser estritamente posterior à captura
4. Existe um par (OB, FVG) com mesma direção, não mitigados, que se sobrepõem geometricamente, **e `preco_atual_m5` está dentro do OB** (não é exigido que esteja na zona OB∩FVG)

**Critério de sobreposição e zona de entrada:**
```
sobreposição = max(ob.preco_fundo, fvg.preco_fundo) < min(ob.preco_topo, fvg.preco_topo)
entrada válida = ob.preco_fundo <= preco_atual_m5 <= ob.preco_topo
```

A sobreposição OB∩FVG é calculada e reportada na mensagem Telegram como informação de contexto, mas não é exigida como condição de entrada. Basta o preço estar dentro do OB que contém pelo menos um FVG sobreposto.

OBs e FVGs podem ter `tempo` anterior ou posterior à captura — o impulso pós-captura frequentemente cria novos OBs (breaker blocks) que são POIs muito relevantes para o retorno.

**Cadeia causal SMC:**
```
Captura de Liquidez → BOS (posterior à captura) → Retorno ao OB com FVG sobreposto
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
| 12 | OB mitigado na confluência | captura + BOS + OB mitigado | `False` |
| 13 | BOS deduplicado | Múltiplos candles fechando acima do mesmo swing high | exatamente 1 `QuebraEstrutura` para o nível |
| 14 | OB fora do horizonte | OB formado > 100 velas atrás num DataFrame de 200 | não retornado |
| 15 | FVG fora do horizonte | FVG formado > 100 velas atrás num DataFrame de 200 | não retornado |
| 16 | OB rejeitado por impulso plano | Dois candles com mesmo fechamento no impulso | `[]` |
| 17 | Open candle excluído de swing | Último candle tem maxima extrema | índice `len-1` não está em `swings_high` |
| 18 | `simbolo` propagado em captura | `simbolo="EURUSD"` passado como argumento | `captura.simbolo == "EURUSD"` |
| 19 | OB "última vela" — rejeita penúltima bearish | Duas velas bearish consecutivas antes do impulso bullish | somente o OB da 2ª vela bearish (imediatamente antes do impulso) é retornado |
| 20 | OB pós-captura aceito em confluência | OB com `tempo >= captura.tempo` (impulso pós-captura) | `verificar_confluencia` retorna `True` |
| 21 | BOS com swing pré-captura aceito | `quebra_estrutura.swing_tempo <= captura.tempo` | `verificar_confluencia` retorna `True` (apenas `bos.tempo > captura.tempo` é exigido) |
| 22 | Preço em OB mas fora da sobreposição | OB [1.1000-1.1060], FVG [1.1035-1.1080], preço=1.1010 | `verificar_confluencia` retorna `True` (preço dentro do OB é suficiente) |
| 23 | OB virgem — nenhum retorno | ALTA OB + impulso acima, sem retrace | `ob.testado = False` |
| 24 | OB testado — wick de retrace | ALTA OB + candle abre acima do topo, wick entra na zona | `ob.testado = True` |
| 25 | Impulso não marca como testado | Candles do impulso abrem abaixo do topo do OB | `ob.testado = False` |
| 26 | OB mitigado ignorado por `_marcar_obs_testados` | OB com `mitigado=True` + retrace | `ob.testado = False` |
| 27 | FVG virgem — nenhum retorno | FVG ALTA + candle posterior fica acima do gap | `fvg.testado = False` |
| 28 | FVG testado — wick de retrace | FVG ALTA + candle abre acima do topo, wick entra no gap | `fvg.testado = True` |
| 29 | FVG mitigado ignorado | FVG com `mitigado=True` + retrace | `fvg.testado = False` |
| 30 | FVG BAIXA testado | FVG BAIXA + candle abre abaixo do fundo, wick entra no gap | `fvg.testado = True` |
| 31 | BOS com displacement | BOS Bullish onde `high[i-1] < low[i+1]` | `quebra.deslocamento = True` |
| 32 | BOS sem displacement | BOS sem gap entre candles adjacentes | `quebra.deslocamento = False` |
| 33 | BOS na última vela disponível (sem i+1) | BOS no índice `len-2` (sem candle posterior) | `quebra.deslocamento = False` sem crash |
| 34 | `calcular_swings` acessível como pública | `from smc.analisador_smc import calcular_swings` | não lança `ImportError` |
