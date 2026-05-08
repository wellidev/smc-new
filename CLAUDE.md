# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos essenciais

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar todos os testes
pytest testes/teste_analisador_smc.py testes/teste_provedor_dados.py testes/teste_notificador.py -v

# Rodar um único arquivo de testes
pytest testes/teste_analisador_smc.py -v

# Rodar um único teste por nome
pytest testes/teste_analisador_smc.py::TestVerificarConfluencia::test_confluencia_completa_baixa -v

# Verificar tipagem estática
mypy src/smc/

# Executar o sistema (requer MT5 aberto e .env preenchido)
python principal.py
```

## Arquitetura

### Src layout
Todo o código de produção fica em `src/smc/`. O arquivo `principal.py` na raiz é um entry point de 2 linhas que delega para `smc.principal.main()`. Novos módulos vão sempre em `src/smc/`.

### Fluxo de dados por ciclo (em `src/smc/principal.py`)
```
para cada símbolo em ATIVOS_MONITORADOS:
  ProvedorDados.obter_velas(H4)  →  cache incremental no SQLite
  ProvedorDados.obter_velas(M15) →  preço atual do gatilho
  detectar_captura_liquidez()    →  sweeps de swing high/low
  detectar_quebra_estrutura()    →  BOS (break of structure)
  mapear_zonas_interesse()       →  Order Blocks + FVGs ativos
  verificar_confluencia()        →  4 condições simultâneas?
    └─ sim → deduplicar via SQLite → enviar alerta Telegram
```

### Lógica de confluência — 4 condições obrigatórias
Implementada em `src/smc/analisador_smc.py::verificar_confluencia`. Todas devem ser verdadeiras:
1. **Captura de Liquidez** — wick rompe swing high/low e fecha do lado oposto; `pavio / range >= 0.30`
2. **BOS** — fechamento (não wick) rompe swing na mesma direção da captura
3. **Toque em Order Block** — preço M15 dentro de um OB não-mitigado na direção correta
4. **FVG sobreposto** — o OB tem pelo menos um FVG não-mitigado com zona de sobreposição

### Cache incremental de velas (`src/smc/provedor_dados.py`)
O SQLite armazena velas fechadas (a última vela retornada pelo MT5, ainda aberta, nunca é persistida). A cada ciclo, `obter_velas` consulta `MAX(tempo)` e busca apenas o delta desde esse ponto — sem recarregar as 500 velas históricas.

### SQLite — duas tabelas
- `velas` — cache de candles gerenciado por `ProvedorDados`
- `sinais` — alertas já disparados; chave `SHA1(simbolo + ob_id + fvg_id)` evita reenvios

## Metodologia: Spec-Driven Development
Cada módulo tem um spec em `specs/spec_<modulo>.md` que define contratos de API, algoritmos e cenários de teste. Ao modificar ou adicionar funcionalidade, atualize o spec correspondente antes do código.

## Testes
Os testes não dependem do MT5 real:
- `teste_analisador_smc.py` — usa DataFrames sintéticos com `pd.Timestamp`; usa `periodo_swing=5` (não 10) para que a janela de swing não inclua a vela de sweep nas fixtures
- `teste_provedor_dados.py` — mocka `MetaTrader5` via `pytest-mock`; usa `sqlite3.connect(":memory:")`
- `teste_notificador.py` — mocka `requests.post`

## Variáveis de ambiente (`.env`)
| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `SMC_TELEGRAM_TOKEN` | Sim | Token do bot Telegram |
| `SMC_TELEGRAM_CHAT_ID` | Sim | ID do chat de destino |
| `SMC_LOG_LEVEL` | Não | Nível de log (padrão: `INFO`) |
