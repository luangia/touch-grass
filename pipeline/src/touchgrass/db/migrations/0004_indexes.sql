-- 0004: indexes.
--
-- GiST on every geometry column: an R-tree of bounding boxes that lets the planner turn
-- ST_Intersects / ST_DWithin / && / <-> into "probe the tree, then recheck exactly".
-- B-tree indexes serve the common non-spatial lookups. Primary keys already index
-- their leading columns, e.g. area_membership (region_id, area_id, ...).

CREATE INDEX study_boundaries_boundary_gist ON touchgrass.study_boundaries USING gist (boundary);
CREATE INDEX analysis_areas_geom_gist       ON touchgrass.analysis_areas   USING gist (geom);
CREATE INDEX census_blocks_geom_gist        ON touchgrass.census_blocks    USING gist (geom);
CREATE INDEX census_blocks_rep_point_gist   ON touchgrass.census_blocks    USING gist (rep_point);
CREATE INDEX block_groups_geom_gist         ON touchgrass.block_groups     USING gist (geom);
CREATE INDEX green_candidates_geom_gist     ON touchgrass.green_candidates USING gist (geom);
CREATE INDEX parks_geom_gist                ON touchgrass.parks            USING gist (geom);

CREATE INDEX analysis_areas_type_idx      ON touchgrass.analysis_areas   (region_id, area_type);
CREATE INDEX area_membership_block_idx    ON touchgrass.area_membership  (region_id, geoid20);
CREATE INDEX green_candidates_reason_idx  ON touchgrass.green_candidates (region_id, reason);
