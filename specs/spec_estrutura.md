# Spec: detectar_eventos_estrutura

## Responsabilidade
Detectar quebras de estrutura (ChoCH e BOS) usando máquina de estados que rastreia HH/HL/LH/LL. Substitui `detectar_quebra_estrutura` (que retorna `QuebraEstrutura` sem contexto de tendência).

## Onde vive
`src/smc/analisador_smc.py` — função pública `detectar_eventos_estrutura`.

## Dependências
- `calcular_swings_confirmados(velas, periodo)` — janela simétrica estrita
- `calcular_atr(velas, periodo=14)` — para classificar displacement
- `EventoEstrutura`, `EstadoEstrutura` de `modelos.py`

---

## Algoritmo

### `calcular_swings_confirmados(velas, periodo) -> list[SwingPoint]`

**Diferença de `calcular_swings`:**
- `calcular_swings` usa janela assimétrica: candles próximos da borda direita usam `lado_dir = min(N, len-1-i)` — pode confirmar swings com apenas 1 barra à direita.
- `calcular_swings_confirmados` usa janela **estritamente simétrica**: só confirma swing em `i` se `i >= periodo` AND `i + periodo <= len - 2` (exige `periodo` barras à esquerda E à direita).
- O último candle (índice `len-1`) nunca é candidato.
- Retorna `list[SwingPoint]` (dataclass) em vez de `dict[int, float]`.

```
Para i em range(periodo, len(velas) - periodo):
    janela_max = maxima[i-periodo : i+periodo+1]   # comprimento 2*periodo+1
    janela_min = minima[i-periodo : i+periodo+1]
    se maxima[i] == janela_max.max():
        yield SwingPoint(indice=i, preco=maxima[i], tipo="HIGH", tempo=...)
    se minima[i] == janela_min.min():
        yield SwingPoint(indice=i, preco=minima[i], tipo="LOW", tempo=...)
```

**Nota:** A janela simétrica atrasa a confirmação em `periodo` barras (estrutura mais lenta mas mais confiável). Use `calcular_swings` apenas onde velocidade de detecção é crítica (capturas de liquidez, BOS na borda direita).

---

### `detectar_eventos_estrutura(velas, simbolo, periodo_swing) -> list[EventoEstrutura]`

#### Fase 1 — Calcular swings confirmados
```
swings = calcular_swings_confirmados(velas, periodo_swing)
highs = [s for s in swings if s.tipo == "HIGH"]  # ordenados por indice
lows  = [s for s in swings if s.tipo == "LOW"]
```

#### Fase 2 — Máquina de estados HH/HL/LH/LL

Estado inicial: `tendencia = "INDEFINIDA"`, `ultimo_high = None`, `ultimo_low = None`

Transições ao processar cada swing em ordem temporal:

**Ao encontrar novo HIGH `h`:**
```
se tendencia == "INDEFINIDA":
    ultimo_high = h; tendencia = "INDEFINIDA"  # aguarda LOW confirmado
se tendencia == "ALTA" e h.preco > ultimo_high.preco:
    → BOS de ALTA (quebrou último HH); update ultimo_high = h
se tendencia == "ALTA" e h.preco <= ultimo_high.preco:
    → possível LH (aguarda next LOW para confirmar bearish)
se tendencia == "BAIXA" e h.preco > ultimo_high.preco:
    → ChoCH de ALTA (quebrou último LH — mudança de tendência); tendencia = "ALTA"; ultimo_high = h
se tendencia == "BAIXA" e h.preco <= ultimo_high.preco:
    → LH confirmado dentro da baixa; update ultimo_high = h
```

**Ao encontrar novo LOW `l`:**
```
se tendencia == "INDEFINIDA":
    ultimo_low = l; tendencia = "INDEFINIDA"
se tendencia == "BAIXA" e l.preco < ultimo_low.preco:
    → BOS de BAIXA (quebrou último LL); update ultimo_low = l
se tendencia == "BAIXA" e l.preco >= ultimo_low.preco:
    → possível HL (aguarda next HIGH)
se tendencia == "ALTA" e l.preco < ultimo_low.preco:
    → ChoCH de BAIXA (quebrou último HL — mudança de tendência); tendencia = "BAIXA"; ultimo_low = l
se tendencia == "ALTA" e l.preco >= ultimo_low.preco:
    → HL confirmado dentro da alta; update ultimo_low = l
```

#### Fase 3 — Geração de EventoEstrutura

Ao classificar um swing como BOS ou ChoCH:
- `nivel_rompido` = preço do swing rompido (último HH/LL/LH/HL)
- `swing_tempo` = tempo do swing rompido
- `tempo` = tempo do swing que rompeu (confirmação simétrica = tempo do próprio swing)
- `deslocamento` = True se na leg de impulso da quebra há pelo menos 1 FVG interno

**Nota:** `tempo` em `EventoEstrutura` refere-se ao tempo do swing que CAUSOU a quebra, não ao candle específico de fechamento. Para precisão de trigger, use `swing.tempo` (ponto de confirmação simétrica).

---

### `calcular_atr(velas, periodo=14) -> float`

ATR de Wilder para as últimas `periodo` velas fechadas.

```
TR[i] = max(
    maxima[i] - minima[i],
    abs(maxima[i] - fechamento[i-1]),
    abs(minima[i] - fechamento[i-1])
)
ATR[0] = média simples dos primeiros `periodo` TRs
ATR[i] = (ATR[i-1] * (periodo-1) + TR[i]) / periodo   # Wilder smoothing
```

- Retorna `float` — ATR da última vela fechada
- Retorna `0.0` se `len(velas) < periodo + 1`
- Usa velas `iloc[:-1]` internamente (exclui vela aberta) para consistência com outros cálculos

---

## Cenários de Teste

| # | Cenário | Entrada | Saída esperada |
|---|---------|---------|----------------|
| 1 | `calcular_swings_confirmados` — swing confirmado | swing no meio do DF | `SwingPoint` retornado |
| 2 | `calcular_swings_confirmados` — borda direita rejeitada | swing nos últimos `periodo` candles | NÃO retornado |
| 3 | `calcular_swings_confirmados` — último candle excluído | candle `len-1` tem maxima máxima | NÃO retornado |
| 4 | `detectar_eventos_estrutura` — ChoCH de alta | tendência baixa + quebra de LH | `EventoEstrutura(tipo="ChoCH", direcao="ALTA")` |
| 5 | `detectar_eventos_estrutura` — BOS de alta | tendência alta + quebra de HH | `EventoEstrutura(tipo="BOS", direcao="ALTA")` |
| 6 | `detectar_eventos_estrutura` — ChoCH de baixa | tendência alta + quebra de HL | `EventoEstrutura(tipo="ChoCH", direcao="BAIXA")` |
| 7 | `detectar_eventos_estrutura` — BOS de baixa | tendência baixa + quebra de LL | `EventoEstrutura(tipo="BOS", direcao="BAIXA")` |
| 8 | `detectar_eventos_estrutura` — sem eventos | trending steadily, no reversal | `[]` |
| 9 | `calcular_atr` — retorno correto | 20 velas sintéticas com range constante | valor ≈ range constante |
| 10 | `calcular_atr` — velas insuficientes | `len(velas) < 15` | `0.0` |
| 11 | `detectar_eventos_estrutura` — deduplicação | múltiplas quebras do mesmo nível | 1 evento por nível |
| 12 | `detectar_eventos_estrutura` — estado inicial indefinido | apenas 1 swing | `[]` (aguarda 2 swings para classificar) |
