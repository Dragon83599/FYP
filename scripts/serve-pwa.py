#!/usr/bin/env python3
"""Serve pwa/ with explicit MIME types, because the OS MIME database lies.

Why this exists
---------------
On Windows, ``HKEY_CLASSES_ROOT\\.js\\Content Type`` is ``text/plain`` on many
machines (some installers rewrite it). Python's ``mimetypes`` module reads that
key, so ``python -m http.server`` serves ``.js`` as ``text/plain`` there. Classic
scripts still run, but **ES modules are rejected** -- the browser reports
"Failed to fetch dynamically imported module" and the page stays on its loading
message with nothing in the console unless you know to look at Content-Type.

That failure is silent, platform-specific and looks exactly like a network
problem, so this server ignores the OS database and maps the extensions this app
uses. It also sends ``Cache-Control: no-store`` so a reload during a demo never
shows a stale page.

Usage
-----
    python3 scripts/serve-pwa.py                 # port 8080, serves ../pwa
    python3 scripts/serve-pwa.py 9000
    python scripts\\serve-pwa.py --dir C:\\Users\\me\\Desktop\\pwa
    python3 scripts/serve-pwa.py --self-check    # verify the MIME types, exit
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import sys
import threading
import urllib.request
from pathlib import Path

# Extensions the page actually requests. Anything else falls back to the
# stdlib map, so this stays a small override rather than a second MIME database.
MIME_OVERRIDES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".bin": "application/octet-stream",
    ".wav": "audio/wav",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".md": "text/plain; charset=utf-8",
}

# What the self-check insists on: modules live or die by the first two.
REQUIRED = {
    "/": ("text/html", True),
    "/app.js": ("text/javascript", True),
    "/preprocess.js": ("text/javascript", True),
    "/filters/resample-poly.js": ("text/javascript", True),
    "/model/model.json": ("application/json", True),
    "/model/group1-shard1of1.bin": ("application/octet-stream", True),
    "/vendor/tf.min.js": ("text/javascript", True),
    # PWA plumbing: these types decide whether the browser will install the app.
    "/manifest.json": ("application/json", True),
    "/sw.js": ("text/javascript", True),
    "/pwa.js": ("text/javascript", True),
    "/icons/icon-192.png": ("image/png", True),
}


class Handler(http.server.SimpleHTTPRequestHandler):
    # SimpleHTTPRequestHandler.guess_type() consults extensions_map before it
    # falls back to mimetypes, which is where the registry value would come in.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        **MIME_OVERRIDES,
    }

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def copyfile(self, source, outputfile) -> None:
        # A client that stops reading (a browser cancelling a fetch, or the
        # self-check closing early) must not print a traceback.
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def make_server(directory: Path, port: int, host: str) -> http.server.ThreadingHTTPServer:
    handler = functools.partial(Handler, directory=str(directory))
    return http.server.ThreadingHTTPServer((host, port), handler)


def lan_addresses() -> list[str]:
    addresses = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127.") and address not in addresses:
                addresses.append(address)
    except OSError:
        pass
    return addresses


def self_check(directory: Path) -> int:
    """Serve on a free port in a thread and assert the Content-Type of each asset."""
    server = make_server(directory, 0, "127.0.0.1")
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    failures = 0
    try:
        for path, (expected, required) in REQUIRED.items():
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                    content_type = response.headers.get_content_type()
                    body = response.read()   # read it fully so the socket closes cleanly
                    length = len(body)
            except Exception as error:  # noqa: BLE001 - report, do not raise
                state = "missing" if required else "skipped"
                print(f"{state:8s} {path}: {error}")
                failures += 1 if required else 0
                continue
            ok = content_type.startswith(expected)
            print(f"{'ok' if ok else 'WRONG':8s} {path}: {content_type} ({length} B)")
            if not ok and required:
                failures += 1
    finally:
        server.shutdown()
        server.server_close()
    print("\nself-check", "FAILED" if failures else "passed", f"({failures} problem(s))")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", type=int, default=8080)
    parser.add_argument("--dir", type=Path, default=root / "pwa",
                        help="directory to serve (default: the repository's pwa/)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--self-check", action="store_true",
                        help="serve on a free port, verify the MIME types, exit")
    args = parser.parse_args(argv)

    directory = args.dir.resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit(f"not found: {directory / 'index.html'}")

    if args.self_check:
        return self_check(directory)

    server = make_server(directory, args.port, args.host)
    print(f"serving {directory}")
    print(f"  this machine : http://localhost:{args.port}/")
    for address in lan_addresses():
        print(f"  phone / LAN  : http://{address}:{args.port}/")
    print("\nContent-Type is pinned by this script (.js -> text/javascript), so ES")
    print("modules load even where the OS MIME database says text/plain.")
    print("Ctrl-C stops the server.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
