-- 0003: OSM green-space candidates (every decision, for auditability) and park units.

CREATE TABLE touchgrass.green_candidates (
    region_id   text NOT NULL REFERENCES touchgrass.regions ON DELETE CASCADE,
    osm_type    text NOT NULL CHECK (osm_type IN ('node', 'way', 'relation')),
    osm_id      bigint NOT NULL CHECK (osm_id > 0),
    name        text,
    tags        jsonb NOT NULL CHECK (jsonb_typeof(tags) = 'object'),
    included    boolean NOT NULL,
    park_class  text CHECK (park_class IN ('park', 'natural_area')),
    -- Reasons are region config (exclude_tags keys), so only their shape is fixed here.
    reason      text NOT NULL CHECK (reason ~ '^[a-z0-9]+(_[a-z0-9]+)*$'),
    rule_tag    text,
    area_m2     double precision NOT NULL CHECK (area_m2 >= 0),
    geom        geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    PRIMARY KEY (region_id, osm_type, osm_id),
    CHECK (included = (reason = 'included')),
    CHECK (included = (park_class IS NOT NULL))
);

-- One row per park unit: overlapping OSM features merged, excluded facilities cut out.
CREATE TABLE touchgrass.parks (
    region_id      text NOT NULL REFERENCES touchgrass.regions ON DELETE CASCADE,
    park_id        text NOT NULL CHECK (park_id ~ '^osm:[nwr][0-9]+$'),
    name           text,
    park_class     text NOT NULL CHECK (park_class IN ('park', 'natural_area')),
    area_m2        double precision NOT NULL CHECK (area_m2 > 0),
    subtracted_m2  double precision NOT NULL CHECK (subtracted_m2 >= 0),
    n_members      integer NOT NULL CHECK (n_members >= 1),
    member_ids     text[] NOT NULL,
    member_names   text,
    geom           geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    PRIMARY KEY (region_id, park_id),
    CHECK (cardinality(member_ids) = n_members)
);
COMMENT ON COLUMN touchgrass.parks.area_m2 IS
    'Area in the region''s analysis CRS (regions.analysis_crs), after subtraction.';
