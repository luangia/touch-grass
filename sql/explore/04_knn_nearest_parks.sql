-- 04 · KNN: the 5 nearest park units to Pioneer Courthouse Square.
-- `<->` is the index-assisted distance operator: the GiST index returns rows in order of
-- distance (between bounding boxes during the search, exact geometry distance for the
-- final ORDER BY in PostGIS >= 2.2), so only a handful of rows are visited.
-- Caveat: this is straight-line distance in DEGREES (4326), used only for ordering.
-- Metres come from the geography cast; walking distance is Stage 6.
\set region '\'portland-or\''
SET statement_timeout = '120s';

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT park_id, name
FROM touchgrass.parks
WHERE region_id = :region
ORDER BY geom <-> ST_SetSRID(ST_MakePoint(-122.6793, 45.5189), 4326)
LIMIT 5;

SELECT park_id, name, park_class,
       round(ST_Distance(geom::geography,
             ST_SetSRID(ST_MakePoint(-122.6793, 45.5189), 4326)::geography)::numeric) AS metres
FROM touchgrass.parks
WHERE region_id = :region
ORDER BY geom <-> ST_SetSRID(ST_MakePoint(-122.6793, 45.5189), 4326)
LIMIT 5;
