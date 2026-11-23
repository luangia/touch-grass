-- 0005: where trips start and end on the walk network (Stage 5, D-020).
--
-- Both tables locate a point ON a network edge (u, v) by its distance from each end, so
-- Stage 6 can seed a shortest-path search from points that are not graph nodes. The
-- network itself (nodes, edges) stays in GeoParquet: the pipeline traverses it in Python.

CREATE TABLE touchgrass.block_origins (
    region_id          text NOT NULL,
    geoid20            text NOT NULL,
    edge_id            text NOT NULL CHECK (edge_id ~ '^[0-9]+-[0-9]+-[0-9]+$'),
    u                  bigint NOT NULL,
    v                  bigint NOT NULL,
    connector_m        double precision NOT NULL CHECK (connector_m >= 0),
    offset_u_m         double precision NOT NULL CHECK (offset_u_m >= 0),
    offset_v_m         double precision NOT NULL CHECK (offset_v_m >= 0),
    nearest_component  integer NOT NULL CHECK (nearest_component >= 0),
    flagged            boolean NOT NULL,
    geom               geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (region_id, geoid20),
    FOREIGN KEY (region_id, geoid20) REFERENCES touchgrass.census_blocks ON DELETE CASCADE,
    CHECK (u <= v)
);
COMMENT ON COLUMN touchgrass.block_origins.geom IS
    'The snapped point on the edge; the block''s own point is census_blocks.rep_point.';
COMMENT ON COLUMN touchgrass.block_origins.nearest_component IS
    '0 = the main network. > 0: the block''s closest street is on a disconnected piece, so it '
    'was snapped to the main network instead (see network_report.json).';

CREATE TABLE touchgrass.park_access_points (
    region_id        text NOT NULL,
    access_id        text NOT NULL,
    park_id          text NOT NULL,
    method           text NOT NULL CHECK (method IN ('edge_contact', 'entrance')),
    edge_id          text NOT NULL CHECK (edge_id ~ '^[0-9]+-[0-9]+-[0-9]+$'),
    u                bigint NOT NULL,
    v                bigint NOT NULL,
    offset_u_m       double precision NOT NULL CHECK (offset_u_m >= 0),
    offset_v_m       double precision NOT NULL CHECK (offset_v_m >= 0),
    contact_start_m  double precision NOT NULL,
    contact_end_m    double precision NOT NULL,
    osm_node_id      bigint,
    geom             geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (region_id, access_id),
    FOREIGN KEY (region_id, park_id) REFERENCES touchgrass.parks ON DELETE CASCADE,
    CHECK (contact_start_m <= contact_end_m),
    CHECK ((method = 'entrance') = (osm_node_id IS NOT NULL)),
    CHECK (u <= v)
);

CREATE INDEX block_origins_geom_gist      ON touchgrass.block_origins      USING gist (geom);
CREATE INDEX park_access_points_geom_gist ON touchgrass.park_access_points USING gist (geom);
CREATE INDEX park_access_points_park_idx  ON touchgrass.park_access_points (region_id, park_id);
