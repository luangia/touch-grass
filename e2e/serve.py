"""Seed the synthetic region into ``<POSTGRES_DB>_e2e`` and serve the API on it (D-026 Q5).

Started by Playwright (``playwright.config.ts``) with the API's environment:
    uv run --project ../api python serve.py          seed, then serve on :8100
    uv run --project ../api python serve.py --drop   drop the throwaway database

uvicorn re-raises SIGTERM after shutting down, so cleanup can't live in a ``finally``
here; Playwright's global teardown (``teardown.ts``) runs ``--drop`` instead.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api" / "tests"))

import uvicorn  # noqa: E402
from seeding import drop, seed  # noqa: E402

from touchgrass_api.main import create_app  # noqa: E402
from touchgrass_api.settings import Settings  # noqa: E402

PORT = 8100
WEB_ORIGINS = ("http://127.0.0.1:4173", "http://localhost:4173")


def main() -> None:
    if "--drop" in sys.argv[1:]:
        drop("e2e")
        print("dropped the e2e database", flush=True)
        return
    with tempfile.TemporaryDirectory(prefix="touchgrass-e2e-") as tmp:
        seeded = seed("e2e", Path(tmp))
    print(f"seeded run {seeded.run_id}", flush=True)
    app = create_app(Settings(conninfo=seeded.conninfo, cors_origins=WEB_ORIGINS))
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
