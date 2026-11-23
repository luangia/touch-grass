"""``touchgrass`` command-line interface.

Stage 1 commands:
  touchgrass regions list
  touchgrass regions validate <region-id | path.yaml> [...]
  touchgrass regions show <region-id | path.yaml>

Later stages add command groups (fetch, load, analyze) to the same ``app``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from touchgrass import __version__
from touchgrass.config import ConfigError, RegionConfig, find_config_dir, list_regions, load_region

app = typer.Typer(
    help="TouchGrass geospatial pipeline.", no_args_is_help=True, add_completion=False
)
regions_app = typer.Typer(help="Inspect and validate region configs.", no_args_is_help=True)
app.add_typer(regions_app, name="regions")

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
