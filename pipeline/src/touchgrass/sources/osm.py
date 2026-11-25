"""OpenStreetMap from dated Geofabrik extracts (DECISIONS.md D-017).

Why a dated extract instead of a live API: the same file can be re-downloaded and
verified (Geofabrik publishes an MD5 per file), so results are reproducible. The public
Overpass API asks automated clients not to rely on it.

Reading: a ``.osm.pbf`` holds nodes, then ways, then relations. Ways store node *ids*,
not coordinates, so geometry must be assembled. pyosmium (libosmium) keeps a
node-location index (``with_locations``) and assembles polygons from closed ways and
multipolygon/boundary relations (``with_areas``) while streaming.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import httpx
import osmium
import shapely
from pyproj import Geod
from shapely import wkb
from shapely.geometry.base import BaseGeometry

from touchgrass.config.models import OsmConfig, WalkFilter
from touchgrass.core.crs import STORAGE_EPSG
from touchgrass.sources import http

GEOFABRIK_BASE = "https://download.geofabrik.de"
OSM_LICENSE = ("Open Database License (ODbL) 1.0", "https://opendatacommons.org/licenses/odbl/1-0/")
OSM_ATTRIBUTION = (
    "© OpenStreetMap contributors, available under the Open Database License (ODbL 1.0), "
    "https://www.openstreetmap.org/copyright; extract by Geofabrik GmbH"
)
_AREA_RELATION_TYPES = frozenset({"multipolygon", "boundary"})


class OsmIntegrityError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- fetch


def extract_filename(osm: OsmConfig) -> str:
    return f"{osm.extract_name}-{osm.snapshot}.osm.pbf"


def extract_url(osm: OsmConfig) -> str:
    return f"{GEOFABRIK_BASE}/{osm.geofabrik_path}/{extract_filename(osm)}"


def md5_file(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)  # matching the publisher's checksum, not security
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_extract(
    client: httpx.Client, osm: OsmConfig, dest: Path, *, refresh: bool = False
) -> tuple[http.Manifest, bool]:
    """Download the extract and verify it against Geofabrik's published MD5."""
    url = extract_url(osm)
    manifest, downloaded = http.download(
        client,
        url,
        dest,
        source_id="osm_extract",
        attribution=OSM_ATTRIBUTION,
        vintage=osm.snapshot,
        refresh=refresh,
    )
    if not downloaded and manifest.extra.get("md5_verified"):
        return manifest, False

    response = client.get(f"{url}.md5")
    response.raise_for_status()
    expected = response.text.split()[0].lower()
    actual = md5_file(dest)
    if actual != expected:
        dest.unlink(missing_ok=True)
        http.manifest_path(dest).unlink(missing_ok=True)
        raise OsmIntegrityError(f"{dest.name}: MD5 {actual} != published {expected}")
    manifest = dataclasses.replace(
        manifest, extra={**manifest.extra, "md5": actual, "md5_verified": True}
    )
    manifest.write(http.manifest_path(dest))
    return manifest, downloaded


# --------------------------------------------------------------------------- extract


@dataclass
class ExtractStats:
    areas_matched: int = 0
    areas_in_study_area: int = 0
    geometry_errors: int = 0
    candidate_area_relations: int = 0
    broken_relations: list[int] = field(default_factory=list)  # in the whole extract


def _matches(tags: dict[str, str], candidate_tags: dict[str, set[str]]) -> bool:
    return any(tags.get(k) in values for k, values in candidate_tags.items())


def extract_areas(
    pbf: Path,
    candidate_tags: dict[str, set[str]],
    study_area: BaseGeometry,
    keep: Literal["representative_point", "intersects"] = "representative_point",
) -> tuple[gpd.GeoDataFrame, ExtractStats]:
    """Every OSM area carrying a candidate tag that belongs to ``study_area``.

    ``keep="representative_point"`` (parks): the area's representative point is inside, so
    each park is counted by the region it mostly lies in. ``keep="intersects"`` (water):
    any overlap counts, because a river's representative point can be 100 km upstream.

    Columns: osm_type ('way' | 'relation'), osm_id, name, tags (JSON), geometry (EPSG:4326).
    Areas are kept whole even if they extend beyond ``study_area``.

    Also reports relations that *should* have become areas but could not be assembled
    (broken multipolygons). libosmium skips those silently, so we detect them by
    comparing relation ids seen with relation ids that produced an area.
    """
    stats = ExtractStats()
    shapely.prepare(study_area)
    minx, miny, maxx, maxy = study_area.bounds
    factory = osmium.geom.WKBFactory()
    candidate_relations: set[int] = set()
    assembled_relations: set[int] = set()
    rows: list[dict[str, Any]] = []

    processor = (
        osmium.FileProcessor(str(pbf))
        .with_locations()
        .with_areas(osmium.filter.KeyFilter(*candidate_tags))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA | osmium.osm.RELATION))
    )
    for obj in processor:
        tags = {t.k: t.v for t in obj.tags}
        if not _matches(tags, candidate_tags):
            continue
        if isinstance(obj, osmium.osm.Relation):
            if tags.get("type") in _AREA_RELATION_TYPES:
                candidate_relations.add(obj.id)
            continue
        if not isinstance(obj, osmium.osm.Area):
            continue

        # An Area assembled from a closed way or from a multipolygon/boundary relation.
        stats.areas_matched += 1
        from_way = obj.from_way()
        if not from_way:
            assembled_relations.add(obj.orig_id())
        try:
            geom = wkb.loads(factory.create_multipolygon(obj), hex=True)
        except (RuntimeError, shapely.errors.ShapelyError):
            stats.geometry_errors += 1
            continue
        bx = geom.bounds
        if bx[2] < minx or bx[0] > maxx or bx[3] < miny or bx[1] > maxy:
            continue
        probe = geom if keep == "intersects" else geom.representative_point()
        if not study_area.intersects(probe):
            continue
        stats.areas_in_study_area += 1
        rows.append(
            {
                "osm_type": "way" if from_way else "relation",
                "osm_id": int(obj.orig_id()),
                "name": tags.get("name"),
                "tags": json.dumps(tags, sort_keys=True, ensure_ascii=False),
                "geometry": geom,
            }
        )

    stats.candidate_area_relations = len(candidate_relations)
    stats.broken_relations = sorted(candidate_relations - assembled_relations)
    gdf = gpd.GeoDataFrame(
        rows or {"osm_type": [], "osm_id": [], "name": [], "tags": [], "geometry": []},
        geometry="geometry",
        crs=STORAGE_EPSG,
    )
    return gdf.sort_values(["osm_type", "osm_id"], ignore_index=True), stats


# ---------------------------------------------------------------------- walk network

ANY_VALUE = "*"


def tag_matches(tags: dict[str, str], rule: dict[str, list[str]]) -> str | None:
    """The first ``key=value`` of ``tags`` matched by ``rule`` (``"*"`` = any value)."""
    for key, values in rule.items():
        value = tags.get(key)
        if value is not None and (value in values or ANY_VALUE in values):
            return f"{key}={value}"
    return None


def walk_exclusion(tags: dict[str, str], walk: WalkFilter) -> str | None:
    """Why a way is NOT walkable (a short rule label), or None if it is walkable."""
    highway = tags.get("highway")
    if highway is None:
        return "no_highway"
    foot_allowed = tags.get("foot") in walk.foot_overrides
    for key, values in walk.exclude_tags.items():
        value = tags.get(key)
        if value is None or not (value in values or ANY_VALUE in values):
            continue
        if foot_allowed and key in walk.foot_overrides_keys:
            continue  # foot=* beats the general access tag for pedestrians
        return f"{key}={value}"
    if re.search(walk.exclude_highway, highway):
        readmitted = any(
            all(tags.get(k) in values for k, values in rule.items()) for rule in walk.readmit
        )
        if not readmitted:
            return f"highway={highway}"
    return None


@dataclass
class WalkExtract:
    ways_kept: int = 0
    nodes_written: int = 0
    readmitted_ways: int = 0
    excluded: dict[str, dict[str, float]] = field(default_factory=dict)  # rule -> ways, km
    entrances: gpd.GeoDataFrame | None = None  # entrance/gate nodes in the bounding box


def _way_km(coords: list[tuple[float, float]]) -> float:
    lons, lats = zip(*coords, strict=True)
    return float(_GEOD.line_length(lons, lats)) / 1000


_GEOD = Geod(ellps="WGS84")


def extract_walk_ways(
    pbf: Path,
    bounds: tuple[float, float, float, float],
    walk: WalkFilter,
    entrance_tags: dict[str, list[str]],
    xml_out: Path,
) -> WalkExtract:
    """Write the walkable ways touching ``bounds`` (lon/lat) to an ``.osm`` XML file.

    OSMnx can build a graph from OSM XML (``graph_from_xml``) but not from PBF, so this is
    the bridge: one streaming pass keeps walkable ``highway=*`` ways with every node they
    reference (so geometry is complete even where a way leaves the box), and records why
    each other highway way was excluded. Nodes and ways are written sorted by id, so the
    same extract always produces the same file.
    """
    x0, y0, x1, y1 = bounds
    out = WalkExtract()
    locations: dict[int, tuple[float, float]] = {}
    ways: list[tuple[int, osmium.osm.mutable.Way]] = []
    entrances: list[dict[str, Any]] = []
    excluded: dict[str, list[float]] = {}

    def inside(lon: float, lat: float) -> bool:
        return x0 <= lon <= x1 and y0 <= lat <= y1

    keys = sorted({"highway", *entrance_tags})
    processor = (
        osmium.FileProcessor(str(pbf)).with_locations().with_filter(osmium.filter.KeyFilter(*keys))
    )
    for obj in processor:
        if isinstance(obj, osmium.osm.Node):
            tags = {t.k: t.v for t in obj.tags}
            kind = tag_matches(tags, entrance_tags)
            if (
                kind is not None
                and obj.location.valid()
                and inside(obj.location.lon, obj.location.lat)
            ):
                entrances.append(
                    {
                        "osm_node_id": int(obj.id),
                        "kind": kind,
                        "access": tags.get("access"),
                        "geometry": shapely.Point(obj.location.lon, obj.location.lat),
                    }
                )
            continue
        if not isinstance(obj, osmium.osm.Way):
            continue
        tags = {t.k: t.v for t in obj.tags}
        if "highway" not in tags:
            continue
        refs = [(n.ref, n.lon, n.lat) for n in obj.nodes if n.location.valid()]
        if len(refs) < 2 or not any(inside(lon, lat) for _, lon, lat in refs):
            continue
        reason = walk_exclusion(tags, walk)
        if reason is not None:
            entry = excluded.setdefault(reason, [0, 0.0])
            entry[0] += 1
            entry[1] += _way_km([(lon, lat) for _, lon, lat in refs])
            continue
        if re.search(walk.exclude_highway, tags["highway"]):
            out.readmitted_ways += 1
        for ref, lon, lat in refs:
            locations[ref] = (lon, lat)
        ways.append(
            (
                int(obj.id),
                osmium.osm.mutable.Way(
                    id=obj.id, nodes=[r for r, _, _ in refs], tags=tags, version=1
                ),
            )
        )

    xml_out.parent.mkdir(parents=True, exist_ok=True)
    xml_out.unlink(missing_ok=True)  # SimpleWriter refuses to overwrite
    with osmium.SimpleWriter(str(xml_out)) as writer:  # nodes, then ways (OSM file order)
        for ref in sorted(locations):
            writer.add_node(osmium.osm.mutable.Node(id=ref, location=locations[ref], version=1))
        for _, way in sorted(ways, key=lambda iw: iw[0]):
            writer.add_way(way)

    out.ways_kept = len(ways)
    out.nodes_written = len(locations)
    out.excluded = {
        k: {"ways": int(v[0]), "km": round(v[1], 2)}
        for k, v in sorted(excluded.items(), key=lambda kv: -kv[1][0])
    }
    out.entrances = gpd.GeoDataFrame(
        entrances or {"osm_node_id": [], "kind": [], "access": [], "geometry": []},
        geometry="geometry",
        crs=STORAGE_EPSG,
    ).sort_values("osm_node_id", ignore_index=True)
    return out
