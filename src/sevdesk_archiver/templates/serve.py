#!/usr/bin/env python3
"""Local viewer for this SevDesk archive.

Serves this directory over HTTP and opens the browser. The `index.html`
viewer fetches `manifest.json` and individual files under `files/`, which
only works over http:// (not file://).

Standard library only — no pip install, no dependencies.

Usage:
    python3 serve.py              # default port 8765
    python3 serve.py 9000         # custom port
    python3 serve.py 9000 all     # bind on 0.0.0.0 (LAN-visible)
"""

import http.server
import os
import socketserver
import sys
import webbrowser
from functools import partial

DEFAULT_PORT = 8765


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    try:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        print(f"Invalid port: {sys.argv[1]!r} (expected 1-65535)", file=sys.stderr)
        return 2
    host = "0.0.0.0" if (len(sys.argv) > 2 and sys.argv[2] == "all") else "127.0.0.1"

    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=here)

    try:
        httpd = Server((host, port), handler)
    except OSError as e:
        print(f"Could not bind {host}:{port} — {e}", file=sys.stderr)
        return 1

    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/index.html"
    print(f"Serving {here}")
    print(f"Open:  {url}")
    print("Stop:  Ctrl+C")
    if host == "0.0.0.0":
        print(
            f"WARNING: listening on all interfaces — anyone who can reach port "
            f"{port} can read the whole archive. There is no authentication.",
            file=sys.stderr,
        )

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
