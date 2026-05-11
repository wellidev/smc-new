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

### Métodos — Eventos Detectados

### `evento_ja_detectado(self, id_evento: str) -> bool`
- `SELECT 1 FROM eventos_detectados WHERE id_evento = ?`
- Retorna `True` se o evento já foi registrado em ciclos anteriores

### `registrar_captura(self, id_evento, simbolo, direcao, tempo, preco_varredura, pavio_percentual) -> None`
- `INSERT OR IGNORE INTO eventos_detectados ...` com `tipo='CAPTURA'`

### `registrar_bos(self, id_evento, simbolo, direcao, nivel_rompido, swing_tempo, tempo, deslocamento) -> None`
- `INSERT OR IGNORE INTO eventos_detectados ...` com `tipo='BOS'`

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

CREATE TABLE IF NOT EXISTS eventos_detectados (
    id_evento        TEXT PRIMARY KEY,
    simbolo          TEXT NOT NULL,
    tipo             TEXT NOT NULL,      -- 'CAPTURA' ou 'BOS'
    direcao          TEXT NOT NULL,
    tempo_vela       TEXT NOT NULL,
    preco            REAL NOT NULL,
    pavio_percentual REAL,               -- CAPTURA only
    swing_tempo      TEXT,               -- BOS only
    deslocamento     INTEGER,            -- BOS only (0/1)
    detectado_em     TEXT NOT NULL
);

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
| 6 | Captura nova | `evento_ja_detectado` → `False`; após `registrar_captura` → `True` |
| 7 | BOS novo | `evento_ja_detectado` → `False`; após `registrar_bos` → `True` |
| 8 | Registrar mesmo evento duas vezes | sem erro (`INSERT OR IGNORE`) |
| 9 | `carregar_capturas_ativas` | retorna apenas eventos dentro do cutoff |
| 10 | `carregar_quebras_ativas` | retorna apenas eventos dentro do cutoff |
