"""Test database seeded with the pipeline's synthetic region (DECISIONS.md D-023 Q1).

A throwaway ``<POSTGRES_DB>_apitest`` database is created for the session and loaded with
the synthetic region (see ``seeding.py``, shared with the browser smoke test). Your loaded
data is never touched.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from seeding import REGION, DatabaseUnreachableError, Seeded, drop, seed

from touchgrass_api.main import create_app
from touchgrass_api.settings import Settings

__all__ = ["REGION", "Seeded", "url"]


@pytest.fixture(scope="session")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Seeded]:
    try:
        s = seed("apitest", tmp_path_factory.mktemp("region"))
    except DatabaseUnreachableError as err:
        pytest.skip(f"database not reachable: {err}")
    yield s
    drop("apitest")


@pytest.fixture
def client(seeded: Seeded) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(conninfo=seeded.conninfo))) as c:
        yield c


def url(path: str) -> str:
    return f"/api/v1/regions/{REGION}{path}"
