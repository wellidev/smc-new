from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from smc.analisador_smc import CapturaLiquidez, FairValueGap, OrderBlock, QuebraEstrutura
from smc.principal import Confluencia, _calcular_contexto, _construir_mensagem, _encontrar_confluencias

_T0 = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)   # London session, before T1
_T1 = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)   # London session, capture time


def _captura(direcao: str = "ALTA") -> CapturaLiquidez:
    return CapturaLiquidez(
        simbolo="EURUSD", direcao=direcao,
        preco_varredura=1.1050, tempo=_T1, pavio_percentual=0.45,
    )


def _bos(direcao: str = "ALTA") -> QuebraEstrutura:
    return QuebraEstrutura(
        simbolo="EURUSD", direcao=direcao,
        nivel_rompido=1.1050, swing_tempo=_T0, tempo=_T1, deslocamento=False,
    )


def _ob(direcao: str = "ALTA", tempo: datetime = _T0) -> OrderBlock:
    return OrderBlock(
        id="ob_1", simbolo="EURUSD", direcao=direcao,
        preco_topo=1.1060, preco_fundo=1.1000, tempo=tempo,
    )


def _fvg(direcao: str = "ALTA", tempo: datetime = _T0) -> FairValueGap:
    return FairValueGap(
        id="fvg_1", simbolo="EURUSD", direcao=direcao,
        preco_topo=1.1050, preco_fundo=1.1010, tempo=tempo,
    )


def _conf(
    direcao: str = "ALTA",
    ob_tempo: datetime = _T0,
    fvg_tempo: datetime = _T0,
) -> Confluencia:
    return Confluencia(
        captura=_captura(direcao),
        bos=_bos(direcao),
        ob=_ob(direcao, ob_tempo),
        fvg=_fvg(direcao, fvg_tempo),
        overlap_fundo=1.1010,
        overlap_topo=1.1050,
    )


# ---------------------------------------------------------------------------
# _encontrar_confluencias
# ---------------------------------------------------------------------------

class TestEncontrarConfluencias:
    def _run(self, captura, bos, ob, fvg, preco_atual):
        with patch("smc.principal.verificar_confluencia", return_value=True):
            return _encontrar_confluencias([captura], [bos], [ob], [fvg], preco_atual)

    def test_confluencia_valida_retorna_uma(self):
        # P1: OB∩FVG com overlap [1.1010, 1.1050], preço 1.1030 dentro
        result = self._run(_captura(), _bos(), _ob(), _fvg(), preco_atual=1.1030)
        assert len(result) == 1
        assert result[0].overlap_fundo == pytest.approx(1.1010)
        assert result[0].overlap_topo == pytest.approx(1.1050)

    def test_preco_fora_do_ob_retorna_vazio(self):
        # P2: preço 1.1100 > ob.preco_topo 1.1060 → fora do OB
        result = self._run(_captura(), _bos(), _ob(), _fvg(), preco_atual=1.1100)
        assert result == []

    def test_ob_direcao_oposta_retorna_vazio(self):
        # P3: OB "BAIXA" != captura "ALTA"
        result = self._run(_captura("ALTA"), _bos("ALTA"), _ob("BAIXA"), _fvg("ALTA"), preco_atual=1.1030)
        assert result == []

    def test_ob_posterior_a_captura_aceito(self):
        # P4 (revisado): OBs formados após a captura são válidos (impulso pós-captura)
        result = self._run(_captura(), _bos(), _ob(tempo=_T1), _fvg(), preco_atual=1.1030)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _calcular_contexto
# ---------------------------------------------------------------------------

class TestCalcularContexto:
    def test_d1_none_retorna_checks_neutros(self):
        # P5: sem D1 → bias e zona ficam como ⚠️
        conf = _conf()
        ctx = _calcular_contexto(conf, preco_atual=1.1030, velas_d1=None)
        assert ctx["check_bias"] == "⚠️ Neutro"
        assert ctx["check_zona"] == "⚠️ Sem confluência"

    def test_d1_none_sessao_london_positiva(self):
        conf = _conf()  # captura.tempo = 09:00 UTC → London
        ctx = _calcular_contexto(conf, preco_atual=1.1030, velas_d1=None)
        assert ctx["check_sessao"] == "✅ London/NY"

    def test_bos_sem_deslocamento_retorna_normal(self):
        conf = _conf()  # bos.deslocamento = False
        ctx = _calcular_contexto(conf, preco_atual=1.1030, velas_d1=None)
        assert ctx["bos_qualidade"] == "Normal"

    def test_sl_tp_calculados(self):
        # ALTA: sl = ob.preco_fundo=1.1000, risco=1.1030-1.1000=0.003, tp=1.1030+0.006=1.1090
        conf = _conf()
        ctx = _calcular_contexto(conf, preco_atual=1.1030, velas_d1=None)
        assert ctx["sl"] == pytest.approx(1.1000)
        assert ctx["tp"] == pytest.approx(1.1090)
        assert ctx["rr"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# _construir_mensagem
# ---------------------------------------------------------------------------

class TestConstruirMensagem:
    def _ctx(self) -> dict:
        return {
            "check_sessao": "✅ London/NY",
            "check_bias": "✅ Alinhado",
            "check_zona": "✅ Desconto",
            "bos_qualidade": "Normal",
            "sl": 1.1000,
            "tp": 1.1090,
            "rr": 2.0,
        }

    def test_mensagem_contem_simbolo_direcao_sl(self):
        # P6: mensagem deve referenciar o símbolo, direção e linha de SL/TP
        conf = _conf()
        msg = _construir_mensagem("EURUSD", conf, self._ctx())
        assert "EURUSD" in msg
        assert "ALTA" in msg
        assert "SL:" in msg
