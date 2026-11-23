"""PostGIS integration tests (marker ``db``): migrations, constraints, loading, parity, index use.

They run against a separate ``<POSTGRES_DB>_test`` database that is created at the start
of the session and dropped at the end, so your loaded data is never touched (D-019 Q6).
If the database server isn't reachable, every test here is skipped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import psycopg
import pytest

from touchgrass.config import RegionConfig, find_config_dir, load_region
from touchgrass.db import load as dbload
from touchgrass.db.connection import DatabaseUnavailableError, DbSettings, connect, load_settings
from touchgrass.db.migrate import MigrationError, discover, migrate, status
from touchgrass.paths import RegionPaths, resolve_data_dir
from touchgrass.pipeline.process import run_process

from .test_pipeline_parks import build_park_raw, parks_config

pytestmark = pytest.mark.db
Conn = psycopg.Connection[Any]


# ------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def test_settings() -> Iterator[DbSettings]:
    try:
        main = load_settings()
        admin = connect(main, autocommit=True, timeout_s=3)
    except DatabaseUnavailableError as err:
        pytest.skip(f"database not reachable: {err}")
    test = main.with_dbname(f"{main.dbname}_test")
    assert test.dbname.endswith("_test") and test.dbname != main.dbname  # never the real one
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{test.dbname}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{test.dbname}"')
    yield test
    with connect(main, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{test.dbname}" WITH (FORCE)')


@pytest.fixture
def empty_db(test_settings: DbSettings) -> Iterator[Conn]:
    """A connection to the test database with no touchgrass schema at all."""
    with connect(test_settings, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS touchgrass CASCADE")
        yield conn


@pytest.fixture
def db(empty_db: Conn) -> Conn:
    """The test database, fully migrated and empty."""
    migrate(empty_db)
    return empty_db


def _synthetic(tmp_path: Path, region_id: str = "testville-zz") -> tuple[RegionConfig, RegionPaths]:
    cfg = parks_config(["West Woods"])
    if region_id != cfg.region_id:
        cfg = RegionConfig.model_validate({**cfg.model_dump(mode="json"), "region_id": region_id})
    paths = RegionPaths(cfg.region_id, tmp_path / "data")
    build_park_raw(cfg, paths)
    run_process(cfg, paths)
    return cfg, paths


# ----------------------------------------------------------------------- migrations


def test_migrate_builds_schema_then_is_a_noop(empty_db: Conn) -> None:
    applied = migrate(empty_db)
    assert [m.version for m in applied] == [1, 2, 3, 4]
    assert migrate(empty_db) == []
    assert all(s.applied for s in status(empty_db))
    tables = {
        r[0]
        for r in empty_db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'touchgrass'"
        )
    }
    assert {"regions", "census_blocks", "acs_estimates", "parks", "schema_migrations"} <= tables


def test_edited_applied_migration_is_refused(db: Conn) -> None:
    edited = [replace(m, sha256="0" * 64) if m.version == 2 else m for m in discover()]
    with pytest.raises(MigrationError, match=r"0002_census\.sql was changed"):
        migrate(db, edited)


def test_database_newer_than_code_is_refused(db: Conn) -> None:
    with pytest.raises(MigrationError, match="no file here"):
        status(db, discover()[:3])


def test_failed_migration_rolls_back(empty_db: Conn) -> None:
    migrations = discover()
    broken = replace(migrations[3], sql="CREATE TABLE touchgrass.half_done (x int); SELECT 1/0;")
    with pytest.raises(MigrationError, match="0004"):
        migrate(empty_db, [*migrations[:3], broken])
    assert [s.applied for s in status(empty_db, migrations)] == [True, True, True, False]
    exists = empty_db.execute("SELECT to_regclass('touchgrass.half_done')").fetchone()
    assert exists == (None,)  # DDL is transactional: the table vanished with the rollback


# ---------------------------------------------------------------------- constraints


def _region_row(conn: Conn) -> None:
    conn.execute(
        "INSERT INTO touchgrass.regions (region_id, name, analysis_crs, config)"
        " VALUES ('r', 'R', 26910, '{}')"
    )


SQUARE = "MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)))"


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        # Wrong SRID: the geometry(MultiPolygon, 4326) column type refuses it.
        (
            f"INSERT INTO touchgrass.block_groups VALUES "
            f"('r', '990010001001', ST_GeomFromText('{SQUARE}', 3857))",
            "SRID",
        ),
        # Wrong geometry type.
        (
            "INSERT INTO touchgrass.block_groups VALUES "
            "('r', '990010001001', ST_GeomFromText('POINT(0 0)', 4326))",
            "type",
        ),
        # Invalid geometry (a self-intersecting "bow tie").
        (
            "INSERT INTO touchgrass.block_groups VALUES ('r', '990010001001',"
            " ST_GeomFromText('MULTIPOLYGON(((0 0,1 1,1 0,0 1,0 0)))', 4326))",
            "block_groups_geom_check",
        ),
        # Negative population.
        (
            f"INSERT INTO touchgrass.census_blocks VALUES ('r', '990010001001001', '001', -1, 0,"
            f" 0, 0, 1, 1, ST_SetSRID(ST_MakePoint(0.5, 0.5), 4326), 'x',"
            f" ST_GeomFromText('{SQUARE}', 4326))",
            "pop20_check",
        ),
        # Excluded candidate that claims a park class.
        (
            f"INSERT INTO touchgrass.green_candidates VALUES ('r', 'way', 1, NULL, '{{}}', false,"
            f" 'park', 'not_open_space', NULL, 1, ST_GeomFromText('{SQUARE}', 4326))",
            "green_candidates_check",
        ),
        # Membership pointing at an area that doesn't exist.
        (
            "INSERT INTO touchgrass.area_membership VALUES ('r', 'nope:1', '990010001001001')",
            "foreign key",
        ),
    ],
)
def test_database_rejects_bad_rows(db: Conn, sql: str, error: str) -> None:
    _region_row(db)
    with pytest.raises(psycopg.Error, match=error):
        db.execute(sql)


# -------------------------------------------------------------------------- loading


def test_load_counts_match_outputs(db: Conn, tmp_path: Path) -> None:
    cfg, paths = _synthetic(tmp_path)
    result = dbload.load_region(db, cfg, paths)
    frames = {n: pd.read_parquet(p) for n, p in _outputs(paths).items()}
    assert result.rows == {
        "datasets": len(dbload.provenance(cfg, paths)),
        "study_boundaries": 1,
        "analysis_areas": len(frames["analysis_areas"]),
        "census_blocks": len(frames["blocks"]),
        "area_membership": len(frames["area_membership"]),
        "block_groups": len(frames["block_groups"]),
        "acs_estimates": len(frames["block_groups"]),  # 1 ACS variable: B01003_001
        "green_candidates": len(frames["green_candidates"]),
        "parks": len(frames["parks"]),
    }
    assert dbload.row_counts(db)[cfg.region_id] == result.rows

    park = db.execute(
        "SELECT member_ids, ST_SRID(geom), ST_Area(geom::geography) FROM touchgrass.parks"
    ).fetchone()
    assert park is not None
    assert park[0] == frames["parks"].iloc[0]["member_ids"].split(";")
    assert park[1] == 4326
    # Geodesic area in SQL ≈ the UTM area computed in Python (UTM scale error < 0.1 %).
    assert park[2] == pytest.approx(frames["parks"].iloc[0]["area_m2"], rel=1e-3)

    tags = db.execute(
        "SELECT tags->>'leisure' FROM touchgrass.green_candidates WHERE name = 'Links'"
    ).fetchone()
    assert tags == ("golf_course",)  # tags are real jsonb, queryable by key


def _outputs(paths: RegionPaths) -> dict[str, Path]:
    names = ["analysis_areas", "blocks", "area_membership", "block_groups"]
    names += ["green_candidates", "parks"]
    return {n: paths.processed / f"{n}.parquet" for n in names}


def test_reload_is_idempotent_and_regions_are_isolated(db: Conn, tmp_path: Path) -> None:
    cfg_a, paths_a = _synthetic(tmp_path / "a")
    cfg_b, paths_b = _synthetic(tmp_path / "b", region_id="otherville-zz")
    dbload.load_region(db, cfg_a, paths_a)
    dbload.load_region(db, cfg_b, paths_b)
    first_a = dbload.content_digests(db, cfg_a.region_id)
    first_b = dbload.content_digests(db, cfg_b.region_id)

    dbload.load_region(db, cfg_a, paths_a)  # reload A only
    assert dbload.content_digests(db, cfg_a.region_id) == first_a
    assert dbload.content_digests(db, cfg_b.region_id) == first_b  # B untouched


def test_failed_load_keeps_previous_data(
    db: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, paths = _synthetic(tmp_path)
    dbload.load_region(db, cfg, paths)
    before = dbload.content_digests(db, cfg.region_id)

    def boom(*_args: Any) -> int:
        raise RuntimeError("disk full (simulated)")

    # Fails after the region was deleted and most tables were re-inserted in the transaction.
    monkeypatch.setattr(dbload, "_load_parks", boom)
    with pytest.raises(RuntimeError, match="simulated"):
        dbload.load_region(db, cfg, paths)
    assert dbload.content_digests(db, cfg.region_id) == before


def test_load_refuses_unvalidated_outputs(db: Conn, tmp_path: Path) -> None:
    cfg, paths = _synthetic(tmp_path)
    blocks = gpd.read_parquet(paths.processed / "blocks.parquet")
    blocks.loc[0, "pop20"] += 1  # breaks the population reconciliation
    blocks.to_parquet(paths.processed / "blocks.parquet", index=False)
    with pytest.raises(dbload.LoadError, match="fail validation"):
        dbload.load_region(db, cfg, paths)
    assert dbload.row_counts(db) == {}


def test_load_refuses_unmigrated_database(empty_db: Conn, tmp_path: Path) -> None:
    cfg, paths = _synthetic(tmp_path)
    with pytest.raises(MigrationError, match="pending"):
        dbload.load_region(empty_db, cfg, paths)


def test_acs_long_keeps_nulls() -> None:
    wide = pd.DataFrame(
        {"geoid": ["1", "2"], "b19013_e001": [50000.0, None], "b19013_m001": [4000.0, None]}
    )
    long = dbload.acs_long(wide)
    assert long["variable"].tolist() == ["b19013_001", "b19013_001"]
    assert long["estimate"].isna().tolist() == [False, True]


# --------------------------------------------------------- real data: parity + index


def _real_region() -> tuple[RegionConfig, RegionPaths] | None:
    try:
        config_dir = find_config_dir()
    except Exception:
        return None
    data_dir = resolve_data_dir(config_dir)
    for region_id in sorted(p.name for p in (data_dir / "processed").glob("*") if p.is_dir()):
        paths = RegionPaths(region_id, data_dir)
        if (paths.processed / "validation.json").is_file():
            return load_region(region_id, config_dir), paths
    return None


REAL = _real_region()
MEMBERSHIP_SQL = """
    SELECT a.area_id, b.geoid20
    FROM touchgrass.census_blocks b
    JOIN touchgrass.analysis_areas a
      ON a.region_id = b.region_id AND ST_Intersects(b.rep_point, a.geom)
    WHERE b.region_id = %(region)s
"""


@pytest.fixture(scope="module")
def real_db(test_settings: DbSettings) -> Iterator[tuple[Conn, RegionConfig, RegionPaths]]:
    if REAL is None:
        pytest.skip("no processed region; run `touchgrass process <region>`")
    cfg, paths = REAL
    with connect(test_settings, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS touchgrass CASCADE")
        migrate(conn)
        dbload.load_region(conn, cfg, paths)
        yield conn, cfg, paths


@pytest.mark.data
def test_postgis_membership_equals_geopandas(
    real_db: tuple[Conn, RegionConfig, RegionPaths],
) -> None:
    conn, cfg, paths = real_db
    in_sql = set(conn.execute(MEMBERSHIP_SQL, {"region": cfg.region_id}).fetchall())
    stage2 = pd.read_parquet(paths.processed / "area_membership.parquet")
    in_python = set(zip(stage2["area_id"], stage2["geoid20"], strict=True))
    assert in_sql == in_python  # same GEOS predicate, same answer, row for row


def _plan_nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("Plans", []):
        yield from _plan_nodes(child)


@pytest.mark.data
def test_planner_uses_a_gist_index_for_the_join(
    real_db: tuple[Conn, RegionConfig, RegionPaths],
) -> None:
    conn, cfg, _ = real_db
    row = conn.execute(f"EXPLAIN (FORMAT JSON) {MEMBERSHIP_SQL}", {"region": cfg.region_id})
    plan = row.fetchone()
    assert plan is not None
    root = (plan[0] if isinstance(plan[0], list) else json.loads(plan[0]))[0]["Plan"]
    indexes = {n.get("Index Name") for n in _plan_nodes(root)}
    assert indexes & {"analysis_areas_geom_gist", "census_blocks_rep_point_gist"}, indexes
