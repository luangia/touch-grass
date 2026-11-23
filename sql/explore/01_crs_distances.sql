-- 01 · CRS check in SQL: one distance, three answers (Stage 4 lesson §5.2, CRS deep dive §6).
-- Downtown Portland → near Gresham. Storage is EPSG:4326, so a plain geometry distance is
-- in DEGREES; geography gives geodesic metres; transforming to UTM 10N gives planar metres.
SELECT round(ST_Distance(a, b)::numeric, 4)                                   AS degrees,
       round(ST_Distance(a::geography, b::geography)::numeric, 1)             AS geodesic_m,
       round(ST_Distance(ST_Transform(a, 26910), ST_Transform(b, 26910))::numeric, 1) AS utm10n_m
FROM (SELECT ST_SetSRID(ST_MakePoint(-122.6765, 45.5231), 4326) AS a,
             ST_SetSRID(ST_MakePoint(-122.4302, 45.4984), 4326) AS b) AS t;

-- PostGIS refuses to mix SRIDs (uncomment to see the error):
-- SELECT ST_Distance(ST_SetSRID(ST_MakePoint(0, 0), 4326), ST_SetSRID(ST_MakePoint(0, 0), 26910));
