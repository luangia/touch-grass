-- 0009: straight-line shares per area, and the licence of every source (Stage 10, D-025).
--
-- Straight-line shares are NULL for runs computed before this migration (their metrics files
-- don't have them); a new `touchgrass metrics` run fills them.

ALTER TABLE touchgrass.area_thresholds
    ADD COLUMN pop_within_straight integer CHECK (pop_within_straight >= 0),
    ADD COLUMN pct_within_straight double precision
        CHECK (pct_within_straight BETWEEN 0 AND 1),
    -- Straight-line distance is a floor on the walk, so its share can't be lower (D-021).
    ADD CONSTRAINT area_thresholds_straight_not_below_walk
        CHECK (pct_within_straight IS NULL OR pct_within_straight >= pct_within - 1e-9);
COMMENT ON COLUMN touchgrass.area_thresholds.pct_within_straight IS
    'Share of residents within the threshold distance as the crow flies (to the nearest park polygon).';

-- Existing rows get a placeholder until the region is reloaded (the loader always fills it).
ALTER TABLE touchgrass.datasets
    ADD COLUMN license_name text NOT NULL DEFAULT 'not recorded' CHECK (license_name <> ''),
    ADD COLUMN license_url text CHECK (license_url ~ '^https://');
ALTER TABLE touchgrass.datasets ALTER COLUMN license_name DROP DEFAULT;
