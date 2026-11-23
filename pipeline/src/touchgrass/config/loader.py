"""Find, parse and validate region config files.

Lookup order for the config directory (the one containing ``regions/``):
  1. an explicit ``config_dir`` argument (CLI ``--config-dir``),
  2. the ``TOUCHGRASS_CONFIG_DIR`` environment variable,
  3. the first ``config/`` with a ``regions/`` subfolder found walking up from the cwd.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from touchgrass.config.models import RegionConfig

CONFIG_DIR_ENV = "TOUCHGRASS_CONFIG_DIR"
_YAML_SUFFIXES = (".yaml", ".yml")


class ConfigError(Exception):
    """A region config could not be found, parsed, or validated.

    ``problems`` holds one human-readable line per issue, so callers can print all of them.
    """

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


def find_config_dir(start: Path | None = None) -> Path:
    env = os.environ.get(CONFIG_DIR_ENV)
    if env:
        path = Path(env).expanduser().resolve()
        if not (path / "regions").is_dir():
            raise ConfigError(str(path), [f"{CONFIG_DIR_ENV} has no 'regions/' subdirectory"])
        return path

    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / "config"
        if (candidate / "regions").is_dir():
            return candidate
    raise ConfigError(
        str(here),
        ["no config/regions/ directory found here or in any parent; use --config-dir"],
    )


def list_regions(config_dir: Path) -> list[str]:
    regions_dir = config_dir / "regions"
    return sorted(p.stem for p in regions_dir.iterdir() if p.suffix in _YAML_SUFFIXES)


def _resolve(region: str, config_dir: Path | None) -> Path:
    """Accept either a region id ('springfield-il') or a path to a YAML file."""
    as_path = Path(region)
    if as_path.suffix in _YAML_SUFFIXES or os.sep in region:
        return as_path.expanduser().resolve()
    directory = config_dir or find_config_dir()
    for suffix in _YAML_SUFFIXES:
        candidate = directory / "regions" / f"{region}{suffix}"
        if candidate.is_file():
            return candidate
    available = ", ".join(list_regions(directory)) or "none"
    raise ConfigError(region, [f"no config for region '{region}' (available: {available})"])


def _format_validation_error(err: ValidationError) -> list[str]:
    lines = []
    for e in err.errors():
        loc = ".".join(str(part) for part in e["loc"]) or "<root>"
        msg = e["msg"].removeprefix("Value error, ")
        lines.append(f"{loc}: {msg}")
    return lines


def load_region(region: str, config_dir: Path | None = None) -> RegionConfig:
    path = _resolve(region, config_dir)
    if not path.is_file():
        raise ConfigError(str(path), ["file not found"])

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))  # safe_load: no code execution
    except yaml.YAMLError as exc:
        raise ConfigError(str(path), [f"invalid YAML: {exc}"]) from exc

    if not isinstance(raw, dict):
        raise ConfigError(str(path), ["top level must be a mapping of keys to values"])

    try:
        config = RegionConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(str(path), _format_validation_error(exc)) from exc

    if config.region_id != path.stem:
        raise ConfigError(
            str(path),
            [f"region_id '{config.region_id}' must match the file name '{path.stem}'"],
        )
    return config
