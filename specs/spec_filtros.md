# Spec: filtros.py

## Responsabilidade
Funções puras de filtragem e análise contextual. Recebe dados já processados e retorna flags/valores informativos usados pelo `principal.py` para enriquecer a mensagem de alerta.

Todas as funções são puras (sem side-effects, sem logging, sem estado global) — fáceis de testar em isolamento.

## Funções Públicas

### `verificar_sessao(tempo: datetime) -> bool`
Retorna `True` se o tempo (UTC) cai dentro de uma sessão institucional relevante:
- **London:** 08:00–11:00 UTC
- **New York:** 13:00–17:00 UTC

```python
hora = tempo.hour + tempo.minute / 60.0
return (SESSAO_LONDON_INICIO <= hora < SESSAO_LONDON_FIM) or (SESSAO_NY_INICIO <= hora < SESSAO_NY_FIM)
```

O `tempo` deve ser `datetime.now(timezone.utc)` no momento do despacho do alerta — não o tempo do evento histórico. Usar o tempo da captura (evento passado) produziria verificação de sessão incorreta. Corrigido em `principal.py::_calcular_contexto`.

---

### `calcular_bias_d1(velas_d1: pd.DataFrame, periodo_swing: int) -> str | None`
Retorna `"ALTA"`, `"BAIXA"` ou `None` (neutro/indefinido).

**Algoritmo:**
1. Calcula swings usando `calcular_swings(velas_d1, periodo_swing)` importado de `analisador_smc`
2. Extrai swing highs em ordem cronológica: `[sh1, sh2]` (últimos 2 confirmados)
3. Extrai swing lows em ordem cronológica: `[sl1, sl2]` (últimos 2 confirmados)
4. Critério:
   - `sh2 > sh1 AND sl2 > sl1` → `"ALTA"` (HH + HL)
   - `sh2 < sh1 AND sl2 < sl1` → `"BAIXA"` (LH + LL)
   - Senão → `None`
5. Se menos de 2 swing highs **ou** menos de 2 swing lows confirmados → `None`

---

### `verificar_zona_premium_discount(velas_d1: pd.DataFrame, preco_atual: float, direcao: str) -> bool`
Retorna `True` se o preço está na zona estruturalmente correta para a direção do sinal.

```
range_high = max(maxima)  dos últimos 20 D1 candles
range_low  = min(minima)  dos últimos 20 D1 candles
midpoint   = (range_high + range_low) / 2.0

ALTA válido  → preco_atual < midpoint  (zona de desconto — preço "barato")
BAIXA válido → preco_atual > midpoint  (zona de premium — preço "caro")
```

Se `len(velas_d1) < 2` → retorna `False`.

---

### `calcular_risco_rr(preco_entrada: float, ob, direcao: str) -> tuple[float, float, float]`
Retorna `(sl, tp, rr)` com R:R fixo de 1:2.

```
ALTA:
  sl    = ob.preco_fundo
  risco = preco_entrada - sl
  tp    = preco_entrada + 2.0 * risco
  rr    = 2.0

BAIXA:
  sl    = ob.preco_topo
  risco = sl - preco_entrada
  tp    = preco_entrada - 2.0 * risco
  rr    = 2.0
```

O `ob` é qualquer objeto com atributos `preco_fundo` e `preco_topo` (duck typing — compatível com `OrderBlock`).

## Cenários de Teste

| # | Função | Cenário | Esperado |
|---|--------|---------|----------|
| F1 | `verificar_sessao` | 09:00 UTC (London) | `True` |
| F2 | `verificar_sessao` | 14:00 UTC (NY) | `True` |
| F3 | `verificar_sessao` | 05:00 UTC (Ásia) | `False` |
| F4 | `verificar_sessao` | 11:30 UTC (entre sessões) | `False` |
| F5 | `calcular_bias_d1` | HH + HL nos últimos 2 swings | `"ALTA"` |
| F6 | `calcular_bias_d1` | LH + LL nos últimos 2 swings | `"BAIXA"` |
| F7 | `calcular_bias_d1` | HH + LL (lateral) | `None` |
| F8 | `calcular_bias_d1` | Menos de 2 swings confirmados | `None` |
| F9 | `verificar_zona_premium_discount` | ALTA, preço abaixo do midpoint | `True` |
| F10 | `verificar_zona_premium_discount` | ALTA, preço acima do midpoint | `False` |
| F11 | `verificar_zona_premium_discount` | BAIXA, preço acima do midpoint | `True` |
| F12 | `calcular_risco_rr` | ALTA, entrada=1.1050, ob.fundo=1.1000 | `sl=1.1000, tp=1.1150, rr=2.0` |
| F13 | `calcular_risco_rr` | BAIXA, entrada=1.1050, ob.topo=1.1100 | `sl=1.1100, tp=1.0950, rr=2.0` |

---

### `calcular_bias_d1_v2(velas_d1: pd.DataFrame, simbolo: str, periodo_swing: int) -> str | None`
Retorna a direção do bias D1 baseado no evento estrutural mais recente.

**Algoritmo:**
1. Chama `detectar_eventos_estrutura(velas_d1, simbolo, periodo_swing)` — state machine ChoCH/BOS
2. Se não há eventos → retorna `None`
3. Retorna `eventos[-1].direcao` (direção do evento mais recente)

Mais robusto que `calcular_bias_d1` pois usa a state machine completa (HH/HL/LH/LL) em vez de apenas 2 swings consecutivos.

---

### `verificar_zona_premium_discount_v2(velas_d1: pd.DataFrame, preco_atual: float, direcao: str, periodo_swing: int) -> bool`
Retorna `True` se o preço está na zona estruturalmente correta usando range baseado em swings confirmados.

**Algoritmo:**
1. Chama `calcular_swings_confirmados(velas_d1, periodo_swing)` — janela simétrica
2. Extrai o swing high mais recente e swing low mais recente (por índice)
3. `equilibrium = (last_high.preco + last_low.preco) / 2.0`
4. `ALTA válido → preco_atual < equilibrium` (desconto)
5. `BAIXA válido → preco_atual > equilibrium` (premium)
6. Se não há swings confirmados suficientes → retorna `False`

Mais preciso que `verificar_zona_premium_discount` pois usa o range estrutural real (swings confirmados) em vez dos últimos 20 candles.

## Cenários de Teste v2

| # | Função | Cenário | Esperado |
|---|--------|---------|----------|
| F14 | `calcular_bias_d1_v2` | Último evento é BOS/ChoCH ALTA | `"ALTA"` |
| F15 | `calcular_bias_d1_v2` | Último evento é BOS/ChoCH BAIXA | `"BAIXA"` |
| F16 | `calcular_bias_d1_v2` | Sem eventos estruturais detectados | `None` |
| F17 | `verificar_zona_premium_discount_v2` | ALTA, preço abaixo do equilíbrio | `True` |
| F18 | `verificar_zona_premium_discount_v2` | ALTA, preço acima do equilíbrio | `False` |
| F19 | `verificar_zona_premium_discount_v2` | BAIXA, preço acima do equilíbrio | `True` |
| F20 | `verificar_zona_premium_discount_v2` | Sem swings confirmados | `False` |
