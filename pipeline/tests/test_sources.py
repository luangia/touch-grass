"""Source adapters, tested against mocked HTTP: no network access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

from touchgrass.sources import acs, arcgis, http, tiger


def client_for(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------------------ http


class TestDownload:
    BODY = b"GEO_ID|X\n1500000US41051|1\n1500000US41099|2\n1500000US41005|3\n"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, content=self.BODY, headers={"etag": '"v1"'})

    def setup_method(self) -> None:
        self.calls = 0

    def test_writes_file_and_manifest(self, tmp_path: Path) -> None:
        dest = tmp_path / "f.dat"
        m, downloaded = http.download(
            client_for(self.handler), "https://x.test/f", dest, source_id="s", attribution="a"
        )
        assert downloaded and dest.read_bytes() == self.BODY
        assert m.stored_sha256 == hashlib.sha256(self.BODY).hexdigest()
        assert m.etag == '"v1"'
        assert http.Manifest.read(http.manifest_path(dest)) == m
        assert not dest.with_name("f.dat.part").exists()  # atomic rename cleaned up

    def test_cache_hit_skips_network(self, tmp_path: Path) -> None:
        dest = tmp_path / "f.dat"
        c = client_for(self.handler)
        http.download(c, "https://x.test/f", dest, source_id="s", attribution="a")
        _, downloaded = http.download(c, "https://x.test/f", dest, source_id="s", attribution="a")
        assert not downloaded and self.calls == 1

    def test_tampered_file_is_redownloaded(self, tmp_path: Path) -> None:
        dest = tmp_path / "f.dat"
        c = client_for(self.handler)
        http.download(c, "https://x.test/f", dest, source_id="s", attribution="a")
        dest.write_bytes(b"edited by hand")
        _, downloaded = http.download(c, "https://x.test/f", dest, source_id="s", attribution="a")
        assert downloaded and dest.read_bytes() == self.BODY

    def test_line_filter_keeps_header_and_matches(self, tmp_path: Path) -> None:
        dest = tmp_path / "f.dat"
        keep = acs.make_line_filter((b"1500000US41051", b"1500000US41005"))
        m, _ = http.download(
            client_for(self.handler),
            "https://x.test/f",
            dest,
            source_id="s",
            attribution="a",
            line_filter=keep,
        )
        assert dest.read_bytes() == b"GEO_ID|X\n1500000US41051|1\n1500000US41005|3\n"
        # Provenance still describes the full upstream file.
        assert m.source_sha256 == hashlib.sha256(self.BODY).hexdigest()
        assert m.source_bytes == len(self.BODY)

    def test_http_error_leaves_no_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "f.dat"
        c = client_for(lambda r: httpx.Response(404))
        with pytest.raises(httpx.HTTPStatusError):
            http.download(c, "https://x.test/f", dest, source_id="s", attribution="a")
        assert not dest.exists()


# ---------------------------------------------------------------------------- arcgis

LAYER = "https://gis.test/arcgis/rest/services/X/MapServer/3"


def feature(i: int) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"OBJECTID": i},
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }


def arcgis_handler(total: int, page: int, flag_in_properties: bool, report: int | None = None):  # type: ignore[no-untyped-def]
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        seen.append(q)
        if request.url.path.endswith("/3"):
            fields = [{"name": "OBJECTID", "type": "esriFieldTypeOID"}]
            return httpx.Response(200, json={"name": "X", "maxRecordCount": page, "fields": fields})
        if q.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": total if report is None else report})
        offset, size = int(q["resultOffset"]), int(q["resultRecordCount"])
        feats = [feature(i) for i in range(offset, min(offset + size, total))]
        body: dict[str, Any] = {"type": "FeatureCollection", "features": feats}
        if offset + size < total:
            if flag_in_properties:
                body["properties"] = {"exceededTransferLimit": True}
            else:
                body["exceededTransferLimit"] = True
        return httpx.Response(200, json=body)

    return handle, seen


class TestArcGIS:
    @pytest.mark.parametrize("flag_in_properties", [True, False])
    def test_pages_until_done(self, flag_in_properties: bool) -> None:
        handle, seen = arcgis_handler(total=5, page=2, flag_in_properties=flag_in_properties)
        fc, _ = arcgis.query_geojson(client_for(handle), LAYER)
        assert [f["properties"]["OBJECTID"] for f in fc["features"]] == [0, 1, 2, 3, 4]
        pages = [q for q in seen if "resultOffset" in q]
        assert [q["resultOffset"] for q in pages] == ["0", "2", "4"]
        assert all(q["orderByFields"] == "OBJECTID" and q["outSR"] == "4326" for q in pages)

    def test_count_mismatch_is_an_error(self) -> None:
        handle, _ = arcgis_handler(total=5, page=2, flag_in_properties=True, report=6)
        with pytest.raises(arcgis.ArcGISError, match="received 5 features but the server counts 6"):
            arcgis.query_geojson(client_for(handle), LAYER)

    def test_error_body_with_http_200(self) -> None:
        c = client_for(lambda r: httpx.Response(200, json={"error": {"code": 400}}))
        with pytest.raises(arcgis.ArcGISError, match="400"):
            arcgis.layer_metadata(c, LAYER)

    def test_fetch_layer_writes_geojson_and_manifest(self, tmp_path: Path) -> None:
        handle, _ = arcgis_handler(total=3, page=500, flag_in_properties=True)
        dest = tmp_path / "areas.geojson"
        m, downloaded = arcgis.fetch_layer(
            client_for(handle), LAYER, dest, source_id="areas", attribution="City"
        )
        assert downloaded and m.extra["feature_count"] == 3
        assert len(json.loads(dest.read_text())["features"]) == 3


# ------------------------------------------------------------------------- acs, tiger


class TestACS:
    def write(self, tmp_path: Path, text: str) -> Path:
        path = tmp_path / "t.dat"
        path.write_text(text)
        return path

    def test_parses_and_nulls_sentinels(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            "GEO_ID|B19013_E001|B19013_M001\n"
            "1500000US410510001011|87431|38950\n"
            "1500000US410510005023|-666666666|-222222222\n",
        )
        df = acs.read_table(path)
        assert df["geoid"].tolist() == ["410510001011", "410510005023"]
        assert df.loc[0, "b19013_e001"] == 87431
        assert pd.isna(df.loc[1, "b19013_e001"]) and pd.isna(df.loc[1, "b19013_m001"])

    def test_unexpected_negative_is_an_error(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, "GEO_ID|B01003_E001\n1500000US410510001011|-5\n")
        with pytest.raises(ValueError, match="unexpected negative"):
            acs.read_table(path)

    def test_non_block_group_rows_dropped(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, "GEO_ID|B01003_E001\n0500000US41051|800000\n")
        assert acs.read_table(path).empty

    def test_urls_are_derived(self) -> None:
        assert acs.acs_url(2024, "B19013").endswith(
            "/2024/table-based-SF/data/5YRData/acsdt5y2024-b19013.dat"
        )
        assert tiger.tiger_url(2020, "41", "tabblock20") == (
            "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_41_tabblock20.zip"
        )
        assert acs.block_group_prefixes("41", ["051"]) == (b"1500000US41051",)
