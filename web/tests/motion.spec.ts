// Motion safety tests (web/MOTION.md section 6, "Playwright changes"): the
// rAF watchdog, settled screens, reduced motion and print showing final
// states, the shared large-area gate, keyboard presses that never scale, hover
// colour pairs that are never a flash transition, decode-before-swap in the
// frame viewer, the spring settling inside 600 ms, and a static scan of the
// source for forbidden patterns.

import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { spring } from "motion";
import { DUR, SPRING } from "../lib/motion";
import { IDS, mockApi } from "./mock-api";

const SCREENS: { path: string; ready: (p: Page) => Promise<void> }[] = [
  { path: "/", ready: (p) => expect(p.getByRole("button", { name: "Run audit" })).toBeVisible() },
  { path: `/run/?id=${IDS.RUNNING}`, ready: (p) => expect(p.getByRole("button", { name: "Cancel audit" })).toBeVisible() },
  { path: `/report/?id=${IDS.FAIL}`, ready: (p) => expect(p.getByRole("heading", { name: /FAIL/ })).toBeVisible() },
  { path: `/report/?id=${IDS.PASS}`, ready: (p) => expect(p.getByRole("heading", { name: /PASS/ })).toBeVisible() },
  { path: "/history/", ready: (p) => expect(p.getByRole("link", { name: /^Open report for/ }).first()).toBeVisible() },
];

/** Every choreography ends within 600 ms of its trigger; 700 ms is the settle margin. */
const SETTLE_MS = 700;

// ------------------------------------------------------------ rAF watchdog

/** Counts requestAnimationFrame callbacks from before the first page script (Motion captures rAF at load). */
const COUNT_RAF = () => {
  const w = window as unknown as { __rafCount: number };
  w.__rafCount = 0;
  const original = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (callback: FrameRequestCallback) =>
    original((time) => {
      w.__rafCount += 1;
      callback(time);
    });
};

const rafCount = (page: Page) => page.evaluate(() => (window as unknown as { __rafCount: number }).__rafCount);

/** 700 ms after the last input, the next 500 ms must run no animation frame at all: nothing loops. */
async function expectQuiet(page: Page, label: string) {
  await page.waitForTimeout(SETTLE_MS);
  const before = await rafCount(page);
  await page.waitForTimeout(500);
  expect((await rafCount(page)) - before, `${label}: rAF callbacks in a quiet 500 ms`).toBe(0);
}

/** Scroll through the page in viewport steps so in-view entrances play, then back to the top. */
async function scrollThrough(page: Page) {
  const { height, step } = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, step: window.innerHeight }));
  for (let y = 0; y < height; y += step) {
    await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y);
    await page.waitForTimeout(40);
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
}

for (const reduced of [false, true]) {
  test(`SAFETY: rAF goes quiet on every screen and after interactions${reduced ? " (reduced motion)" : ""}`, async ({ page }) => {
    if (reduced) await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(COUNT_RAF);
    await mockApi(page);
    for (const screen of SCREENS) {
      await page.goto(screen.path);
      await screen.ready(page);
      await scrollThrough(page);
      await page.mouse.move(0, 0); // no pointer over a spotlight
      // The Running screen keeps polling once a second: each answer moves the
      // bar once, so measure it between two polls only when nothing changes.
      if (screen.path.startsWith("/run/")) continue;
      await expectQuiet(page, screen.path);
    }

    // Scripted interactions on the report: culprit glide, overlay, dialog open/step/close.
    await page.goto(`/report/?id=${IDS.FAIL}`);
    await page.getByRole("button", { name: /Outline culprit 1 / }).click();
    await page.waitForTimeout(400);
    await page.getByRole("button", { name: /Outline culprit 2 / }).click();
    await page.getByRole("button", { name: "Heatmap overlay" }).click();
    await page.mouse.move(0, 0);
    await expectQuiet(page, "report after outline and overlay");
    await page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ }).click();
    await page.waitForTimeout(750); // the viewer's first step waits 500 ms after its 160 ms opening fade
    await page.getByRole("button", { name: "Next frame" }).click();
    await page.mouse.move(0, 0);
    await expectQuiet(page, "frame viewer after a step");
    await page.getByRole("button", { name: "Close" }).click();
    await page.mouse.move(0, 0);
    await expectQuiet(page, "report after closing the viewer");

    // New audit: radio glide and custom row; History: route change.
    await page.goto("/");
    await page.getByLabel("Custom size").check();
    await page.getByLabel("1366 × 768 (laptop)").check();
    await page.getByRole("link", { name: "History" }).click();
    await expect(page.getByRole("heading", { name: "Past audits" })).toBeVisible();
    // A route that mounts within 500 ms of the last display change is held (blank, aria-busy) and
    // shown 500 ms later, with the nav underline moving then; quiet is measured from there.
    await expect(page.locator(".route[aria-busy]")).toHaveCount(0);
    await page.mouse.move(0, 0);
    await expectQuiet(page, "history after a route change");
  });
}

// ------------------------------------------------------------ settled screens

async function viewportImagesLoaded(page: Page) {
  await page.waitForFunction(() =>
    Array.from(document.images)
      .filter((img) => {
        const r = img.getBoundingClientRect();
        return r.bottom > 0 && r.top < window.innerHeight && r.width > 0;
      })
      .every((img) => img.complete && img.naturalWidth > 0),
  );
}

test("settle: screenshots 700 ms and 1400 ms after the last input are identical", async ({ page }) => {
  await mockApi(page);
  for (const screen of SCREENS.filter((s) => !s.path.startsWith("/run/"))) {
    await page.goto(screen.path);
    await screen.ready(page);
    await viewportImagesLoaded(page);
    await page.mouse.move(0, 0);
    await page.waitForTimeout(SETTLE_MS);
    const first = await page.screenshot();
    await page.waitForTimeout(SETTLE_MS);
    const second = await page.screenshot();
    expect(first.equals(second), screen.path).toBe(true);
  }
});

test("reduced motion: the first ready frame is final, and nothing in main is see-through", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockApi(page);
  for (const screen of SCREENS.filter((s) => !s.path.startsWith("/run/"))) {
    await page.goto(screen.path);
    await screen.ready(page);
    await viewportImagesLoaded(page);
    await page.mouse.move(0, 0);
    const ready = await page.screenshot();
    await page.waitForTimeout(SETTLE_MS);
    const later = await page.screenshot();
    expect(ready.equals(later), screen.path).toBe(true);
    const faint = await page.evaluate(() =>
      Array.from(document.querySelectorAll("main *"))
        .filter((el) => Number(getComputedStyle(el).opacity) < 1)
        .map((el) => el.outerHTML.slice(0, 80)),
    );
    expect(faint, screen.path).toEqual([]);
  }
});

test("the in-app motion switch reduces motion like the OS setting", async ({ page }) => {
  await mockApi(page);
  await page.goto("/history/");
  const toggle = page.getByRole("button", { name: /^Reduce motion/ });
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("html")).toHaveAttribute("data-motion", "reduce");
  // It survives a reload (localStorage) and applies before the first frame.
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: /FAIL/ })).toBeVisible();
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
  const faint = await page.evaluate(
    () => Array.from(document.querySelectorAll("main *")).filter((el) => Number(getComputedStyle(el).opacity) < 1).length,
  );
  expect(faint).toBe(0);
  await page.getByRole("button", { name: /^Reduce motion/ }).click();
  await expect(page.locator("html")).not.toHaveAttribute("data-motion", "reduce");
});

test("print: every in-view group shows its final state", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: /FAIL/ })).toBeVisible();
  await page.waitForTimeout(SETTLE_MS);
  // Below-the-fold groups have not arrived yet (they wait for the viewport); print must not care.
  await page.emulateMedia({ media: "print" });
  const faint = await page.evaluate(() =>
    Array.from(document.querySelectorAll("main *"))
      .filter((el) => Number(getComputedStyle(el).opacity) < 1 || /scale\(0|matrix\(1, 0, 0, 0/.test(getComputedStyle(el).transform))
      .map((el) => el.outerHTML.slice(0, 80)),
  );
  expect(faint).toEqual([]);
});

// ------------------------------------------------------------ gates

test("SAFETY: the heatmap overlay and the data table share one 350 ms gate", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const overlay = page.getByRole("button", { name: "Heatmap overlay" });
  const table = page.getByRole("button", { name: "Show data table" });
  await expect(overlay).toBeVisible();
  const handles = [await overlay.elementHandle(), await table.elementHandle()];
  await page.evaluate(() => {
    const w = window as unknown as { __changes: number };
    w.__changes = 0;
    const stage = document.querySelector("[data-overlay]")!;
    const tableBox = document.getElementById("timeline-table")!;
    const observer = new MutationObserver((records) => (w.__changes += records.length));
    observer.observe(stage, { attributes: true, attributeFilter: ["data-overlay"] });
    observer.observe(tableBox, { attributes: true, attributeFilter: ["hidden"] });
  });
  const t0 = Date.now();
  for (let i = 0; i < 24; i++) {
    await handles[i % 2]?.click({ delay: 0 });
    await page.waitForTimeout(50);
  }
  const elapsed = Date.now() - t0;
  const changes = await page.evaluate(() => (window as unknown as { __changes: number }).__changes);
  expect(changes).toBeGreaterThanOrEqual(1);
  expect(changes).toBeLessThanOrEqual(1 + Math.floor(elapsed / 350));
});

test("SAFETY: the frame viewer opens at most once a second; a click inside that second opens it when it ends", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const thumb = page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ });
  const dialog = page.getByRole("dialog", { name: "Frame viewer" });
  await thumb.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  const t0 = Date.now();
  await thumb.click(); // inside the 1 s gate: not lost, deferred
  await expect(dialog).toBeVisible({ timeout: 3000 });
  expect(Date.now() - t0).toBeGreaterThanOrEqual(300);
});

// ------------------------------------------------------------ keyboard presses

test("Space on a keyboard-focused button starts no transform", async ({ page }) => {
  await mockApi(page);
  await page.goto("/history/");
  const del = page.getByRole("button", { name: /^Delete audit of / }).first();
  await del.focus();
  await page.keyboard.down("Space");
  const state = await del.evaluate((el) => ({
    transform: getComputedStyle(el).transform,
    transitions: el.getAnimations().filter((a) => (a as CSSTransition).transitionProperty === "transform").length,
  }));
  await page.keyboard.up("Space").catch(() => undefined);
  expect(state).toEqual({ transform: "none", transitions: 0 });
});

// ------------------------------------------------------------ hover colour pairs

/** Relative luminance of a computed CSS colour (rgb() or color(srgb ...)), composited on white for alpha. */
function luminance(css: string): number | null {
  let rgb: number[] | null = null;
  let alpha = 1;
  const m = css.match(/^rgba?\(([^)]+)\)$/);
  if (m) {
    const parts = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    rgb = parts.slice(0, 3).map((v) => v / 255);
    alpha = parts[3] ?? 1;
  }
  const c = css.match(/^color\(srgb ([^)]+)\)$/);
  if (c) {
    const parts = c[1].split(/[ /]+/).filter(Boolean).map(Number);
    rgb = parts.slice(0, 3);
    alpha = parts[3] ?? 1;
  }
  if (!rgb) return null;
  if (alpha === 0) return null; // transparent: the surface below shows
  const lin = rgb.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

/** docs/SPEC.md 5.4: a transition is a change of 0.10 or more with the darker side below 0.80. */
const isTransition = (a: number, b: number) => Math.abs(a - b) >= 0.1 && Math.min(a, b) < 0.8;

const HOVER_TARGETS: { page: string; selector: string }[] = [
  { page: "/history/", selector: "a.btn" },
  { page: "/history/", selector: ".btn-danger" },
  { page: "/history/", selector: "tbody tr" },
  { page: "/history/", selector: ".primary-nav a:not([aria-current])" },
  { page: "/", selector: ".btn-primary" },
  { page: "/", selector: ".radio-card" },
  { page: "/", selector: "#target" },
  { page: `/report/?id=${IDS.FAIL}`, selector: "button[class*='thumb']" },
  { page: `/report/?id=${IDS.FAIL}`, selector: "[data-overlay] ~ * , button[aria-pressed]" },
  { page: `/report/?id=${IDS.FAIL}`, selector: ".site-footer .link-button" },
];

for (const scheme of ["light", "dark"] as const) {
  test(`SAFETY: no hover colour pair is a flash transition (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await mockApi(page);
    await page.goto("/");
    // The hover rules are gated by (hover: hover) and (pointer: fine); make sure they are exercised.
    expect(await page.evaluate(() => matchMedia("(hover: hover) and (pointer: fine)").matches)).toBe(true);
    for (const target of HOVER_TARGETS) {
      await page.goto(target.page);
      const el = page.locator(target.selector).first();
      await el.scrollIntoViewIfNeeded();
      await page.mouse.move(0, 0);
      await page.waitForTimeout(250);
      const read = () =>
        el.evaluate((node) => {
          const cell = node.tagName === "TR" ? node.querySelector("td")! : node;
          const s = getComputedStyle(cell);
          return { bg: s.backgroundColor, color: s.color, border: s.borderTopColor };
        });
      const before = await read();
      await el.hover();
      await page.waitForTimeout(250); // transitions are 200 ms or less
      const after = await read();
      for (const key of ["bg", "color", "border"] as const) {
        const a = luminance(before[key]);
        const b = luminance(after[key]);
        if (a === null || b === null) continue;
        expect(isTransition(a, b), `${scheme} ${target.selector} ${key}: ${before[key]} -> ${after[key]}`).toBe(false);
      }
    }
  });
}

// ------------------------------------------------------------ frame viewer

test("frame viewer: a step swaps the picture only after the next frame is decoded", async ({ page }) => {
  await mockApi(page);
  // Frames 1 and up answer slowly; frame 0 at once.
  await page.route(/\/frames\/([1-9]\d*)\.png$/, async (route) => {
    await new Promise((r) => setTimeout(r, 1200));
    await route.fallback();
  });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await page.getByRole("button", { name: /^Enlarge: .*frame 0 ·/ }).click();
  const dialog = page.getByRole("dialog", { name: "Frame viewer" });
  const image = dialog.getByRole("img");
  await expect(image).toHaveAttribute("src", /\/frames\/0\.png$/);
  await page.waitForTimeout(750); // the first step waits 500 ms after the 160 ms opening fade
  await page.keyboard.press("ArrowRight");
  await expect(dialog.getByText(/^frame 1 of 600/)).toBeVisible(); // the counter follows the request at once
  // While frame 1 loads, the old frame stays fully drawn: never an empty box between two frames.
  for (let i = 0; i < 6; i++) {
    const state = await image.evaluate((img: HTMLImageElement) => ({ src: img.getAttribute("src"), drawn: img.complete && img.naturalWidth > 0 }));
    expect(state.drawn, `sample ${i}`).toBe(true);
    if (/\/frames\/1\.png$/.test(state.src ?? "")) break;
    expect(state.src).toMatch(/\/frames\/0\.png$/);
    await page.waitForTimeout(100);
  }
  await expect(image).toHaveAttribute("src", /\/frames\/1\.png$/, { timeout: 4000 });
  expect(await image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
});

// ------------------------------------------------------------ review fixes (MOTION.md 8)

/** An element's box in page coordinates, so a scroll between two readings does not count as a move. */
const pageTop = (locator: ReturnType<Page["locator"]>) =>
  locator.evaluate((el) => Math.round(el.getBoundingClientRect().top + window.scrollY));

for (const how of ["os", "switch"] as const) {
  test(`reduced motion (${how}): the open frame viewer runs no animation, its veil included`, async ({ page }) => {
    if (how === "os") await page.emulateMedia({ reducedMotion: "reduce" });
    else
      await page.addInitScript(() => {
        try {
          window.localStorage.setItem("flashtrace-motion", "reduce");
        } catch {
          // storage blocked: the test then fails on data-motion below
        }
      });
    await mockApi(page);
    await page.goto(`/report/?id=${IDS.FAIL}`);
    if (how === "switch") await expect(page.locator("html")).toHaveAttribute("data-motion", "reduce");
    await page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ }).click();
    await expect(page.getByRole("dialog", { name: "Frame viewer" })).toBeVisible();
    const running = await page.evaluate(() =>
      document.getAnimations().map((a) => `${a.constructor.name} ${(a as CSSAnimation).animationName ?? ""}`),
    );
    expect(running).toEqual([]);
  });
}

test("a keyboard-opened frame viewer is fully visible at once, with focus on Next frame", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const thumb = page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ });
  await thumb.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Next frame" })).toBeFocused();
  const state = await page.evaluate(() => {
    let opacity = 1;
    for (let el: Element | null = document.activeElement; el; el = el.parentElement) opacity *= Number(getComputedStyle(el).opacity);
    return { opacity, veil: document.getAnimations().filter((a) => a instanceof CSSAnimation).length };
  });
  expect(state).toEqual({ opacity: 1, veil: 0 });
});

test("SAFETY layout: choosing a viewport size never moves the blocks below it (390 and 1280 px)", async ({ page }) => {
  await mockApi(page);
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Run audit" })).toBeVisible();
    await page.waitForTimeout(SETTLE_MS);
    const attribution = page.getByRole("group", { name: "Find the cause (attribution)" });
    const customWidth = page.getByLabel("Width (px)");
    await expect(customWidth).toBeDisabled();
    await expect(customWidth).toHaveValue("1024"); // the chosen preset's size
    const before = await pageTop(attribution);
    await page.getByLabel("Custom size").check();
    await expect(customWidth).toBeEnabled();
    expect(await pageTop(attribution), `${width} px, Custom`).toBe(before);
    await page.getByLabel("1366 × 768 (laptop)").check();
    await expect(customWidth).toHaveValue("1366");
    expect(await pageTop(attribution), `${width} px, preset`).toBe(before);
  }
});

test("SAFETY layout: selecting a culprit or toggling the overlay moves nothing below the figure (390 px)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const outline = page.getByRole("button", { name: /Outline culprit 1 / });
  const overlay = page.getByRole("button", { name: "Heatmap overlay" });
  await outline.scrollIntoViewIfNeeded();
  await page.waitForTimeout(SETTLE_MS);
  const before = { overlay: await pageTop(overlay), outline: await pageTop(outline) };
  await outline.click();
  await expect(outline).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("figcaption")).toContainText("culprit 1 (#loader)"); // announced, visually hidden
  expect({ overlay: await pageTop(overlay), outline: await pageTop(outline) }).toEqual(before);
  await page.waitForTimeout(400); // the report's shared 350 ms gate
  await overlay.click();
  await expect(overlay).toHaveAttribute("aria-pressed", "false");
  expect({ overlay: await pageTop(overlay), outline: await pageTop(outline) }).toEqual(before);
});

test("SAFETY: a History delete at 390 px fades the card and closes the gap at once (nothing slides)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto("/history/");
  const cards = page.getByRole("list", { name: "Past audits, newest first" }).getByRole("listitem");
  await expect(cards).toHaveCount(5);
  await page.evaluate(() => {
    // Sample every card's transform on each frame for about 1.5 s (a bounded loop: it stops by itself).
    const w = window as unknown as { __moved: string[] };
    w.__moved = [];
    let frames = 0;
    const sample = () => {
      for (const li of document.querySelectorAll("ul[aria-label='Past audits, newest first'] > li")) {
        const t = getComputedStyle(li).transform;
        if (t !== "none") w.__moved.push(t);
      }
      if (++frames < 90) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /^Delete audit of / }).first().click();
  await expect(cards).toHaveCount(4);
  await page.waitForTimeout(1600);
  expect(await page.evaluate(() => (window as unknown as { __moved: string[] }).__moved)).toEqual([]);
});

test("turning reduced motion off never hides or replays content that is on screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: /FAIL/ })).toBeVisible();
  await page.waitForTimeout(SETTLE_MS); // the chart, below the fold, was reported off screen and waits there
  const toggle = page.getByRole("button", { name: /^Reduce motion/ });
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-motion", "reduce");
  // Scroll the chart into view while motion is reduced, keyboard focus staying on the switch.
  await page.evaluate(() => {
    const el = document.querySelector("svg[role='img']")!;
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 120, behavior: "instant" });
  });
  await page.waitForTimeout(400);
  await expect(toggle).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("html")).not.toHaveAttribute("data-motion", "reduce");
  const hidden = () =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll("main [data-arrive]"))
        .filter((el) => {
          const r = el.getBoundingClientRect();
          if (r.bottom < 0 || r.top > window.innerHeight) return false;
          const s = getComputedStyle(el);
          return Number(s.opacity) < 1 || (s.transform !== "none" && !/^matrix\(1, 0, 0, 1, /.test(s.transform));
        })
        .map((el) => el.outerHTML.slice(0, 60)),
    );
  expect(await hidden()).toEqual([]);
  await page.waitForTimeout(100);
  expect(await hidden()).toEqual([]);
});

// ------------------------------------------------------------ tokens

test("the glide spring settles within 600 ms; tween tokens stay within 600 ms", () => {
  const generator = spring({ keyframes: [0, 1], ...SPRING.glide });
  let t = 0;
  while (!generator.next(t).done) {
    t += 1;
    expect(t, "spring still moving").toBeLessThanOrEqual(600);
  }
  for (const value of Object.values(DUR)) expect(value).toBeLessThanOrEqual(0.6);
  expect(SPRING.glide.bounce).toBe(0);
});

// ------------------------------------------------------------ static scan

const SOURCE_DIRS = ["app", "components", "lib"].map((d) => path.join(__dirname, "..", d));

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return /\.(tsx?|css)$/.test(entry.name) ? [full] : [];
  });
}

/** Comments may explain what is forbidden; only code counts. */
const stripComments = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1").replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "");

const FORBIDDEN: { name: string; pattern: RegExp }[] = [
  { name: "repeat:", pattern: /\brepeat\s*:/ },
  { name: "Infinity", pattern: /(?<![_A-Z])Infinity\b/ },
  { name: "infinite", pattern: /\binfinite\b/ },
  { name: "iteration-count", pattern: /iteration-?count/i },
  { name: "setInterval", pattern: /\bsetInterval\s*\(/ },
  { name: "filter in a transition or animation", pattern: /(transition|animation)[^;{}]*\bfilter\b/ },
  { name: "transition: all", pattern: /transition\s*:\s*all\b/ },
  { name: "startViewTransition", pattern: /startViewTransition/ },
  { name: "ViewTransition", pattern: /\bViewTransition\b|@view-transition|viewTransition/ },
  { name: "useScroll", pattern: /\buseScroll\b|animation-timeline|scroll-behavior\s*:\s*smooth/ },
  { name: "blur animation", pattern: /blur\(\d/ },
];

test("static scan: no forbidden motion pattern in app, components or lib", () => {
  const offenders: string[] = [];
  for (const file of SOURCE_DIRS.flatMap(sourceFiles)) {
    const code = stripComments(fs.readFileSync(file, "utf8"));
    for (const { name, pattern } of FORBIDDEN) if (pattern.test(code)) offenders.push(`${path.relative(path.join(__dirname, ".."), file)}: ${name}`);
    for (const m of code.matchAll(/layoutId\s*[=:]\s*\{?\s*["'`]([^"'`]*)["'`]/g)) {
      if (!m[1].includes(":")) offenders.push(`${path.relative(path.join(__dirname, ".."), file)}: layoutId "${m[1]}" has no route:component namespace`);
    }
  }
  expect(offenders).toEqual([]);
});
