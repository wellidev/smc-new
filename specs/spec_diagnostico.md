# Spec: Módulo de Diagnóstico de Pipeline

## Responsabilidade

Permitir auditoria ponto a ponto do pipeline de geração de setups SMC, mostrando os valores
reais computados em cada gate para que o trader possa verificar contra o chart (MT5/TradingView).

Não é usado em produção. Serve para:
- Confirmar que PDH/PDL, EQH/EQL estão no preço correto
- Ver quais wicks formam cada cluster EQH/EQL
- Verificar swings confirmados H4 intermediários
- Checar qual BOS D1 definiu o bias e em que nível
- Ver o midpoint do equilíbrio premium/discount
- Ver OBs e FVGs com faixas de preço exatas e status de mitigação
- Ver o range completo da leg (índices, preços, displacement)
- Confirmar que o sweep foi detectado no candle correto
- Auditar POI composta e derivação
- Conferir score componente a componente
- Verificar deduplicação (G9/G10) contra o banco
- Auditar todos os setups ativos no banco e o MSS M5 de cada um

## Onde vive

`src/smc/diagnostico.py` — funções públicas chamadas por `debug_pipeline.py` (raiz do projeto).

## Dependências

- `analisador_smc.py` — todas as funções de detecção
- `filtros.py` — bias D1, sessão, zona premium/discount
- `configuracoes.py` — constantes de período e limiar
- `repositorio.py` — leitura de setups ativos (opcional nos gates G9/G10; obrigatório em `auditar_setups_ativos`)

---

## Funções Públicas

### `auditar_pipeline(simbolo, velas_h4, velas_m5, velas_d1, repo=None) -> None`

Roda o pipeline Estágio 1 completo e imprime relatório estruturado de cada gate.
Se `repo` for passado, executa também os gates G9 e G10 (deduplicação contra banco).

**Gates auditados em ordem:**

| Gate | O que mostra |
|------|-------------|
| G1 | ATR H4 em preço e pips |
| G2 | Swings confirmados H4 (lista HIGH/LOW com preço e timestamp) + eventos ChoCH/BOS com nível |
| G3 | PDH/PDL com data do dia anterior; EQH/EQL com preço centro, tolerância **e wicks membros** |
| CTX | Bias D1 com **qual BOS D1 o definiu** (tipo, nível, timestamp); equilibrium premium/discount com **midpoint calculado**; sessão; preço atual |
| G4 | Sweeps detectados com preço, % de pavio e timestamp |
| G5 | Matching pool→sweep com diferença em pips |
| G6 | Eventos pós-sweep; evento selecionado marcado; **leg completa**: direção, range de preço, índices, displacement |
| G7 | OBs com faixa, zona50, status mitigado/válido, timestamp; FVGs com faixa e status; POI composta com range, tamanho em pips e derivação |
| G8 | Score componente a componente com total e decisão pass/block |
| G9 | (requer `repo`) Verifica `setup_ativo_na_zona` — mostra se a zona POI já está coberta |
| G10 | (requer `repo`) Verifica `setup_ja_existe` — mostra se o ID já existe no banco |

---

### `auditar_setups_ativos(simbolo, velas_m5, atr, repo) -> None`

Lê todos os setups ativos no banco para o símbolo e audita o MSS M5 de cada um
automaticamente (Estágio 2). Para cada setup mostra:

1. ID, direção, score, POI, timestamp de criação
2. Velas M5 após cutoff (evento_tempo)
3. Velas dentro da POI com OHLC
4. Swings na POI (periodo=1)
5. Sequência MSS candidata passo a passo
6. Resultado: entrada, SL, TP, RR — ou motivo de falha em cada etapa

---

### `auditar_mss_para_setup(simbolo, velas_m5, atr, poi_fundo, poi_topo, direcao, cutoff=None, tp_ref=None) -> None`

Versão manual de auditoria MSS para quando o usuário tem os valores do setup em mãos.
Mesma saída de `auditar_setups_ativos` mas para um único setup passado como parâmetro.

---

## Seção CTX — Detalhes do Bias D1

```
[CTX] CONTEXTO DIRECIONAL
  Preço atual (M5 close) : 1.09350
  Bias D1                : BAIXA
    derivado de: BOS BAIXA  nível=1.09100  05-24 00:00
    (último evento D1 com tipo=BOS; ChoCH=sem bias)
  Sessão London/NY       : ✓  (14:30 UTC)
  Premium/Discount
    último swing HIGH D1  : 1.10200  05-20
    último swing LOW  D1  : 1.08400  05-23
    equilíbrio (midpoint) : 1.09300
    preço atual           : 1.09350  → PREMIUM (acima do midpoint)
    zona desconto (ALTA)  : ✗
    zona premium (BAIXA)  : ✓
```

---

## Seção G3 — Detalhes dos Clusters EQH/EQL

```
[G3] POOLS DE LIQUIDEZ
  PDH  @ 1.09850  tolerância=±9 pips  (D1 anterior: 05-26)
  PDL  @ 1.08420  tolerância=±9 pips  (D1 anterior: 05-26)
  EQH  @ 1.09783  tolerância=±9 pips  centro de 3 wicks:
    wick 1: maxima=1.09790  05-24 08:00
    wick 2: maxima=1.09780  05-25 12:00
    wick 3: maxima=1.09780  05-26 00:00
  EQL  @ 1.08512  tolerância=±9 pips  centro de 2 wicks:
    wick 1: minima=1.08520  05-25 16:00
    wick 2: minima=1.08504  05-26 04:00
```

---

## Seção G6 — Detalhes da Leg

```
│ [G6] Leg vinculada: BAIXA  displ=✓
│   início  : índice=142  preço=1.09620  05-26 14:00
│   fim     : índice=148  preço=1.09050  05-26 18:00
│   range   : 57 pips  (6 velas H4)
```

---

## Script Runner

`debug_pipeline.py` (raiz do projeto):
- Conecta ao MT5
- Busca H4, M5, D1
- Chama `auditar_pipeline(simbolo, velas_h4, velas_m5, velas_d1, repo=repo)`
- Chama `auditar_setups_ativos(simbolo, velas_m5, atr, repo)` para o Estágio 2

```
python debug_pipeline.py EURUSD
python debug_pipeline.py AUDUSD GBPUSD EURAUD
```

---

## Cenários de Verificação Manual

| # | O que auditar | O que conferir no chart |
|---|---------------|------------------------|
| 1 | PDH/PDL | Maxima/mínima do candle D1 anterior |
| 2 | EQH cluster | Wicks listados nos timestamps correspondem a topos no chart |
| 3 | EQL cluster | Wicks listados nos timestamps correspondem a fundos no chart |
| 4 | Swings H4 | HIGH/LOW nos índices/timestamps batem com swings visíveis |
| 5 | Bias D1 | Último BOS D1 listado confirma direção da tendência no D1 |
| 6 | Equilíbrio | Midpoint divide o range D1 recente ao meio |
| 7 | Sweep | Candle no timestamp tem wick longo e fecha do outro lado |
| 8 | OB | Última vela contra-tendência antes do impulso da leg |
| 9 | FVG | Gap entre maxima[i-1] e minima[i+1] nos timestamps |
| 10 | POI | Sobreposição OB∩FVG ou envelope visível no chart |
| 11 | Score | Somar componentes manualmente |
| 12 | G9/G10 | Verificar no banco se setup existe ou zona coberta |
| 13 | MSS M5 | Sequência LOW→HIGH→confirmação (ALTA) ou HIGH→LOW→confirmação (BAIXA) |
