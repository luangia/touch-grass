"""Guard for the multi-region requirement: application code must not name any region.

Region-specific values are read from every config in config/regions/, so this check
grows automatically as cities are added. Comments and docstrings are included on purpose:
a city name in a comment is usually a sign that its logic leaked into shared code.
"""

from __future__ import annotations

from pathlib import Path

from touchgrass.config import list_regions, load_region

from .conftest import REPO_CONFIG_DIR

SRC = Path(__file__).resolve().parents[1] / "src" / "touchgrass"


def _forbidden_tokens() -> set[str]:
    tokens: set[str] = set()
    for region_id in list_regions(REPO_CONFIG_DIR):
        cfg = load_region(region_id, REPO_CONFIG_DIR)
        tokens.add(cfg.region_id)
        tokens.add(str(cfg.analysis_crs))
        tokens.add(cfg.name.split(",")[0].strip().lower())  # e.g. the city name
        if cfg.validation.expected_population:
            tokens.add(str(cfg.validation.expected_population))  # official totals belong in config
    return tokens


def test_source_names_no_region() -> None:
    tokens = _forbidden_tokens()
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        offenders += [f"{path.relative_to(SRC)}: '{t}'" for t in tokens if t in text]
    assert not offenders, "region-specific values in shared code:\n" + "\n".join(offenders)
