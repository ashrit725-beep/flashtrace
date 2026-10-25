// Report correctness: the chart's text alternative and violation spans, the
// heatmap legend and box labels, the non-deterministic notice, the minimal
// set column, evidence card sizes, and History at phone width. Each test
// serves a modified copy of a synthetic fixture (TEST FIXTURE DATA).

import { expect, test, type Page } from "@playwright/test";
import { IDS, mockApi, readFixture, replaceJson } from "./mock-api";

type Json = Record<string, unknown>;

/** Serve a modified copy of the FAIL report (or another) for this test. */
async function serveReport(page: Page, change: (r: Json) => void, id: string = IDS.FAIL, file = "report-fail.json") {
  const report = readFixture<Json>(file);
  change(report);
  await replaceJson(page, new RegExp(`^/jobs/${id}/report$`), () => report);
}

/** base64 little-endian uint16 -> numbers, and back (the heatmap encoding). */
const decode = (b64: string) => {
  const bytes = Buffer.from(b64, "base64");
  return Array.from({ length: bytes.length / 2 }, (_, i) => bytes.readUInt16LE(i * 2));
};
const encode = (values: number[]) => {
  const bytes = Buffer.alloc(values.length * 2);
  values.forEach((v, i) => bytes.writeUInt16LE(v, i * 2));
  return bytes.toString("base64");
};

test("chart: a FAIL whose whole-second bars stay at or under 3 still shows and names the violation", async ({ page }) => {
  await mockApi(page);
  // Bins alternating 2 and 3 (no bar over the line), while the sliding
  // window found 4 per second: the case fixed-second bars cannot show.
  await serveReport(page, (r) => {
    r.timeline = (r.timeline as Json[]).map((b, i) => ({ ...b, generalFlashes: i % 2 ? 3 : 2 }));
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const chart = page.getByRole("img", { name: /^Bar chart of flashes counted in each whole second/ });
  await expect(chart).toHaveAccessibleName(/found 1 violation: violation 1, general flashes up to 4 per second, from 0\.000 s to 9\.967 s/);
  await expect(chart).not.toHaveAccessibleName(/exceed the limit in 0/);
  await expect(chart.locator("text", { hasText: "Violation 1: up to 4/s (sliding window)" })).toBeVisible();

  await page.getByRole("button", { name: "Show data table" }).click();
  const table = page.getByRole("table", { name: /Flashes counted in each whole second/ });
  await expect(table.getByRole("row", { name: /^0–1 s/ })).toContainText("Yes: violation 1 (general, up to 4 per second)");
});

test("chart: a lone bar is centred on its second's label; the toggle is a disclosure", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`); // no red flashes in any bin
  await expect(page.getByRole("img", { name: /^Bar chart/ })).toBeVisible();
  const offset = await page.evaluate(() => {
    const svg = document.querySelector("svg[role='img']")!;
    const bar = svg.querySelector("rect[fill='var(--chart-general)']")!.getBoundingClientRect();
    // x-axis labels are centred (text-anchor middle); the y-axis "0" is right-aligned.
    const label = svg.querySelector("text[text-anchor='middle']")!.getBoundingClientRect();
    return Math.abs(bar.left + bar.width / 2 - (label.left + label.width / 2));
  });
  expect(offset).toBeLessThanOrEqual(1);

  const toggle = page.getByRole("button", { name: "Show data table" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("table", { name: /Flashes counted/ })).toHaveCount(0); // really hidden
  await toggle.click();
  const hide = page.getByRole("button", { name: "Hide data table" });
  await expect(hide).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("table", { name: /Flashes counted/ })).toBeVisible();
});

test("heatmap legend: max 1 shows one swatch, not '1 1 1'", async ({ page }) => {
  await mockApi(page);
  await serveReport(page, (r) => {
    const h = r.heatmap as { data: string };
    h.data = encode(decode(h.data).map((v) => Math.min(v, 1)));
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByText("Every flashing cell: 1 flash (deep red)")).toBeVisible();
  await expect(page.locator("[class*='rampScale']")).toHaveCount(0);
});

test("heatmap legend: ticks sit where their counts are painted", async ({ page }) => {
  await mockApi(page);
  await serveReport(page, (r) => {
    // counts 1..4 only: max 4, mid 1 + round(1.5) = 3, which belongs at 2/3 of the ramp
    const h = r.heatmap as { data: string };
    h.data = encode(decode(h.data).map((v) => (v === 0 ? 0 : 1 + (v % 4))));
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const spans = page.locator("[class*='rampScale'] span");
  await expect(spans).toHaveCount(3); // evaluateAll does not wait for the legend to render
  const ticks = await spans.evaluateAll((spans) => {
    const bar = spans[0].parentElement!.getBoundingClientRect();
    return spans.map((s) => {
      const r = s.getBoundingClientRect();
      return { text: s.textContent, centre: (r.left + r.width / 2 - bar.left) / bar.width };
    });
  });
  expect(ticks.map((t) => t.text)).toEqual(["1", "3", "4"]);
  expect(Math.abs(ticks[1].centre - 2 / 3)).toBeLessThan(0.03);
});

test("heatmap: labels of boxes at the frame's top and bottom edges stay visible", async ({ page }) => {
  await mockApi(page);
  await serveReport(page, (r) => {
    (r.violations as Json[])[0].window = { x: 0, y: 0, w: 340, h: 256 };
    (r.culprits as Json[])[0].rect = { x: 0, y: 0, w: 1024, h: 768 };
    (r.culprits as Json[])[0].selector = "body";
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await page.getByRole("button", { name: /Outline culprit 1 / }).click();
  const windowLabel = page.getByTestId("window-label");
  const culpritLabel = page.getByTestId("culprit-label");
  // The label is built from the box it labels (340 × 256), not settings.windowPx (341 × 256).
  await expect(windowLabel).toHaveText("Worst 340 × 256 px window");
  await expect(culpritLabel).toHaveText("Culprit 1: body");
  const frame = await page.locator("[class*='stageFrame']").boundingBox();
  for (const label of [windowLabel, culpritLabel]) {
    const box = await label.boundingBox();
    expect(box && frame).toBeTruthy();
    expect(box!.y).toBeGreaterThanOrEqual(frame!.y - 0.5);
    expect(box!.y + box!.height).toBeLessThanOrEqual(frame!.y + frame!.height + 0.5);
    expect(box!.x).toBeGreaterThanOrEqual(frame!.x - 0.5);
    expect(box!.x + box!.width).toBeLessThanOrEqual(frame!.x + frame!.width + 0.5);
  }
});

test("notice: INCONCLUSIVE after a deterministic capture has no real-time warning", async ({ page }) => {
  await mockApi(page);
  await serveReport(page, (r) => {
    r.verdict = "INCONCLUSIVE";
    r.reason = "Fewer than 120 frames were captured, so no verdict can be given.";
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: /INCONCLUSIVE/ })).toBeVisible();
  await expect(page.getByRole("note")).toHaveCount(0); // the non-deterministic notice
  await expect(page.getByText(/recorded in real time/)).toHaveCount(0);
});

test("notice: a stepped capture that lost determinism blames the clock, not real time", async ({ page }) => {
  await mockApi(page);
  await serveReport(page, (r) => {
    (r.capture as Json).deterministic = false;
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const notice = page.getByRole("note");
  await expect(notice).toContainText("Non-deterministic capture");
  await expect(notice).toContainText("outside the controlled animation clock");
  await expect(notice).not.toContainText("real time");
  // The summary no longer claims "(deterministic)" next to "Deterministic: No".
  await expect(page.getByText("Animation clock stepped", { exact: true })).toBeVisible();
});

test("notice: a real-time capture still says so", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.INCONCLUSIVE}`);
  await expect(page.getByRole("note")).toContainText("Frames were recorded in real time");
});

test("culprits: without ablation the minimal set is 'not measured', never Yes/No", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const rows = page.getByRole("table", { name: "Culprits, most likely first" }).getByRole("row");
  await expect(rows).toHaveCount(4);
  for (let i = 1; i < 4; i++) {
    await expect(rows.nth(i).getByRole("cell").last()).toHaveText("not measured");
  }
});

test("culprits: after ablation the minimal set reads Yes/No", async ({ page }) => {
  await mockApi(page);
  await serveReport(page, (r) => {
    const [a, b] = r.culprits as Json[];
    Object.assign(a, { ablationDelta: 37, inMinimalSet: true });
    Object.assign(b, { ablationDelta: 0, inMinimalSet: false });
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const rows = page.getByRole("table", { name: "Culprits, most likely first" }).getByRole("row");
  await expect(rows.nth(1).getByRole("cell").last()).toHaveText("Yes, in minimal set");
  await expect(rows.nth(2).getByRole("cell").last()).toHaveText("No");
});

test("fixes: a PASS that is not verified says why, and the table shows determinism", async ({ page }) => {
  await mockApi(page);
  await serveReport(page, (r) => {
    const fix = (r.fixes as Json[])[1];
    (fix.after as Json).deterministic = false;
    fix.verified = false;
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const fix2 = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Fix 2:/ }) });
  await expect(fix2).toContainText("Not verified: the re-run passed, but its capture was not deterministic");
  await expect(fix2.getByRole("row", { name: /Deterministic capture/ })).toHaveText(/Deterministic capture\s*Yes\s*No/);
});

test("evidence: frames are at least 240 px wide and the cards in a row are equally tall", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const cards = page.getByRole("button", { name: /^Enlarge: / });
  await expect(cards).toHaveCount(3);
  const boxes = await cards.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON()));
  for (const b of boxes) expect(b.width).toBeGreaterThanOrEqual(240);
  expect(new Set(boxes.map((b) => Math.round(b.height))).size).toBe(1);
});

test("history at 390 px: every audit's actions are on screen and URLs are readable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto("/history/");
  await expect(page.getByRole("table")).toHaveCount(0); // cards instead
  const list = page.getByRole("list", { name: "Past audits, newest first" });
  await expect(list.getByRole("listitem")).toHaveCount(5);
  for (const control of await list.getByRole("link").or(list.getByRole("button")).all()) {
    const box = await control.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  }
  const url = await list.locator(".url").first().boundingBox();
  expect(url!.width).toBeGreaterThan(200);
});

test("culprits at 390 px: Outline does not scroll the element's name away", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await page.getByRole("button", { name: /Outline culprit 1 / }).click();
  const name = page.getByRole("cell", { name: "#loader", exact: true });
  const box = await name.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(box!.width).toBeGreaterThanOrEqual(120); // readable, not squeezed to one character per line
  // Scrolled fully sideways, Rank and Element stay pinned at the left.
  await page.locator("[class*='culprits']").evaluate((el) => (el.scrollLeft = el.scrollWidth));
  const pinned = await name.boundingBox();
  expect(pinned!.x).toBeGreaterThanOrEqual(0);
  expect(pinned!.x + pinned!.width).toBeLessThanOrEqual(390);
  // The explanation is outside the sideways-scrolling box, so it cannot scroll away.
  await expect(page.locator(".table-wrap #culprits-help")).toHaveCount(0);
  await expect(page.locator("#culprits-help")).toBeVisible();
});

test("heatmap: the outline marks the worst violation (highest rate), not the first", async ({ page }) => {
  await mockApi(page);
  // A mild early violation and a worse later one (TEST FIXTURE DATA): the box, its caption and the
  // still must follow the worse one, as the reason sentence and the HTML export do.
  await serveReport(page, (r) => {
    const first = (r.violations as Json[])[0] as Json & { frameIndices: number[] };
    const worse = { ...first, start: 300, end: 420, maxRatePerSec: 8, frameIndices: [310, 318],
                    window: { x: 500, y: 300, w: 340, h: 256 } };
    r.violations = [first, worse];
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByText(/The dashed box marks the worst 340 × 256 px window of violation 2, at x 500, y 300\./)).toBeVisible();
});
