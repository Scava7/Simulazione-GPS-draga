"""Avvio del simulatore di posizione per la draga."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from dredger_sim.config import HOST, HTML_PATH, SEND_INTERVAL_SECONDS, TCP_PORT
from dredger_sim.state import AppState
from dredger_sim.tcp_server import _local_ipv4_addresses, _run_tcp_server
from dredger_sim.opcua_client import _run_opcua_client
from dredger_sim.web_server import SimulatorHandler

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
