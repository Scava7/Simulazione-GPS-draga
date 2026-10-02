"""Connessione OPC UA e acquisizione di tag e celle della griglia PLC."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from asyncua import Client
import utm

from .config import (GRID_CELL_PROPERTIES, GRID_COLUMNS, GRID_POLL_SECONDS, GRID_READ_BATCH_SIZE, GRID_ROWS, OPCUA_ENDPOINT, OPCUA_POLL_SECONDS)
from .coordinates import coordinate_units_per_meter
from .state import AppState

async def _browse_symbol_path(objects_node, target_path: list[str]):
    """Find a symbol by its sequence of OPC UA BrowseNames."""
    queue = deque([(objects_node, [])])
    visited: set[str] = set()
    while queue and len(visited) < 12000:
        node, names = queue.popleft()
        try:
            node_id = node.nodeid.to_string()
            if node_id in visited:
                continue
            visited.add(node_id)
            browse_name = (await node.read_browse_name()).Name
            current = names + [browse_name]
            if len(current) >= len(target_path) and current[-len(target_path):] == target_path:
                return node
            if len(current) < 18:
                for child in await node.get_children():
                    queue.append((child, current))
        except Exception:
            continue
    raise RuntimeError("simbolo non trovato: " + ".".join(target_path))


async def _find_child(parent, name: str):
    for child in await parent.get_children():
        if (await child.read_browse_name()).Name == name:
            return child
    raise RuntimeError(f"simbolo non trovato: {name}")


async def _resolve_opcua_nodes(client):
    gps = await _browse_symbol_path(client.nodes.objects, ["IO", "GPS"])
    sts = await _find_child(gps, "Sts")
    cfg = await _find_child(gps, "Cfg")
    ref_points = await _find_child(cfg, "stRef_Points")
    target_relative = await _find_child(sts, "TargetPos_UTM_Relative")
    grid_root = await _browse_symbol_path(client.nodes.objects, ["GVL", "GPS_Grid_data"])
    return {
        "UTM_North_Offset": await _find_child(sts, "UTM_North_Offset"),
        "UTM_East_Offset": await _find_child(sts, "UTM_East_Offset"),
        "Target_East": await _find_child(target_relative, "East"),
        "Target_North": await _find_child(target_relative, "North"),
        "UTM_North_ref_points": await _find_child(ref_points, "UTM_North"),
        "UTM_East_ref_points": await _find_child(ref_points, "UTM_East"),
        "UTM_Zone": await _find_child(cfg, "UTM_Zone"),
        "Grid_Cell_Size_dm": await _find_child(sts, "Grid_Cell_Size_dm"),
        "GPS_Grid_Loaded_Properly": await _find_child(sts, "GPS_Grid_Loaded_Properly"),
        "GPS_Grid_data": grid_root,
    }


async def _read_opcua_values(nodes) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, node in nodes.items():
        if name == "GPS_Grid_data":
            continue
        value = await node.read_value()
        if name in ("UTM_North_ref_points", "UTM_East_ref_points"):
            if not isinstance(value, (list, tuple)):
                raise RuntimeError(f"{name} non è stato esposto come array OPC UA")
            values[name] = [int(item) for item in value[:4]]
            if len(values[name]) != 4:
                raise RuntimeError(f"{name} contiene meno di 4 elementi")
        elif name == "Grid_Cell_Size_dm":
            values[name] = float(value)
        elif name == "GPS_Grid_Loaded_Properly":
            values[name] = bool(value)
        else:
            values[name] = int(value)
    return values


async def _read_values_in_batches(client, nodes):
    values = []
    for start in range(0, len(nodes), GRID_READ_BATCH_SIZE):
        values.extend(await client.get_values(nodes[start:start + GRID_READ_BATCH_SIZE]))
    return values


async def _read_grid_cells(client, grid_root, values):
    namespace = grid_root.nodeid.NamespaceIndex
    identifier = grid_root.nodeid.Identifier

    def cell_node(row, column, prop):
        node_id = f"ns={namespace};s={identifier}[{row}][{column}].{prop}"
        return client.get_node(node_id)

    all_indices = [(row, column) for row in range(GRID_ROWS) for column in range(GRID_COLUMNS)]
    included_nodes = [cell_node(row, column, "Included") for row, column in all_indices]
    included_values = await _read_values_in_batches(client, included_nodes)
    included = [
        index for index, value in zip(all_indices, included_values)
        if value is True
    ]

    requested_nodes = [
        cell_node(row, column, prop)
        for row, column in included
        for prop in GRID_CELL_PROPERTIES
    ]
    raw_values = await _read_values_in_batches(client, requested_nodes)

    north_offset_m = int(values["UTM_North_Offset"]) / coordinate_units_per_meter(
        int(values["UTM_North_Offset"])
    )
    east_offset_m = int(values["UTM_East_Offset"]) / coordinate_units_per_meter(
        int(values["UTM_North_Offset"])
    )
    zone = int(values["UTM_Zone"])
    cell_size_dm = float(values["Grid_Cell_Size_dm"])
    if cell_size_dm <= 0:
        raise RuntimeError(f"Grid_Cell_Size_dm non valido: {cell_size_dm}")
    half_size_m = cell_size_dm / 20.0

    cells = []
    invalid = []
    stride = len(GRID_CELL_PROPERTIES)
    for position, (row, column) in enumerate(included):
        cell_values = dict(zip(
            GRID_CELL_PROPERTIES,
            raw_values[position * stride:(position + 1) * stride],
        ))
        north_dm = cell_values["Center_Relative_North_dm"]
        east_dm = cell_values["Center_Relative_East_dm"]
        if not _is_numeric_center(north_dm) or not _is_numeric_center(east_dm):
            invalid.append(f"GPS_Grid_data[{row}][{column}]")
            continue

        northing_m = north_offset_m + float(north_dm) / 10.0
        easting_m = east_offset_m + float(east_dm) / 10.0
        corners_utm = [
            (easting_m - half_size_m, northing_m - half_size_m),
            (easting_m + half_size_m, northing_m - half_size_m),
            (easting_m + half_size_m, northing_m + half_size_m),
            (easting_m - half_size_m, northing_m + half_size_m),
        ]
        corners = [
            list(utm.to_latlon(easting, northing, zone, northern=True))
            for easting, northing in corners_utm
        ]
        cells.append({
            "row": row,
            "column": column,
            "path_index": int(cell_values["Path_Index"] or 0),
            "first_depth_read_cm": int(cell_values["First_Depth_Read_cm"] or 0),
            "last_depth_read_cm": int(cell_values["Last_Depth_Read_cm"] or 0),
            "target_depth_cm": int(cell_values["Target_Depth_cm"] or 0),
            "edges_crossed": int(cell_values["Edges_Crossed"] or 0),
            "error": bool(cell_values["Error"]),
            "corners": corners,
        })
    return cells, invalid, len(included)


def _is_numeric_center(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def _opcua_poll_loop(state: AppState):
    while not state.stop_event.is_set():
        try:
            async with Client(url=OPCUA_ENDPOINT, timeout=5) as client:
                nodes = await _resolve_opcua_nodes(client)
                grid_root = nodes["GPS_Grid_data"]
                next_grid_read = 0.0
                while not state.stop_event.is_set():
                    values = await _read_opcua_values(nodes)
                    now = time.monotonic()
                    if now >= next_grid_read:
                        try:
                            cells, invalid, included_count = await _read_grid_cells(
                                client, grid_root, values
                            )
                            grid_status = f"{included_count} celle incluse"
                            if invalid:
                                sample = ", ".join(invalid[:5])
                                extra = "…" if len(invalid) > 5 else ""
                                grid_status += f" · centri mancanti: {sample}{extra}"
                            if not values["GPS_Grid_Loaded_Properly"]:
                                grid_status += " · il PLC segnala reticolo non caricato"
                            with state.lock:
                                state.grid_cells = cells
                                state.grid_status = grid_status
                                state.grid_updated_at = time.time()
                        except Exception as grid_exc:
                            with state.lock:
                                state.grid_status = f"Errore lettura reticolo: {grid_exc}"
                        next_grid_read = time.monotonic() + GRID_POLL_SECONDS

                    with state.lock:
                        state.opcua_values = values
                        state.opcua_error = "Lettura attiva"
                        state.opcua_updated_at = time.time()
                    await asyncio.sleep(OPCUA_POLL_SECONDS)
        except Exception as exc:
            with state.lock:
                state.opcua_error = str(exc)
                state.grid_status = f"Errore connessione OPC UA: {exc}"
            await asyncio.sleep(3)


def _run_opcua_client(state: AppState):
    asyncio.run(_opcua_poll_loop(state))
