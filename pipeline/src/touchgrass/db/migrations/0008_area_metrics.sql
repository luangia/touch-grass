-- 0008: area metrics, threshold shares, ACS context and per-block park reach (Stage 7, D-022).
--
-- Keyed by analysis run: metrics describe one run's distances. Like block_access, no
-- foreign keys to the CURRENT analysis_areas: a historical run keeps the areas it used.
-- The 'city' row (area_type 'city') is computed from blocks, never by summing areas.

CREATE TABLE touchgrass.metrics_runs (
    region_id     text NOT NULL,
    run_id        text NOT NULL,
    generated_at  timestamptz NOT NULL,
    params        jsonb NOT NULL,
    inputs        jsonb NOT NULL,
    code          jsonb NOT NULL,
    summary       jsonb NOT NULL,
    PRIMARY KEY (region_id, run_id),
    FOREIGN KEY (region_id, run_id) REFERENCES touchgrass.analysis_runs ON DELETE CASCADE
);

CREATE TABLE touchgrass.area_metrics (
    region_id                    text NOT NULL,
    run_id                       text NOT NULL,
    area_id                      text NOT NULL,
    area_type                    text NOT NULL,
    name                         text NOT NULL,
    blocks                       integer NOT NULL CHECK (blocks > 0),
    pop_total                    integer NOT NULL CHECK (pop_total > 0),
    populated_land_m2            double precision NOT NULL CHECK (populated_land_m2 >= 0),
    unreachable_pop              integer NOT NULL CHECK (unreachable_pop >= 0),
    underserved_pop              integer NOT NULL CHECK (underserved_pop >= 0),
    underserved_share            double precision NOT NULL
                                 CHECK (underserved_share BETWEEN 0 AND 1),
    median_walk_m                double precision CHECK (median_walk_m >= 0),
    priority_score               double precision CHECK (priority_score >= 0),
    block_share_within           double precision NOT NULL
                                 CHECK (block_share_within BETWEEN 0 AND 1),
    land_share_within            double precision CHECK (land_share_within BETWEEN 0 AND 1),
    reachable_park_ha_mean       double precision NOT NULL CHECK (reachable_park_ha_mean >= 0),
    reachable_park_ha_median     double precision NOT NULL CHECK (reachable_park_ha_median >= 0),
    park_m2_inside               double precision NOT NULL CHECK (park_m2_inside >= 0),
    park_m2_inside_per_resident  double precision NOT NULL
                                 CHECK (park_m2_inside_per_resident >= 0),
    ranked                       boolean NOT NULL,
    PRIMARY KEY (region_id, run_id, area_id),
    FOREIGN KEY (region_id, run_id) REFERENCES touchgrass.metrics_runs ON DELETE CASCADE,
    CHECK (underserved_pop <= pop_total),
    CHECK (unreachable_pop <= underserved_pop),
    CHECK (NOT (ranked AND area_type = 'city'))
);
COMMENT ON COLUMN touchgrass.area_metrics.median_walk_m IS
    'Population-weighted median walk; NULL when over half the residents have no route.';
COMMENT ON COLUMN touchgrass.area_metrics.priority_score IS
    'Underserved residents per km² of the area''s populated-block land (D-011 amendment).';
COMMENT ON COLUMN touchgrass.area_metrics.ranked IS
    'In share/priority rankings: pop_total >= metrics.min_residents_for_ranking (D-022 Q2).';

CREATE TABLE touchgrass.area_thresholds (
    region_id      text NOT NULL,
    run_id         text NOT NULL,
    area_id        text NOT NULL,
    threshold_min  integer NOT NULL CHECK (threshold_min > 0),
    threshold_m    double precision NOT NULL CHECK (threshold_m > 0),
    pop_within     integer NOT NULL CHECK (pop_within >= 0),
    pct_within     double precision NOT NULL CHECK (pct_within BETWEEN 0 AND 1),
    PRIMARY KEY (region_id, run_id, area_id, threshold_min),
    FOREIGN KEY (region_id, run_id, area_id) REFERENCES touchgrass.area_metrics ON DELETE CASCADE
);

CREATE TABLE touchgrass.area_context (
    region_id        text NOT NULL,
    run_id           text NOT NULL,
    area_id          text NOT NULL,
    indicator        text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('count', 'share')),
    estimate         double precision,
    moe              double precision CHECK (moe >= 0),
    cv               double precision CHECK (cv >= 0),
    reliability      text NOT NULL CHECK (reliability IN ('high', 'medium', 'low')),
    numerator        double precision,
    numerator_moe    double precision CHECK (numerator_moe >= 0),
    denominator      double precision,
    denominator_moe  double precision CHECK (denominator_moe >= 0),
    acs_vintage      text NOT NULL,
    PRIMARY KEY (region_id, run_id, area_id, indicator),
    FOREIGN KEY (region_id, run_id, area_id) REFERENCES touchgrass.area_metrics ON DELETE CASCADE,
    CHECK (kind = 'count' OR estimate BETWEEN 0 AND 1)
);
COMMENT ON COLUMN touchgrass.area_context.estimate IS
    'Allocated by 2020 block population (D-022 Q5); a fraction for kind = share.';

CREATE TABLE touchgrass.block_park_reach (
    region_id          text NOT NULL,
    run_id             text NOT NULL,
    geoid20            text NOT NULL,
    parks_reachable    integer NOT NULL CHECK (parks_reachable >= 0),
    reachable_park_ha  double precision NOT NULL CHECK (reachable_park_ha >= 0),
    PRIMARY KEY (region_id, run_id, geoid20),
    FOREIGN KEY (region_id, run_id) REFERENCES touchgrass.metrics_runs ON DELETE CASCADE,
    FOREIGN KEY (region_id, run_id, geoid20) REFERENCES touchgrass.block_access ON DELETE CASCADE,
    CHECK ((parks_reachable = 0) = (reachable_park_ha = 0))
);
COMMENT ON TABLE touchgrass.block_park_reach IS
    'Parks (and their total area) reachable within the underserved walk (D-022 Q4).';
