-- 05 · "Parks within 800 m of a point" on EPSG:4326 storage, three ways.
-- The index is on `geom` itself; any function wrapped around the COLUMN hides it.
\set region '\'portland-or\''
SET statement_timeout = '120s';
\set pt 'ST_SetSRID(ST_MakePoint(-122.6793, 45.5189), 4326)'

-- A: geography cast on both sides. Geodesic metres, exact, but the cast is on the column,
--    so the GEOMETRY index can't be used (needs an expression index on geom::geography).
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM touchgrass.parks
WHERE region_id = :region AND ST_DWithin(geom::geography, (:pt)::geography, 800);

-- B: transform the CONSTANT side: build an 800 m circle around the point in UTM 10N,
--    bring it to 4326, and intersect. The column is untouched, so the GiST index is used.
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM touchgrass.parks
WHERE region_id = :region
  AND ST_Intersects(geom, ST_Transform(ST_Buffer(ST_Transform(:pt, 26910), 800, 32), 4326));

-- C: anti-pattern: transform the COLUMN. Every row is projected, then measured. No index.
EXPLAIN (ANALYZE, COSTS OFF)
SELECT count(*) FROM touchgrass.parks
WHERE region_id = :region
  AND ST_DWithin(ST_Transform(geom, 26910), ST_Transform(:pt, 26910), 800);

-- The three answers (they should agree; B's 32-segment circle is a tiny bit smaller):
SELECT
  (SELECT count(*) FROM touchgrass.parks WHERE region_id = :region
     AND ST_DWithin(geom::geography, (:pt)::geography, 800)) AS a_geography,
  (SELECT count(*) FROM touchgrass.parks WHERE region_id = :region
     AND ST_Intersects(geom, ST_Transform(ST_Buffer(ST_Transform(:pt, 26910), 800, 32), 4326)))
     AS b_constant_side,
  (SELECT count(*) FROM touchgrass.parks WHERE region_id = :region
     AND ST_DWithin(ST_Transform(geom, 26910), ST_Transform(:pt, 26910), 800)) AS c_column_transform;
