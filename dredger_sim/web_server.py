"""Server HTTP locale, API della mappa e comandi di pilotaggio."""

from __future__ import annotations

import json
import math
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any

import utm

from .config import COORDINATE_SCALE, CURSOR_IMAGE_PATH, HOST, HTML_PATH, TARGET_IMAGE_PATH, TCP_PORT
from .coordinates import reference_points_for_map, target_point_for_map
from .state import AppState
from .tcp_server import _local_ipv4_addresses

class SimulatorHandler(BaseHTTPRequestHandler):
    server_version = "DredgerSimulator/1.0"

    @property
    def state(self) -> AppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def _json_response(self, status: int, payload: dict[str, Any]):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/DRP ombra.png":
            try:
                body = CURSOR_IMAGE_PATH.read_bytes()
            except OSError as exc:
                self.send_error(404, f"Immagine cursore non disponibile: {exc}")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/gps.png":
            try:
                body = TARGET_IMAGE_PATH.read_bytes()
            except OSError as exc:
                self.send_error(404, f"Immagine del target non disponibile: {exc}")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/opcua/read":
            if not secrets.compare_digest(self.headers.get("X-App-Token", ""), self.state.token):
                self._json_response(403, {"ok": False, "error": "Richiesta locale non autorizzata."})
                return
            with self.state.lock:
                values = self.state.opcua_values.copy()
                error = self.state.opcua_error
                updated_at = self.state.opcua_updated_at
                grid_cells = list(self.state.grid_cells)
                grid_status = self.state.grid_status
                grid_updated_at = self.state.grid_updated_at
            payload = {"ok": True, "values": values, "status": error,
                       "error": error if error != "Lettura attiva" else None,
                       "updated_at": updated_at, "grid_cells": grid_cells,
                       "grid_status": grid_status, "grid_updated_at": grid_updated_at,
                       "target_point": None}
            norths = values.get("UTM_North_ref_points", [])
            easts = values.get("UTM_East_ref_points", [])
            points_configured = any(value != 0 for value in [*norths, *easts])
            payload["reference_points_configured"] = points_configured
            if values:
                try:
                    payload["target_point"] = target_point_for_map(values)
                except Exception as exc:
                    payload["target_error"] = str(exc)
            if points_configured:
                try:
                    payload["ref_points"] = reference_points_for_map(values)
                except Exception as exc:
                    payload["coordinates_error"] = str(exc)
            else:
                payload["ref_points"] = []
                payload["grid_cells"] = []
                payload["grid_status"] = "punti di riferimento non configurati"
            self._json_response(200, payload)
            return
        if path not in ("/", "/mappa.html"):
            self.send_error(404)
            return
        try:
            addresses = _local_ipv4_addresses()
            endpoint = ", ".join(f"{a}:{TCP_PORT}" for a in addresses) or f"IP del PC:{TCP_PORT}"
            page = HTML_PATH.read_text(encoding="utf-8")
            page = page.replace("__APP_TOKEN__", self.state.token)
            page = page.replace("__TCP_ENDPOINT__", endpoint)
            body = page.encode("utf-8")
        except OSError as exc:
            self.send_error(500, f"Impossibile leggere mappa.html: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            request_token = self.headers.get("X-App-Token", "")
            if not secrets.compare_digest(request_token, self.state.token):
                self._json_response(403, {"ok": False, "error": "Richiesta locale non autorizzata."})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 100_000:
                raise ValueError("Richiesta troppo grande.")
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(data, dict):
                raise ValueError("Formato richiesta non valido.")

            path = urllib.parse.urlparse(self.path).path
            if path == "/api/position":
                result = self._set_position(data)
            elif path == "/api/drive":
                result = self._drive_position(data)
            else:
                self.send_error(404)
                return
            self._json_response(200, {"ok": True, **result})
        except ValueError as exc:  # include JSONDecodeError
            self._json_response(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json_response(500, {"ok": False, "error": str(exc)})

    def _set_position(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            lat, lon = float(data["lat"]), float(data["lon"])
            easting, northing, zone, band = utm.from_latlon(lat, lon)
        except KeyError as exc:
            raise ValueError("La richiesta non contiene latitudine e longitudine.") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Posizione non convertibile in UTM: {exc}") from exc

        with self.state.lock:
            heading = self.state.heading_deg

        position = {
            "lat": lat, "lon": lon, "easting": easting, "northing": northing,
            "zone": zone, "band": band, "heading_deg": heading,
            "easting_cm": round(easting * COORDINATE_SCALE),
            "northing_cm": round(northing * COORDINATE_SCALE),
        }
        with self.state.lock:
            self.state.position = position
        line = self.state.tcp_message().decode("ascii").rstrip("\r\n")
        return {"position": position,
                "message": "Posizione pronta per il prossimo invio TCP.",
                "tcp_line": line}

    def _drive_position(self, data: dict[str, Any]) -> dict[str, Any]:
        def finite_number(name: str, default: float = 0.0) -> float:
            value = data.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} deve essere numerico.")
            result = float(value)
            if not math.isfinite(result):
                raise ValueError(f"{name} non è valido.")
            return result

        delta_east = finite_number("delta_east_m")
        delta_north = finite_number("delta_north_m")
        heading_delta = finite_number("heading_delta_deg")
        if abs(delta_east) > 1000 or abs(delta_north) > 1000:
            raise ValueError("Ogni comando di movimento è limitato a 1000 metri.")
        if abs(heading_delta) > 180:
            raise ValueError("Ogni comando di rotazione è limitato a 180 gradi.")

        with self.state.lock:
            current = self.state.position.copy() if self.state.position else None
            heading = (self.state.heading_deg + heading_delta) % 360
            self.state.heading_deg = round(heading, 6)
            if current is None:
                raise ValueError("Prima scegli la posizione iniziale cliccando sulla mappa.")

            easting, northing, zone, _band = utm.from_latlon(current["lat"], current["lon"])
            easting += delta_east
            northing += delta_north
            lat, lon = utm.to_latlon(easting, northing, zone, northern=current["lat"] >= 0)
            current.update({
                "lat": lat, "lon": lon, "easting": easting, "northing": northing,
                "zone": zone, "heading_deg": self.state.heading_deg,
                "easting_cm": round(easting * COORDINATE_SCALE),
                "northing_cm": round(northing * COORDINATE_SCALE),
            })
            self.state.position = current

        line = self.state.tcp_message().decode("ascii").rstrip("\r\n")
        return {"position": current, "message": "Comando draga applicato.", "tcp_line": line}
    def log_message(self, format, *args):
        return
