"""Stato condiviso e composizione della riga GPS inviata via TCP."""

from __future__ import annotations

import secrets
import threading
from typing import Any

class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.position: dict[str, Any] | None = None
        self.heading_deg = 0
        self.token = secrets.token_urlsafe(32)
        self.stop_event = threading.Event()
        self.tcp_ready = threading.Event()
        self.tcp_error: str | None = None
        self.tcp_server: Any = None
        self.opcua_values: dict[str, Any] = {}
        self.opcua_error = "Connessione al PLC in corso…"
        self.opcua_updated_at: float | None = None
        self.grid_cells: list[dict[str, Any]] = []
        self.grid_status = "Lettura reticolo OPC UA in corso…"
        self.grid_updated_at: float | None = None

    def tcp_message(self) -> bytes:
        with self.lock:
            position = self.position.copy() if self.position else None

        if position:
            north = position["northing_cm"]
            east = position["easting_cm"]
            zone = position["zone"]
            band = position["band"]
            band_number = ord(band) if band else 0
            heading = int(round(position.get("heading_deg", 0) * 100))
        else:
            north = east = zone = band_number = heading = 0
            band = ""

        # Campi non ancora simulati: zero provvisorio.
        fields = [north, east, 0, zone, band, band_number, heading, 0, 15, 0]
        return (";".join(str(v) for v in fields) + "\r\n").encode("ascii")
