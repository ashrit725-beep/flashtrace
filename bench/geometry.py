"""Cell-aligned geometry for one benchmark page.

Every size and position here is in 4 x 4 px cells (ADR-002), so the number of
flashing cells the engine will see equals the number the truth file records.

Layout rules (from the build brief):

* All flashing elements sit inside ONE 85 x 64-cell region (one window) as
  non-overlapping rectangles of at least 2 x 2 cells, placed side by side.
* Distractors (fade, pattern, marquee) and the static text never overlap the
  flashing elements and stay at least 2 cells (8 px) away from the region.
  The marquee really does change pixels as its text scrolls, so it is kept a
  whole window away (85 cells horizontally or 64 cells vertically): no window
  position can then contain both marquee cells and flashing cells, and the
  marquee can never add to the flashing area.
"""
from dataclasses import dataclass

from bench.params import CELL_PX, VIEWPORT_CELLS, WINDOW_CELLS

MIN_SIDE = 2                 # smallest element side, in cells
ELEMENT_GAP = 1              # cells between neighbouring flashing elements
ITEM_GAP = 1                 # cells between distractors / text and each other
NEAR_GAP = 2                 # cells between the region and any other item (8 px)
EDGE_MARGIN = 2              # cells kept free along the viewport edges
MAX_SPLIT_ATTEMPTS = 500
MAX_PLACE_TRIES = 50

# Sizes of the non-flashing items, in cells (the brief gives them in px).
TEXT_SIZE = (110, 36)        # 440 x 144 px box for the heading and paragraph
DISTRACTOR_SIZES = {
    "fade": (75, 30),        # 300 x 120 px block whose opacity fades at 0.5 Hz
    "pattern": (50, 50),     # 200 x 200 px static checkerboard
    "marquee": (100, 10),    # 400 x 40 px strip of scrolling text
}


@dataclass(frozen=True)
class Box:
    """A rectangle in cells: top-left (x, y), width w, height h."""
    x: int
    y: int
    w: int
    h: int

    @property
    def cells(self):
        return self.w * self.h

    def to_px(self):
        """The same rectangle in CSS pixels, as the dict the truth file stores."""
        return {"x": self.x * CELL_PX, "y": self.y * CELL_PX,
                "w": self.w * CELL_PX, "h": self.h * CELL_PX}

    def overlaps(self, other, gap=0):
        """True when the two boxes touch or come closer than `gap` cells."""
        separated = (self.x + self.w + gap <= other.x or other.x + other.w + gap <= self.x
                     or self.y + self.h + gap <= other.y or other.y + other.h + gap <= self.y)
        return not separated

    def contains(self, other):
        return (self.x <= other.x and self.y <= other.y
                and other.x + other.w <= self.x + self.w and other.y + other.h <= self.y + self.h)


def factor_pairs(cells):
    """All (w, h) with w * h == cells that fit one window and are >= 2 x 2."""
    max_w, max_h = WINDOW_CELLS
    pairs = []
    for w in range(MIN_SIDE, max_w + 1):
        if cells % w == 0 and MIN_SIDE <= cells // w <= max_h:
            pairs.append((w, cells // w))
    return pairs


def nearest_rectangle_cells(cells):
    """The cell count closest to `cells` that is a single legal rectangle.

    Some grid values are not: 1306 = 2 x 653 and 1414 = 2 x 7 x 101 have no
    factor pair inside 85 x 64. Smaller counts are tried before larger ones at
    equal distance, so the answer is deterministic (1306 -> 1305, 1414 -> 1416).
    """
    for distance in range(0, 64):
        for candidate in (cells - distance, cells + distance):
            if candidate >= MIN_SIDE * MIN_SIDE and factor_pairs(candidate):
                return candidate
    raise ValueError(f"no rectangle near {cells} cells")


def largest_rectangle_cells_at_most(cells):
    """Round `cells` down to the nearest count that is a legal rectangle."""
    for candidate in range(cells, MIN_SIDE * MIN_SIDE - 1, -1):
        if factor_pairs(candidate):
            return candidate
    raise ValueError(f"no rectangle at or below {cells} cells")


def random_parts(rng, total, count):
    """Split `total` into `count` positive parts, each at least 2 x 2 cells."""
    minimum = MIN_SIDE * MIN_SIDE
    spare = total - minimum * count
    cuts = sorted(rng.randint(0, spare) for _ in range(count - 1))
    edges = [0] + cuts + [spare]
    return [minimum + (edges[i + 1] - edges[i]) for i in range(count)]


def row_width(shapes):
    """Width of the shapes laid side by side with one gap cell between them."""
    return sum(w for w, _ in shapes) + ELEMENT_GAP * (len(shapes) - 1)


def choose_shape(rng, cells):
    """Pick a (w, h) for `cells`, preferring shapes that are not thin strips."""
    pairs = factor_pairs(cells)
    comfortable = [pair for pair in pairs if min(pair) >= 4]
    return rng.choice(comfortable or pairs)


def narrowest_shape(cells):
    """The (w, h) for `cells` with the smallest width (tallest shape)."""
    return min(factor_pairs(cells))


def split_cells(rng, total, count):
    """Split `total` cells into `count` rectangles that fit in one window row.

    Exact for count >= 2: the first count-1 parts are rounded down to legal
    rectangle counts and the last part absorbs the difference, so the sum is
    always `total`; attempts whose last part is not a rectangle, or whose row
    is wider than the window, are redrawn. A single element gets the nearest
    legal count instead (see nearest_rectangle_cells).
    """
    max_w = WINDOW_CELLS[0]
    if count == 1:
        return [choose_shape(rng, nearest_rectangle_cells(total))]
    for _ in range(MAX_SPLIT_ATTEMPTS):
        parts = random_parts(rng, total, count)
        fixed = [largest_rectangle_cells_at_most(part) for part in parts[:-1]]
        last = total - sum(fixed)
        if last < MIN_SIDE * MIN_SIDE or not factor_pairs(last):
            continue
        counts = fixed + [last]
        shapes = [choose_shape(rng, cells) for cells in counts]
        if row_width(shapes) <= max_w:
            return shapes
        shapes = [narrowest_shape(cells) for cells in counts]
        if row_width(shapes) <= max_w:
            return shapes
    raise RuntimeError(f"could not split {total} cells into {count} rectangles")


def place_in_row(rng, shapes, region):
    """Put the shapes side by side inside `region`, each at a random height."""
    slack = region.w - row_width(shapes)
    x = region.x + rng.randint(0, slack)
    boxes = []
    for w, h in shapes:
        y = region.y + rng.randint(0, region.h - h)
        boxes.append(Box(x, y, w, h))
        x += w + ELEMENT_GAP
    return boxes


def layout_flashing_elements(rng, total_cells, count, region):
    """Rectangles for the flashing elements and the cell count they really cover."""
    shapes = split_cells(rng, total_cells, count)
    boxes = place_in_row(rng, shapes, region)
    return boxes, sum(box.cells for box in boxes)


def free_zones(region, far):
    """Bands of the viewport where a non-flashing item may be placed.

    `far=False`: at least NEAR_GAP cells from the region on every side.
    `far=True`: at least one whole window away (85 cells horizontally or 64
    cells vertically), so no window can hold both the item and the region.
    Each band is clipped to the viewport minus an edge margin.
    """
    gap_x, gap_y = WINDOW_CELLS if far else (NEAR_GAP, NEAR_GAP)
    view_w, view_h = VIEWPORT_CELLS
    canvas = Box(EDGE_MARGIN, EDGE_MARGIN, view_w - 2 * EDGE_MARGIN, view_h - 2 * EDGE_MARGIN)
    bands = [
        Box(0, 0, view_w, region.y - gap_y),                                   # above
        Box(0, region.y + region.h + gap_y, view_w, view_h),                   # below
        Box(0, 0, region.x - gap_x, view_h),                                   # left
        Box(region.x + region.w + gap_x, 0, view_w, view_h),                   # right
    ]
    zones = []
    for band in bands:
        clipped = clip(band, canvas)
        if clipped is not None:
            zones.append(clipped)
    return zones


def clip(box, frame):
    """The part of `box` inside `frame`, or None when nothing is left."""
    x1, y1 = max(box.x, frame.x), max(box.y, frame.y)
    x2 = min(box.x + box.w, frame.x + frame.w)
    y2 = min(box.y + box.h, frame.y + frame.h)
    if x2 <= x1 or y2 <= y1:
        return None
    return Box(x1, y1, x2 - x1, y2 - y1)


def place_item(rng, size, zones, taken):
    """Place a `size` box inside one of `zones`, clear of every box in `taken`.

    Random positions are tried first (so pages differ), then every position on
    a 2-cell grid; None means nothing fits and the caller drops the item.
    """
    width, height = size
    fitting = [zone for zone in zones if zone.w >= width and zone.h >= height]
    if not fitting:
        return None

    def clear(box):
        return not any(box.overlaps(other, ITEM_GAP) for other in taken)

    for _ in range(MAX_PLACE_TRIES):
        zone = rng.choice(fitting)
        box = Box(zone.x + rng.randint(0, zone.w - width),
                  zone.y + rng.randint(0, zone.h - height), width, height)
        if clear(box):
            return box
    for zone in fitting:
        for y in range(zone.y, zone.y + zone.h - height + 1, 2):
            for x in range(zone.x, zone.x + zone.w - width + 1, 2):
                box = Box(x, y, width, height)
                if clear(box):
                    return box
    return None


def layout_page(rng, params, total_cells):
    """All geometry for one page.

    Returns (region, element boxes, actual total cells, text box or None,
    {distractor name: box}) — distractors that found no room are left out of
    the dict, and therefore out of the page and the truth file.
    """
    region = Box(params.region_x, params.region_y, *WINDOW_CELLS)
    elements, actual_total = layout_flashing_elements(rng, total_cells, params.element_count, region)
    taken = []
    distractor_boxes = {}

    def place_distractor(name):
        zones = free_zones(region, far=(name == "marquee"))
        box = place_item(rng, DISTRACTOR_SIZES[name], zones, taken)
        if box is not None:
            distractor_boxes[name] = box
            taken.append(box)

    # The marquee has the least room (a whole window away from the region), so
    # it is placed first; then the large text box; then the other distractors.
    if "marquee" in params.distractors:
        place_distractor("marquee")
    text_box = place_item(rng, TEXT_SIZE, free_zones(region, far=False), taken)
    if text_box is not None:
        taken.append(text_box)
    for name in params.distractors:
        if name != "marquee":
            place_distractor(name)
    return region, elements, actual_total, text_box, distractor_boxes
