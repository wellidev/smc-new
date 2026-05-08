# Spec: repositorio.py

## Responsabilidade
Centralizar toda conexão e comunicação com o banco de dados SQLite. Único módulo do projeto que importa `sqlite3`. Gerencia o ciclo de vida da conexão e expõe métodos de domínio para velas e sinais.

## Problema Resolvido
SQL e operações de banco espalhados em `principal.py` e `provedor_dados.py`, com `sqlite3.Connection` sendo criada num módulo e injetada em outro. `Repositorio` encapsula tudo: criação de tabelas, queries e gerenciamento de conexão.

## Classe: Repositorio

### `__init__(self, caminho: str) -> None`
- Cria o diretório pai se necessário (exceto para `":memory:"`)
- Abre `sqlite3.connect(caminho)`
- Executa `CREATE TABLE IF NOT EXISTS` para `velas` e `sinais`
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

### Métodos — Sinais

### `sinal_ja_disparado(self, id_sinal: str) -> bool`
- `SELECT 1 FROM sinais WHERE id_sinal = ?`
- Retorna `True` se já existe

### `persistir_sinal(self, id_sinal: str, simbolo: str, ob, fvg, direcao: str) -> None`
- `INSERT INTO sinais ...` com timestamp UTC ISO 8601

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

CREATE TABLE IF NOT EXISTS sinais (
    id_sinal        TEXT PRIMARY KEY,
    simbolo         TEXT NOT NULL,
    ob_id           TEXT NOT NULL,
    fvg_id          TEXT NOT NULL,
    direcao         TEXT NOT NULL,
    preco_ob_topo   REAL,
    preco_ob_fundo  REAL,
    preco_fvg_topo  REAL,
    preco_fvg_fundo REAL,
    timestamp       TEXT NOT NULL
);
```

## Cenários de Teste

| # | Cenário | Resultado esperado |
|---|---------|-------------------|
| 1 | Cache vazio | `obter_ultimo_tempo_vela` → `None` |
| 2 | Persistir e consultar velas | `carregar_velas` retorna as tuplas persistidas |
| 3 | `INSERT OR IGNORE` | Persistir mesma vela duas vezes → sem duplicata |
| 4 | Sinal novo | `sinal_ja_disparado` → `False`; após `persistir_sinal` → `True` |
| 5 | `fechar` | Conexão encerrada sem erro |
