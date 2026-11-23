# TouchGrass

**How far is nature, really?** TouchGrass measures how easily residents can *walk* to parks and natural areas, neighborhood by neighborhood — starting with Portland, Oregon, and designed to support more cities through configuration.

> **Status: Stage 2 of 11 implemented**: region config, CRS rules, and a reproducible Census/TIGER/City data pipeline. No parks or walking analysis yet. See [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md).

## Quickstart (what works today)

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # then set a local password
docker compose up -d db       # PostgreSQL 17 + PostGIS 3.6
cd pipeline
uv sync                       # installs the exact versions in uv.lock
uv run touchgrass regions validate portland-or
uv run touchgrass fetch portland-or     # downloads ~480 MB once (cached afterwards)
uv run touchgrass process portland-or   # offline: GeoParquet + validation report
uv run touchgrass db migrate            # create/upgrade the PostGIS schema
uv run touchgrass network portland-or   # walk graph, block origins, park access points (~2 min)
uv run touchgrass load portland-or      # load the region (one transaction; rerun-safe)
uv run pytest                           # db tests use a throwaway touchgrass_test database
```

Adding a city = adding `config/regions/<region-id>.yaml` (more fields arrive in Stages 2–3).

## Questions it answers

- How accessible are parks and natural spaces to residents?
- Which neighborhoods have the best and worst access to nature?
- How far must someone walk to reach a park — on the actual pedestrian network?
- How does real walking distance differ from straight-line distance?
- Which neighborhoods have many residents beyond a 10-minute walk?
- Are there geographic or demographic disparities in access? (Observed patterns, not causal claims.)

## Approach (planned)

1. Population from **2020 Census blocks**; demographics from **ACS 2020–2024** block groups.
2. Parks from **OpenStreetMap**, validated against Oregon Metro's ORCA layer.
3. Walking network from **OSM via OSMnx**; park **access points** instead of centroids.
4. **Multi-source Dijkstra** gives each block its network distance to the nearest park; straight-line distance kept as a comparison.
5. **Population-weighted** 5 / 10 / 15-minute metrics per neighborhood.
6. Served by **FastAPI + PostGIS**, explored in a **React + MapLibre** map.

## Planned stack

Python · GeoPandas · Shapely · PyProj · OSMnx · NetworkX · PostgreSQL/PostGIS · FastAPI · React · TypeScript · Vite · MapLibre GL JS · Docker Compose (PostGIS only)

## Documentation

| Doc | Purpose |
|---|---|
| [PROJECT_PLAN.md](docs/PROJECT_PLAN.md) | Scope, stages, acceptance criteria, risks |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, schema, API, multi-region model, data sources |
| [DECISIONS.md](docs/DECISIONS.md) | Engineering decisions and trade-offs |
| [LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) | Concept map for the learning goals |
| [IMPLEMENTATION_LOG.md](docs/IMPLEMENTATION_LOG.md) | What was actually done, when |
| [CURRENT_STATUS.md](docs/CURRENT_STATUS.md) | Current work, open decisions, resume draft |

## Data attribution (planned)

© OpenStreetMap contributors (ODbL) · U.S. Census Bureau (public domain) · City of Portland (Data Distribution Policy) · Regional Land Information System data are a product of Metro and made publicly available according to the license agreement found in [https://rlisdiscovery.oregonmetro.gov/pages/open-database-license](https://rlisdiscovery.oregonmetro.gov/pages/open-database-license).
