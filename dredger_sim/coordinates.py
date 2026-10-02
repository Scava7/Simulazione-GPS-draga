"""Conversioni e preparazione delle coordinate UTM/WGS84."""

from __future__ import annotations

from typing import Any

import utm

def coordinate_units_per_meter(north_raw: int) -> int:
    magnitude = abs(north_raw)
    if magnitude < 10_000_000:
        return 1
    if magnitude < 100_000_000:
        return 10
    if magnitude < 1_000_000_000:
        return 100
    return 1000


def reference_points_for_map(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Combine PLC UTM offsets and relative INT arrays, then convert to WGS84."""
    norths = values["UTM_North_ref_points"]
    easts = values["UTM_East_ref_points"]
    zone = int(values["UTM_Zone"])
    north_offset = int(values["UTM_North_Offset"])
    east_offset = int(values["UTM_East_Offset"])
    points = []

    for index, (north_delta, east_delta) in enumerate(zip(norths, easts), start=1):
        north_raw = north_offset + int(north_delta)
        east_raw = east_offset + int(east_delta)
        divisor = coordinate_units_per_meter(north_raw)

        northing_m = north_raw / divisor
        easting_m = east_raw / divisor
        lat, lon = utm.to_latlon(easting_m, northing_m, zone, northern=True)
        points.append({
            "number": index,
            "lat": lat,
            "lon": lon,
            "easting_m": easting_m,
            "northing_m": northing_m,
            "raw_northing": north_raw,
            "raw_easting": east_raw,
            "raw_units_per_meter": divisor,
        })
    return points
