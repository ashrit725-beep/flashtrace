"""Parameter grids and per-page sampling for the FlashTrace benchmark.

`sample_page(rng, index)` draws every parameter of one page from a seeded
`random.Random` in a fixed order, so the same seed always yields the same
page. Nothing in this module touches files, the browser or the engine.

Geometry is counted in 4 x 4 px cells (ADR-002). The engine's 10-degree window
is 85 x 64 cells = 5440 cells (ADR-006) and a flash qualifies when it covers
MORE than 25% of a window, i.e. more than 1360 cells (SPEC T4).
"""
from dataclasses import dataclass
import random

CELL_PX = 4
VIEWPORT_PX = (1024, 768)                     # SPEC T4 reference viewport
VIEWPORT_CELLS = (VIEWPORT_PX[0] // CELL_PX, VIEWPORT_PX[1] // CELL_PX)   # 256 x 192
WINDOW_PX = (341, 256)                        # WCAG Note 1 window
WINDOW_CELLS = (85, 64)                       # ADR-006: round(341/4), 256/4
WINDOW_CELL_COUNT = WINDOW_CELLS[0] * WINDOW_CELLS[1]                     # 5440
QUALIFYING_CELLS = WINDOW_CELL_COUNT // 4     # 1360; strictly more qualifies

# Sampling grids (the build brief fixes these values).
RATES_HZ = (1, 2, 3, 3.5, 4, 6, 8, 10)        # full on/off cycles per second
AREA_FRACTIONS = (0.05, 0.20, 0.24, 0.26, 0.40, 0.60)
DELTA_L_TARGETS = (0.05, 0.09, 0.11, 0.30, 0.60)
BASE_LUMINANCES = (0.05, 0.20, 0.40)
ELEMENT_COUNTS = (1, 2, 3, 4)
MECHANISMS = ("css", "waapi", "raf", "canvas", "video")
DISTRACTORS = ("fade", "pattern", "marquee")

# Top-left anchors (in cells) for the 85 x 64-cell region that holds every
# flashing element. 171 + 85 = 256 and 128 + 64 = 192, so the region always
# stays inside the 1024 x 768 viewport.
REGION_ANCHORS_X = (0, 24, 64, 120, 171)
REGION_ANCHORS_Y = (0, 16, 48, 96, 128)

WHITE = (255, 255, 255)
LIGHT_GREY = (240, 240, 240)
GREY_BACKGROUND_EVERY = 7                     # every 7th page gets the grey background


@dataclass(frozen=True)
class PageParams:
    """Everything the generator samples for one page, before any geometry."""
    index: int
    rate_hz: float
    area_fraction: float
    delta_l_target: float
    base_luminance: float
    red: bool
    element_count: int
    mechanism: str
    distractors: tuple
    region_x: int            # cells
    region_y: int            # cells
    background: tuple        # (r, g, b)


def page_rng(seed, index):
    """A random stream that depends only on (seed, index).

    Seeding per page means page 7 is the same whether 15 or 200 pages are
    generated, which keeps small test runs comparable with the full set.
    """
    return random.Random(f"flashtrace-bench:{seed}:{index}")


def choose_base_luminance(rng, delta_l_target):
    """Pick the darker state's luminance so the brighter state stays <= 1.0.

    If base + step would exceed white, step down to the next lower grid value
    (the brief's rule). With the current grids 0.40 + 0.60 = 1.0 is the
    largest sum, so the loop is a guard rather than a frequent path.
    """
    base = rng.choice(BASE_LUMINANCES)
    while base + delta_l_target > 1.0 + 1e-9:
        lower = [value for value in BASE_LUMINANCES if value < base]
        if not lower:
            break
        base = max(lower)
    return base


def background_for(index):
    """White, except every 7th page (1-based) which is light grey."""
    if index % GREY_BACKGROUND_EVERY == 0:
        return LIGHT_GREY
    return WHITE


def sample_page(rng, index):
    """Draw one page's parameters. The draw order is part of the contract."""
    rate_hz = rng.choice(RATES_HZ)
    area_fraction = rng.choice(AREA_FRACTIONS)
    delta_l_target = rng.choice(DELTA_L_TARGETS)
    red = rng.choice((False, True))
    base_luminance = choose_base_luminance(rng, delta_l_target)
    element_count = rng.choice(ELEMENT_COUNTS)
    mechanism = rng.choice(MECHANISMS)
    # Each distractor is in or out with probability one half: a random subset.
    distractors = tuple(name for name in DISTRACTORS if rng.random() < 0.5)
    region_x = rng.choice(REGION_ANCHORS_X)
    region_y = rng.choice(REGION_ANCHORS_Y)
    return PageParams(
        index=index,
        rate_hz=rate_hz,
        area_fraction=area_fraction,
        delta_l_target=delta_l_target,
        base_luminance=base_luminance,
        red=red,
        element_count=element_count,
        mechanism=mechanism,
        distractors=distractors,
        region_x=region_x,
        region_y=region_y,
        background=background_for(index),
    )


def target_cells(area_fraction):
    """The number of flashing cells that `area_fraction` of one window means."""
    return round(area_fraction * WINDOW_CELL_COUNT)
