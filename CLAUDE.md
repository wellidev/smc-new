# CLAUDE.md

## Comandos essenciais

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar todos os testes
pytest testes/ -v

# Rodar um único arquivo de testes
pytest testes/teste_analisador_smc.py -v

# Verificar tipagem estática
mypy src/smc/

# Executar o sistema (requer MT5 aberto e .env preenchido)
python principal.py
```

## Arquitetura e domínio

A arquitetura, o pipeline, os algoritmos e os contratos de API estão documentados nos specs:

```
specs/
├── spec_analisador_smc.md  ← visão geral do módulo central + funções públicas
├── spec_estrutura.md       ← state machine ChoCH/BOS
├── spec_legs.md            ← legs, OBs v2, FVGs, POI composta
├── spec_pools.md           ← EQH/EQL/PDH/PDL
├── spec_setup.md           ← MSS LTF, score, risco/retorno
├── spec_filtros.md         ← bias D1, sessão, premium/discount
└── spec_configuracoes.md   ← constantes e env vars
```

**Regra SDD:** spec antes do código. Ao modificar ou adicionar funcionalidade, atualize o spec correspondente antes de tocar no código.

## Protocolo de validação canônica

SDD garante consistência interna (código faz o que o spec diz), mas **não garante corretude de domínio** (o spec pode estar errado em relação ao ICT/SMC canônico).

O padrão de retrabalho que ocorreu neste projeto:
1. Escreve spec baseado no entendimento atual do SMC
2. Implementa conforme o spec — testes passam
3. Auditoria canônica posterior descobre que o spec capturava errado o conceito ICT
4. Correção custosa: spec + código + testes

**Para evitar este ciclo, antes de especificar qualquer conceito SMC:**

> "Qual é a sequência exata de condições que o ICT usa para confirmar este conceito, e o que o distingue de um sinal falso?"

A pergunta não é "como isso funciona?" (descrição), mas "quando isso FALHA em ser correto do ponto de vista ICT?" (validação de borda).

Exemplos do custo desta validação tardia neste projeto:
- **Bias D1:** spec dizia "último evento da state machine → direção". O correto: ChoCH = sinal, BOS = confirmação — bias `None` entre os dois.
- **FVG mitigation:** spec usava wick a 50%. O correto: close a 50% (wick é ruído).
- **eventos_pos temporal:** spec filtrava por `pool.tempo`. O correto: `captura.tempo` (quando o pool foi swept, não quando foi criado).

**Regra:** validação canônica acontece na revisão do spec, antes do código — não na auditoria do código implementado.

Se não houver fonte canônica consultável (vídeo, PDF, referência ICT) para responder a pergunta com segurança, sinalize a incerteza explicitamente no spec antes de implementar — por exemplo: `> ⚠️ Comportamento de borda não confirmado contra fonte ICT. Implementação assume X; revisar antes de usar em produção.`

## Variáveis de ambiente (`.env`)

| Variável | Obrigatória | Descrição                     |
|----------|-------------|-------------------------------|
| `SMC_MT5_LOGIN` | Sim | MT5 login                     |
| `SMC_MT5_PASSWORD` | Sim | MT5 senha                     |
| `SMC_MT5_SERVER` | Sim | MT5 servidor                  |
| `SMC_MT5_PATH` | Sim | MT5 caminho executável        |
| `SMC_TELEGRAM_TOKEN` | Sim | Token do bot Telegram         |
| `SMC_TELEGRAM_CHAT_ID` | Sim | ID do chat de destino         |
| `SMC_LOG_LEVEL` | Não | Nível de log (padrão: `INFO`) |

## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
