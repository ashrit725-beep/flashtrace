"""Write the benchmark results as Markdown (docs/RESULTS.md by default).

Every figure in the output comes from the ``results`` dict built by bench/run.py from one real run;
this module only formats it.  The page states the date, commit, exact command and machine, so a
reader can tell which code produced which number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bench.params import QUALIFYING_CELLS, WINDOW_CELLS
from bench.score import EXPECTED_VERDICTS, GOT_VERDICTS
from bench.truth import MAX_FLASHES_PER_SECOND
from cli.report import DISCLAIMER

FULL_RUN_PAGES = 200
FULL_RUN_SECONDS = 10.0

REQUIRED_HEADINGS: tuple[str, ...] = (
    "## Run",
    "## Settings",
    "## Verdict accuracy on clear cases",
    "## Boundary cases",
    "## Accuracy by mechanism (clear cases)",
    "## Violation types on FAIL pages",
    "## Advisory agreement",
    "## Attribution",
    "## Determinism",
    "## Runtime",
    "## Mismatches",
)


def is_partial(n: int, duration_s: float) -> bool:
    """A run smaller or shorter than the full benchmark (200 pages, 10 s each)."""
    return n < FULL_RUN_PAGES or duration_s < FULL_RUN_SECONDS


def title(n: int, duration_s: float) -> str:
    if is_partial(n, duration_s):
        return f"# RESULTS \u2014 partial run (n={n}, duration={duration_s:g} s)"
    return "# RESULTS"


def ratio_text(part: int, total: int) -> str:
    """'2/3 (66.7%)', or '0/0 (n/a)' when there is nothing to count."""
    if not total:
        return f"{part}/{total} (n/a)"
    return f"{part}/{total} ({100 * part / total:.1f}%)"


def cell(value: object) -> str:
    """Make any value safe inside a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(cell(h) for h in headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return lines


def confusion_lines(confusion: Mapping[str, Mapping[str, int]]) -> list[str]:
    rows = [[f"expected {expected}"] + [confusion[expected][got] for got in GOT_VERDICTS] for expected in EXPECTED_VERDICTS]
    return table(["", *(f"got {got}" for got in GOT_VERDICTS)], rows)


# --------------------------------------------------------------------------------------- sections
def run_section(results: Mapping[str, Any]) -> list[str]:
    machine = results["machine"]
    machine_text = (f"{machine['platform']}; Python {machine['python']}; Chromium {machine['chromium']}; "
                    f"{machine['cpus']} CPUs")
    return ["## Run", ""] + table(["Field", "Value"], [
        ["Date (UTC)", results["date"]],
        ["Commit", results["commit"]],
        ["Command", f"`{results['commandLine']}`"],
        ["Machine", machine_text],
        # The JSON written next to this page by the same run, and its sha256, so a reader can check
        # that the per-page data they open is the data these figures came from.
        ["Per-page results", f"`{results['resultsJson']}` (sha256 `{results['resultsJsonSha256']}`)"],
    ])


def settings_section(results: Mapping[str, Any]) -> list[str]:
    s, counts = results["settings"], results["counts"]
    return ["## Settings", ""] + table(["Setting", "Value"], [
        ["Pages", f"{s['n']} (audited {counts['audited']}, skipped {counts['skipped']}, errors {counts['errors']})"],
        ["Seed", s["seed"]],
        ["Duration per page", f"{s['duration']:g} s"],
        ["Frame rate", f"{s['fps']} fps"],
        ["Viewport", f"{s['viewport'][0]}x{s['viewport'][1]} px"],
        ["Capture strategy", s["strategy"]],
        ["Red-flash rule", s["redRule"]],
        ["Attribution", s["attribute"]],
        ["Worker processes", f"{s['poolProcesses']} used ({s['workers']} requested)"],
        ["Determinism sample", s["determinismSample"]],
        ["Truth file", s["truth"]],
    ])


def clear_section(scores: Mapping[str, Any]) -> list[str]:
    acc = scores["clear"]["accuracy"]
    return [
        "## Verdict accuracy on clear cases", "",
        "Clear cases are audited pages that are not near any limit. INCONCLUSIVE counts as not correct; pages "
        "whose audit raised an error appear in the table but not in the accuracy.", "",
        f"**Accuracy: {ratio_text(acc['correct'], acc['total'])}** ({acc['errors']} errors left out)", "",
    ] + confusion_lines(scores["clear"]["confusion"])


def boundary_section(scores: Mapping[str, Any]) -> list[str]:
    b = scores["boundary"]
    lines = ["## Boundary cases", "",
             "Pages within a small band of a limit (see bench/truth.py). Reported separately and not part of "
             "the accuracy above.", "",
             f"Matching the expected verdict: {ratio_text(b['accuracy']['correct'], b['accuracy']['total'])}", ""]
    lines += confusion_lines(b["confusion"]) + [""]
    reasons = [[r["reason"], r["pages"], ratio_text(r["matching"], r["pages"])] for r in b["byReason"]]
    return lines + (table(["Boundary reason", "Pages", "Matching expected"], reasons) if reasons else ["No boundary pages."])


def mechanism_section(scores: Mapping[str, Any]) -> list[str]:
    rows = [[m["mechanism"], ratio_text(m["correct"], m["total"]), m["errors"]] for m in scores["mechanisms"]]
    body = table(["Mechanism", "Correct", "Errors (left out)"], rows) if rows else ["No clear cases."]
    return ["## Accuracy by mechanism (clear cases)", ""] + body


def types_section(scores: Mapping[str, Any]) -> list[str]:
    t = scores["types"]
    lines = ["## Violation types on FAIL pages", "",
             "Pages expected to FAIL that FlashTrace also failed: do the violation types (general / red) equal "
             "the expected types?", "", f"Agreement: {ratio_text(t['agree'], t['total'])}"]
    if t["disagreements"]:
        lines.append(f"Disagreeing pages: {', '.join(t['disagreements'])}")
    return lines


def advisory_section(scores: Mapping[str, Any]) -> list[str]:
    a = scores["advisory"]
    text = f"Agreement: {ratio_text(a['agree'], a['total'])} ({a['note']})" if a["measured"] else a["note"]
    return ["## Advisory agreement", "",
            "Only clear pages expected to PASS are scored: the truth marks an advisory only for 2-3 flashes per "
            "second, which never fails, and on a failing page the advisory adds nothing.", "", text]


MINIMAL_SET_RULE = (
    "A minimal set counts as correct when every element marked as in the minimal set is a truth culprit, "
    f"suppressing them leaves at most {QUALIFYING_CELLS} flashing cells of the truth elements (25% of the "
    f"{WINDOW_CELLS[0]} x {WINDOW_CELLS[1]}-cell window) or a rate of {MAX_FLASHES_PER_SECOND} per second or "
    "less, and putting back any one of them makes the page fail again. It is not compared with the truth "
    "list for equality: elements that flash in sync can be explained by more than one minimal set."
)


def main_attribution_lines(a: Mapping[str, Any]) -> list[str]:
    """Attribution of the main pass (--attribute bbox or ablate), on pages expected and audited FAIL."""
    if not a["measured"]:
        return [a["note"].capitalize()]
    lines = [f"Main pass, attribution mode {a['mode']}, scored on the {a['pages']} pages expected to FAIL that "
             "FlashTrace also failed.", "",
             f"Top-1 culprit in the truth culprits: {ratio_text(a['top1']['agree'], a['top1']['total'])}"]
    minimal = a["minimalSet"]
    if minimal.get("measured", True):
        lines.append(f"Minimal culprit set correct: {ratio_text(minimal['agree'], minimal['total'])}")
    else:
        lines.append(f"Minimal culprit set: {minimal['note']}")
    return lines


def ablation_sample_lines(s: Mapping[str, Any]) -> list[str]:
    """The separate ablation sample (--ablate-sample K --ablate-duration D), with its size and settings."""
    if not s["measured"]:
        return [s["note"].capitalize()]
    lines = [f"Ablation sample: the first {s['requested']} pages that FAILed in the main pass were audited again "
             f"with attribute ablate at {s['duration']:g} s per capture. Sampled {s['sampled']}; scored {s['pages']} "
             f"(the ones that FAILed again at that duration; {s['notFail']} did not). This is a small sample, not "
             "the whole benchmark.", "",
             f"Top-1 culprit in the truth culprits: {ratio_text(s['top1']['agree'], s['top1']['total'])}",
             f"Minimal culprit set correct: {ratio_text(s['minimalSet']['agree'], s['minimalSet']['total'])}"]
    if s.get("recaptures"):
        r = s["recaptures"]
        lines.append(f"Re-captures per page: mean {r['mean']:.1f}, min {r['min']:g}, max {r['max']:g} ({r['count']} pages)")
    if s.get("auditWall"):
        w = s["auditWall"]
        lines.append(f"Wall time per sampled audit, baseline and ablation together (s): mean {w['mean']:.1f}, "
                     f"median {w['median']:.1f}, max {w['max']:.1f} ({w['count']} pages)")
    if s["pageIds"]:
        lines.append(f"Sampled pages: {', '.join(s['pageIds'])}")
    return lines


def attribution_section(scores: Mapping[str, Any]) -> list[str]:
    lines = ["## Attribution", ""] + main_attribution_lines(scores["attribution"])
    sample = scores.get("ablationSample")
    if sample is not None:
        lines += [""] + ablation_sample_lines(sample)
    if scores["attribution"].get("mode") == "ablate" or (sample or {}).get("measured"):
        lines += ["", MINIMAL_SET_RULE]
    return lines


def determinism_section(results: Mapping[str, Any]) -> list[str]:
    d = results["determinism"]
    if not d["total"]:
        return ["## Determinism", "", "Determinism: not measured (determinism sample 0)."]
    compared = d["compared"]
    # Pages where either run errored or captured no frames are not compared: two failed captures would
    # "match" without saying anything about determinism.
    not_compared = (f"Not compared: {d['notCompared']} of {d['total']} sampled pages (one of the two runs "
                    "raised an error or its capture produced fewer than two frames).")
    if not compared:
        return ["## Determinism", "", "Determinism: not measured (no sampled page captured frames on both runs).",
                "", not_compared]
    return ["## Determinism", "",
            f"Determinism: {d['identical']}/{compared} compared pages identical on a second run (frame hashes "
            f"{d['frameHashesIdentical']}/{compared}, report fingerprints {d['fingerprintsIdentical']}/{compared}; "
            "a fingerprint is the sha256 of the report without capturedAt).", "", not_compared]


def runtime_section(results: Mapping[str, Any]) -> list[str]:
    s, runtime, counts = results["settings"], results["scores"]["runtime"], results["counts"]
    rows = []
    for label, key in (("Capture", "capture"), ("Analysis", "analysis")):
        stats = runtime[key]
        if stats is None:
            rows.append([label, 0, "-", "-", "-", "-"])
        else:
            rows.append([label, stats["count"]] + [f"{stats[k]:.3f}" for k in ("mean", "median", "min", "max")])
    intro = (f"Wall time per page in seconds (time.perf_counter) for {s['duration']:g} s of animation at {s['fps']} "
             f"fps, {s['viewport'][0]}x{s['viewport'][1]} px, with up to {s['poolProcesses']} pages audited at the "
             f"same time ({s['poolProcesses']} worker processes). Measured at this duration; not scaled to any other. "
             f"Left out: pages whose audit raised an error ({counts['errors']}) and pages whose capture failed, "
             f"so no animation was recorded and their time is not a capture time ({runtime['failedCaptures']}).")
    return ["## Runtime", "", intro, ""] + table(["Stage", "Pages", "Mean", "Median", "Min", "Max"], rows)


def mismatch_rows(rows: Sequence[Mapping[str, Any]]) -> list[list[object]]:
    """Audited pages whose verdict differs from the truth, or whose FAIL types differ."""
    found = []
    for row in rows:
        expected = row["expected"]
        if expected["verdict"] == "SKIP":
            continue
        types_differ = (row["verdict"] == "FAIL" == expected["verdict"]
                        and sorted(set(expected["types"])) != sorted(set(row.get("violationTypes", []))))
        if row["verdict"] != expected["verdict"] or types_differ:
            got = row["verdict"] + (f" ({', '.join(row.get('violationTypes', []))})" if row.get("violationTypes") else "")
            want = expected["verdict"] + (f" ({', '.join(expected['types'])})" if expected["types"] else "")
            params = (f"{row['rateHz']} Hz, area {row['areaFractionActual']}, dL {row['deltaLActual']}, "
                      f"red {'yes' if row['red'] else 'no'}{', boundary' if expected['boundary'] else ''}")
            found.append([row["id"], row["mechanism"], params, want, got, row.get("error") or row.get("reason", "")])
    return found


def mismatch_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    found = mismatch_rows(rows)
    body = table(["Page", "Mechanism", "Key parameters", "Expected", "Got", "Reason"], found) if found else ["None."]
    return ["## Mismatches", ""] + body


# --------------------------------------------------------------------------------------- page
def render_results_md(results: Mapping[str, Any]) -> str:
    s = results["settings"]
    blocks = [
        [title(int(s["n"]), float(s["duration"])), "",
         "Written by `flashtrace bench` from one run; nothing on this page is typed by hand. Full per-page "
         f"results: `{results['resultsJson']}`, written by the same run (its sha256 is in the Run table)."],
        run_section(results), settings_section(results), clear_section(results["scores"]),
        boundary_section(results["scores"]), mechanism_section(results["scores"]),
        types_section(results["scores"]), advisory_section(results["scores"]),
        attribution_section(results["scores"]), determinism_section(results), runtime_section(results),
        mismatch_section(results["rows"]), [DISCLAIMER],
    ]
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def write_results_md(results: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_results_md(results), encoding="utf-8")
    return target
