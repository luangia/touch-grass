"""``touchgrass`` command-line interface.

Stage 1 commands:
  touchgrass regions list
  touchgrass regions validate <region-id | path.yaml> [...]
  touchgrass regions show <region-id | path.yaml>

Stage 2 commands:
  touchgrass fetch <region-id> [--refresh]     download raw inputs (network)
  touchgrass process <region-id>               raw → GeoParquet + validation (offline)
  touchgrass validate <region-id>              re-check existing processed outputs

Stage 4 commands (PostGIS; settings from $DATABASE_URL or the repo's .env):
  touchgrass db migrate                        create/upgrade the schema
  touchgrass db status                         migrations + row counts per region
  touchgrass db create-reader                  the API's SELECT-only role (Stage 11)
  touchgrass load <region-id>                  replace a region's rows, one transaction

Stage 5 commands:
  touchgrass network <region-id>               walk graph, block origins, park access points

Stage 6 commands:
  touchgrass analyze <region-id>               walking distance from every block to a park

Stage 7 commands:
  touchgrass metrics <region-id>               area metrics, ACS context, report

Stage 11 commands:
  touchgrass reference <region-id> [--write]   compare with results/<id>/reference.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from touchgrass import __version__
from touchgrass.config import ConfigError, RegionConfig, find_config_dir, list_regions, load_region
from touchgrass.db import connection as dbconn
from touchgrass.db import migrate as dbmigrate
from touchgrass.db.load import LoadError, row_counts
from touchgrass.db.load import load_region as db_load_region
from touchgrass.paths import RegionPaths, resolve_data_dir
from touchgrass.pipeline import reference as ref
from touchgrass.pipeline.analyze import run_analysis
from touchgrass.pipeline.fetch import run_fetch
from touchgrass.pipeline.metrics import METRICS_FILES, run_metrics
from touchgrass.pipeline.network import run_network
from touchgrass.pipeline.process import MissingDataError, load_outputs, load_stats, run_process
from touchgrass.pipeline.validate import Check, failed, run_validation, write_report

app = typer.Typer(
    help="TouchGrass geospatial pipeline.", no_args_is_help=True, add_completion=False
)
regions_app = typer.Typer(help="Inspect and validate region configs.", no_args_is_help=True)
app.add_typer(regions_app, name="regions")
db_app = typer.Typer(help="PostGIS schema: migrations and status.", no_args_is_help=True)
app.add_typer(db_app, name="db")

ConfigDirOption = Annotated[
    Path | None,
    typer.Option(
        "--config-dir",
        help="Directory containing regions/ (default: $TOUCHGRASS_CONFIG_DIR or nearest config/).",
        file_okay=False,
        exists=True,
    ),
]


def _print_errors(err: ConfigError) -> None:
    count = len(err.problems)
    typer.secho(f"✗ {err.source}: {count} error{'s' if count != 1 else ''}", fg="red", err=True)
    for problem in err.problems:
        typer.echo(f"  {problem}", err=True)


def _summary(cfg: RegionConfig) -> list[str]:
    crs = cfg.crs()
    units = crs.axis_info[0].unit_name
    a = cfg.analysis
    mins = " / ".join(str(t) for t in a.thresholds_min)
    meters = " / ".join(f"{d:.0f}" for d in a.threshold_distances_m.values())
    return [
        f"  analysis CRS: EPSG:{cfg.analysis_crs} — {crs.name} ({units})",
        f"  thresholds:   {mins} min → {meters} m at {a.walk_speed_kmh:g} km/h",
        f"  buffer:       {cfg.buffer_m:g} m",
        f"  census:       state {cfg.state_fips}, counties {', '.join(cfg.county_fips)}",
    ]


@app.callback(invoke_without_command=True)
def _main(
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if version:
        typer.echo(f"touchgrass {__version__}")
        raise typer.Exit()


@regions_app.command("list")
def regions_list(config_dir: ConfigDirOption = None) -> None:
    """List region ids that have a config file."""
    try:
        directory = config_dir or find_config_dir()
    except ConfigError as err:
        _print_errors(err)
        raise typer.Exit(code=1) from err
    for region_id in list_regions(directory):
        typer.echo(region_id)


@regions_app.command("validate")
def regions_validate(
    regions: Annotated[list[str], typer.Argument(help="Region ids or YAML file paths.")],
    config_dir: ConfigDirOption = None,
) -> None:
    """Validate one or more region configs. Exits 1 if any is invalid."""
    failed = False
    for region in regions:
        try:
            cfg = load_region(region, config_dir)
        except ConfigError as err:
            _print_errors(err)
            failed = True
            continue
        typer.secho(f"✓ {cfg.region_id} ({cfg.name})", fg="green")
        for line in _summary(cfg):
            typer.echo(line)
    if failed:
        raise typer.Exit(code=1)


@regions_app.command("show")
def regions_show(
    region: Annotated[str, typer.Argument(help="Region id or YAML file path.")],
    config_dir: ConfigDirOption = None,
) -> None:
    """Print the fully-resolved config (defaults filled in) as JSON."""
    try:
        cfg = load_region(region, config_dir)
    except ConfigError as err:
        _print_errors(err)
        raise typer.Exit(code=1) from err
    typer.echo(cfg.model_dump_json(indent=2))


# --------------------------------------------------------------------- Stage 2: data

DataDirOption = Annotated[
    Path | None,
    typer.Option(
        "--data-dir",
        help="Data directory (default: $TOUCHGRASS_DATA_DIR or <repo>/data).",
        file_okay=False,
    ),
]


def _region_and_paths(
    region: str, config_dir: Path | None, data_dir: Path | None
) -> tuple[RegionConfig, RegionPaths]:
    try:
        directory = config_dir or find_config_dir()
        cfg = load_region(region, directory)
    except ConfigError as err:
        _print_errors(err)
        raise typer.Exit(code=1) from err
    return cfg, RegionPaths(cfg.region_id, resolve_data_dir(directory, data_dir))


def _report(cfg: RegionConfig, checks: list[Check], out_dir: Path) -> None:
    _print_checks(checks, write_report(cfg, checks, out_dir))


def _print_checks(checks: list[Check], path: Path) -> None:
    for c in checks:
        if c.severity == "hard":
            mark, color = ("✓", "green") if c.passed else ("✗", "red")
            line = f"{mark} {c.name}"
            if not c.passed:
                line += f"  expected={c.expected} actual={c.actual} {c.details or ''}"
            typer.secho(line, fg=color)
        elif c.name != "process.stats":
            typer.echo(f"• {c.name}: {c.actual}")
    bad = failed(checks)
    summary = f"{len(bad)} hard check(s) failed" if bad else "all hard checks passed"
    typer.secho(f"\n{summary}; report: {path}", fg="red" if bad else "green")
    if bad:
        raise typer.Exit(code=1)


@app.command("fetch")
def fetch_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    refresh: Annotated[bool, typer.Option("--refresh", help="Re-download cached files.")] = False,
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Download every raw input for a region, with checksums and manifests."""
    cfg, paths = _region_and_paths(region, config_dir, data_dir)
    results = run_fetch(cfg, paths, refresh=refresh, progress=typer.echo)
    downloaded = sum(r.downloaded for r in results)
    typer.secho(
        f"\n{len(results)} sources ({downloaded} downloaded, {len(results) - downloaded} cached)"
        f" in {paths.raw}",
        fg="green",
    )


@app.command("process")
def process_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Build clean GeoParquet outputs from raw files (offline), then validate them."""
    cfg, paths = _region_and_paths(region, config_dir, data_dir)
    try:
        result = run_process(cfg, paths, progress=lambda m: typer.echo(f"→ {m}"))
    except MissingDataError as err:
        typer.secho(f"✗ {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err
    typer.echo(f"wrote {len(result.frames)} files to {paths.processed}\n")
    _report(cfg, run_validation(cfg, result.frames, result.stats), paths.processed)


@app.command("validate")
def validate_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Re-run validation on existing processed outputs."""
    cfg, paths = _region_and_paths(region, config_dir, data_dir)
    try:
        frames = load_outputs(paths.processed)
    except MissingDataError as err:
        typer.secho(f"✗ {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err
    _report(cfg, run_validation(cfg, frames, load_stats(paths.processed)), paths.processed)


# ------------------------------------------------------------------ Stage 4: database


def _db_connect() -> psycopg.Connection[tuple[object, ...]]:
    try:
        settings = dbconn.load_settings()
        return dbconn.connect(settings, autocommit=True)
    except dbconn.DatabaseUnavailableError as err:
        typer.secho(f"✗ {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err


@db_app.command("migrate")
def db_migrate_cmd() -> None:
    """Apply pending schema migrations (each in its own transaction)."""
    with _db_connect() as conn:
        try:
            applied = dbmigrate.migrate(conn)
        except dbmigrate.MigrationError as err:
            typer.secho(f"✗ {err}", fg="red", err=True)
            raise typer.Exit(code=1) from err
    for m in applied:
        typer.secho(f"✓ applied {m.filename}", fg="green")
    typer.echo("schema is up to date" if not applied else f"{len(applied)} migration(s) applied")


@db_app.command("create-reader")
def db_create_reader_cmd(
    role: Annotated[str, typer.Option(help="Role name for the API.")] = "touchgrass_reader",
) -> None:
    """Create or update the API's SELECT-only login role (asks for its password)."""
    from touchgrass.db.roles import ensure_reader

    password = typer.prompt(f"password for {role}", hide_input=True, confirmation_prompt=True)
    with _db_connect() as conn:
        created = ensure_reader(conn, role, password)
    typer.secho(
        f"✓ {'created' if created else 'updated'} {role}: SELECT only, read-only", fg="green"
    )


@db_app.command("status")
def db_status_cmd() -> None:
    """Show applied/pending migrations and row counts per loaded region."""
    with _db_connect() as conn:
        typer.echo(f"database: {dbconn.load_settings().describe()}")
        row = conn.execute(
            "SELECT current_setting('server_version'), "
            "(SELECT extversion FROM pg_extension WHERE extname = 'postgis')"
        ).fetchone()
        if row:
            typer.echo(f"server:   PostgreSQL {row[0]}, PostGIS {row[1] or 'not installed'}")
        try:
            states = dbmigrate.status(conn)
        except dbmigrate.MigrationError as err:
            typer.secho(f"✗ {err}", fg="red", err=True)
            raise typer.Exit(code=1) from err
        for s in states:
            when = f"applied {s.applied_at:%Y-%m-%d %H:%M}" if s.applied_at else "PENDING"
            typer.echo(f"  {s.migration.filename:<24} {when}")
        if not all(s.applied for s in states):
            typer.echo("run `touchgrass db migrate` to apply pending migrations")
            return
        counts = row_counts(conn)
    if not counts:
        typer.echo("no regions loaded")
    for region_id, tables in sorted(counts.items()):
        typer.echo(f"\n{region_id}")
        for table, n in tables.items():
            typer.echo(f"  {table:<18} {n:>9,}")


@app.command("load")
def load_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Load a region's processed outputs into PostGIS, replacing its previous rows."""
    cfg, paths = _region_and_paths(region, config_dir, data_dir)
    with _db_connect() as conn:
        try:
            result = db_load_region(conn, cfg, paths, progress=lambda m: typer.echo(f"→ {m}"))
        except (LoadError, MissingDataError, dbmigrate.MigrationError) as err:
            typer.secho(f"✗ {err}", fg="red", err=True)
            raise typer.Exit(code=1) from err
    total = sum(result.rows.values())
    typer.secho(
        f"\nloaded {cfg.region_id}: {total:,} rows in {len(result.rows)} tables", fg="green"
    )


# ------------------------------------------------------------------- Stage 5: network


@app.command("network")
def network_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Build the walk graph, snap block origins and find park access points (offline)."""
    cfg, paths = _region_and_paths(region, config_dir, data_dir)
    try:
        result = run_network(cfg, paths, progress=lambda m: typer.echo(f"→ {m}"))
    except MissingDataError as err:
        typer.secho(f"✗ {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err
    g, o, a = result.report["graph"], result.report["origins"], result.report["access"]
    typer.echo(
        f"\ngraph: {g['main_nodes']:,} nodes / {g['main_km']:,} km in the main component "
        f"({g['main_component_share']:.1%} of nodes; {g['components']} pieces)"
    )
    typer.echo(
        f"origins: {o['populated_blocks']:,} blocks, {o['population_within_100m']:.2%} of "
        f"residents within 100 m; {o['stranded_population']:,} nearest to a stranded piece"
    )
    typer.echo(
        f"access: {a['points']:,} points; {a['parks_reachable']} parks reachable, "
        f"{len(a['parks_without_access'])} without network contact\n"
    )
    _print_checks(result.checks, paths.processed / "network_validation.json")


# ------------------------------------------------------------------ Stage 6: analysis


@app.command("analyze")
def analyze_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Walking distance and time from every populated block to its nearest park."""
    cfg, paths = _region_and_paths(region, config_dir, data_dir)
    repo = (config_dir or find_config_dir()).parent
    try:
        result = run_analysis(cfg, paths, repo, progress=lambda m: typer.echo(f"→ {m}"))
    except MissingDataError as err:
        typer.secho(f"✗ {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err
    s = result.record["summary"]
    typer.echo(
        f"\nrun {result.run_id}  ({result.record['seconds']['total']} s; search "
        f"{result.record['seconds']['search']} s)"
    )
    typer.echo("share of residents within (network | straight line):")
    for minutes, shares in s["share_within_minutes"].items():
        typer.echo(f"  {minutes:>2} min: {shares['network']:6.1%} | {shares['straight_line']:6.1%}")
    typer.echo(
        f"median walk {s['network_m']['median_population_weighted']} m (population-weighted); "
        f"median detour ratio {s['detour_ratio']['median']}; unreachable {len(s['unreachable'])}\n"
    )
    assert result.run_dir is not None
    _print_checks(result.checks, result.run_dir / "analysis_validation.json")


# ------------------------------------------------------------------- Stage 7: metrics


@app.command("metrics")
def metrics_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Area metrics, rankings and ACS context for the latest analysis run."""
    cfg, paths = _region_and_paths(region, config_dir, data_dir)
    repo = (config_dir or find_config_dir()).parent
    try:
        result = run_metrics(cfg, paths, repo, progress=lambda m: typer.echo(f"→ {m}"))
    except MissingDataError as err:
        typer.secho(f"✗ {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err
    s = result.record["summary"]
    typer.echo(f"\nrun {result.run_id}  ({result.record['seconds']['total']} s)")
    typer.echo(
        f"city: {s['population']:,} residents; underserved {s['underserved_pop']:,}; areas "
        + ", ".join(f"{n} {t}" for t, n in s["areas"].items())
        + f" ({s['ranked_areas']} ranked)"
    )
    assert result.run_dir is not None
    typer.echo(f"report: {result.run_dir / METRICS_FILES['report']}\n")
    _print_checks(result.checks, result.run_dir / METRICS_FILES["validation"])


# ----------------------------------------------------------------- Stage 11: reference


@app.command("reference")
def reference_cmd(
    region: Annotated[str, typer.Argument(help="Region id.")],
    write: Annotated[
        bool, typer.Option("--write", help="Write the latest run as the new reference.")
    ] = False,
    config_dir: ConfigDirOption = None,
    data_dir: DataDirOption = None,
) -> None:
    """Compare the latest run with the committed reference (exit 1 if unexplained)."""
    _, paths = _region_and_paths(region, config_dir, data_dir)
    repo = (config_dir or find_config_dir()).parent
    try:
        current = ref.build_reference(paths)
    except MissingDataError as err:
        typer.secho(f"✗ {err}", fg="red", err=True)
        raise typer.Exit(code=1) from err
    target = ref.reference_path(repo, region)
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ref.dump(current), "utf-8")
        typer.secho(f"✓ wrote {target}", fg="green")
        return
    if not target.is_file():
        typer.secho(f"✗ no reference at {target}: run with --write first", fg="red", err=True)
        raise typer.Exit(code=1)
    c = ref.compare(json.loads(target.read_text("utf-8")), current)
    if c.run_note:
        typer.echo(f"note: {c.run_note}")
    if c.reproduced:
        typer.secho("✓ reproduced: every source checksum and headline figure matches", fg="green")
        return
    for s in c.changed_sources:
        typer.echo(f"  source {s}")
    for f in c.changed_figures:
        typer.echo(f"  figure {f}")
    if c.unexplained:
        typer.secho("✗ figures changed although every source matches: not reproduced", fg="red")
        raise typer.Exit(code=1)
    typer.secho(
        "! figures differ, explained by the changed sources above (live layers can be edited)",
        fg="yellow",
    )
