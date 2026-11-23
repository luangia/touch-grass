-- 0001: extension, regions, provenance, study boundaries, analysis areas.
--
-- Conventions for every table (D-004, D-008, D-019):
--   * rows belong to a region: region_id is the first column of the primary key, and a
--     foreign key to regions ON DELETE CASCADE, so deleting a region removes all its rows;
--   * geometry is stored in EPSG:4326 only; the column type geometry(<Type>, 4326) makes
--     PostgreSQL reject any other SRID or geometry type;
--   * CHECK constraints restate what Stage 2-3 validation guarantees, so the database
--     enforces it too.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE touchgrass.regions (
    region_id     text PRIMARY KEY CHECK (region_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name          text NOT NULL,
    analysis_crs  integer NOT NULL CHECK (analysis_crs > 0),
    config        jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    loaded_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE touchgrass.regions IS
    'One row per loaded region; config is the fully-resolved region YAML at load time.';

-- Provenance: one row per raw input, copied from its download manifest.
CREATE TABLE touchgrass.datasets (
    region_id     text NOT NULL REFERENCES touchgrass.regions ON DELETE CASCADE,
    source_id     text NOT NULL,
    url           text NOT NULL,
    stored_file   text NOT NULL,
    stored_bytes  bigint NOT NULL CHECK (stored_bytes >= 0),
    sha256        text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    retrieved_at  timestamptz NOT NULL,
    vintage       text,
    attribution   text NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (region_id, source_id)
);

CREATE TABLE touchgrass.study_boundaries (
    region_id        text PRIMARY KEY REFERENCES touchgrass.regions ON DELETE CASCADE,
    buffer_m         double precision NOT NULL CHECK (buffer_m >= 0),
    boundary         geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(boundary)),
    analysis_buffer  geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(analysis_buffer))
);

CREATE TABLE touchgrass.analysis_areas (
    region_id         text NOT NULL REFERENCES touchgrass.regions ON DELETE CASCADE,
    area_id           text NOT NULL,
    area_type         text NOT NULL CHECK (area_type ~ '^[a-z0-9]+(_[a-z0-9]+)*$'),
    source_key        text NOT NULL,
    name              text,
    check_population  integer CHECK (check_population >= 0),
    geom              geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    PRIMARY KEY (region_id, area_id),
    CHECK (area_id = area_type || ':' || source_key)
);
COMMENT ON COLUMN touchgrass.analysis_areas.check_population IS
    'Official population published with the layer, used only to cross-check Stage 2 sums.';
