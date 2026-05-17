"""Tests for src/smc/modelos.py — dataclass instantiation and invariants."""

from datetime import datetime, timezone

import pytest

from smc.modelos import (
    ConfirmacaoEntrada,
    EstadoEstrutura,
    EventoEstrutura,
    LegImpulso,
    OrderBlockV2,
    PoolLiquidez,
    SetupSMC,
    SwingPoint,
    gerar_id_confirmacao,
    gerar_id_leg,
    gerar_id_pool,
    gerar_id_setup,
)

_TS = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_TS2 = datetime(2024, 1, 15, 16, 0, 0, tzinfo=timezone.utc)


def test_swing_point_instanciacao():
    sp = SwingPoint(indice=5, preco=1.1200, tipo="HIGH", tempo=_TS)
    assert sp.indice == 5
    assert sp.preco == 1.1200
    assert sp.tipo == "HIGH"
    assert sp.tempo == _TS


def test_estado_estrutura_default_indefinido():
    est = EstadoEstrutura(
        ultimo_high=1.1200,
        ultimo_low=1.1000,
        ultimo_high_tempo=_TS,
        ultimo_low_tempo=_TS,
    )
    assert est.tendencia == "INDEFINIDA"


def test_estado_estrutura_alta():
    est = EstadoEstrutura(1.15, 1.10, _TS, _TS, tendencia="ALTA")
    assert est.tendencia == "ALTA"


def test_evento_estrutura_choch_alta():
    ev = EventoEstrutura(
        simbolo="EURUSD",
        tipo="ChoCH",
        direcao="ALTA",
        nivel_rompido=1.1150,
        swing_tempo=_TS,
        tempo=_TS2,
    )
    assert ev.tipo == "ChoCH"
    assert ev.direcao == "ALTA"
    assert ev.deslocamento is False


def test_evento_estrutura_bos_baixa():
    ev = EventoEstrutura("GBPUSD", "BOS", "BAIXA", 1.2500, _TS, _TS2, deslocamento=True)
    assert ev.tipo == "BOS"
    assert ev.deslocamento is True


def test_leg_impulso_instanciacao():
    leg = LegImpulso(
        id="abc123",
        simbolo="EURUSD",
        direcao="ALTA",
        tempo_inicio=_TS,
        tempo_fim=_TS2,
        indice_inicio=3,
        indice_fim=10,
        preco_inicio=1.1000,
        preco_fim=1.1200,
        range_pontos=0.0200,
        atr_multiplo=2.5,
        proporcao_corpo=0.71,
        tem_fvg_interno=True,
        eh_displacement=True,
    )
    assert leg.eh_displacement is True
    assert leg.id == "abc123"


def test_order_block_v2_zona_50():
    ob = OrderBlockV2(
        id="ob001",
        simbolo="EURUSD",
        direcao="ALTA",
        preco_topo=1.1060,
        preco_fundo=1.0980,
        zona_50=(1.1060 + 1.0980) / 2,
        tempo=_TS,
        leg_id="leg001",
    )
    assert ob.zona_50 == pytest.approx(1.1020)
    assert ob.mitigado is False
    assert ob.testado is False


def test_pool_liquidez_eqh():
    pool = PoolLiquidez(
        id="p001",
        simbolo="EURUSD",
        tipo="EQH",
        preco=1.1200,
        tolerancia=0.0005,
        tempo=_TS,
    )
    assert pool.tipo == "EQH"
    assert pool.varredido is False


def test_setup_smc_defaults():
    setup = SetupSMC(
        id="s001",
        simbolo="EURUSD",
        direcao="ALTA",
        pool_id="p001",
        evento_tipo="ChoCH",
        evento_tempo=_TS,
        evento_nivel=1.1100,
        leg_id="leg001",
        poi_fundo=1.1000,
        poi_topo=1.1060,
        score=65,
    )
    assert setup.ativo is True
    assert setup.criado_em is not None


def test_confirmacao_entrada():
    conf = ConfirmacaoEntrada(
        id="c001",
        setup_id="s001",
        simbolo="EURUSD",
        tipo_confirmacao="MSS",
        preco_confirmacao=1.1020,
        tempo=_TS,
        sl=1.0980,
        tp=1.1100,
        rr=2.0,
    )
    assert conf.rr == 2.0
    assert conf.tipo_confirmacao == "MSS"


def test_gerar_id_leg_deterministico():
    id1 = gerar_id_leg("EURUSD", _TS, _TS2)
    id2 = gerar_id_leg("EURUSD", _TS, _TS2)
    assert id1 == id2
    assert len(id1) == 16


def test_gerar_id_pool():
    id1 = gerar_id_pool("EURUSD", "EQH", 1.1200, _TS)
    assert len(id1) == 16


def test_gerar_id_setup():
    id1 = gerar_id_setup("EURUSD", "p001", _TS)
    assert len(id1) == 20


def test_gerar_id_confirmacao():
    id1 = gerar_id_confirmacao("EURUSD", "s001", _TS)
    assert len(id1) == 20
