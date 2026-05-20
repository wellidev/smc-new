import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SQL_CRIAR_VELAS = """
                   CREATE TABLE IF NOT EXISTS velas
                   (
                       simbolo    TEXT    NOT NULL,
                       timeframe  INTEGER NOT NULL,
                       tempo      TEXT    NOT NULL,
                       abertura   REAL    NOT NULL,
                       maxima     REAL    NOT NULL,
                       minima     REAL    NOT NULL,
                       fechamento REAL    NOT NULL,
                       volume     INTEGER NOT NULL,
                       PRIMARY KEY (simbolo, timeframe, tempo)
                   ); \
                   """

_SQL_CRIAR_INDICE_VELAS = """
                          CREATE INDEX IF NOT EXISTS idx_velas_lookup ON velas (simbolo, timeframe, tempo DESC); \
                          """

_SQL_CRIAR_SINAIS = """
                    CREATE TABLE IF NOT EXISTS sinais
                    (
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
                    ); \
                    """

_SQL_ULTIMO_TEMPO_VELA = "SELECT MAX(tempo) FROM velas WHERE simbolo = ? AND timeframe = ?"

_SQL_INSERIR_VELA = """
INSERT OR REPLACE INTO velas (simbolo, timeframe, tempo, abertura, maxima, minima, fechamento, volume)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_BUSCAR_VELAS = """
                    SELECT tempo, abertura, maxima, minima, fechamento, volume
                    FROM (SELECT tempo, abertura, maxima, minima, fechamento, volume
                          FROM velas
                          WHERE simbolo = ?
                            AND timeframe = ?
                          ORDER BY tempo DESC
                          LIMIT ?)
                    ORDER BY tempo \
                    """

_SQL_SINAL_EXISTE = "SELECT 1 FROM sinais WHERE id_sinal = ?"

_SQL_INSERIR_SINAL = """
                     INSERT OR IGNORE INTO sinais
                     (id_sinal, simbolo, ob_id, fvg_id, direcao, preco_ob_topo, preco_ob_fundo,
                      preco_fvg_topo, preco_fvg_fundo, timestamp)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
                     """

_SQL_CRIAR_SETUPS = """
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
"""

_SQL_CRIAR_CONFIRMACOES = """
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
"""

_SQL_SETUP_EXISTE = "SELECT 1 FROM setups WHERE id = ?"
_SQL_SETUP_ATIVO_NA_ZONA = """
SELECT 1 FROM setups
WHERE simbolo = ? AND direcao = ? AND criado_em >= ?
  AND poi_fundo < ? AND poi_topo > ?
LIMIT 1
"""
_SQL_INSERIR_SETUP = """
INSERT OR IGNORE INTO setups
(id, simbolo, direcao, pool_id, evento_tipo, evento_tempo, evento_nivel,
 leg_id, poi_fundo, poi_topo, score, ativo, criado_em)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
"""
_SQL_SETUPS_ATIVOS = """
SELECT id, simbolo, direcao, pool_id, evento_tipo, evento_tempo, evento_nivel,
       leg_id, poi_fundo, poi_topo, score
FROM setups
WHERE simbolo = ? AND ativo = 1 AND criado_em >= ?
ORDER BY criado_em
"""
_SQL_DESATIVAR_SETUP = "UPDATE setups SET ativo = 0 WHERE id = ?"

_SQL_INSERIR_CONFIRMACAO = """
INSERT OR IGNORE INTO confirmacoes
(id, setup_id, simbolo, tipo_confirmacao, preco_confirmacao, tempo, sl, tp, rr)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_LIMPAR_SETUPS_ANTIGOS = "DELETE FROM setups WHERE ativo = 0 AND criado_em < ?"


class Repositorio:
    def __init__(self, caminho: str) -> None:
        if caminho != ":memory:":
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
        self._conn = sqlite3.connect(caminho)
        self._conn.execute(_SQL_CRIAR_VELAS)
        self._conn.execute(_SQL_CRIAR_INDICE_VELAS)
        self._conn.execute(_SQL_CRIAR_SINAIS)
        self._conn.execute(_SQL_CRIAR_SETUPS)
        self._conn.execute(_SQL_CRIAR_CONFIRMACOES)
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

    def registrar_sinal_v2(
        self,
        id_sinal: str,
        setup_id: str,
        simbolo: str,
        direcao: str,
        poi_fundo: float,
        poi_topo: float,
    ) -> None:
        agora = datetime.now(timezone.utc).isoformat()
        self._conn.execute(_SQL_INSERIR_SINAL, (
            id_sinal, simbolo, setup_id, setup_id,
            direcao, poi_topo, poi_fundo, poi_topo, poi_fundo, agora,
        ))
        self._conn.commit()

    # --- Setups (Phase 5) ---

    def setup_ja_existe(self, id_setup: str) -> bool:
        cursor = self._conn.execute(_SQL_SETUP_EXISTE, (id_setup,))
        return cursor.fetchone() is not None

    def setup_ativo_na_zona(
        self,
        simbolo: str,
        direcao: str,
        poi_fundo: float,
        poi_topo: float,
        cutoff: datetime,
    ) -> bool:
        cursor = self._conn.execute(
            _SQL_SETUP_ATIVO_NA_ZONA,
            (simbolo, direcao, cutoff.isoformat(), poi_topo, poi_fundo),
        )
        return cursor.fetchone() is not None

    def persistir_setup(
        self,
        id_setup: str,
        simbolo: str,
        direcao: str,
        pool_id: str,
        evento_tipo: str,
        evento_tempo: datetime,
        evento_nivel: float,
        leg_id: str | None,
        poi_fundo: float,
        poi_topo: float,
        score: int,
    ) -> None:
        agora = datetime.now(timezone.utc).isoformat()
        self._conn.execute(_SQL_INSERIR_SETUP, (
            id_setup, simbolo, direcao, pool_id, evento_tipo,
            evento_tempo.isoformat(), evento_nivel, leg_id,
            poi_fundo, poi_topo, score, agora,
        ))
        self._conn.commit()

    def carregar_setups_ativos(self, simbolo: str, cutoff: datetime) -> list[tuple]:
        cursor = self._conn.execute(_SQL_SETUPS_ATIVOS, (simbolo, cutoff.isoformat()))
        return cursor.fetchall()

    def desativar_setup(self, id_setup: str) -> None:
        self._conn.execute(_SQL_DESATIVAR_SETUP, (id_setup,))
        self._conn.commit()

    def persistir_confirmacao(
        self,
        id_conf: str,
        setup_id: str,
        simbolo: str,
        tipo: str,
        preco: float,
        tempo: datetime,
        sl: float,
        tp: float,
        rr: float,
    ) -> None:
        self._conn.execute(_SQL_INSERIR_CONFIRMACAO, (
            id_conf, setup_id, simbolo, tipo, preco, tempo.isoformat(), sl, tp, rr,
        ))
        self._conn.commit()

    def limpar_setups_antigos(self, cutoff: datetime) -> int:
        cursor = self._conn.execute(_SQL_LIMPAR_SETUPS_ANTIGOS, (cutoff.isoformat(),))
        self._conn.commit()
        return cursor.rowcount
