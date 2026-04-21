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


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    host = "0.0.0.0" if (len(sys.argv) > 2 and sys.argv[2] == "all") else "127.0.0.1"

    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=here)

    try:
        httpd = socketserver.ThreadingTCPServer((host, port), handler)
    except OSError as e:
        print(f"Could not bind {host}:{port} — {e}", file=sys.stderr)
        return 1

    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/index.html"
    print(f"Serving {here}")
    print(f"Open:  {url}")
    print("Stop:  Ctrl+C")

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
