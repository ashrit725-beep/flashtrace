"""Letting the page's network requests finish before virtual time moves on (strategy A, SPEC 6.2).

Strategy A advances virtual time much faster than real time. A request the page starts while virtual
time advances (an image whose ``src`` a timer sets, a ``fetch`` whose answer changes the page) would
otherwise finish at a frame chosen by network speed, so two captures of the same page would differ
while both claimed to be deterministic. ``NetworkTracker`` counts the requests the page has started and
not yet finished; before every frame (and every interaction pump) the strategy waits, in real time,
until none is left. In virtual time every request therefore answers instantly, which is the same for
every run.

A request still unfinished after ``NETWORK_WAIT_MS`` of real time (a stalled server, a stream that
never ends) is not waited for again. It is reported, and the strategy marks the capture
``deterministic=False``, because its answer can arrive at any later frame.

Strategy B gets the same behaviour from Chromium's virtual-time policy, which pauses virtual time while
fetches are pending.
"""

from __future__ import annotations

import time
from typing import Any

#: Real milliseconds one request may stay unfinished before it is given up on and reported.
NETWORK_WAIT_MS = 5000

#: Real milliseconds between two looks at the in-flight list while waiting.
POLL_MS = 10


class NetworkTracker:
    """Keeps the page's unfinished requests, fed by Playwright's request events."""

    def __init__(self) -> None:
        # request -> (url, real time it started, from time.monotonic())
        self._in_flight: dict[Any, tuple[str, float]] = {}
        # URLs given up on since the last call to take_abandoned()
        self._abandoned: list[str] = []
        # URLs of image requests started since the last call to take_image_requests()
        self._images: list[str] = []

    def attach(self, page: Any) -> None:
        """Start listening to ``page`` (call before it navigates, so load requests are seen too)."""
        page.on("request", self._started)
        page.on("requestfinished", self._ended)
        page.on("requestfailed", self._ended)

    def _started(self, request: Any) -> None:
        self._in_flight[request] = (request.url, time.monotonic())
        if getattr(request, "resource_type", None) == "image":
            self._images.append(request.url)

    def _ended(self, request: Any) -> None:
        self._in_flight.pop(request, None)

    @property
    def in_flight(self) -> int:
        """How many requests are still being waited for."""
        return len(self._in_flight)

    def _give_up_on_slow(self) -> None:
        """Stop waiting for requests older than ``NETWORK_WAIT_MS`` and remember their URLs."""
        now = time.monotonic()
        for request, (url, started) in list(self._in_flight.items()):
            if (now - started) * 1000 >= NETWORK_WAIT_MS:
                del self._in_flight[request]
                self._abandoned.append(url)

    def wait_for_idle(self, page: Any) -> None:
        """Wait in real time until no request is in flight (slow ones are given up on, see above).

        ``page.wait_for_timeout`` waits in the Playwright driver, not with the page's frozen timers, and
        lets Playwright deliver the request events that empty the list.
        """
        while True:
            self._give_up_on_slow()
            if not self._in_flight:
                return
            page.wait_for_timeout(POLL_MS)

    def take_abandoned(self) -> list[str]:
        """URLs given up on since the previous call (each URL is reported once)."""
        abandoned, self._abandoned = self._abandoned, []
        return abandoned

    def take_image_requests(self) -> list[str]:
        """URLs of the image requests (``<img>``, CSS images, ...) the page started since the previous call."""
        images, self._images = self._images, []
        return images
