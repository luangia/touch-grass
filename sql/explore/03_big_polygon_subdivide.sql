-- 03 · The big-polygon effect: which blocks' representative points fall in the city boundary?
-- The boundary is ONE multipolygon with thousands of vertices whose bounding box covers
-- the whole city, so the index filter passes nearly everything. ST_Subdivide splits it
-- into pieces of <= 256 vertices with tight boxes.
\set region '\'portland-or\''
SET statement_timeout = '120s';

SELECT ST_NPoints(boundary) AS boundary_vertices,
       (SELECT count(*) FROM ST_Subdivide(boundary, 256)) AS pieces
FROM touchgrass.study_boundaries WHERE region_id = :region;

-- A: points in the raw boundary
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*)
FROM touchgrass.census_blocks b
JOIN touchgrass.study_boundaries s
  ON s.region_id = b.region_id AND ST_Intersects(b.rep_point, s.boundary)
WHERE b.region_id = :region;

-- B: points in subdivided pieces. MATERIALIZED matters: since PostgreSQL 12 a CTE used once
--    is inlined, and an inlined EXISTS (SELECT … FROM ST_Subdivide(…)) re-subdivides the
--    boundary for every one of the 12,357 blocks (we hit exactly that: > 2 min, cancelled).
--    Each piece then probes the rep_point GiST index with its own tight box.
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
WITH pieces AS MATERIALIZED (
    SELECT ST_Subdivide(boundary, 256) AS geom
    FROM touchgrass.study_boundaries WHERE region_id = :region
)
SELECT count(DISTINCT b.geoid20)   -- a point on an edge shared by two pieces counts once
FROM pieces p
JOIN touchgrass.census_blocks b
  ON b.region_id = :region AND ST_Intersects(b.rep_point, p.geom);

-- C: the stress case: which block POLYGONS touch the city's edge? Raw boundary line …
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*)
FROM touchgrass.census_blocks b
JOIN touchgrass.study_boundaries s
  ON s.region_id = b.region_id AND ST_Intersects(b.geom, ST_Boundary(s.boundary))
WHERE b.region_id = :region;

-- … vs the boundary line cut into short pieces.
EXPLAIN (ANALYZE, COSTS OFF)
WITH edge AS MATERIALIZED (
    SELECT ST_Subdivide(ST_Boundary(boundary), 256) AS geom
    FROM touchgrass.study_boundaries WHERE region_id = :region
)
SELECT count(DISTINCT b.geoid20)
FROM edge e
JOIN touchgrass.census_blocks b
  ON b.region_id = :region AND ST_Intersects(b.geom, e.geom);
