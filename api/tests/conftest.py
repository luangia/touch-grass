"""Test database seeded with the pipeline's synthetic region (DECISIONS.md D-023 Q1).

A throwaway ``<POSTGRES_DB>_apitest`` database is created for the session, migrated with
the pipeline's migrations, and loaded with the synthetic region the pipeline's own tests
use (3 blocks, 2 parks, 2 neighborhoods, 2 districts), run through
process → network → analyze → metrics → load. Your loaded data is never touched.

The pipeline is a dev-only dependency. Its test helpers are imported from
``pipeline/tests`` (a package named ``tests``; this directory has no ``__init__.py``, so
the name is free).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from touchgrass_api.main import create_app
from touchgrass_api.settings import Settings

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))

REGION = "testville-zz"


@dataclass(frozen=True)
class Seeded:
    conninfo: str
    run_id: str
    populated_blocks: list[str]
    parks: int


@pytest.fixture(scope="session")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Seeded]:
    from tests.test_metrics import with_metrics  # pipeline/tests
    from tests.test_network import build_network_region
    from touchgrass.db import load as dbload
    from touchgrass.db.connection import DatabaseUnavailableError, connect, load_settings
    from touchgrass.db.migrate import migrate
    from touchgrass.pipeline.analyze import run_analysis
    from touchgrass.pipeline.metrics import run_metrics
    from touchgrass.pipeline.network import run_network

    try:
        main = load_settings()
        admin = connect(main, autocommit=True, timeout_s=3)
    except DatabaseUnavailableError as err:
        pytest.skip(f"database not reachable: {err}")
    test = main.with_dbname(f"{main.dbname}_apitest")
    assert test.dbname != main.dbname
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{test.dbname}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{test.dbname}"')

    tmp = tmp_path_factory.mktemp("region")
    cfg, paths = build_network_region(tmp)
    run_network(cfg, paths)
    analysis = run_analysis(cfg, paths, repo=tmp)
    cfg = with_metrics(cfg)
    run_metrics(cfg, paths, repo=tmp)
    with connect(test, autocommit=True) as conn:
        migrate(conn)
        dbload.load_region(conn, cfg, paths)
        blocks = [
            r[0]
            for r in conn.execute(
                "SELECT geoid20 FROM touchgrass.block_access ORDER BY geoid20"
            ).fetchall()
        ]
        (parks,) = conn.execute("SELECT count(*) FROM touchgrass.parks").fetchone() or (0,)
    yield Seeded(test.conninfo, analysis.run_id, blocks, parks)
    with connect(main, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{test.dbname}" WITH (FORCE)')


@pytest.fixture
def client(seeded: Seeded) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(conninfo=seeded.conninfo))) as c:
        yield c


def url(path: str) -> str:
    return f"/api/v1/regions/{REGION}{path}"
