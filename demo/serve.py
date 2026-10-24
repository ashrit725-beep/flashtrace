"""Tiny local server for the FlashTrace demo site. Standard library only.

It serves the ``demo/`` directory so that:

* ``/``       is the warning interstitial (``demo/index.html``), and
* ``/site/``  is the deliberately flashing test page (``demo/site/index.html``).

The CLI (``flashtrace demo``) imports :func:`serve`; keep its signature.
The server binds to the loopback interface only: the test page is hazardous
to photosensitive people and must never be reachable from another machine.
"""

from __future__ import annotations

import argparse
import functools
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The directory this file lives in; it holds index.html and site/.
DEMO_DIR = Path(__file__).resolve().parent

# Only these hosts are accepted. Anything else would expose the flashing page
# to the local network, which SPEC.md section 9 forbids.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})

WARNING_LINE = (
    "WARNING: /site/ flashes at 4-5 Hz on purpose. Do not open it if you are "
    "photosensitive. The audit runs headless and does not need you to look."
)


class _NoStoreHandler(SimpleHTTPRequestHandler):
    """Plain static file handler that asks browsers not to cache anything.

    No caching means an edit to the demo page shows up on the next request,
    which matters when people tweak the page to try different fixes.
    """

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # One short line per request on stderr; stdout is kept for the URLs.
        sys.stderr.write("demo  " + (format % args) + "\n")


def serve(port: int = 8765, host: str = "127.0.0.1", open_browser: bool = False) -> None:
    """Serve the demo on ``http://host:port/`` until Ctrl-C.

    ``open_browser=True`` opens the warning interstitial (never ``/site/``
    directly) in the default browser.
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"refusing to bind {host!r}: the demo page flashes and may only be "
            f"served on the loopback interface ({', '.join(sorted(LOOPBACK_HOSTS))})"
        )

    handler = functools.partial(_NoStoreHandler, directory=str(DEMO_DIR))
    with ThreadingHTTPServer((host, port), handler) as httpd:
        bound_port = httpd.server_address[1]  # real port, also when port=0
        root_url = f"http://{host}:{bound_port}/"
        site_url = root_url + "site/"

        print(f"FlashTrace demo server (serving {DEMO_DIR})")
        print(f"  Interstitial : {root_url}")
        print(f"  Flashing page: {site_url}")
        print(f"  Audit it with: flashtrace audit {site_url}")
        print(f"  {WARNING_LINE}")
        print("  Press Ctrl-C to stop.", flush=True)

        if open_browser:
            webbrowser.open(root_url)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nDemo server stopped.")


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the FlashTrace demo site on localhost.")
    parser.add_argument("--port", type=int, default=8765, help="TCP port (default 8765; 0 picks a free one)")
    parser.add_argument("--open", action="store_true", help="open the warning interstitial in a browser")
    args = parser.parse_args(argv)
    serve(port=args.port, open_browser=args.open)


if __name__ == "__main__":
    _main()
