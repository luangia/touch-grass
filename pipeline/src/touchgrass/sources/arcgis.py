"""Esri ArcGIS Server layer adapter (MapServer / FeatureServer ``/query``).

The server does the filtering (``where``) and the reprojection (``outSR=4326``) and returns
GeoJSON. Results are paged: each response holds at most ``maxRecordCount`` features and
flags ``exceededTransferLimit`` while more remain. We page with a stable ``orderByFields``
(otherwise pages may overlap or skip rows) and cross-check the total against a
``returnCountOnly`` query, so a paging bug can never silently drop features.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from touchgrass.sources.http import Manifest, cached_manifest, write_json_with_manifest

STORAGE_SR = 4326


class ArcGISError(RuntimeError):
    pass


def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    response = client.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ArcGISError(f"{url}: expected a JSON object")
    if "error" in data:  # ArcGIS reports errors with HTTP 200 and an 'error' body
        raise ArcGISError(f"{url}: {data['error']}")
    return data


def layer_metadata(client: httpx.Client, layer_url: str) -> dict[str, Any]:
    return _get_json(client, layer_url, {"f": "json"})


def _exceeded(page: dict[str, Any]) -> bool:
    # Esri puts the flag at the top level and/or under "properties" in GeoJSON output.
    props = page.get("properties") or {}
    return bool(page.get("exceededTransferLimit") or props.get("exceededTransferLimit"))


def query_geojson(
    client: httpx.Client,
    layer_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    page_size: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch every matching feature as one GeoJSON FeatureCollection in EPSG:4326.

    Returns (feature_collection, layer_metadata).
    """
    meta = layer_metadata(client, layer_url)
    max_records = int(meta.get("maxRecordCount") or 1000)
    size = min(page_size or max_records, max_records)
    oid_field = next(
        (f["name"] for f in meta.get("fields", []) if f.get("type") == "esriFieldTypeOID"),
        None,
    )
    if oid_field is None:
        raise ArcGISError(f"{layer_url}: no ObjectID field; cannot page with a stable order")

    query_url = f"{layer_url}/query"
    expected = int(
        _get_json(client, query_url, {"where": where, "returnCountOnly": "true", "f": "json"})[
            "count"
        ]
    )

    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = _get_json(
            client,
            query_url,
            {
                "where": where,
                "outFields": out_fields,
                "outSR": STORAGE_SR,
                "orderByFields": oid_field,
                "resultOffset": offset,
                "resultRecordCount": size,
                "f": "geojson",
            },
        )
        batch = page.get("features", [])
        features.extend(batch)
        offset += len(batch)
        if not batch or not _exceeded(page):
            break

    if len(features) != expected:
        raise ArcGISError(
            f"{layer_url}: received {len(features)} features but the server counts {expected}"
        )
    return {"type": "FeatureCollection", "features": features}, meta


def fetch_layer(
    client: httpx.Client,
    layer_url: str,
    dest: Path,
    *,
    source_id: str,
    attribution: str,
    where: str = "1=1",
    refresh: bool = False,
    page_size: int | None = None,
) -> tuple[Manifest, bool]:
    """Download a layer to ``dest`` (GeoJSON) with a manifest. Returns (manifest, downloaded?)."""
    if not refresh and (cached := cached_manifest(dest)) is not None:
        return cached, False

    collection, meta = query_geojson(client, layer_url, where=where, page_size=page_size)
    manifest = write_json_with_manifest(
        collection,
        dest,
        {
            "source_id": source_id,
            "url": f"{layer_url}/query",
            "params": {"where": where, "outSR": STORAGE_SR, "f": "geojson"},
            "attribution": attribution,
            "extra": {
                "layer_name": meta.get("name"),
                "feature_count": len(collection["features"]),
                # The publisher's own statements: evidence for licence/attribution review.
                "copyright_text": meta.get("copyrightText"),
                "description": (meta.get("description") or "")[:2000],
                "source_spatial_reference": (meta.get("extent") or {}).get("spatialReference"),
            },
        },
    )
    return manifest, True
