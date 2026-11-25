"""Seed a throwaway database with the pipeline's synthetic region (D-023 Q1, D-026 Q5).

Shared by the API tests (``conftest.py``) and the browser smoke test (``e2e/serve.py``):
the synthetic region (3 blocks, 2 parks, 2 neighborhoods, 2 districts) goes through
process → network → analyze → metrics → load into ``<POSTGRES_DB>_<suffix>``. Your loaded
data is never touched.

The pipeline is a dev-only dependency. Its test helpers are imported from
``pipeline/tests`` (a package named ``tests``; this directory has no ``__init__.py``, so
the name is free).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "pipeline") not in sys.path:
    sys.path.insert(0, str(REPO / "pipeline"))

REGION = "testville-zz"


@dataclass(frozen=True)
class Seeded:
    conninfo: str
    run_id: str
    populated_blocks: list[str]
    parks: int


class DatabaseUnreachableError(RuntimeError):
    pass


def seed(suffix: str, workdir: Path) -> Seeded:
    """Create (or recreate) ``<POSTGRES_DB>_<suffix>`` and load the synthetic region."""
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
        raise DatabaseUnreachableError(str(err)) from err
    test = main.with_dbname(f"{main.dbname}_{suffix}")
    assert test.dbname != main.dbname
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{test.dbname}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{test.dbname}"')

    cfg, paths = build_network_region(workdir)
    run_network(cfg, paths)
    analysis = run_analysis(cfg, paths, repo=workdir)
    cfg = with_metrics(cfg)
    run_metrics(cfg, paths, repo=workdir)
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
    return Seeded(test.conninfo, analysis.run_id, blocks, parks)


def drop(suffix: str) -> None:
    from touchgrass.db.connection import connect, load_settings

    main = load_settings()
    with connect(main, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{main.dbname}_{suffix}" WITH (FORCE)')
