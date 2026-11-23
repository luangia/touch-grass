# Exploration queries (Stage 4)

Documented example queries against the loaded `touchgrass` schema. Each file explains what it shows. Measured plans and what they mean are in [docs/learning/stage-04-postgis-spatial-sql.md §14](../../docs/learning/stage-04-postgis-spatial-sql.md).

Run one against the local database (after `touchgrass db migrate` and `touchgrass load portland-or`):

```bash
docker exec -i touchgrass-db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/explore/02_point_in_polygon_join.sql
```

The files use `\set region 'portland-or'`. Change it to explore another loaded region.

| File | Shows |
|---|---|
| `01_crs_distances.sql` | One distance in degrees, geodesic metres and UTM metres |
| `02_point_in_polygon_join.sql` | GiST filter-and-refine vs a nested-loop seq scan (about 60× here) |
| `03_big_polygon_subdivide.sql` | When `ST_Subdivide` helps (polygon vs boundary, 39×) and when it doesn't (points, thanks to prepared geometry) |
| `04_knn_nearest_parks.sql` | Index-ordered nearest neighbours with `<->` |
| `05_metres_from_4326.sql` | Metres from 4326 storage: geography cast, transform the constant side, and the column-transform anti-pattern |
