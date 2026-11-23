"""A small synthetic walk network over the Stage 2-3 synthetic region (see the layout there).

    lat 45.0135   island footway (never connected)                  ── component 1
    lat 45.0120   H2 ───────┬─────────┬─────────┬─────────
                            │V1       │V2       │V3       │V4
    lat 45.0080             │  (sidewalk=separate street V2-V3: excluded)
    lat 45.0045   H1 ───────┼─────────┼─────────┼─────────┼─────
    lat 45.0020             gate(private)       entrance(main)
    lat 44.9980   H0 ───────┴─────────┴─────────┴─────────┘
    lat 44.9965                    south street ╲ cycleway foot=designated → H0
          lon  -123.02   -123.01   -123.00   -122.99

West Woods (park unit) covers lon -123.02..-123.00, lat 45.00..45.01. Blocks' points sit at
lat 45.005, about 55.6 m north of H1.
"""

from __future__ import annotations

from .osm_fixture import OsmFixture

XS = [-123.02, -123.01, -123.00, -122.99]


def add_walk_network(f: OsmFixture) -> None:
    f.line_way(
        1001,
        [(-123.025, 45.0045), *[(x, 45.0045) for x in XS], (-122.985, 45.0045)],
        highway="residential",
        name="H1",
    )
    f.line_way(1002, [(x, 45.012) for x in XS], highway="residential", name="H2")
    f.line_way(1003, [(x, 44.998) for x in XS], highway="residential", name="H0")
    for i, x in enumerate(XS):
        pts = [(x, 44.998), (x, 45.0045), (x, 45.012)]
        if i == 0:
            pts.insert(1, (x, 45.002))  # private gate on West Woods' west edge
        if i == 2:
            pts.insert(1, (x, 45.002))  # public entrance on its east edge
        f.line_way(1010 + i, pts, highway="residential", name=f"V{i + 1}")
    f.node_at(-123.02, 45.002, barrier="gate", access="private")
    f.node_at(-123.00, 45.002, entrance="main")
    # Sidewalks mapped separately elsewhere → the street itself is excluded.
    f.line_way(
        1020, [(-123.01, 45.008), (-123.00, 45.008)], highway="residential", sidewalk="separate"
    )
    f.line_way(1021, [(-123.012, 45.0135), (-123.011, 45.0135)], highway="footway")  # island
    # Reachable only through a shared-use path (OSMnx's walk filter drops all cycleways).
    f.line_way(
        1022, [(-123.006, 44.9965), (-123.004, 44.9965)], highway="residential", name="South St"
    )
    f.line_way(
        1023, [(-123.004, 44.9965), (-123.00, 44.998)], highway="cycleway", foot="designated"
    )
    f.line_way(1024, [(-122.99, 45.0045), (-122.989, 45.006)], highway="service", access="private")
