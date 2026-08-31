from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atmpl.demos import seed_demo_data
from atmpl.web.app import create_app


@pytest.fixture
def seeded_engine(tmp_path: Path):
    return seed_demo_data(tmp_path / "demo.db", tmp_path / "audit.jsonl")


@pytest.fixture
def client(seeded_engine):
    return TestClient(create_app(seeded_engine))

