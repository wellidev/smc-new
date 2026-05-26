# Spec: repositorio.py

## Responsabilidade
Centralizar toda conexão e comunicação com o banco de dados SQLite. Único módulo do projeto que importa `sqlite3`. Gerencia o ciclo de vida da conexão e expõe métodos de domínio para velas, setups e confirmações.

## Classe: Repositorio

### `__init__(self, caminho: str) -> None`
- Cria o diretório pai se necessário (exceto para `":memory:"`)
- Abre `sqlite3.connect(caminho)`
- Executa `CREATE TABLE IF NOT EXISTS` para `velas`, `setups` e `confirmacoes`
- Executa `CREATE INDEX IF NOT EXISTS` para `velas`
- Executa migrações aditivas (`ALTER TABLE ... ADD COLUMN`) silenciando `OperationalError` se a coluna já existir — permite atualizar bancos existentes sem recriar tabelas

---

### Métodos — Velas

### `obter_ultimo_tempo_vela(self, simbolo: str, timeframe: int) -> datetime | None`
- `SELECT MAX(tempo) FROM velas WHERE simbolo=? AND timeframe=?`
- Retorna `datetime` com `tzinfo=UTC` ou `None` se cache vazio

### `persistir_velas(self, simbolo: str, timeframe: int, linhas: list[tuple]) -> None`
- `INSERT OR REPLACE INTO velas ...` via `executemany`
- Cada tupla: `(simbolo, timeframe, tempo_iso, abertura, maxima, minima, fechamento, volume)`

### `carregar_velas(self, simbolo: str, timeframe: int, quantidade: int) -> list[tuple]`
- `SELECT tempo, abertura, maxima, minima, fechamento, volume FROM velas ... LIMIT ?`
- Retorna lista de tuplas; lista vazia se não houver registros

---

### Métodos — Setups

### `setup_ja_existe(self, id_setup: str) -> bool`
- `SELECT 1 FROM setups WHERE id = ?`

### `setup_ativo_na_zona(self, simbolo, direcao, poi_fundo, poi_topo, cutoff) -> bool`
- Verifica se já existe setup ativo sobreposto à zona POI, mais recente que `cutoff`
- Evita registrar setups redundantes na mesma região de preço

### `persistir_setup(self, id_setup, simbolo, direcao, pool_id, pool_tipo, pool_preco, evento_tipo, evento_tempo, evento_nivel, leg_id, poi_fundo, poi_topo, score) -> None`
- `INSERT OR IGNORE INTO setups ...`
- `pool_tipo`: tipo do pool que originou o setup (ex: `"EQH"`, `"PDL"`)
- `pool_preco`: nível de preço do pool, usado na mensagem Telegram

### `carregar_setups_ativos(self, simbolo: str, cutoff: datetime) -> list[tuple]`
- `SELECT ... FROM setups WHERE simbolo=? AND ativo=1 AND criado_em >= ?`
- Retorna por linha: `(id, simbolo, direcao, pool_id, pool_tipo, pool_preco, evento_tipo, evento_tempo, evento_nivel, leg_id, poi_fundo, poi_topo, score)`

### `desativar_setup(self, id_setup: str) -> None`
- `UPDATE setups SET ativo=0 WHERE id=?`
- Chamado após sinal confirmado enviado com sucesso via Telegram

### `persistir_confirmacao(self, id_conf, setup_id, simbolo, tipo, preco, tempo, sl, tp, rr) -> None`
- `INSERT OR IGNORE INTO confirmacoes ...`

### `limpar_setups_antigos(self, cutoff: datetime) -> int`
- `DELETE FROM setups WHERE ativo=0 AND criado_em < ?`
- Retorna número de registros removidos

---

## Schemas SQLite

```sql
CREATE TABLE IF NOT EXISTS velas (
    simbolo    TEXT    NOT NULL,
    timeframe  INTEGER NOT NULL,
    tempo      TEXT    NOT NULL,
    abertura   REAL    NOT NULL,
    maxima     REAL    NOT NULL,
    minima     REAL    NOT NULL,
    fechamento REAL    NOT NULL,
    volume     INTEGER NOT NULL,
    PRIMARY KEY (simbolo, timeframe, tempo)
);
CREATE INDEX IF NOT EXISTS idx_velas_lookup ON velas (simbolo, timeframe, tempo DESC);

CREATE TABLE IF NOT EXISTS setups (
    id              TEXT PRIMARY KEY,
    simbolo         TEXT NOT NULL,
    direcao         TEXT NOT NULL,
    pool_id         TEXT NOT NULL,
    pool_tipo       TEXT NOT NULL DEFAULT '',
    pool_preco      REAL NOT NULL DEFAULT 0.0,
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

### Migração de bancos existentes
Bancos criados antes da adição de `pool_tipo`/`pool_preco` recebem as colunas automaticamente via `ALTER TABLE` no `__init__`. O `OperationalError` ("duplicate column name") é silenciado — idempotente.

---

## Cenários de Teste

| # | Cenário | Resultado esperado |
|---|---------|-------------------|
| 1 | Cache vazio | `obter_ultimo_tempo_vela` → `None` |
| 2 | Persistir e consultar velas | `carregar_velas` retorna as tuplas persistidas |
| 3 | `INSERT OR IGNORE` | Persistir mesma vela duas vezes → sem duplicata |
| 4 | `fechar` | Conexão encerrada sem erro |
| 5 | `setup_ja_existe` antes e depois | `False` → `True` após `persistir_setup` |
| 6 | `setup_ativo_na_zona` com sobreposição | Retorna `True`; sem sobreposição → `False` |
| 7 | `carregar_setups_ativos` com cutoff | Retorna apenas setups dentro da janela temporal |