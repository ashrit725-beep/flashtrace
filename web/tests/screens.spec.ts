// Screenshots of every screen for review. Skipped unless SHOTS=1, so the
// normal test run stays fast. Usage:
//   SHOTS=1 SHOTS_DIR=/some/folder npx playwright test screens
// Uses the mocked API (synthetic fixtures), never a real audit.

import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { IDS, mockApi } from "./mock-api";

test.skip(!process.env.SHOTS, "set SHOTS=1 to save screenshots");

const DIR = process.env.SHOTS_DIR ?? path.join(__dirname, "..", "test-results", "shots");

const SCREENS: { name: string; path: string; ready: (p: Page) => Promise<void> }[] = [
  { name: "new-audit", path: "/", ready: (p) => expect(p.getByRole("button", { name: "Run audit" })).toBeVisible() },
  { name: "running", path: `/run/?id=${IDS.RUNNING}`, ready: (p) => expect(p.getByRole("button", { name: "Cancel audit" })).toBeVisible() },
  { name: "report-fail", path: `/report/?id=${IDS.FAIL}`, ready: (p) => expect(p.getByRole("heading", { name: /FAIL/ })).toBeVisible() },
  { name: "report-pass", path: `/report/?id=${IDS.PASS}`, ready: (p) => expect(p.getByRole("heading", { name: /PASS/ })).toBeVisible() },
  { name: "report-inconclusive", path: `/report/?id=${IDS.INCONCLUSIVE}`, ready: (p) => expect(p.getByRole("heading", { name: /INCONCLUSIVE/ })).toBeVisible() },
  // Width-agnostic: below 640 px History shows cards instead of the table.
  { name: "history", path: "/history/", ready: (p) => expect(p.getByRole("link", { name: /^Open report for/ }).first()).toBeVisible() },
];

const SIZES = [
  { label: "1280", width: 1280, height: 900 },
  { label: "390", width: 390, height: 844 },
];

async function settle(page: Page) {
  // Scroll through the page so in-view entrances (chart, evidence) have played,
  // then wait for every <img> and every animation (MOTION.md: all done in 600 ms).
  const { height, step } = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, step: window.innerHeight }));
  for (let y = 0; y < height; y += step) {
    await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y);
    await page.waitForTimeout(40);
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForFunction(() => Array.from(document.images).every((img) => img.complete));
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => null))));
  await page.mouse.move(0, 0);
  await page.waitForTimeout(700);
}

for (const size of SIZES) {
  for (const screen of SCREENS) {
    const schemes = screen.name === "report-fail" ? (["light", "dark"] as const) : (["light"] as const);
    for (const scheme of schemes) {
      test(`${screen.name} ${size.label} ${scheme}`, async ({ page }) => {
        await page.setViewportSize({ width: size.width, height: size.height });
        await page.emulateMedia({ colorScheme: scheme });
        await mockApi(page);
        await page.goto(screen.path);
        await screen.ready(page);
        if (screen.name === "report-fail") {
          await page.getByRole("button", { name: /Outline culprit 1/ }).click();
        }
        await settle(page);
        await page.evaluate(() => Promise.all(Array.from(document.images).map((img) => img.decode().catch(() => null))));
        await page.screenshot({ path: path.join(DIR, `${screen.name}-${size.label}-${scheme}.png`), fullPage: true });
      });
    }
  }
}

test("frame dialog 1280 light", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await settle(page);
  await page.screenshot({ path: path.join(DIR, "frame-dialog-1280-light.png") });
});

// ---------------------------------------------------------------------------
// Mid-transition stills: proof that the motion exists. Playwright's fake clock
// drives requestAnimationFrame and performance.now, which Motion's spring,
// layout and SVG-transform animations run on; the clock is paused, the action
// is taken, and the clock is advanced part of the way before the shot.

/** Pause the page clock (installed before load) a little in the future, past any rate gate. */
async function pauseClock(page: Page, aheadMs = 1000) {
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(now + aheadMs);
}

/** Click at an element's centre with a real mouse event (a pointer click, detail 1), without actionability waits. */
async function mouseClick(page: Page, locator: ReturnType<Page["locator"]>) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("element has no box");
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

test("still: culprit outline mid-glide", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.clock.install();
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: /FAIL/ })).toBeVisible();
  const figure = page.locator("figure").first();
  await figure.scrollIntoViewIfNeeded();
  await page.getByRole("button", { name: /Outline culprit 2 / }).click();
  await page.waitForTimeout(700);
  await pauseClock(page);
  await mouseClick(page, page.getByRole("button", { name: /Outline culprit 1 / }));
  for (let i = 0; i < 5; i++) await page.clock.runFor(16);
  await figure.screenshot({ path: path.join(DIR, "still-culprit-outline-mid-glide-1280-light.png") });
});

test("still: frame dialog mid-open", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.clock.install();
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: /FAIL/ })).toBeVisible();
  const thumb = page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ });
  await thumb.scrollIntoViewIfNeeded();
  await page.waitForTimeout(700);
  await page.waitForFunction(() => Array.from(document.images).every((img) => img.complete));
  // Only a short pause ahead, so the open gate's clock and the real one stay
  // close (the panel fade and the ghost both run on the paused page clock).
  await pauseClock(page, 20);
  await mouseClick(page, thumb);
  // Frame by frame: the ghost is placed on the next frame, then grows for DUR.quick (160 ms) with the
  // panel's fade; at 64 ms it is about half way from the thumbnail to the picture.
  for (let i = 0; i < 4; i++) await page.clock.runFor(16);
  await page.screenshot({ path: path.join(DIR, "still-frame-dialog-mid-open-1280-light.png") });
});

test("still: timeline bars mid-grow", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.clock.install();
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: /FAIL/ })).toBeVisible();
  await page.waitForTimeout(700);
  await pauseClock(page);
  const chart = page.getByRole("img", { name: /^Bar chart/ });
  await page.evaluate(() => {
    const el = document.querySelector("svg[role='img']")!;
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: "instant" });
  });
  await page.waitForTimeout(150); // the IntersectionObserver reports on real rendering, not the paused clock
  for (let i = 0; i < 20; i++) await page.clock.runFor(16);
  await chart.screenshot({ path: path.join(DIR, "still-timeline-mid-grow-1280-light.png") });
});
