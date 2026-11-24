"""The FastAPI application: routers, middleware and the pool's lifecycle (DECISIONS.md D-023).

Run locally:  uv run uvicorn --factory touchgrass_api.main:create_app --reload
Docs:         http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from touchgrass_api.db import EXPECTED_SCHEMA_VERSION, make_pool, require_schema, schema_version
from touchgrass_api.models import Health
from touchgrass_api.routers import areas, layers, regions
from touchgrass_api.settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(cfg.conninfo, cfg.pool_min, cfg.pool_max)
        pool.open(wait=True, timeout=10)
        try:
            with pool.connection() as conn:
                require_schema(conn)  # refuse to serve a schema this code wasn't written for
            app.state.pool = pool
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="TouchGrass API",
        version="1.0.0",
        summary="Read-only walkable park access data: regions, areas, metrics, parks, tiles.",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_methods=["GET"],
        allow_headers=["If-None-Match"],
        expose_headers=["ETag"],
    )
    app.include_router(regions.router)
    app.include_router(areas.router)
    app.include_router(layers.router)

    @app.get("/health", response_model=Health, tags=["health"])
    def health(request: Request) -> Health | JSONResponse:
        """Liveness plus a database round trip and the schema version."""
        reachable, version = True, None
        try:
            with request.app.state.pool.connection(timeout=3) as conn:
                version = schema_version(conn)
        except Exception:  # any DB failure is "unavailable", never a 500 here
            reachable = False
        ok = reachable and version == EXPECTED_SCHEMA_VERSION
        body = Health(
            status="ok" if ok else "unavailable",
            database="reachable" if reachable else "unreachable",
            schema_version=version,
            expected_schema_version=EXPECTED_SCHEMA_VERSION,
        )
        return body if ok else JSONResponse(body.model_dump(), status_code=503)

    return app
