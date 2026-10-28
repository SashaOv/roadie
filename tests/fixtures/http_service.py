"""Serve a loopback health endpoint for process and executor tests."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer


class HealthServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer resolves the bound IP before listening; loopback tests
        # must start without depending on the host's DNS resolver.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address


def serve(port: int, ready: Path | None) -> None:
    class Handler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self) -> None:
            Handler.hits += 1
            healthy = self.path == "/health" and Handler.hits >= 3
            if healthy and ready is not None:
                ready.write_text("ok")
            self.send_response(200 if healthy else 503)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HealthServer(("127.0.0.1", port), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("ready", type=Path, nargs="?")
    arguments = parser.parse_args()
    serve(arguments.port, arguments.ready)
