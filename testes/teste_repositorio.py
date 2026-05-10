from datetime import datetime, timezone
from unittest.mock import MagicMock

from smc.repositorio import Repositorio


def _repo() -> Repositorio:
    return Repositorio(":memory:")


def _vela(simbolo: str, timeframe: int, tempo_iso: str) -> tuple:
    return (simbolo, timeframe, tempo_iso, 1.1000, 1.1050, 1.0950, 1.1020, 1000)


class TestInicializacao:
    def test_cria_tabelas_sem_erro(self):
        repo = _repo()
        cursor = repo._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tabelas = {row[0] for row in cursor.fetchall()}
        assert "velas" in tabelas
        assert "sinais" in tabelas
        repo.fechar()

    def test_fechar_sem_erro(self):
        repo = _repo()
        repo.fechar()


class TestVelas:
    def test_ultimo_tempo_cache_vazio_retorna_none(self):
        repo = _repo()
        assert repo.obter_ultimo_tempo_vela("EURUSD", 16408) is None

    def test_persistir_e_obter_ultimo_tempo(self):
        repo = _repo()
        tempo = "2024-01-01T00:00:00+00:00"
        repo.persistir_velas("EURUSD", 16408, [_vela("EURUSD", 16408, tempo)])
        resultado = repo.obter_ultimo_tempo_vela("EURUSD", 16408)
        assert resultado == datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_carregar_velas_vazio_retorna_lista_vazia(self):
        repo = _repo()
        assert repo.carregar_velas("EURUSD", 16408, 10) == []

    def test_persistir_e_carregar_velas(self):
        repo = _repo()
        linhas = [_vela("EURUSD", 16408, f"2024-01-0{i+1}T00:00:00+00:00") for i in range(3)]
        repo.persistir_velas("EURUSD", 16408, linhas)
        resultado = repo.carregar_velas("EURUSD", 16408, 10)
        assert len(resultado) == 3

    def test_insert_or_ignore_sem_duplicata(self):
        repo = _repo()
        vela = _vela("EURUSD", 16408, "2024-01-01T00:00:00+00:00")
        repo.persistir_velas("EURUSD", 16408, [vela])
        repo.persistir_velas("EURUSD", 16408, [vela])
        resultado = repo.carregar_velas("EURUSD", 16408, 10)
        assert len(resultado) == 1

    def test_limite_quantidade_respeitado(self):
        repo = _repo()
        linhas = [_vela("EURUSD", 16408, f"2024-01-{i+1:02d}T00:00:00+00:00") for i in range(5)]
        repo.persistir_velas("EURUSD", 16408, linhas)
        resultado = repo.carregar_velas("EURUSD", 16408, 3)
        assert len(resultado) == 3


class TestSinais:
    def _ob(self):
        ob = MagicMock()
        ob.id = "ob1"
        ob.preco_topo = 1.1050
        ob.preco_fundo = 1.1000
        return ob

    def _fvg(self):
        fvg = MagicMock()
        fvg.id = "fvg1"
        fvg.preco_topo = 1.1040
        fvg.preco_fundo = 1.1010
        return fvg

    def test_sinal_novo_retorna_false(self):
        repo = _repo()
        assert repo.sinal_ja_disparado("abc123") is False

    def test_persistir_sinal_e_verificar(self):
        repo = _repo()
        repo.persistir_sinal("abc123", "EURUSD", self._ob(), self._fvg(), "alta")
        assert repo.sinal_ja_disparado("abc123") is True

    def test_sinais_diferentes_independentes(self):
        repo = _repo()
        repo.persistir_sinal("sinal1", "EURUSD", self._ob(), self._fvg(), "alta")
        assert repo.sinal_ja_disparado("sinal1") is True
        assert repo.sinal_ja_disparado("sinal2") is False
