-- 02 · Point-in-polygon join: population per neighborhood from block representative points.
-- Run 1 lets the planner use the GiST indexes; run 2 disables index scans inside a
-- transaction that is rolled back, so the setting never leaks. Compare plans and times.
\set region '\'portland-or\''
SET statement_timeout = '120s';

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT a.area_id, count(*) AS blocks, sum(b.pop20) AS pop
FROM touchgrass.analysis_areas a
JOIN touchgrass.census_blocks b
  ON b.region_id = a.region_id AND ST_Intersects(b.rep_point, a.geom)
WHERE a.region_id = :region AND a.area_type = 'neighborhood'
GROUP BY a.area_id;

BEGIN;
SET LOCAL enable_indexscan = off;
SET LOCAL enable_bitmapscan = off;
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT a.area_id, count(*) AS blocks, sum(b.pop20) AS pop
FROM touchgrass.analysis_areas a
JOIN touchgrass.census_blocks b
  ON b.region_id = a.region_id AND ST_Intersects(b.rep_point, a.geom)
WHERE a.region_id = :region AND a.area_type = 'neighborhood'
GROUP BY a.area_id;
ROLLBACK;
