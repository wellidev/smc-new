from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SwingPoint:
    indice: int
    preco: float
    tipo: str          # "HIGH" | "LOW"
    tempo: datetime


@dataclass
class EstadoEstrutura:
    ultimo_high: float
    ultimo_low: float
    ultimo_high_tempo: datetime
    ultimo_low_tempo: datetime
    tendencia: str = "INDEFINIDA"   # "ALTA" | "BAIXA" | "INDEFINIDA"


@dataclass
class EventoEstrutura:
    simbolo: str
    tipo: str          # "ChoCH" | "BOS"
    direcao: str       # "ALTA" | "BAIXA"
    nivel_rompido: float
    swing_tempo: datetime
    tempo: datetime
    deslocamento: bool = False


@dataclass
class LegImpulso:
    id: str
    simbolo: str
    direcao: str       # "ALTA" | "BAIXA"
    tempo_inicio: datetime
    tempo_fim: datetime
    indice_inicio: int
    indice_fim: int
    preco_inicio: float
    preco_fim: float
    range_pontos: float
    atr_multiplo: float
    proporcao_corpo: float
    tem_fvg_interno: bool
    eh_displacement: bool


@dataclass
class OrderBlockV2:
    id: str
    simbolo: str
    direcao: str       # "ALTA" | "BAIXA"
    preco_topo: float
    preco_fundo: float
    zona_50: float
    tempo: datetime
    leg_id: str
    mitigado: bool = False
    testado: bool = False


@dataclass
class PoolLiquidez:
    id: str
    simbolo: str
    tipo: str          # "EQH" | "EQL" | "PDH" | "PDL"
    preco: float
    tolerancia: float
    tempo: datetime
    varredido: bool = False


@dataclass
class SetupSMC:
    id: str
    simbolo: str
    direcao: str       # "ALTA" | "BAIXA"
    pool_id: str
    evento_tipo: str   # "ChoCH" | "BOS"
    evento_tempo: datetime
    evento_nivel: float
    leg_id: str | None
    poi_fundo: float
    poi_topo: float
    score: int
    ativo: bool = True
    criado_em: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ConfirmacaoEntrada:
    id: str
    setup_id: str
    simbolo: str
    tipo_confirmacao: str   # "MSS" | "DIRETO"
    preco_confirmacao: float
    tempo: datetime
    sl: float
    tp: float
    rr: float


def gerar_id_leg(simbolo: str, tempo_inicio: datetime, tempo_fim: datetime) -> str:
    chave = f"{simbolo}|leg|{tempo_inicio.isoformat()}|{tempo_fim.isoformat()}"
    return hashlib.sha1(chave.encode()).hexdigest()[:16]


def gerar_id_pool(simbolo: str, tipo: str, preco: float, tempo: datetime) -> str:
    # tempo excluído: o mesmo nível de preço deve gerar o mesmo pool_id
    # independente do swing que o identificou (evita setup_ids instáveis entre ciclos)
    chave = f"{simbolo}|{tipo}|{round(preco, 5)}"
    return hashlib.sha1(chave.encode()).hexdigest()[:16]


def gerar_id_setup(simbolo: str, ancora_id: str, evento_tempo: datetime) -> str:
    # ancora_id deve ser leg.id (estável) — não pool.id (instável por ATR variável)
    chave = f"{simbolo}|{ancora_id}|{evento_tempo.isoformat()}"
    return hashlib.sha1(chave.encode()).hexdigest()[:20]


def gerar_id_confirmacao(simbolo: str, setup_id: str, tempo: datetime) -> str:
    chave = f"{simbolo}|{setup_id}|{tempo.isoformat()}"
    return hashlib.sha1(chave.encode()).hexdigest()[:20]
