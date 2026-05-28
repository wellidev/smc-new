# Spec: modelos.py

## Responsabilidade
Centralizar todos os dataclasses de domínio SMC. Não contém lógica — apenas estruturas de dados tipadas. Importado por todos os outros módulos; nunca importa de módulos internos do projeto.

## Dependências
`from __future__ import annotations` (permite referências forward em type hints)
`dataclasses`, `datetime`, `typing` (Literal, Optional, List)

---

## Dataclasses Existentes (mantidos em `analisador_smc.py` temporariamente)

Os dataclasses `OrderBlock`, `FairValueGap`, `CapturaLiquidez`, `QuebraEstrutura` continuam em `analisador_smc.py` durante a transição e serão migrados para `modelos.py` na Fase 7 (remoção de legados).

---

## Novos Dataclasses

### `SwingPoint`
Ponto de swing confirmado — high ou low local com janela simétrica confirmada.

```python
@dataclass
class SwingPoint:
    indice: int           # índice no DataFrame original
    preco: float          # maxima (HIGH) ou minima (LOW)
    tipo: str             # "HIGH" | "LOW"
    tempo: datetime
```

**Invariantes:**
- `tipo` ∈ {"HIGH", "LOW"}
- `preco > 0`
- Confirmado apenas quando há `periodo` candles à esquerda E à direita — janela estritamente simétrica (diferente de `calcular_swings` que usa janela assimétrica para a borda direita)

---

### `EstadoEstrutura`
Estado atual da máquina de estados do mercado. Rastreado por símbolo ao longo dos ciclos.

```python
@dataclass
class EstadoEstrutura:
    ultimo_high: float
    ultimo_low: float
    ultimo_high_tempo: datetime
    ultimo_low_tempo: datetime
    tendencia: str        # "ALTA" | "BAIXA" | "INDEFINIDA"
```

**Invariantes:**
- `tendencia` ∈ {"ALTA", "BAIXA", "INDEFINIDA"}
- "ALTA" = mercado fazendo HH e HL; "BAIXA" = LH e LL; "INDEFINIDA" = estado inicial ou ambíguo
- Atualizado a cada novo swing confirmado; não persiste no banco (reconstruído a partir dos swings a cada ciclo)

---

### `EventoEstrutura`
Quebra de estrutura classificada como ChoCH ou BOS com base na máquina de estados.

```python
@dataclass
class EventoEstrutura:
    simbolo: str
    tipo: str             # "ChoCH" | "BOS"
    direcao: str          # "ALTA" (rompeu high) | "BAIXA" (rompeu low)
    nivel_rompido: float
    swing_tempo: datetime  # tempo do swing rompido
    tempo: datetime        # tempo do fechamento que rompeu
    deslocamento: bool = False  # True se a vela do evento tem FVG interno com i-1 ou i+1
```

**Invariantes:**
- `tipo` ∈ {"ChoCH", "BOS"}
- `direcao` ∈ {"ALTA", "BAIXA"}
- ChoCH: quebra CONTRA a tendência atual (LH em tendência de alta, LL em tendência de baixa)
- BOS: quebra NA mesma direção da tendência (HH em alta, LL em baixa)
- `deslocamento=True` requer gap entre a vela do evento e candles adjacentes

---

### `LegImpulso`
Trecho direcional de preço que cria structure (EventoEstrutura). Usado para extrair POIs vinculados.

```python
@dataclass
class LegImpulso:
    simbolo: str
    direcao: str          # "ALTA" | "BAIXA"
    tempo_inicio: datetime
    tempo_fim: datetime
    indice_inicio: int    # índice no DataFrame
    indice_fim: int
    preco_inicio: float   # preço da primeira vela da leg
    preco_fim: float      # preço da última vela da leg
    range_pontos: float   # abs(preco_fim - preco_inicio)
    atr_multiplo: float   # range_pontos / ATR14
    proporcao_corpo: float  # fração de velas com corpo na direção (0.0–1.0)
    tem_fvg_interno: bool
    eh_displacement: bool   # atr_multiplo >= 2.0 AND proporcao_corpo >= 0.60 AND tem_fvg_interno
```

**Invariantes:**
- `tempo_fim > tempo_inicio`
- `range_pontos >= 0`
- `eh_displacement = (atr_multiplo >= 2.0 and proporcao_corpo >= 0.60 and tem_fvg_interno)`
- `proporcao_corpo ∈ [0.0, 1.0]`

---

### `OrderBlockV2`
Order Block vinculado a uma `LegImpulso`. Usa zona de CORPO (open/close) em vez de range (high/low).

```python
@dataclass
class OrderBlockV2:
    id: str               # SHA1(simbolo|leg.tempo_inicio.isoformat()|str(indice))[:16]
    simbolo: str
    direcao: str          # "ALTA" | "BAIXA"
    preco_topo: float     # max(abertura, fechamento) da vela OB
    preco_fundo: float    # min(abertura, fechamento) da vela OB
    zona_50: float        # midpoint = (preco_topo + preco_fundo) / 2
    tempo: datetime
    leg_id: str           # liga ao LegImpulso que o criou
    mitigado: bool = False
    testado: bool = False
```

**Diferença do OrderBlock legado:**
- `preco_topo/preco_fundo` usam `max/min(abertura, fechamento)` — corpo da vela
- `zona_50` marca o ponto de 50% do OTE (Optimal Trade Entry)
- `leg_id` vincula o OB ao impulso que o criou — rastreabilidade causal

**Mitigação:**
- ALTA: `close < preco_fundo` (fecha abaixo do corpo)
- BAIXA: `close > preco_topo` (fecha acima do corpo)

---

### `PoolLiquidez`
Cluster de ordens não preenchidas — alvo primário de sweeps.

```python
@dataclass
class PoolLiquidez:
    id: str               # SHA1(simbolo|tipo|str(round(preco, 5))|tempo.isoformat())[:16]
    simbolo: str
    tipo: str             # "EQH" | "EQL" | "PDH" | "PDL"
    preco: float          # centro do cluster (média dos wicks)
    tolerancia: float     # 0.1 × ATR14 (raio do cluster)
    tempo: datetime       # tempo do primeiro wick no cluster
    varredido: bool = False
```

**Invariantes:**
- `tipo` ∈ {"EQH", "EQL", "PDH", "PDL"}
- EQH/EQL: ≥ 2 wicks dentro de `tolerancia` do `preco`
- PDH/PDL: máxima/mínima do dia anterior (D1)
- `varredido=True` quando uma vela fecha além do `preco` (inversão de liquidez)

---

### `SetupSMC`
Estado intermediário entre detecção do pool e confirmação LTF. Persiste entre ciclos.

```python
@dataclass
class SetupSMC:
    id: str               # SHA1(simbolo|pool_id|evento.tempo.isoformat())[:20]
    simbolo: str
    direcao: str          # "ALTA" | "BAIXA"
    pool_id: str          # FK para PoolLiquidez
    evento_tipo: str      # "ChoCH" | "BOS"
    evento_tempo: datetime
    evento_nivel: float
    leg_id: str | None    # FK para LegImpulso (None se leg não classificado como displacement)
    poi_fundo: float      # zona de entrada: min dos preco_fundo de OBs + FVGs da leg
    poi_topo: float       # zona de entrada: max dos preco_topo de OBs + FVGs da leg
    score: int            # score composicional (0–100)
    ativo: bool = True    # False após confirmação ou expiração (IDADE_MAX_SETUP ciclos)
    criado_em: datetime = None  # preenchido no __post_init__
```

**Invariantes:**
- `poi_topo > poi_fundo`
- `score ∈ [0, 100]`
- `ativo=False` desativa o setup para consultas futuras sem deletar o registro
- `direcao` determina direção do sinal — ALTA espera preço na POI de demanda, BAIXA na POI de oferta

---

### `ConfirmacaoEntrada`
Confirmação LTF (MSS no M5) dentro da POI do setup.

```python
@dataclass
class ConfirmacaoEntrada:
    id: str               # SHA1(setup_id|tempo.isoformat())[:20]
    setup_id: str
    simbolo: str
    tipo_confirmacao: str  # "MSS" | "DIRETO"
    preco_confirmacao: float
    tempo: datetime
    sl: float
    tp: float
    rr: float
    expiration_time: datetime | None = None  # tempo + 2 × TIMEFRAME_GATILHO_MINUTOS
```

---

## Cenários de Teste

| # | Dataclass | Cenário | Verificação |
|---|-----------|---------|-------------|
| 1 | `SwingPoint` | Instanciação válida | campos acessíveis, sem erro |
| 2 | `EstadoEstrutura` | Tendência "INDEFINIDA" por padrão | `tendencia == "INDEFINIDA"` |
| 3 | `EventoEstrutura` | ChoCH de Alta | `tipo == "ChoCH"`, `direcao == "ALTA"` |
| 4 | `LegImpulso` | `eh_displacement` calculado externamente | campo aceita `True/False` |
| 5 | `OrderBlockV2` | `zona_50 == (preco_topo + preco_fundo) / 2` | verificado na criação |
| 6 | `PoolLiquidez` | Tipo EQH | `tipo == "EQH"`, `varredido == False` |
| 7 | `SetupSMC` | `ativo=True` por padrão | padrão correto |
| 8 | `ConfirmacaoEntrada` | `rr == 2.0` | invariante por convenção (calculado externamente) |
