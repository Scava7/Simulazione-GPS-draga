"""Simulatore di posizione per draga: mappa Leaflet in Chrome e protocollo TCP testuale."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections import deque
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import utm
from asyncua import Client

HOST = "127.0.0.1"
TCP_BIND_HOST = "0.0.0.0"
TCP_PORT = 5020
SEND_INTERVAL_SECONDS = 1
COORDINATE_SCALE = 10  # metri -> centimetri
HTML_PATH = Path(__file__).with_name("mappa.html")
CURSOR_IMAGE_PATH = Path(__file__).with_name("DRP ombra.png")
OPCUA_ENDPOINT = "opc.tcp://192.168.10.30:4840"
OPCUA_POLL_SECONDS = 1
OPCUA_TAGS = {
    "UTM_North_Offset": ["IO", "GPS", "Sts", "UTM_North_Offset"],
    "UTM_East_Offset": ["IO", "GPS", "Sts", "UTM_East_Offset"],
    "UTM_North_ref_points": ["IO", "GPS", "Cfg", "stRef_Points", "UTM_North"],
    "UTM_East_ref_points": ["IO", "GPS", "Cfg", "stRef_Points", "UTM_East"],
}


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.position: dict[str, Any] | None = None
        self.token = secrets.token_urlsafe(32)
        self.stop_event = threading.Event()
        self.tcp_ready = threading.Event()
        self.tcp_error: str | None = None
        self.tcp_server: DredgerTCPServer | None = None
        self.opcua_values: dict[str, Any] = {}
        self.opcua_error = "Connessione al PLC in corso…"
        self.opcua_updated_at: float | None = None

    def tcp_message(self) -> bytes:
        with self.lock:
            position = self.position.copy() if self.position else None

        if position:
            north = position["northing_cm"]
            east = position["easting_cm"]
            zone = position["zone"]
            band = position["band"]
            band_number = ord(band) if band else 0
        else:
            north = east = zone = band_number = 0
            band = ""

        # Campi non ancora simulati: zero provvisorio.
        fields = [north, east, 0, zone, band, band_number, 0, 0, 15, 0]
        return (";".join(str(v) for v in fields) + "\r\n").encode("ascii")


def _local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


# ---------------------------- Server TCP ----------------------------

class DredgerTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, app_state: AppState):
        self.app_state = app_state
        super().__init__(address, handler)


class DredgerTCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        state: AppState = self.server.app_state  # type: ignore[attr-defined]
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        print(f"Client TCP collegato: {peer}")
        self.request.settimeout(3)
        try:
            while not state.stop_event.is_set():
                self.request.sendall(state.tcp_message())
                if state.stop_event.wait(SEND_INTERVAL_SECONDS):
                    break
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            print(f"Client TCP disconnesso: {peer}")


def _run_tcp_server(state: AppState):
    try:
        server = DredgerTCPServer((TCP_BIND_HOST, TCP_PORT), DredgerTCPHandler, state)
        state.tcp_server = server
        state.tcp_ready.set()
        print(f"Server TCP in ascolto sulla porta {TCP_PORT}.")
        server.serve_forever(poll_interval=0.5)
    except Exception as exc:
        state.tcp_error = str(exc)
        state.tcp_ready.set()




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
    return {
        "UTM_North_Offset": await _find_child(sts, "UTM_North_Offset"),
        "UTM_East_Offset": await _find_child(sts, "UTM_East_Offset"),
        "UTM_North_ref_points": await _find_child(ref_points, "UTM_North"),
        "UTM_East_ref_points": await _find_child(ref_points, "UTM_East"),
    }


async def _read_opcua_values(nodes) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, node in nodes.items():
        value = await node.read_value()
        if name in ("UTM_North_ref_points", "UTM_East_ref_points"):
            if not isinstance(value, (list, tuple)):
                raise RuntimeError(f"{name} non è stato esposto come array OPC UA")
            values[name] = [int(item) for item in value[:4]]
            if len(values[name]) != 4:
                raise RuntimeError(f"{name} contiene meno di 4 elementi")
        else:
            values[name] = int(value)
    return values


async def _opcua_poll_loop(state: AppState):
    while not state.stop_event.is_set():
        try:
            async with Client(url=OPCUA_ENDPOINT, timeout=5) as client:
                nodes = await _resolve_opcua_nodes(client)
                while not state.stop_event.is_set():
                    values = await _read_opcua_values(nodes)
                    with state.lock:
                        state.opcua_values = values
                        state.opcua_error = "Lettura attiva"
                        state.opcua_updated_at = time.time()
                    await asyncio.sleep(OPCUA_POLL_SECONDS)
        except Exception as exc:
            with state.lock:
                state.opcua_error = str(exc)
            await asyncio.sleep(3)


def _run_opcua_client(state: AppState):
    asyncio.run(_opcua_poll_loop(state))




# ---------------------------- Server HTTP ----------------------------

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
        if path == "/api/opcua/read":
            if not secrets.compare_digest(self.headers.get("X-App-Token", ""), self.state.token):
                self._json_response(403, {"ok": False, "error": "Richiesta locale non autorizzata."})
                return
            with self.state.lock:
                values = self.state.opcua_values.copy()
                error = self.state.opcua_error
                updated_at = self.state.opcua_updated_at
            self._json_response(200, {"ok": True, "values": values, "status": error,
                                      "error": error if error != "Lettura attiva" else None,
                                      "updated_at": updated_at})
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
            if path != "/api/position":
                self.send_error(404)
                return
            result = self._set_position(data)
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

        position = {
            "lat": lat, "lon": lon, "easting": easting, "northing": northing,
            "zone": zone, "band": band,
            "easting_cm": round(easting * COORDINATE_SCALE),
            "northing_cm": round(northing * COORDINATE_SCALE),
        }
        with self.state.lock:
            self.state.position = position
        line = self.state.tcp_message().decode("ascii").rstrip("\r\n")
        return {"position": position,
                "message": "Posizione pronta per il prossimo invio TCP.",
                "tcp_line": line}

    def log_message(self, format, *args):
        return


# ---------------------------- Chrome / main ----------------------------

def _find_chrome() -> str | None:
    for command in ("chrome.exe", "chrome", "google-chrome"):
        found = shutil.which(command)
        if found:
            return found
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidate = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                return str(candidate)
    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(
                        hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
                    ) as key:
                        return winreg.QueryValue(key, None)
                except OSError:
                    pass
        except ImportError:
            pass
    return None


def main():
    if not HTML_PATH.is_file():
        raise SystemExit(f"File mappa mancante: {HTML_PATH}")
    chrome = _find_chrome()
    if not chrome:
        raise SystemExit("Chrome non trovato. Installa Google Chrome e rilancia il simulatore.")

    state = AppState()
    threading.Thread(target=_run_tcp_server, args=(state,), daemon=True).start()
    threading.Thread(target=_run_opcua_client, args=(state,), daemon=True).start()
    state.tcp_ready.wait(timeout=3)

    server = ThreadingHTTPServer((HOST, 0), SimulatorHandler)
    server.app_state = state  # type: ignore[attr-defined]
    url = f"http://{HOST}:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    subprocess.Popen([chrome, "--new-window", url], close_fds=True)

    print("Simulatore avviato in Chrome.")
    print(f"Indirizzo locale: {url}")
    if state.tcp_error:
        print(f"ERRORE server TCP: {state.tcp_error}")
    else:
        local = _local_ipv4_addresses()
        print(f"Server TCP in ascolto sulla porta {TCP_PORT} (tutte le interfacce).")
        print("IP del PC da configurare nel PLC: " + (", ".join(local) if local else "non rilevato"))
        print(f"Invio della stringa ogni {SEND_INTERVAL_SECONDS} secondi; terminatore CRLF.")
    print("Lascia aperto questo terminale. Premi Ctrl+C per arrestare.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("Arresto del simulatore…")
    finally:
        state.stop_event.set()
        if state.tcp_server:
            state.tcp_server.shutdown()
            state.tcp_server.server_close()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
