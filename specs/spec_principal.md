# Spec: principal.py

## Responsabilidade
Orquestrar o loop de varredura contínua. Coordena todos os módulos no pipeline de dois estágios: detecção de setups (H4) e confirmação de entrada (M5).

## Fluxo por Ciclo

```
para cada símbolo em ATIVOS_MONITORADOS:
    1. _obter_dados_mercado → (velas_h4, velas_m5, velas_d1 | None)
    2. _detectar_e_registrar_setups(velas_h4, velas_m5, velas_d1)
    3. _verificar_confirmacoes(velas_h4, velas_m5, velas_d1)
```

---

## `_obter_dados_mercado(simbolo, provedor) -> tuple | None`

- Busca H4 (TIMEFRAME_ESTRUTURAL, VELAS_TIMEFRAME_ESTRUTURAL velas; mínimo 20)
- Busca M5 (TIMEFRAME_GATILHO, 50 velas; mínimo 1)
- Busca D1 (TIMEFRAME_CONTEXTO_MACRO, VELAS_TIMEFRAME_CONTEXTO_MACRO velas; `None` não aborta)
- Retorna `None` com `logger.warning` se H4 ou M5 insuficientes

---

## `_detectar_e_registrar_setups(simbolo, velas_h4, velas_m5, velas_d1, repo)`

Pipeline sequencial com early-return em cada gate:

| Gate | Condição de saída | Motivo |
|------|------------------|--------|
| ATR | `atr <= 0` | Dados insuficientes |
| Eventos | `not eventos` | Nenhum ChoCH/BOS no H4 |
| Pools | `not pools` | Nenhum EQH/EQL/PDH/PDL |
| Sweep | `captura is None` | Pool não foi varrido |
| Eventos pós-sweep | `not eventos_pos` | Sem reversão estrutural após captura |
| Score | `score < SCORE_MINIMO_SETUP` | Qualidade insuficiente |
| POI | `poi_fundo >= poi_topo` | POI degenerada |
| Zona duplicada | `setup_ativo_na_zona(...)` | Setup já cobre essa região |
| ID duplicado | `setup_ja_existe(setup_id)` | Deduplicação por setup |

Fluxo detalhado:
1. `calcular_atr(velas_h4, ATR_PERIODO)`
2. `detectar_eventos_estrutura(velas_h4, simbolo, PERIODO_SWING_ESTRUTURA)` → lista de ChoCH/BOS
3. `extrair_legs(velas_h4, eventos, simbolo, PERIODO_SWING_ESTRUTURA, atr)` → `leg_por_evento`
4. `detectar_eqh_eql(velas_h4, simbolo, atr)` + `detectar_pdh_pdl(velas_d1, simbolo, atr)`
5. `detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO, simbolo)` → capturas H4
6. `calcular_bias_d1_v2`, `verificar_sessao`, `verificar_zona_premium_discount_v2`
7. Para cada pool:
   - `pool_varrido_por_sweep(pool, capturas)` → captura associada
   - Filtra `eventos_pos`: eventos estruturais com `tempo > captura.tempo` e direção reversal
   - Recupera `leg` associada ao evento mais recente
   - Se `leg` existe: detecta OBs e FVGs na leg, marca mitigados, calcula POI composta
   - Se `leg` não existe: POI = `evento.nivel_rompido`
   - Calcula score e aplica todos os gates
   - `repo.persistir_setup(...)` com `pool_tipo` e `pool_preco` do objeto pool

---

## `_verificar_confirmacoes(simbolo, velas_h4, velas_m5, velas_d1, preco_atual, repo, notificador)`

Para cada setup ativo no banco (dentro de `IDADE_MAX_SETUP_HORAS`):

### Com `EXIGIR_CONFIRMACAO_LTF=True` (padrão)
- `detectar_mss_no_poi(velas_m5, poi_fundo, poi_topo, direcao, simbolo, cutoff=evento_tempo, atr=atr, tp_ref=evento_nivel)`
- Se `None` → skip (sem MSS no M5 dentro da POI)

### Com `EXIGIR_CONFIRMACAO_LTF=False`
- Verifica se `poi_fundo <= preco_atual <= poi_topo`
- `_calcular_risco_rr_v2(preco_atual, sl_ref, direcao, atr, tp_ref=evento_nivel)`
- Cria `ConfirmacaoEntrada` com `tipo_confirmacao="DIRETO"`

Após confirmação:
1. Calcula `check_sessao`, `check_bias`, `check_zona` (informativos — não bloqueiam)
2. Monta `MENSAGEM_SETUP` com todos os campos incluindo `pool_tipo` e `pool_preco`
3. `repo.persistir_confirmacao(...)`
4. `notificador.enviar_alerta(mensagem)` → se sucesso: `repo.desativar_setup(setup_id)`

---

## Template da Mensagem

```
🔔 <b>SETUP SMC — {simbolo}</b>  [{score}/100] {score_emoji}

📊 {evento_tipo} {direcao}  |  {pool_tipo} @ {pool_preco:.5f}
🎯 POI: {poi_fundo:.5f} – {poi_topo:.5f}
📍 Entrada: {entrada:.5f}  |  SL: {sl:.5f} (-{sl_pips}p)  |  TP: {tp:.5f} (+{tp_pips}p)
📐 R:R 1:{rr:.1f}  |  Displacement: {displacement}

{check_sessao}
{checks_aviso}⏰ {timestamp}
```

- `score_emoji`: 🟢 se score ≥ 70; 🟡 se ≥ 55; 🔴 se < 55
- `sl_pips` / `tp_pips`: `round(abs(p1 - p2) * 10000)` — para JPY: `* 100`
- `checks_aviso`: linha de avisos consolidada só se `check_bias` ou `check_zona` começar com `⚠️`
- `displacement`: `"✅ Sim"` se `leg_id` presente; `"—"` caso contrário

---

## Funções Auxiliares

### `_score_emoji(score: int) -> str`
- `>= 70` → `"🟢"` | `>= 55` → `"🟡"` | `< 55` → `"🔴"`

### `_pips(p1: float, p2: float, simbolo: str) -> int`
- Factor: `100` se `"JPY"` no símbolo, `10000` caso contrário
- Retorna `round(abs(p1 - p2) * factor)`

### `_checks_aviso(check_bias: str, check_zona: str) -> str`
- Retorna string consolidada de avisos se algum começar com `"⚠️"`
- Formato: `"⚠️ Bias D1: X | Zona: Y\n\n"` ou `""`

---

## Filtros Contextuais (informativos)

Calculados em ambas as funções. **Não bloqueiam** o setup nem a confirmação — apenas informam na mensagem.

| Filtro | Função | Positivo | Negativo |
|--------|--------|----------|----------|
| Sessão | `verificar_sessao(datetime.now(UTC))` | `✅ London/NY` | `⚠️ Fora de sessão` |
| Bias D1 | `calcular_bias_d1_v2(velas_d1, simbolo, PERIODO_SWING_D1)` | `✅ Alinhado` | `⚠️ Neutro` / `⚠️ Contra D1` |
| Zona | `verificar_zona_premium_discount_v2(velas_d1, preco_atual, direcao, PERIODO_SWING_D1)` | `✅ Desconto`/`✅ Premium` | `⚠️ Sem confluência` |

Se `velas_d1` for `None`: bias e zona mostram `⚠️` automaticamente.

---

## Tratamento de Erros no Loop

| Situação | Comportamento |
|----------|---------------|
| Falha de conexão MT5 | Log crítico, retry via `_conectar_mt5_com_retry` (3×, intervalo 5s) |
| Símbolo indisponível | Log warning, pula para próximo símbolo |
| D1 indisponível | Log debug, filtros D1 mostram `⚠️`, loop continua |
| Falha no Telegram | Log error, setup mantido ativo (retry no próximo ciclo) |
| Exceção não tratada no ciclo | Log exception com traceback, loop continua |

## Inicialização

1. Carregar variáveis de ambiente via `dotenv`
2. Configurar logging (nível via `SMC_LOG_LEVEL`; arquivo de debug em `logs/smc_debug.log` se DEBUG)
3. Instanciar `Repositorio(CAMINHO_BANCO)`, `ProvedorDados`, `Notificador`
4. `_conectar_mt5_com_retry` (3 tentativas, 5s entre elas)
5. Loop: `_processar_simbolo` para cada ativo; `time.sleep(INTERVALO_VARREDURA_SEGUNDOS)` entre ciclos
6. `KeyboardInterrupt`: desconectar MT5, fechar SQLite
