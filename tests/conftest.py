import os
import sys
from pathlib import Path

import pytest

os.environ["PRECEDENT_IGNORE_DOTENV"] = "1"  # never let a real .env reach the tests
os.environ["MEMORY_BACKEND"] = "local"
os.environ.pop("GROQ_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.data_store import DataStore  # noqa: E402
from app.memory import LocalMemory  # noqa: E402
from app.service import PrecedentService  # noqa: E402


@pytest.fixture(scope="session")
def store():
    return DataStore()


def make_service(tmp_path, store, llm=False, memory=None):
    return PrecedentService(Settings(memory_backend="local"), store=store, memory=memory or LocalMemory(),
                            llm=llm, ledger_path=tmp_path / "ledger.json")


@pytest.fixture
def svc(tmp_path, store):
    s = make_service(tmp_path, store)
    s.reset()
    return s


@pytest.fixture(scope="session")
def replayed(tmp_path_factory, store):
    s = make_service(tmp_path_factory.mktemp("replay"), store)
    s.reset()
    s.replay()
    return s
