# Spec: repositorio.py

## Responsabilidade
Centralizar toda conexão e comunicação com o banco de dados SQLite. Único módulo do projeto que importa `sqlite3`. Gerencia o ciclo de vida da conexão e expõe métodos de domínio para velas, setups e confirmações.

## Problema Resolvido
SQL e operações de banco espalhados em `principal.py` e `provedor_dados.py`, com `sqlite3.Connection` sendo criada num módulo e injetada em outro. `Repositorio` encapsula tudo: criação de tabelas, queries e gerenciamento de conexão.

## Classe: Repositorio

### `__init__(self, caminho: str) -> None`
- Cria o diretório pai se necessário (exceto para `":memory:"`)
- Abre `sqlite3.connect(caminho)`
- Executa `CREATE TABLE IF NOT EXISTS` para `velas`, `setups` e `confirmacoes`
- Executa `CREATE INDEX IF NOT EXISTS` para `velas`
- Expõe `_conn: sqlite3.Connection` (acessível nos testes)

### `fechar(self) -> None`
- Fecha a conexão

---

### Métodos — Velas

### `obter_ultimo_tempo_vela(self, simbolo: str, timeframe: int) -> datetime | None`
- `SELECT MAX(tempo) FROM velas WHERE simbolo=? AND timeframe=?`
- Retorna `datetime` com `tzinfo=UTC` ou `None` se cache vazio

### `persistir_velas(self, simbolo: str, timeframe: int, linhas: list[tuple]) -> None`
- `INSERT OR IGNORE INTO velas ...` via `executemany`
- Cada tupla: `(simbolo, timeframe, tempo_iso, abertura, maxima, minima, fechamento, volume)`

### `carregar_velas(self, simbolo: str, timeframe: int, quantidade: int) -> list[tuple]`
- `SELECT tempo, abertura, maxima, minima, fechamento, volume FROM velas ... LIMIT ?`
- Retorna lista de tuplas; lista vazia se não houver registros

---

### Métodos — Eventos Detectados

### `carregar_capturas_ativas(self, simbolo: str, cutoff: datetime) -> list[tuple]`
- `SELECT ... WHERE simbolo=? AND tipo='CAPTURA' AND tempo_vela >= ?`
- Retorna `(simbolo, direcao, preco, tempo_vela, pavio_percentual)` por linha

### `carregar_quebras_ativas(self, simbolo: str, cutoff: datetime) -> list[tuple]`
- `SELECT ... WHERE simbolo=? AND tipo='BOS' AND tempo_vela >= ?`
- Retorna `(simbolo, direcao, preco, swing_tempo, tempo_vela, deslocamento)` por linha

---

## Schemas SQLite

```sql
CREATE TABLE IF NOT EXISTS velas (
    simbolo    TEXT NOT NULL,
    timeframe  INTEGER NOT NULL,
    tempo      TEXT NOT NULL,
    abertura   REAL NOT NULL,
    maxima     REAL NOT NULL,
    minima     REAL NOT NULL,
    fechamento REAL NOT NULL,
    volume     INTEGER NOT NULL,
    PRIMARY KEY (simbolo, timeframe, tempo)
);
CREATE INDEX IF NOT EXISTS idx_velas_lookup ON velas (simbolo, timeframe, tempo DESC);

```

---

### Métodos — Setups (Phase 5)

### `setup_ja_existe(self, id_setup: str) -> bool`
- `SELECT 1 FROM setups WHERE id = ?`

### `persistir_setup(self, id_setup, simbolo, direcao, pool_id, evento_tipo, evento_tempo, evento_nivel, leg_id, poi_fundo, poi_topo, score) -> None`
- `INSERT OR IGNORE INTO setups ...`

### `carregar_setups_ativos(self, simbolo: str, cutoff: datetime) -> list[tuple]`
- `SELECT ... FROM setups WHERE simbolo=? AND ativo=1 AND criado_em >= ?`
- Retorna `(id, simbolo, direcao, pool_id, evento_tipo, evento_tempo, evento_nivel, leg_id, poi_fundo, poi_topo, score)` por linha

### `desativar_setup(self, id_setup: str) -> None`
- `UPDATE setups SET ativo=0 WHERE id=?`

### `persistir_confirmacao(self, id_conf, setup_id, simbolo, tipo, preco, tempo, sl, tp, rr) -> None`
- `INSERT OR IGNORE INTO confirmacoes ...`

---

## Schemas SQLite (adicionais)

```sql
CREATE TABLE IF NOT EXISTS setups (
    id              TEXT PRIMARY KEY,
    simbolo         TEXT NOT NULL,
    direcao         TEXT NOT NULL,
    pool_id         TEXT NOT NULL,
    evento_tipo     TEXT NOT NULL,
    evento_tempo    TEXT NOT NULL,
    evento_nivel    REAL NOT NULL,
    leg_id          TEXT,
    poi_fundo       REAL NOT NULL,
    poi_topo        REAL NOT NULL,
    score           INTEGER NOT NULL,
    ativo           INTEGER NOT NULL DEFAULT 1,
    criado_em       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmacoes (
    id                  TEXT PRIMARY KEY,
    setup_id            TEXT NOT NULL,
    simbolo             TEXT NOT NULL,
    tipo_confirmacao    TEXT NOT NULL,
    preco_confirmacao   REAL NOT NULL,
    tempo               TEXT NOT NULL,
    sl                  REAL NOT NULL,
    tp                  REAL NOT NULL,
    rr                  REAL NOT NULL
);
```

---

## Cenários de Teste

| # | Cenário | Resultado esperado |
|---|---------|-------------------|
| 1 | Cache vazio | `obter_ultimo_tempo_vela` → `None` |
| 2 | Persistir e consultar velas | `carregar_velas` retorna as tuplas persistidas |
| 3 | `INSERT OR IGNORE` | Persistir mesma vela duas vezes → sem duplicata |
| 4 | `fechar` | Conexão encerrada sem erro |
| 6 | Captura nova | `evento_ja_detectado` → `False`; após `registrar_captura` → `True` |
| 7 | BOS novo | `evento_ja_detectado` → `False`; após `registrar_bos` → `True` |
| 8 | Registrar mesmo evento duas vezes | sem erro (`INSERT OR IGNORE`) |
| 9 | `carregar_capturas_ativas` | retorna apenas eventos dentro do cutoff |
| 10 | `carregar_quebras_ativas` | retorna apenas eventos dentro do cutoff |
