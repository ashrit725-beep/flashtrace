"""Capture strategy registry (SPEC.md section 6.2: A ``waapi_step`` -> B ``virtual_time`` -> C ``realtime``).

To add a strategy, implement ``CaptureStrategy`` in its own module and register it here with one
line, for example::

    from capture.strategies.virtual_time import VirtualTimeStrategy
    REGISTRY["virtual_time"] = VirtualTimeStrategy

``capturer.capture`` walks ``FALLBACK_ORDER`` in "auto" mode and raises ``NotImplementedError`` (with
the reason it needed to fall through) when it reaches a name that is not registered yet.
"""

from __future__ import annotations

from capture.strategies.base import CaptureSettings, CaptureStrategy, FrameSink, StrategyOutcome
from capture.strategies.realtime import RealtimeStrategy
from capture.strategies.virtual_time import VirtualTimeStrategy
from capture.strategies.waapi_step import WaapiStepStrategy

#: Order tried by ``strategy="auto"``; also the complete list of valid strategy names.
FALLBACK_ORDER: tuple[str, ...] = ("waapi_step", "virtual_time", "realtime")

#: Implemented strategies by name, in fall-through order (SPEC 6.2 A -> B -> C).
REGISTRY: dict[str, type[CaptureStrategy]] = {
    WaapiStepStrategy.name: WaapiStepStrategy,
    VirtualTimeStrategy.name: VirtualTimeStrategy,
    RealtimeStrategy.name: RealtimeStrategy,
}

__all__ = [
    "CaptureSettings",
    "CaptureStrategy",
    "FALLBACK_ORDER",
    "FrameSink",
    "REGISTRY",
    "RealtimeStrategy",
    "StrategyOutcome",
    "VirtualTimeStrategy",
    "WaapiStepStrategy",
]
