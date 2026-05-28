# Spec: Papel dos Timeframes no Funil de Decisão

## Visão geral

O sistema usa três timeframes de dados + verificação de tempo real. Cada um impacta etapas distintas do pipeline de 2 estágios.

```
D1 bias + zona          ← filtra direção permitida e pondera score
    │
    ▼
H4 sweep + ChoCH/BOS    ← detecta o setup (Estágio 1)
    │ → poi_fundo/poi_topo + evento_nivel
    ▼
M5 MSS dentro da POI    ← confirma entrada (Estágio 2)
    │ → preco_entrada + sl_ref
    ▼
Sessão UTC              ← valida timing operacional
    │
    ▼
Telegram alert
```

---

## D1 — Contexto macro

Constante: `TIMEFRAME_CONTEXTO_MACRO` | Janela: `VELAS_TIMEFRAME_CONTEXTO_MACRO = 50` | Swing: `PERIODO_SWING_D1 = 5`

Usado em `_detectar_e_registrar_setups` e `_verificar_confirmacoes` via `velas_d1`.

| Etapa | Função | O que decide |
|-------|--------|--------------|
| Bias macro | `calcular_bias_d1_v2` | direção permitida — só BOS D1 confirma; ChoCH → `None` (bias incerto) |
| Zona premium/discount | `verificar_zona_premium_discount_v2` | preço está do lado estruturalmente correto? |
| Pools institucionais | `detectar_pdh_pdl` | PDH/PDL como alvos de liquidez (+15 pts vs +10 do EQH/EQL) |

D1 não dispara nada por conta própria — **pondera o score** e pode bloquear o sinal se bias for `None` ou zona divergir.

---

## H4 — Detecção de setup (Estágio 1 inteiro)

Constante: `TIMEFRAME_ESTRUTURAL` | Janela: `VELAS_TIMEFRAME_ESTRUTURAL = 500` | Swing: `PERIODO_SWING = 5`

É o timeframe de trabalho principal. Tudo que constrói o `SetupSMC` roda aqui:

```
calcular_atr(velas_h4)
  └─ threshold de displacement (Gate 4: 1.5×ATR, corpo ≥ 50%)
  └─ buffer SL: 0.1×ATR em _calcular_risco_rr_v2

detectar_eqh_eql(velas_h4) + detectar_pdh_pdl(velas_d1)
  └─ pools candidatos (alvos de liquidez)

detectar_captura_liquidez(velas_h4, PERIODO_SWING=5, LIMIAR_PAVIO=0.30)
  └─ sweep canônico: wick rompe swing + close oposto + pavio ≥ 30%

detectar_eventos_estrutura(velas_h4, PERIODO_SWING=5)
  └─ ChoCH/BOS → direção + nivel_rompido (→ tp_ref no Estágio 2)

extrair_legs → detectar_obs_corpo + extrair_fvgs_no_intervalo
  └─ OB∩FVG → poi_fundo/poi_topo (zona de entrada)

calcular_score_setup → gate SCORE_MINIMO_SETUP=40
  └─ abaixo do limiar → setup descartado silenciosamente
```

O `evento.nivel_rompido` (nível do ChoCH/BOS H4) é passado como `tp_ref` para o Estágio 2.

---

## M5 — Confirmação de entrada (Estágio 2)

Constante: implícito em `_verificar_confirmacoes` via `velas_m5` | Swing: `periodo=1` (mínimo para micro-estrutura)

Ativo apenas quando `EXIGIR_CONFIRMACAO_LTF = True`.

Função: `detectar_mss_no_poi(velas_m5, poi_fundo, poi_topo, direcao)` — MSS canônico dentro da POI H4.

Sequência exigida dentro da POI (mínimo 3 candles; na prática ~6):
- **ALTA:** swing low → swing high posterior → fechamento acima do swing high
- **BAIXA:** swing high → swing low posterior → fechamento abaixo do swing low

Produz:
- `preco_confirmacao`: fechamento da vela de confirmação
- `sl_ref`: swing estrutural M5 (ponto de invalidação da tese)
- `tp`: usa `evento_nivel` H4 se RR ≥ 1.5; se RR < 1.5 descarta o setup (barreira H4 próxima demais); sem `tp_ref` usa extensão mecânica 2×risco

---

## Sessão UTC — Filtro de timing operacional

Não é timeframe de candle — é `datetime.now(timezone.utc)` no momento do despacho do alerta.

| Sessão | Janela UTC | Score |
|--------|------------|-------|
| London | 08:00–11:00 | +10 pts |
| New York | 13:00–17:00 | +10 pts |
| Fora de sessão | — | +0 pts |

Função: `verificar_sessao(tempo)` em `filtros.py`. O `tempo` deve ser o momento atual — não o timestamp do evento histórico.
