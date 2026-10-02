"""Server TCP che invia periodicamente i dati simulati al PLC."""

from __future__ import annotations

import socket
import socketserver
import threading

from .config import SEND_INTERVAL_SECONDS, TCP_BIND_HOST, TCP_PORT
from .state import AppState

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
