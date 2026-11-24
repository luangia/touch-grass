-- 0007: walking-accessibility analysis runs and per-block results (Stage 6, D-021).
--
-- Runs accumulate: each is identified by a hash of its parameters, input checksums and
-- code version. block_access has no foreign keys to the CURRENT blocks or parks on
-- purpose: an old run records the ids as they were when it ran, and must survive later
-- changes to the park rules. The loader reloads every run kept on disk.

CREATE TABLE touchgrass.analysis_runs (
    region_id     text NOT NULL REFERENCES touchgrass.regions ON DELETE CASCADE,
    run_id        text NOT NULL CHECK (run_id ~ '^[0-9a-f]{16}$'),
    generated_at  timestamptz NOT NULL,
    params        jsonb NOT NULL,
    inputs        jsonb NOT NULL,
    code          jsonb NOT NULL,
    summary       jsonb NOT NULL,
    seconds       double precision NOT NULL CHECK (seconds >= 0),
    is_latest     boolean NOT NULL,
    PRIMARY KEY (region_id, run_id)
);
-- At most one latest run per region.
CREATE UNIQUE INDEX analysis_runs_one_latest ON touchgrass.analysis_runs (region_id) WHERE is_latest;

CREATE TABLE touchgrass.block_access (
    region_id          text NOT NULL,
    run_id             text NOT NULL,
    geoid20            text NOT NULL CHECK (geoid20 ~ '^[0-9]{15}$'),
    pop20              integer NOT NULL CHECK (pop20 > 0),
    snap_status        text NOT NULL,
    reachable          boolean NOT NULL,
    network_m          double precision CHECK (network_m >= 0),
    walk_min           double precision CHECK (walk_min >= 0),
    straight_m         double precision NOT NULL CHECK (straight_m >= 0),
    straight_park_id   text NOT NULL,
    detour_ratio       double precision CHECK (detour_ratio > 0),
    nearest_park_id    text,
    nearest_access_id  text,
    route_kind         text CHECK (route_kind IN ('via_node', 'same_edge')),
    geom               geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (region_id, run_id, geoid20),
    FOREIGN KEY (region_id, run_id) REFERENCES touchgrass.analysis_runs ON DELETE CASCADE,
    CHECK (reachable = (network_m IS NOT NULL)),
    CHECK (reachable = (route_kind IS NOT NULL)),
    CHECK (reachable = (nearest_park_id IS NOT NULL)),
    CHECK ((network_m IS NULL) = (walk_min IS NULL))
);
COMMENT ON COLUMN touchgrass.block_access.network_m IS
    'Walking distance to the nearest park access point; NULL = no path (e.g. an island).';
COMMENT ON COLUMN touchgrass.block_access.detour_ratio IS
    'network_m / straight_m; NULL where straight_m <= 50 m (ratio is noise) or unreachable.';

CREATE INDEX block_access_park_idx ON touchgrass.block_access (region_id, run_id, nearest_park_id);
CREATE INDEX block_access_geom_gist ON touchgrass.block_access USING gist (geom);
