# Spec: principal.py

## Responsabilidade
Orquestrar o loop de varredura contínua. Coordena todos os módulos, gerencia o banco de dados e a deduplicação de sinais.

## Fluxo por Ciclo

```
para cada símbolo em ATIVOS_MONITORADOS:
    1. obter velas H4 via ProvedorDados (estrutura SMC)
    2. obter velas M15 via ProvedorDados (preço atual do gatilho)
    3. obter velas D1 via ProvedorDados (bias macro + premium/discount) — não aborta se None
    4. detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO)
    5. detectar_quebra_estrutura(velas_h4, símbolo, PERIODO_SWING)
    6. mapear_zonas_interesse(velas_h4, símbolo)  → (obs, fvgs)
    6a. registrar no SQLite capturas e BOS novos (via _registrar_novos_eventos)
    6b. carregar todos os eventos ativos do SQLite dentro de IDADE_MAX_EVENTO_H4 × 4h
    7. preco_atual = velas_m15.iloc[-1].fechamento
    8. para cada captura × bos × ob × fvg:
       verificar_confluencia(captura, bos, obs, fvgs, preco_atual)
    9. se confluência detectada:
       a. calcular filtros contextuais (sessao, bias D1, premium/discount)
       b. calcular TP/SL com R:R 1:2
       c. id_sinal = SHA1(simbolo + ob.id + fvg.id)
       d. verificar no SQLite se id_sinal já existe
       e. se novo → INSERT em tabela_sinais + enviar alerta Telegram
```

## Filtros Contextuais (informativos)
Calculados após confluência confirmada; incluídos na mensagem, **não bloqueiam** o sinal:

| Filtro | Função | Positivo | Negativo |
|--------|--------|----------|----------|
| Sessão | `verificar_sessao(captura.tempo)` | `✅ London/NY` | `⚠️ Fora de sessão` |
| Bias D1 | `calcular_bias_d1(velas_d1, PERIODO_SWING_D1)` | `✅ Alinhado` | `⚠️ Contra D1` / `⚠️ Neutro` |
| Zona | `verificar_zona_premium_discount(velas_d1, preco_atual, direcao)` | `✅ Desconto`/`✅ Premium` | `⚠️ Sem confluência` |

Se `velas_d1` for `None` (D1 indisponível): bias e zona mostram `⚠️` automaticamente.

## Cálculo de TP/SL
```python
sl, tp, rr = calcular_risco_rr(preco_atual, ob, captura.direcao)
# ALTA:  sl = ob.preco_fundo, risco = preco_atual - sl, tp = preco_atual + 2 * risco
# BAIXA: sl = ob.preco_topo,  risco = sl - preco_atual, tp = preco_atual - 2 * risco
```

## Qualidade do BOS
```python
bos_qualidade = "Forte 💪" if bos.deslocamento else "Normal"
```

## Schema SQLite
Ver `specs/spec_repositorio.md` — toda comunicação com o banco é feita via `Repositorio`.

## Repositorio
`principal.py` instancia `Repositorio(CAMINHO_BANCO)` e o injeta em `ProvedorDados`. Operações de deduplicação de sinais usam `repo.sinal_ja_disparado()` e `repo.persistir_sinal()`.

## Tratamento de Erros no Loop

| Situação | Comportamento |
|----------|---------------|
| Falha de conexão MT5 | Log crítico, tentativa de reconexão, aguarda `INTERVALO * 2` |
| Símbolo indisponível | Log warning, pula para próximo símbolo |
| D1 indisponível | Log debug, filtros D1 mostram ⚠️ na mensagem, loop continua |
| Falha no Telegram | Log error, sinal é persistido no SQLite mesmo assim |
| Exceção não tratada no ciclo | Log exception com traceback, loop continua |

## Inicialização
1. Carregar variáveis de ambiente via `dotenv`
2. Configurar logging com formato: `%(asctime)s | %(levelname)s | %(name)s | %(message)s`
3. Instanciar `Repositorio(CAMINHO_BANCO)`
4. Instanciar `ProvedorDados(repo)`, `Notificador`
5. Conectar MT5 (com retry de 3 tentativas espaçadas de 5s)
6. Entrar no loop principal com `time.sleep(INTERVALO_VARREDURA_SEGUNDOS)` entre ciclos
7. Ao encerrar (KeyboardInterrupt): desconectar MT5 e fechar SQLite

## Logging
- Nível: `INFO` por padrão (configurável via env `SMC_LOG_LEVEL`)
- Um logger por módulo: `logging.getLogger(__name__)`
- Logs de cada ciclo: símbolo processado, capturas encontradas, BOS detectados, sinais disparados

---

## Decomposição de `_processar_simbolo`

A função pública `_processar_simbolo` é um orquestrador de ~10 linhas. A lógica foi extraída
para seis funções privadas coesas:

### `Confluencia` (NamedTuple)
```python
class Confluencia(NamedTuple):
    captura: CapturaLiquidez
    bos: QuebraEstrutura
    ob: OrderBlock
    fvg: FairValueGap
    overlap_fundo: float
    overlap_topo: float
```

### `_obter_dados_mercado(simbolo, provedor) -> tuple[DataFrame, DataFrame, DataFrame | None] | None`
- Busca H4 (≥ 20 velas), M15 (≥ 1 vela), D1 (opcional)
- Retorna `None` com `logger.warning` se H4 ou M15 insuficientes
- `velas_d1=None` não bloqueia — retorna a tripla mesmo assim

### `_detectar_estrutura_h4(velas_h4, simbolo) -> tuple[list, list, list[OrderBlock], list[FairValueGap]]`
- Chama os 4 detectores: `detectar_captura_liquidez`, `detectar_quebra_estrutura`, `mapear_zonas_interesse`
- Retorna `(capturas, quebras, obs, fvgs)` sem efeitos colaterais

### `_encontrar_confluencias(capturas, quebras, obs, fvgs, preco_atual) -> list[Confluencia]`
- Função pura: itera o loop 4-aninhado (capturas × quebras × obs × fvgs)
- Filtra por direção, temporalidade, sobreposição e preço na zona
- Retorna lista de `Confluencia` (vazia se nenhuma válida)

### `_calcular_contexto(conf, preco_atual, velas_d1) -> dict[str, Any]`
- Calcula checks de sessão, bias D1, zona premium/discount, qualidade do BOS
- Chama `calcular_risco_rr` para SL/TP/RR
- Chaves retornadas: `check_sessao`, `check_bias`, `check_zona`, `bos_qualidade`, `sl`, `tp`, `rr`

### `_construir_mensagem(simbolo, conf, ctx) -> str`
- Pura: desestrutura `conf` e `ctx` para preencher `MENSAGEM_ALERTA.format(...)`
- Adiciona `timestamp` com `datetime.now(timezone.utc)`

### `_gerar_id_evento(simbolo, tipo, tempo, preco) -> str`
- `SHA1(f"{simbolo}|{tipo}|{tempo.isoformat()}|{preco}")[:20]`
- `tipo`: `"CAPTURA"` ou `"BOS"`

### `_registrar_novos_eventos(capturas, quebras, repo, simbolo) -> None`
- Para cada captura/quebra: gera id, chama `evento_ja_detectado`; se novo, chama `registrar_captura`/`registrar_bos`
- Sem retorno — efeito colateral puro no banco

### `_carregar_eventos_ativos(repo, simbolo, cutoff) -> tuple[list[CapturaLiquidez], list[QuebraEstrutura]]`
- Chama `repo.carregar_capturas_ativas` e `repo.carregar_quebras_ativas`
- Reconstrói os dataclasses a partir das tuplas retornadas pelo repositório
- `cutoff = now - IDADE_MAX_EVENTO_H4 * 4h`

### `_processar_confluencia(simbolo, conf, preco_atual, velas_d1, repo, notificador) -> None`
- Gera `id_sinal` via SHA1, verifica dedup, chama `_calcular_contexto` + `_construir_mensagem`
- Persiste o sinal e envia o alerta Telegram
