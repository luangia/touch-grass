-- 0002: census blocks, block-to-area membership, block groups and ACS estimates.

CREATE TABLE touchgrass.census_blocks (
    region_id         text NOT NULL REFERENCES touchgrass.regions ON DELETE CASCADE,
    geoid20           text NOT NULL CHECK (geoid20 ~ '^[0-9]{15}$'),
    county_fips       text NOT NULL CHECK (county_fips ~ '^[0-9]{3}$'),
    pop20             integer NOT NULL CHECK (pop20 >= 0),
    housing20         integer NOT NULL CHECK (housing20 >= 0),
    aland20           bigint NOT NULL CHECK (aland20 >= 0),
    awater20          bigint NOT NULL CHECK (awater20 >= 0),
    area_m2           double precision NOT NULL CHECK (area_m2 > 0),
    frac_in_boundary  double precision NOT NULL CHECK (frac_in_boundary BETWEEN 0 AND 1),
    rep_point         geometry(Point, 4326) NOT NULL,
    rep_point_source  text NOT NULL,
    geom              geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    PRIMARY KEY (region_id, geoid20)
);
COMMENT ON COLUMN touchgrass.census_blocks.rep_point IS
    'Where the block''s population is placed (Census internal point, D-015); used for joins.';

-- Many-to-many: a block can be in several overlapping areas (e.g. Portland neighborhoods).
CREATE TABLE touchgrass.area_membership (
    region_id  text NOT NULL,
    area_id    text NOT NULL,
    geoid20    text NOT NULL,
    PRIMARY KEY (region_id, area_id, geoid20),
    FOREIGN KEY (region_id, area_id)
        REFERENCES touchgrass.analysis_areas ON DELETE CASCADE,
    FOREIGN KEY (region_id, geoid20)
        REFERENCES touchgrass.census_blocks ON DELETE CASCADE
);

CREATE TABLE touchgrass.block_groups (
    region_id  text NOT NULL REFERENCES touchgrass.regions ON DELETE CASCADE,
    geoid      text NOT NULL CHECK (geoid ~ '^[0-9]{12}$'),
    geom       geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    PRIMARY KEY (region_id, geoid)
);

-- Long format (D-019 Q4): one row per (block group, ACS variable), estimate and MOE together.
-- variable = '<table>_<line>', e.g. 'b19013_001' (median household income).
-- NULL estimate/MOE = the Census published a sentinel (not available / not applicable).
CREATE TABLE touchgrass.acs_estimates (
    region_id  text NOT NULL,
    geoid      text NOT NULL,
    variable   text NOT NULL CHECK (variable ~ '^b[0-9]{5}[a-i]?_[0-9]{3}$'),
    estimate   double precision CHECK (estimate >= 0),
    moe        double precision CHECK (moe >= 0),
    vintage    text NOT NULL,
    PRIMARY KEY (region_id, geoid, variable),
    FOREIGN KEY (region_id, geoid) REFERENCES touchgrass.block_groups ON DELETE CASCADE
);

-- Convenience view: the coefficient of variation (CV = (MOE / 1.645) / estimate) tells how
-- reliable each estimate is; ACS MOEs are published at 90% confidence.
CREATE VIEW touchgrass.acs_estimates_cv AS
SELECT region_id, geoid, variable, estimate, moe, vintage,
       CASE WHEN estimate > 0 THEN (moe / 1.645) / estimate END AS cv
FROM touchgrass.acs_estimates;
