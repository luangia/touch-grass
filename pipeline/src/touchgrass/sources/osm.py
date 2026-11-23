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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx
import osmium
import shapely
from shapely import wkb
from shapely.geometry.base import BaseGeometry

from touchgrass.config.models import OsmConfig
from touchgrass.core.crs import STORAGE_EPSG
from touchgrass.sources import http

GEOFABRIK_BASE = "https://download.geofabrik.de"
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
    pbf: Path, candidate_tags: dict[str, set[str]], study_area: BaseGeometry
) -> tuple[gpd.GeoDataFrame, ExtractStats]:
    """Every OSM area carrying a candidate tag whose representative point is in ``study_area``.

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
        if not study_area.intersects(geom.representative_point()):
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
