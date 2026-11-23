-- 0006: water-aware snapping and park gaps (D-020 amendment, after the Stage 5 field check).
--
-- A new file, not an edit of 0005: 0005 is already applied, and the runner refuses edited
-- history. Defaults exist only to fill rows that predate this migration; they are dropped
-- so the loader must always state these values.

ALTER TABLE touchgrass.block_origins
    ADD COLUMN snap_status text NOT NULL DEFAULT 'ok'
        CHECK (snap_status IN ('ok', 'moorage', 'isolated', 'crosses_water')),
    ADD COLUMN component integer NOT NULL DEFAULT 0 CHECK (component >= 0),
    ADD CHECK ((snap_status = 'isolated') = (component > 0));
ALTER TABLE touchgrass.block_origins
    ALTER COLUMN snap_status DROP DEFAULT,
    ALTER COLUMN component DROP DEFAULT;
COMMENT ON COLUMN touchgrass.block_origins.snap_status IS
    'ok: dry route to the main network; moorage: floating home (dock assumed); isolated: no '
    'dry route to the main network, snapped to its own piece (component); crosses_water: '
    'no dry edge within max_snap_m, flagged.';

ALTER TABLE touchgrass.park_access_points
    DROP CONSTRAINT park_access_points_method_check,
    ADD CONSTRAINT park_access_points_method_check
        CHECK (method IN ('edge_contact', 'entrance', 'gap')),
    ADD COLUMN gap_m double precision NOT NULL DEFAULT 0 CHECK (gap_m >= 0),
    ADD CHECK ((method = 'gap') = (gap_m > 0));
ALTER TABLE touchgrass.park_access_points ALTER COLUMN gap_m DROP DEFAULT;
COMMENT ON COLUMN touchgrass.park_access_points.offset_u_m IS
    'Walking cost from u to the park, including gap_m for method = gap.';
