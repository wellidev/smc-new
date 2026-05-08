import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SQL_CRIAR_VELAS = """
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
"""

_SQL_CRIAR_INDICE_VELAS = """
CREATE INDEX IF NOT EXISTS idx_velas_lookup ON velas (simbolo, timeframe, tempo DESC);
"""

_SQL_CRIAR_SINAIS = """
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
"""

_SQL_ULTIMO_TEMPO_VELA = "SELECT MAX(tempo) FROM velas WHERE simbolo = ? AND timeframe = ?"

_SQL_INSERIR_VELA = """
INSERT OR REPLACE INTO velas (simbolo, timeframe, tempo, abertura, maxima, minima, fechamento, volume)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_BUSCAR_VELAS = """
SELECT tempo, abertura, maxima, minima, fechamento, volume
FROM (
    SELECT tempo, abertura, maxima, minima, fechamento, volume
    FROM velas
    WHERE simbolo = ? AND timeframe = ?
    ORDER BY tempo DESC
    LIMIT ?
)
ORDER BY tempo ASC
"""

_SQL_SINAL_EXISTE = "SELECT 1 FROM sinais WHERE id_sinal = ?"

_SQL_INSERIR_SINAL = """
INSERT INTO sinais
    (id_sinal, simbolo, ob_id, fvg_id, direcao, preco_ob_topo, preco_ob_fundo,
     preco_fvg_topo, preco_fvg_fundo, timestamp)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class Repositorio:
    def __init__(self, caminho: str) -> None:
        if caminho != ":memory:":
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
        self._conn = sqlite3.connect(caminho)
        self._conn.execute(_SQL_CRIAR_VELAS)
        self._conn.execute(_SQL_CRIAR_INDICE_VELAS)
        self._conn.execute(_SQL_CRIAR_SINAIS)
        self._conn.commit()

    def fechar(self) -> None:
        self._conn.close()

    # --- Velas ---

    def obter_ultimo_tempo_vela(self, simbolo: str, timeframe: int) -> datetime | None:
        cursor = self._conn.execute(_SQL_ULTIMO_TEMPO_VELA, (simbolo, timeframe))
        linha = cursor.fetchone()
        if linha[0] is None:
            return None
        return datetime.fromisoformat(linha[0]).replace(tzinfo=timezone.utc)

    def persistir_velas(self, simbolo: str, timeframe: int, linhas: list[tuple]) -> None:
        self._conn.executemany(_SQL_INSERIR_VELA, linhas)
        self._conn.commit()
        logger.debug("Persistidas %d velas para %s TF=%d", len(linhas), simbolo, timeframe)

    def carregar_velas(self, simbolo: str, timeframe: int, quantidade: int) -> list[tuple]:
        cursor = self._conn.execute(_SQL_BUSCAR_VELAS, (simbolo, timeframe, quantidade))
        return cursor.fetchall()

    # --- Sinais ---

    def sinal_ja_disparado(self, id_sinal: str) -> bool:
        cursor = self._conn.execute(_SQL_SINAL_EXISTE, (id_sinal,))
        return cursor.fetchone() is not None

    def persistir_sinal(self, id_sinal: str, simbolo: str, ob, fvg, direcao: str) -> None:
        agora = datetime.now(timezone.utc).isoformat()
        self._conn.execute(_SQL_INSERIR_SINAL, (
            id_sinal, simbolo, ob.id, fvg.id, direcao,
            ob.preco_topo, ob.preco_fundo,
            fvg.preco_topo, fvg.preco_fundo,
            agora,
        ))
        self._conn.commit()
