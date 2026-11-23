"""Coordinate reference system rules shared by every region.

Two CRS roles exist in TouchGrass (DECISIONS.md D-008):

* **Storage/exchange CRS**: EPSG:4326 (WGS 84 longitude/latitude, degrees). Used by the
  database, the API, and GeoJSON (RFC 7946 mandates it).
* **Analysis CRS**: a *projected* CRS in *meters* chosen per region, used for every distance,
  area, and buffer. Measuring in degrees or in Web Mercator gives silently wrong numbers.

This module enforces the second rule and builds coordinate transformers that always use
(x=longitude/easting, y=latitude/northing) axis order.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

#: CRS used for storage, the API, and GeoJSON.
STORAGE_EPSG = 4326

_METER_UNIT_NAMES = frozenset({"metre", "meter"})


class AnalysisCRSError(ValueError):
    """Raised when a CRS is unsuitable for measuring distances and areas.

    Subclasses ValueError so Pydantic validators report it as a normal validation error.
    """


@dataclass(frozen=True, slots=True)
class LonLatBounds:
    """A geographic bounding box in degrees (west, south, east, north).

    ``west > east`` means the box crosses the antimeridian (180°), which some CRS
    areas of use do (e.g. zones around Alaska's Aleutian Islands or Fiji).
    """

    west: float
    south: float
    east: float
    north: float

    def contains(self, lon: float, lat: float) -> bool:
        if not self.south <= lat <= self.north:
            return False
        if self.west <= self.east:
            return self.west <= lon <= self.east
        return lon >= self.west or lon <= self.east  # wraps across 180°


def load_crs(epsg: int) -> CRS:
    """Look up an EPSG code in the PROJ database, raising AnalysisCRSError if unknown."""
    try:
        return CRS.from_epsg(epsg)
    except CRSError as exc:  # pyproj raises a RuntimeError subclass; normalise it
        raise AnalysisCRSError(f"EPSG:{epsg} is not a known CRS ({exc})") from exc


def _is_normal_mercator(crs: CRS) -> bool:
    """True for Mercator-family projections other than Transverse Mercator.

    Normal-aspect Mercator (Web Mercator / 'Pseudo Mercator', World Mercator) has a scale
    factor of 1/cos(latitude): at 45°N lengths are ~1.41x and areas ~2.0x too large.
    Transverse Mercator (used by UTM) is accurate within its narrow zone and is allowed.
    """
    op = crs.coordinate_operation
    if op is None:
        return False
    method = op.method_name.lower()
    return "mercator" in method and "transverse" not in method


def assert_analysis_crs(epsg: int) -> CRS:
    """Return the CRS for ``epsg`` if it is valid for measurement, else raise.

    Rules, in order:
      1. The code exists in the PROJ/EPSG database.
      2. The CRS is projected (planar x/y), not geographic (angles in degrees).
      3. Every axis is in meters (rejects e.g. US State Plane systems in feet).
      4. It is not a normal-aspect Mercator projection (rejects Web Mercator).
    """
    crs = load_crs(epsg)

    if not crs.is_projected:
        raise AnalysisCRSError(
            f"EPSG:{epsg} ({crs.name}) is geographic (units: degrees); "
            "analysis needs a projected CRS in meters"
        )

    units = {axis.unit_name for axis in crs.axis_info}
    if not units <= _METER_UNIT_NAMES:
        raise AnalysisCRSError(
            f"EPSG:{epsg} ({crs.name}) uses {sorted(units)}; analysis CRS axes must be in meters"
        )

    if _is_normal_mercator(crs):
        raise AnalysisCRSError(
            f"EPSG:{epsg} ({crs.name}) is a normal-aspect Mercator projection, which inflates "
            "distances and areas away from the equator; use a local projection such as UTM"
        )

    return crs


def area_of_use(crs: CRS) -> LonLatBounds | None:
    """Geographic extent (degrees) where the CRS is intended to be used, if PROJ knows it."""
    aou = crs.area_of_use
    if aou is None:
        return None
    return LonLatBounds(west=aou.west, south=aou.south, east=aou.east, north=aou.north)


def assert_point_in_area_of_use(crs: CRS, lon: float, lat: float) -> None:
    """Raise if (lon, lat) lies outside the CRS's area of use.

    Catches wrong-zone mistakes (e.g. an East Coast UTM zone for a West Coast city) and
    swapped (lat, lon) centers, which often still pass simple range checks.
    If PROJ has no area of use for the CRS, the check is skipped.
    """
    bounds = area_of_use(crs)
    if bounds is None:
        return
    if not bounds.contains(lon, lat):
        raise AnalysisCRSError(
            f"({lon}, {lat}) is outside the area of use of {crs.name} "
            f"(lon {bounds.west}..{bounds.east}, lat {bounds.south}..{bounds.north})"
        )


def make_transformer(source: int | CRS, target: int | CRS) -> Transformer:
    """Build a transformer that always takes and returns (x, y) = (lon, lat) / (east, north).

    EPSG:4326 officially lists latitude first. Without ``always_xy=True`` PROJ honours that,
    so passing (lon, lat), as GeoJSON and Shapely do, silently swaps the axes,
    typically producing (inf, inf) or points on the wrong side of the planet.
    """
    return Transformer.from_crs(source, target, always_xy=True)
