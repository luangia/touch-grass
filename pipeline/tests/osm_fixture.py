"""Write small but real ``.osm.pbf`` files for tests, using pyosmium's writer.

Coordinates are around lon -123.0 / lat 45.0 (inside UTM 10N). Cells are 0.01° squares
(about 787 m x 1,112 m at this latitude).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import osmium
from osmium.osm.mutable import Node, Relation, Way


@dataclass
class OsmFixture:
    nodes: list[Node] = field(default_factory=list)
    ways: list[Way] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    _next_node: int = 1
    _by_coord: dict[tuple[float, float], int] = field(default_factory=dict)

    def ring(self, x0: float, y0: float, x1: float, y1: float) -> list[int]:
        """Add 4 corner nodes (counter-clockwise) and return a closed node-id ring."""
        ids = []
        for lon, lat in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            self.nodes.append(Node(id=self._next_node, location=(lon, lat), version=1))
            ids.append(self._next_node)
            self._next_node += 1
        return [*ids, ids[0]]

    def node_at(self, lon: float, lat: float, **tags: str) -> int:
        """A node at (lon, lat), shared by every line that passes the same point."""
        key = (round(lon, 7), round(lat, 7))
        if key in self._by_coord:
            if tags:  # tag an existing node: replace it with a tagged copy
                nid = self._by_coord[key]
                self.nodes = [n for n in self.nodes if n.id != nid]
                self.nodes.append(Node(id=nid, location=(lon, lat), tags=tags, version=1))
            return self._by_coord[key]
        nid = self._next_node
        self._next_node += 1
        self.nodes.append(Node(id=nid, location=(lon, lat), tags=tags, version=1))
        self._by_coord[key] = nid
        return nid

    def line_way(self, way_id: int, coords: list[tuple[float, float]], **tags: str) -> None:
        """An open way (street, path) whose nodes are shared at equal coordinates."""
        self.ways.append(
            Way(id=way_id, nodes=[self.node_at(x, y) for x, y in coords], tags=tags, version=1)
        )

    def area_way(self, way_id: int, box: tuple[float, float, float, float], **tags: str) -> None:
        self.ways.append(Way(id=way_id, nodes=self.ring(*box), tags=tags, version=1))

    def multipolygon(
        self,
        rel_id: int,
        outer: tuple[float, float, float, float],
        inner: tuple[float, float, float, float] | None,
        way_ids: tuple[int, int],
        rel_type: str = "multipolygon",
        **tags: str,
    ) -> None:
        self.ways.append(Way(id=way_ids[0], nodes=self.ring(*outer), version=1))
        members = [("w", way_ids[0], "outer")]
        if inner is not None:
            self.ways.append(Way(id=way_ids[1], nodes=self.ring(*inner), version=1))
            members.append(("w", way_ids[1], "inner"))
        self.relations.append(
            Relation(id=rel_id, members=members, tags={"type": rel_type, **tags}, version=1)
        )

    def broken_relation(self, rel_id: int, missing_way: int, **tags: str) -> None:
        """A multipolygon whose only member way does not exist in the file."""
        self.relations.append(
            Relation(
                id=rel_id,
                members=[("w", missing_way, "outer")],
                tags={"type": "multipolygon", **tags},
                version=1,
            )
        )

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)  # SimpleWriter refuses to overwrite
        with osmium.SimpleWriter(str(path)) as w:  # nodes, then ways, then relations
            for n in sorted(self.nodes, key=lambda n: n.id or 0):
                w.add_node(n)
            for way in self.ways:
                w.add_way(way)
            for r in self.relations:
                w.add_relation(r)
        return path


def cell(i: int, j: int = 0, size: float = 0.01) -> tuple[float, float, float, float]:
    x0, y0 = -123.02 + i * size, 45.00 + j * size
    return (x0, y0, x0 + size, y0 + size)
