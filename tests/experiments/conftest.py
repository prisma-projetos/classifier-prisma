"""Os testes de experiments/ não usam banco. O tests/conftest.py raiz tem uma
fixture autouse de sessão que exige Postgres; esta versão vazia a substitui
só nesta pasta, para quem não tem o banco local (ex.: só rodando métricas)
conseguir rodar `pytest tests/experiments`."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments"))


@pytest.fixture(scope="session", autouse=True)
def _test_database_ready():
    yield
