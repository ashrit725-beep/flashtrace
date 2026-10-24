"""Synthetic benchmark generator for FlashTrace.

    python -m bench.generate --n 200 --seed 1 [--out bench/pages] [--truth bench/truth.json]

Writes one HTML page per index (plus one WebM per flashing element on video
pages) and `truth.json`, the answer key the benchmark scores FlashTrace
against. The same seed always produces byte-identical HTML and truth files:
every random draw comes from `random.Random` seeded by (seed, page index),
and no timestamps or machine-specific paths are written.

Pipeline for one page: sample parameters (params.py) -> choose the two state
colours (colors.py) -> lay out cell-aligned rectangles (geometry.py) ->
derive the expected verdict (truth.py) -> render HTML (pages_templates.py)
-> encode videos if needed (ffmpeg.py).
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from bench import ffmpeg as ffmpeg_tools
from bench.colors import flash_colours, relative_luminance
from bench.geometry import layout_page
from bench.params import (CELL_PX, QUALIFYING_CELLS, VIEWPORT_PX, WINDOW_CELLS, WINDOW_CELL_COUNT,
                          WINDOW_PX, page_rng, sample_page, target_cells)
from bench.pages_templates import render_page
from bench.truth import expected_outcome, skipped_outcome

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = PACKAGE_DIR / "pages"
DEFAULT_N = 200
DEFAULT_SEED = 1
SKIP_REASON = ("ffmpeg not found: video not generated (set FLASHTRACE_FFMPEG or install "
               "Playwright's bundled ffmpeg)")


def page_id(index):
    return f"bench-{index:04d}"


def relative_to(path, base):
    """`path` written relative to `base` with forward slashes (stable across machines)."""
    return Path(os.path.relpath(path, base)).as_posix()


def window_description():
    """The engine window the truth numbers refer to (SPEC T4, ADR-006)."""
    return {
        "viewportPx": list(VIEWPORT_PX),
        "windowPx": list(WINDOW_PX),
        "cellPx": CELL_PX,
        "windowCells": list(WINDOW_CELLS),
        "windowCellCount": WINDOW_CELL_COUNT,
        "qualifyingCells": QUALIFYING_CELLS,
        "note": "a flash qualifies when it covers more than qualifyingCells cells of one window",
    }


def build_page(seed, index, out_dir, truth_dir, ffmpeg):
    """Write one page (and its videos) and return its truth entry.

    All random draws happen before any file is touched and do not depend on
    whether ffmpeg exists, so a machine without ffmpeg produces the same
    parameters and merely marks video pages as skipped.
    """
    rng = page_rng(seed, index)
    params = sample_page(rng, index)
    colour_a, colour_b, delta_l_actual = flash_colours(params.red, params.base_luminance,
                                                       params.delta_l_target)
    delta_l_actual = round(delta_l_actual, 6)
    region, boxes, total_cells, text_box, distractor_boxes = layout_page(
        rng, params, target_cells(params.area_fraction))
    area_fraction_actual = round(total_cells / WINDOW_CELL_COUNT, 6)

    ident = page_id(index)
    html_path = out_dir / f"{ident}.html"
    elements = [{"selector": f"#flash-{k}", "rect": box.to_px()} for k, box in enumerate(boxes, start=1)]

    wants_video = params.mechanism == "video"
    skipped = wants_video and ffmpeg is None
    video_names = [f"{ident}-{k}.webm" for k in range(1, len(boxes) + 1)] if wants_video and not skipped else []

    if skipped:
        expected = skipped_outcome(SKIP_REASON)
    else:
        expected = expected_outcome(
            rate_hz=params.rate_hz, total_cells=total_cells, delta_l_actual=delta_l_actual,
            red=params.red, area_fraction=params.area_fraction,
            area_fraction_actual=area_fraction_actual, delta_l_target=params.delta_l_target,
            mechanism=params.mechanism)

    entry = {
        "id": ident,
        "file": relative_to(html_path, truth_dir),
        "mechanism": "video-skipped" if skipped else params.mechanism,
        "rateHz": params.rate_hz,
        "areaFraction": params.area_fraction,
        "areaFractionActual": area_fraction_actual,
        "totalCells": total_cells,
        "elementCount": len(boxes),
        "red": params.red,
        "deltaLTarget": params.delta_l_target,
        "deltaLActual": delta_l_actual,
        "luminance": {"a": round(relative_luminance(colour_a), 6),
                      "b": round(relative_luminance(colour_b), 6)},
        "colors": {"a": list(colour_a), "b": list(colour_b)},
        "background": list(params.background),
        "distractors": [name for name in params.distractors if name in distractor_boxes],
        "region": region.to_px(),
        "elements": elements,
        "videos": [relative_to(out_dir / name, truth_dir) for name in video_names],
        "layout": {
            "text": text_box.to_px() if text_box else None,
            "distractorRects": {name: box.to_px() for name, box in distractor_boxes.items()},
        },
        "expected": expected,
        # Skipped pages flash nothing, so nothing is to blame on them.
        "culprits": [] if skipped else [element["selector"] for element in elements],
    }

    html = render_page(entry, colour_a, colour_b, entry["layout"]["text"],
                       entry["layout"]["distractorRects"], video_names or None, skipped)
    html_path.write_text(html, encoding="utf-8", newline="\n")
    for name, box in zip(video_names, boxes):
        rect = box.to_px()
        ffmpeg_tools.encode_flash_video(ffmpeg, out_dir / name, rect["w"], rect["h"],
                                        colour_a, colour_b, params.rate_hz)
    return entry


def generate(n=DEFAULT_N, seed=DEFAULT_SEED, out_dir=DEFAULT_OUT_DIR, truth_path=None, ffmpeg="auto"):
    """Generate `n` pages and the truth file; returns the truth as a dict.

    `ffmpeg="auto"` looks the binary up; pass None to force the no-ffmpeg path.
    """
    out_dir = Path(out_dir)
    truth_path = Path(truth_path) if truth_path else out_dir.parent / "truth.json"
    if ffmpeg == "auto":
        ffmpeg = ffmpeg_tools.find_ffmpeg()
    if ffmpeg is None:
        print("warning: ffmpeg not found; video pages will be written as skipped "
              "(mechanism video-skipped, verdict SKIP)", file=sys.stderr)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth_path.parent.mkdir(parents=True, exist_ok=True)

    pages = [build_page(seed, index, out_dir, truth_path.parent, ffmpeg) for index in range(1, n + 1)]
    truth = {
        "seed": seed,
        "n": n,
        "window": window_description(),
        "ffmpegAvailable": ffmpeg is not None,
        "pages": pages,
    }
    truth_path.write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8", newline="\n")
    return truth


def summary_line(truth, out_dir, truth_path):
    """One line of counts: per mechanism, per expected verdict, boundary, advisory."""
    pages = truth["pages"]
    mechanisms = Counter(page["mechanism"] for page in pages)
    verdicts = Counter(page["expected"]["verdict"] for page in pages)
    boundary = sum(1 for page in pages if page["expected"]["boundary"])
    advisory = sum(1 for page in pages if page["expected"]["advisory"])
    videos = sum(len(page["videos"]) for page in pages)

    def counts(counter):
        return " ".join(f"{key}={value}" for key, value in sorted(counter.items()))

    return (f"wrote {len(pages)} pages to {out_dir} and truth to {truth_path} | "
            f"mechanisms: {counts(mechanisms)} | expected: {counts(verdicts)} "
            f"boundary={boundary} advisory={advisory} | webm files: {videos}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate FlashTrace's synthetic benchmark pages.")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="number of pages (default 200)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default 1)")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="pages directory (default bench/pages)")
    parser.add_argument("--truth", default=None, help="truth file (default: truth.json next to the pages directory)")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    truth_path = Path(args.truth) if args.truth else out_dir.parent / "truth.json"
    truth = generate(args.n, args.seed, out_dir, truth_path)
    print(summary_line(truth, out_dir, truth_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
