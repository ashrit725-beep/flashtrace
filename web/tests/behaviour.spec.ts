// Behaviour and SAFETY tests: keyboard-only use, the frame viewer, culprit
// selection, reduced motion, "nothing animates faster than 3 Hz", polling at
// most once per second, and the run -> report / verify / cancel flows.

import { expect, test, type Locator, type Page } from "@playwright/test";
import { IDS, NEW_JOB_ID, mockApi, readFixture, replaceJson } from "./mock-api";

/** Every screen in its loaded state, with a locator that proves it rendered. */
const SCREENS: { path: string; ready: (p: Page) => Promise<void> }[] = [
  { path: "/", ready: (p) => expect(p.getByRole("button", { name: "Run audit" })).toBeVisible() },
  { path: `/run/?id=${IDS.RUNNING}`, ready: (p) => expect(p.getByRole("button", { name: "Cancel audit" })).toBeVisible() },
  { path: `/report/?id=${IDS.FAIL}`, ready: (p) => expect(p.getByRole("heading", { name: /FAIL/ })).toBeVisible() },
  { path: `/report/?id=${IDS.PASS}`, ready: (p) => expect(p.getByRole("heading", { name: /PASS/ })).toBeVisible() },
  { path: `/report/?id=${IDS.INCONCLUSIVE}`, ready: (p) => expect(p.getByRole("heading", { name: /INCONCLUSIVE/ })).toBeVisible() },
  // Not getByRole("table"): below 640 px History shows cards instead of the table.
  { path: "/history/", ready: (p) => expect(p.getByRole("link", { name: /^Open report for/ }).first()).toBeVisible() },
];

const STEP_GAP_MS = 400; // a little more than the viewer's 350 ms step limit

/** Press Tab until `name` has focus (keyboard only, no clicks). */
async function tabTo(page: Page, name: string, max = 40) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? "");
    if (focused === name) return;
  }
  throw new Error(`Could not reach "${name}" with Tab`);
}

test("keyboard only: fill the form, Tab to Run, submit, land on /run", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Run audit" })).toBeVisible();

  // The skip link is the first stop and jumps to <main>.
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();

  await tabTo(page, ""); // the URL input has no text content: first empty-text stop after the nav
  await expect(page.getByLabel("Page URL or local file path")).toBeFocused();
  await page.keyboard.type("https://example.com/");

  await tabTo(page, "Run audit");
  const run = page.getByRole("button", { name: "Run audit" });
  await expect(run).toBeFocused();
  // Focus is visible: a real outline, not "none".
  expect(await run.evaluate((el) => getComputedStyle(el).outlineStyle)).not.toBe("none");

  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/run/\\?id=${NEW_JOB_ID}$`));
  await expect(page.getByRole("heading", { name: "Waiting in queue" })).toBeVisible();
  const post = api.calls.find((c) => c.method === "POST" && c.path === "/jobs");
  expect(post?.body).toEqual({
    url: "https://example.com/",
    duration: 10,
    viewport: { width: 1024, height: 768 },
    attribute: "bbox",
  });
});

test("inline validation is tied to the field and moves focus", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Run audit" }).click();
  const input = page.getByLabel("Page URL or local file path");
  await expect(input).toBeFocused();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await expect(input).toHaveAccessibleDescription(/Enter a URL or a local file path/);
  // A web address without its scheme is caught here, with the fix spelled out.
  for (const bare of ["example.com", "www.example.com/page", "127.0.0.1:8765/site/", "localhost:3000"]) {
    await input.fill(bare);
    await expect(input, bare).toHaveAccessibleDescription(/looks like a web address\. Add https:\/\//);
  }
  // Paths the server accepts are not blocked: absolute, and relative to the
  // FlashTrace folder (the server's default; docs/API.md).
  for (const path of ["/Users/me/site/index.html", "demo/site/index.html", "./demo/site/", "index.html"]) {
    await input.fill(path);
    await expect(input, path).not.toHaveAttribute("aria-invalid", "true");
  }
});

test("a project-relative path is submitted as typed", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/");
  await page.getByLabel("Page URL or local file path").fill("demo/site/index.html");
  await page.getByRole("button", { name: "Run audit" }).click();
  await expect(page).toHaveURL(new RegExp(`/run/\\?id=${NEW_JOB_ID}$`));
  const post = api.calls.find((c) => c.method === "POST" && c.path === "/jobs");
  expect((post?.body as { url: string }).url).toBe("demo/site/index.html");
});

test("server down: error state with Retry and the start command", async ({ page }) => {
  await mockApi(page, { mode: "error" });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "The FlashTrace server is not running" })).toBeVisible();
  await expect(page.locator("code", { hasText: "flashtrace serve" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("frame viewer: open with Enter, step with arrow keys, throttled, Escape restores focus", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const thumb = page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ });
  await thumb.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog", { name: "Frame viewer" });
  await expect(dialog).toBeVisible();
  const counter = dialog.getByText(/^frame \d+ of 600/);
  await expect(counter).toHaveText(/^frame 14 of 600 \(t = 0\.233 s\)/);

  await page.keyboard.press("ArrowRight");
  await expect(counter).toHaveText(/^frame 15 of 600 \(t = 0\.250 s\)/);
  await page.waitForTimeout(STEP_GAP_MS);
  await page.keyboard.press("ArrowLeft");
  await expect(counter).toHaveText(/^frame 14 of 600/);
  await page.waitForTimeout(STEP_GAP_MS);
  await page.keyboard.press("ArrowLeft");
  await expect(counter).toHaveText(/^frame 13 of 600/);

  // The image follows the counter.
  await expect(dialog.getByRole("img")).toHaveAttribute("src", /\/frames\/13\.png$/);

  // Buttons step too.
  await page.waitForTimeout(STEP_GAP_MS);
  await dialog.getByRole("button", { name: "Next frame" }).click();
  await expect(counter).toHaveText(/^frame 14 of 600/);

  // Tab stays inside the modal dialog.
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate((d) => d.contains(document.activeElement))).toBe(true);
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(thumb).toBeFocused();
});

test("SAFETY: frame viewer ignores key auto-repeat and steps at most ~3 times per second", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  await page.getByRole("button", { name: /^Enlarge: .*frame 0 ·/ }).click();
  const counter = page.getByRole("dialog").getByText(/^frame \d+ of 600/);
  await expect(counter).toHaveText(/^frame 0 of 600/);

  // Holding the key: the first keydown steps, the auto-repeats (repeat=true) do not.
  for (let i = 0; i < 15; i++) await page.keyboard.down("ArrowRight");
  await page.keyboard.up("ArrowRight");
  await expect(counter).toHaveText(/^frame 1 of 600/);

  // Hammering the button: 10 clicks in well under a second give at most 3 steps.
  await page.waitForTimeout(STEP_GAP_MS);
  const next = page.getByRole("button", { name: "Next frame" });
  const t0 = Date.now();
  for (let i = 0; i < 10; i++) await next.click({ delay: 0 });
  const elapsed = Date.now() - t0;
  const text = (await counter.textContent()) ?? "";
  const index = Number(text.match(/^frame (\d+)/)?.[1]);
  const allowed = 1 + Math.floor(elapsed / 350) + 1;
  expect(index - 1).toBeLessThanOrEqual(allowed);
  expect(index - 1).toBeGreaterThanOrEqual(1);
});

test("culprit selection highlights its rectangle and announces state", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const first = page.getByRole("button", { name: /Outline culprit 1 / });
  const second = page.getByRole("button", { name: /Outline culprit 2 / });
  const outline = page.getByTestId("culprit-outline");
  await expect(outline).toHaveCount(0);

  await first.click();
  await expect(first).toHaveAttribute("aria-pressed", "true");
  await expect(outline).toContainText("Culprit 1: #loader");

  // Keyboard: Enter on another row's button moves the selection. (Each step
  // waits out the 350 ms SAFETY throttle shared by every culprit toggle.)
  await page.waitForTimeout(STEP_GAP_MS);
  await second.focus();
  await page.keyboard.press("Enter");
  await expect(second).toHaveAttribute("aria-pressed", "true");
  await expect(first).toHaveAttribute("aria-pressed", "false");
  await expect(outline).toContainText("Culprit 2: #live-badge");
  await expect(page.locator("figcaption")).toContainText("culprit 2 (#live-badge)");

  // Clicking the row itself (mouse convenience) works too, and toggles off.
  await page.waitForTimeout(STEP_GAP_MS);
  await page.getByRole("row", { name: /#live-badge/ }).click();
  await expect(second).toHaveAttribute("aria-pressed", "false");
  await expect(outline).toHaveCount(0);
});

test("reduced motion: no running animations on any screen", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockApi(page);
  for (const screen of SCREENS) {
    await page.goto(screen.path);
    await screen.ready(page);
    // Hover and focus controls so any transitions would start now.
    const button = page.getByRole("button").first();
    await button.hover();
    await button.focus();
    expect(await page.evaluate(() => document.getAnimations().length), screen.path).toBe(0);
  }
});

test("SAFETY: nothing on any screen animates faster than 3 Hz", async ({ page }) => {
  await mockApi(page);
  for (const screen of SCREENS) {
    await page.goto(screen.path);
    await screen.ready(page);
    const buttons = page.getByRole("button");
    const count = Math.min(await buttons.count(), 6);
    // Sample repeatedly for ~1.2 s while hovering controls (which starts
    // their colour transitions), so anything that moves gets caught.
    for (let sample = 0; sample < 12; sample++) {
      if (count > 0) await buttons.nth(sample % count).hover();
      const found = await page.evaluate(() =>
        document.getAnimations().map((a) => {
          const timing = a.effect?.getComputedTiming();
          return {
            kind: a.constructor.name,
            duration: Number(timing?.duration ?? 0),
            iterations: Number(timing?.iterations ?? 1),
          };
        }),
      );
      for (const a of found) {
        if (a.kind === "CSSTransition") {
          // One-way colour change on hover/focus: short (<= 200 ms) and never repeating.
          expect(a.duration, `${screen.path} transition`).toBeLessThanOrEqual(200);
          expect(a.iterations, `${screen.path} transition`).toBe(1);
        } else {
          // Anything periodic must have a period of at least 1 s (well under 3 Hz).
          expect(a.duration, `${screen.path} ${a.kind}`).toBeGreaterThanOrEqual(1000);
        }
      }
      await page.waitForTimeout(100);
    }
    const hazards = await page.evaluate(() => ({
      // Chrome animates an indeterminate <progress>; every bar must have a value.
      indeterminate: document.querySelectorAll("progress:not([value])").length,
      video: document.querySelectorAll("video").length,
      gif: Array.from(document.images).filter((img) => /\.gif($|\?)/i.test(img.src)).length,
      autoplay: document.querySelectorAll("[autoplay]").length,
    }));
    expect(hazards, screen.path).toEqual({ indeterminate: 0, video: 0, gif: 0, autoplay: 0 });
  }
});

test("phone width (390 px): no screen scrolls sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  for (const screen of SCREENS) {
    await page.goto(screen.path);
    await screen.ready(page);
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(scrollWidth, screen.path).toBeLessThanOrEqual(390);
  }
});

test("polling: the running screen asks at most once per second", async ({ page }) => {
  // Timestamp every fetch where the app makes it (in the browser), so test
  // runner load cannot blur the measurement.
  await page.addInitScript(() => {
    const times: number[] = [];
    (window as unknown as { __jobPolls: number[] }).__jobPolls = times;
    const original = window.fetch.bind(window);
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/jobs/")) times.push(performance.now());
      return original(input, init);
    };
  });
  await mockApi(page);
  await page.goto(`/run/?id=${IDS.RUNNING}`);
  await expect(page.getByRole("heading", { name: /Capturing frames 312\/600/ })).toBeVisible();
  await page.waitForTimeout(3500);
  const times = await page.evaluate(() => (window as unknown as { __jobPolls: number[] }).__jobPolls);
  expect(times.length).toBeGreaterThanOrEqual(2);
  for (let i = 1; i < times.length; i++) {
    expect(times[i] - times[i - 1]).toBeGreaterThanOrEqual(995);
  }
  const span = times[times.length - 1] - times[0];
  expect(times.length - 1).toBeLessThanOrEqual(Math.floor(span / 1000) + 1);
  // The progress bar carries a text alternative.
  await expect(page.locator("progress")).toHaveAttribute("aria-valuetext", "Capturing frames 312/600");
});

test("a finished job redirects from /run to its report", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/run/?id=${IDS.FAIL}`);
  await expect(page).toHaveURL(new RegExp(`/report/\\?id=${IDS.FAIL}$`));
  await expect(page.getByRole("heading", { name: /FAIL/ })).toBeVisible();
});

test("cancel asks first, then deletes the job", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto(`/run/?id=${IDS.RUNNING}`);
  page.once("dialog", (d) => d.dismiss());
  await page.getByRole("button", { name: "Cancel audit" }).click();
  expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Cancel audit" }).click();
  await expect(page.getByRole("heading", { name: "Audit cancelled" })).toBeVisible();
  expect(api.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual([`/jobs/${IDS.RUNNING}`]);
});

test("a failed job shows its error, and Retry re-submits the same settings", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto(`/run/?id=${IDS.ERROR}`);
  await expect(page.getByText(/did not respond within 30 s/)).toBeVisible();
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page).toHaveURL(new RegExp(`/run/\\?id=${NEW_JOB_ID}$`));
  const post = api.calls.find((c) => c.method === "POST" && c.path === "/jobs");
  expect(post?.body).toEqual({
    url: "https://example.test/broken",
    duration: 10,
    viewport: { width: 1024, height: 768 },
    attribute: "bbox",
  });
});

test("verify a fix: POST, poll the job, reload the report, badge turns Verified, result announced", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const fix1 = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Fix 1:/ }) });
  const badge = fix1.locator(".badge");
  const status = fix1.getByRole("status");
  await expect(badge).toHaveText(/Not verified/);

  // Keyboard: Enter on the button. It is aria-disabled while busy (not
  // disabled), so it keeps focus instead of dropping it to <body>.
  const verify = fix1.getByRole("button", { name: "Verify this fix" });
  await verify.focus();
  await page.keyboard.press("Enter");
  await expect(status).toHaveText(/^Re-capturing the page/);
  const busy = fix1.getByRole("button", { name: "Verifying…" });
  await expect(busy).toBeFocused();
  await expect(busy).toHaveAttribute("aria-disabled", "true");

  await expect(badge).toHaveText(/^\W*Verified$/, { timeout: 5000 });
  await expect(fix1.getByRole("cell", { name: "PASS" })).toBeVisible();
  // The live region says that it finished and what it found.
  await expect(status).toHaveText(
    "Verification finished: verdict after the fix is PASS; general flashes max 0 per second, red 0 per second. Verified.",
  );
  await expect(fix1.getByRole("button", { name: "Verify again" })).toBeFocused();
  expect(api.calls.some((c) => c.method === "POST" && c.path === `/jobs/${IDS.FAIL}/fixes/0/verify`)).toBe(true);
  expect(api.calls.filter((c) => c.path === `/jobs/${IDS.FAIL}/report`).length).toBe(2);
});

test("verify waits for the phase, not just the status: done + verifying is still running", async ({ page }) => {
  const api = await mockApi(page);
  const job = readFixture<Record<string, unknown>[]>("jobs.json").find((j) => j.id === IDS.FAIL);
  // The first three polls say status "done" but phase "verifying" (allowed by
  // docs/API.md); only then does the normal mock finish the verification.
  await replaceJson(page, new RegExp(`^/jobs/${IDS.FAIL}$`), (n) =>
    n <= 3 ? { ...job, status: "done", phase: "verifying" } : undefined,
  );
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const fix1 = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Fix 1:/ }) });
  await fix1.getByRole("button", { name: "Verify this fix" }).click();
  await page.waitForTimeout(2500); // two polls answered "done + verifying"
  await expect(fix1.getByRole("status")).toHaveText(/^Re-capturing the page/);
  expect(api.calls.filter((c) => c.path === `/jobs/${IDS.FAIL}/report`).length).toBe(1);
  await expect(fix1.locator(".badge")).toHaveText(/^\W*Verified$/, { timeout: 6000 });
  expect(api.calls.filter((c) => c.path === `/jobs/${IDS.FAIL}/report`).length).toBe(2);
});

test("a done job that is re-running ablation stays on /run until its phase is done too", async ({ page }) => {
  await mockApi(page);
  const job = readFixture<Record<string, unknown>[]>("jobs.json").find((j) => j.id === IDS.FAIL);
  let rerunning = true;
  await replaceJson(page, new RegExp(`^/jobs/${IDS.FAIL}$`), () =>
    rerunning
      ? { ...job, status: "done", phase: "ablating", progress: { current: 2, total: 6, label: "ablating 2/6" } }
      : undefined,
  );
  await page.goto(`/run/?id=${IDS.FAIL}`);
  await expect(page.getByRole("heading", { name: "Ablating 2/6" })).toBeVisible();
  // A re-run on a bbox audit shows only its own step.
  await expect(page.getByRole("list", { name: "Audit phases" }).getByRole("listitem")).toHaveText([
    /Waiting in queue/,
    /Ablating/,
    /Report ready/,
  ]);
  await page.waitForTimeout(2200);
  await expect(page).toHaveURL(new RegExp(`/run/\\?id=${IDS.FAIL}$`));
  rerunning = false;
  await expect(page).toHaveURL(new RegExp(`/report/\\?id=${IDS.FAIL}$`), { timeout: 4000 });
});

test("an ablate audit lists the ablation step before it starts", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/run/?id=${IDS.RUNNING}`); // attribute "ablate", phase "capturing"
  await expect(page.getByRole("list", { name: "Audit phases" }).getByRole("listitem")).toHaveText([
    /Waiting in queue/,
    /Capturing frames/,
    /Analyzing/,
    /Attributing/,
    /Ablating/,
    /Report ready/,
  ]);
});

test("report empty states: PASS explains calmly, no culprits says why", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.PASS}`);
  await expect(page.getByText(/^No violations\./)).toBeVisible();
  await expect(page.getByText("No attribution was run (mode: none)", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Re-run attribution with ablation" })).toHaveCount(0);

  await page.goto(`/report/?id=${IDS.INCONCLUSIVE}`);
  await expect(page.locator("strong", { hasText: "Non-deterministic capture" })).toBeVisible();

  await page.goto(`/report/?id=${IDS.FAIL}`);
  await expect(page.getByRole("button", { name: "Re-run attribution with ablation" })).toBeVisible();
  await expect(page.getByText("advisory - not a WCAG 2.3.1 criterion", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download report JSON" })).toHaveAttribute("download", /\.json$/);
  // Never claims compliance.
  expect(await page.locator("body").innerText()).not.toMatch(/WCAG compliant/i);
});

test("history: newest first, open a report, delete with confirm", async ({ page }) => {
  await mockApi(page);
  await page.goto("/history/");
  const rows = page.getByRole("row");
  await expect(rows).toHaveCount(6); // header + 5 jobs
  await expect(rows.nth(1)).toContainText("example.test/broken");

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /Delete audit of https:\/\/example.test\/broken/ }).click();
  await expect(rows).toHaveCount(5);
  // Focus moves to the next audit's Delete button, not back to <body>.
  await expect(page.getByRole("button", { name: /^Delete audit of http:\/\/127.0.0.1:8765\/site\/, .*:15:11$/ })).toBeFocused();

  await page.getByRole("link", { name: /^Open report for http:\/\/127.0.0.1:8765\/site\// }).click();
  await expect(page).toHaveURL(new RegExp(`/report/\\?id=${IDS.FAIL}$`));
});

// ------------------------------------------------ SAFETY: toggles under 3 Hz

/**
 * Counts how often `target` changes state (its aria-pressed / aria-expanded
 * attribute) while `act` runs, and how often anything appears or disappears
 * under `area` (the heatmap canvas, the culprit outline, the table).
 */
async function countChanges(target: Locator, area: Locator, act: () => Promise<void>) {
  await target.evaluate((el) => {
    const w = window as unknown as { __toggles: number };
    w.__toggles = 0;
    new MutationObserver((records) => (w.__toggles += records.length)).observe(el, {
      attributes: true,
      attributeFilter: ["aria-pressed", "aria-expanded"],
    });
  });
  await area.evaluate((el) => {
    const w = window as unknown as { __swaps: number };
    w.__swaps = 0;
    new MutationObserver((records) => {
      for (const r of records) w.__swaps += r.type === "attributes" ? 1 : r.addedNodes.length + r.removedNodes.length;
    }).observe(el, { childList: true, attributes: true, attributeFilter: ["hidden"] });
  });
  await act();
  return target.page().evaluate(() => {
    const w = window as unknown as { __toggles: number; __swaps: number };
    return { toggles: w.__toggles, swaps: w.__swaps };
  });
}

/** Hold Enter for `ms`: a keydown every 33 ms, like a fast keyboard auto-repeat (repeat=true after the first). */
async function holdEnter(page: Page, ms = 2000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    await page.keyboard.down("Enter");
    await page.waitForTimeout(33);
  }
  await page.keyboard.up("Enter");
}

const TOGGLES: { name: string; button: (p: Page) => Locator; area: (p: Page) => Locator }[] = [
  {
    name: "Heatmap overlay",
    button: (p) => p.getByRole("button", { name: "Heatmap overlay" }),
    area: (p) => p.locator("figure img").locator(".."),
  },
  {
    name: "Show data table",
    button: (p) => p.getByRole("button", { name: "Show data table" }),
    area: (p) => p.locator("#timeline-table"),
  },
  {
    name: "Outline culprit 1",
    button: (p) => p.getByRole("button", { name: /Outline culprit 1 / }),
    area: (p) => p.locator("figure img").locator(".."),
  },
];

for (const toggle of TOGGLES) {
  test(`SAFETY: holding Enter on "${toggle.name}" changes it at most 3 times per second`, async ({ page }) => {
    await mockApi(page);
    await page.goto(`/report/?id=${IDS.FAIL}`);
    const button = toggle.button(page);
    await button.focus();
    const { toggles, swaps } = await countChanges(button, toggle.area(page), () => holdEnter(page, 2000));
    expect(toggles).toBeGreaterThanOrEqual(1); // the first press still works
    expect(toggles).toBeLessThanOrEqual(6); // 2 s at no more than 3 per second
    expect(swaps).toBeLessThanOrEqual(6); // and what is on screen follows the same limit
  });

  test(`SAFETY: clicking "${toggle.name}" as fast as possible is throttled`, async ({ page }) => {
    await mockApi(page);
    await page.goto(`/report/?id=${IDS.FAIL}`);
    const button = toggle.button(page);
    const handle = await button.elementHandle();
    const t0 = Date.now();
    const changes = await countChanges(button, toggle.area(page), async () => {
      // Twenty clicks, dispatched straight to the element so the label change
      // ("Show" -> "Hide") cannot make a locator miss.
      for (let i = 0; i < 20; i++) await handle?.click({ delay: 0 });
    });
    const elapsed = Date.now() - t0;
    const { toggles, swaps } = changes;
    expect(toggles).toBeGreaterThanOrEqual(1);
    // One change per 350 ms at most (+1 for the first click).
    expect(toggles).toBeLessThanOrEqual(1 + Math.floor(elapsed / 350));
    expect(swaps).toBeLessThanOrEqual(1 + Math.floor(elapsed / 350));
  });
}

test("SAFETY: alternating clicks on two culprit rows share one throttle", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const rows = [page.getByRole("row", { name: /#loader/ }), page.getByRole("row", { name: /#live-badge/ })];
  const outlineText = () =>
    page.evaluate(() => document.querySelector("[data-testid='culprit-outline']")?.textContent ?? "");
  const labels: string[] = [];
  const t0 = Date.now();
  for (let i = 0; i < 12; i++) {
    await rows[i % 2].click({ delay: 0 });
    labels.push(await outlineText());
  }
  const elapsed = Date.now() - t0;
  const changes = labels.filter((l, i) => i > 0 && l !== labels[i - 1]).length + 1;
  expect(changes).toBeLessThanOrEqual(1 + Math.floor(elapsed / 350));
});
