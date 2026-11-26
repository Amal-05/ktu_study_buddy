import os
from tempfile import TemporaryDirectory

import pytest

TEST_DATA = TemporaryDirectory(prefix="ktu-buddy-tests-")
os.environ["BUDDY_DATA"] = TEST_DATA.name
os.environ["BUDDY_MOCK"] = "1"
os.environ["BUDDY_EMBEDDINGS"] = "lexical"
os.environ["SEARCH_MOCK"] = "1"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.server import app

    with TestClient(app, base_url="http://localhost") as client:
        yield client


def pytest_sessionfinish(session, exitstatus):
    from backend.db import engine
    engine.dispose()
    TEST_DATA.cleanup()
