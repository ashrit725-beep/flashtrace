// axe-core on every screen and every state (loaded, empty, error, loading),
// the states reached by an action (verify running/failed/finished, a failed
// submit, cancel, delete or ablation, lost contact, a failed reload), the
// open frame dialog, a selected culprit and the phone layouts, in light AND
// dark, and once more under reduced motion. Pass condition: 0 violations for
// WCAG 2.0/2.1/2.2 A and AA rules.
//
// Contrast is measured on settled frames (web/MOTION.md section 6): before
// every run the page is scrolled through in viewport steps (so in-view
// entrances play; axe skips opacity-0 content), every Web Animation (CSS
// transitions) is awaited, and then 700 ms pass, because Motion's fades,
// springs, layout animations and motion values run on requestAnimationFrame
// (lib/motion.ts PAGE_CLOCK) and every choreography ends within 600 ms.

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { IDS, failApi, mockApi, type MockMode } from "./mock-api";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function settle(page: Page) {
  const { height, step, start } = await page.evaluate(() => ({
    height: document.documentElement.scrollHeight,
    step: window.innerHeight,
    start: window.scrollY,
  }));
  for (let y = 0; y < height; y += step) {
    await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y);
    await page.waitForTimeout(30);
  }
  await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), start);
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => null))));
  await page.waitForTimeout(700);
}

async function expectNoAxeViolations(page: Page) {
  await settle(page);
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const summary = results.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help} -> ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`,
  );
  expect(summary).toEqual([]);
}

interface Case {
  name: string;
  path: string;
  mode?: MockMode;
  /** Runs after mockApi and before the page loads, e.g. to make one endpoint fail. */
  setup?: (page: Page) => Promise<void>;
  /** Waits until the state under test is on screen (and drives the page there if needed). */
  ready: (page: Page) => Promise<void>;
}

const heading = (page: Page, name: string | RegExp) => expect(page.getByRole("heading", { name })).toBeVisible();

const fix1 = (page: Page) =>
  page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Fix 1:/ }) });

const phone = (page: Page) => page.setViewportSize({ width: 390, height: 844 });

const CASES: Case[] = [
  // ---- New audit
  { name: "new audit: loaded", path: "/", ready: (p) => expect(p.getByRole("button", { name: "Run audit" })).toBeVisible() },
  { name: "new audit: loading", path: "/", mode: "loading", ready: (p) => expect(p.getByRole("status")).toContainText("Checking") },
  {
    name: "new audit: error (server down)",
    path: "/",
    mode: "error",
    ready: (p) => expect(p.locator("code", { hasText: "flashtrace serve" })).toBeVisible(),
  },
  {
    name: "new audit: empty form submitted (inline errors)",
    path: "/",
    ready: async (p) => {
      await p.getByRole("button", { name: "Run audit" }).click();
      await expect(p.locator("#target-error")).toBeVisible();
    },
  },
  {
    name: "new audit: submit failed (409)",
    path: "/",
    setup: (p) => failApi(p, "POST", /^\/jobs$/, 409, { detail: "Another audit is starting." }),
    ready: async (p) => {
      await p.getByLabel("Page URL or local file path").fill("https://example.com/");
      await p.getByRole("button", { name: "Run audit" }).click();
      await heading(p, "The audit could not be started");
    },
  },
  {
    name: "new audit: custom viewport with invalid sizes",
    path: "/",
    ready: async (p) => {
      await p.getByLabel("Custom size").check();
      await p.getByLabel("Width (px)").fill("50");
      await p.getByLabel("Height (px)").fill("9000");
      await p.getByRole("button", { name: "Run audit" }).click();
      await expect(p.locator("#width-error")).toBeVisible();
    },
  },

  // ---- Running
  { name: "run: loaded (capturing)", path: `/run/?id=${IDS.RUNNING}`, ready: (p) => heading(p, /Capturing frames 312\/600/) },
  { name: "run: loading", path: `/run/?id=${IDS.RUNNING}`, mode: "loading", ready: (p) => expect(p.getByRole("status").first()).toContainText("Loading") },
  { name: "run: error (server 500)", path: `/run/?id=${IDS.RUNNING}`, mode: "error", ready: (p) => expect(p.getByRole("button", { name: "Retry" })).toBeVisible() },
  { name: "run: job failed", path: `/run/?id=${IDS.ERROR}`, ready: (p) => heading(p, "The audit stopped with an error") },
  { name: "run: empty (no id)", path: "/run/", ready: (p) => heading(p, "No audit selected") },
  { name: "run: unknown id", path: "/run/?id=nope", ready: (p) => heading(p, "No audit with this ID") },
  {
    name: "run: lost contact with the server (error over stale data)",
    path: `/run/?id=${IDS.RUNNING}`,
    setup: (p) => failApi(p, "GET", new RegExp(`^/jobs/${IDS.RUNNING}$`), 500, { skip: 1 }),
    ready: (p) => heading(p, "Lost contact with the server"),
  },
  {
    name: "run: audit cancelled",
    path: `/run/?id=${IDS.RUNNING}`,
    ready: async (p) => {
      p.once("dialog", (d) => d.accept());
      await p.getByRole("button", { name: "Cancel audit" }).click();
      await heading(p, "Audit cancelled");
    },
  },
  {
    name: "run: cancel failed",
    path: `/run/?id=${IDS.RUNNING}`,
    setup: (p) => failApi(p, "DELETE", /^\/jobs\/[^/]+$/, 500),
    ready: async (p) => {
      p.once("dialog", (d) => d.accept());
      await p.getByRole("button", { name: "Cancel audit" }).click();
      await heading(p, "Could not cancel the audit");
    },
  },
  {
    name: "run: retry of a failed job failed",
    path: `/run/?id=${IDS.ERROR}`,
    setup: (p) => failApi(p, "POST", /^\/jobs$/, 409, { detail: "The queue is full." }),
    ready: async (p) => {
      await p.getByRole("button", { name: "Retry" }).click();
      await expect(p.getByText("Retry failed:")).toBeVisible();
    },
  },

  // ---- Report
  { name: "report: FAIL", path: `/report/?id=${IDS.FAIL}`, ready: (p) => heading(p, /FAIL/) },
  { name: "report: PASS", path: `/report/?id=${IDS.PASS}`, ready: (p) => heading(p, /PASS/) },
  { name: "report: INCONCLUSIVE", path: `/report/?id=${IDS.INCONCLUSIVE}`, ready: (p) => heading(p, /INCONCLUSIVE/) },
  { name: "report: loading", path: `/report/?id=${IDS.FAIL}`, mode: "loading", ready: (p) => expect(p.getByRole("status").first()).toContainText("Loading") },
  { name: "report: error (server 500)", path: `/report/?id=${IDS.FAIL}`, mode: "error", ready: (p) => heading(p, "Could not load the report") },
  { name: "report: empty (no id)", path: "/report/", ready: (p) => heading(p, "No report selected") },
  { name: "report: not finished (409)", path: `/report/?id=${IDS.RUNNING}`, ready: (p) => heading(p, "This audit has not finished yet") },
  { name: "report: errored job (404)", path: `/report/?id=${IDS.ERROR}`, ready: (p) => heading(p, "No report for this audit") },
  {
    name: "report: verify in progress",
    path: `/report/?id=${IDS.FAIL}`,
    setup: (p) => failApi(p, "POST", /\/fixes\/\d+\/verify$/, 0, { hang: true }),
    ready: async (p) => {
      await fix1(p).getByRole("button", { name: "Verify this fix" }).click();
      await expect(fix1(p).getByRole("status")).toHaveText(/^Re-capturing the page/);
    },
  },
  {
    name: "report: verify failed",
    path: `/report/?id=${IDS.FAIL}`,
    setup: (p) => failApi(p, "POST", /\/fixes\/\d+\/verify$/, 409, { detail: "The job is busy." }),
    ready: async (p) => {
      await fix1(p).getByRole("button", { name: "Verify this fix" }).click();
      await expect(fix1(p).getByRole("status")).toHaveText(/^Verification failed/);
    },
  },
  {
    name: "report: verify finished and announced",
    path: `/report/?id=${IDS.FAIL}`,
    ready: async (p) => {
      await fix1(p).getByRole("button", { name: "Verify this fix" }).click();
      await expect(fix1(p).getByRole("status")).toHaveText(/^Verification finished: verdict after the fix is PASS/, {
        timeout: 5000,
      });
    },
  },
  {
    name: "report: could not reload the report",
    path: `/report/?id=${IDS.FAIL}`,
    setup: (p) => failApi(p, "GET", new RegExp(`^/jobs/${IDS.FAIL}/report$`), 500, { skip: 1 }),
    ready: async (p) => {
      await fix1(p).getByRole("button", { name: "Verify this fix" }).click();
      await expect(p.getByRole("heading", { name: "Could not reload the report" })).toBeVisible({ timeout: 5000 });
    },
  },
  {
    name: "report: ablation failed",
    path: `/report/?id=${IDS.FAIL}`,
    setup: (p) => failApi(p, "POST", /\/ablate$/, 409, { detail: "The job is busy." }),
    ready: async (p) => {
      await p.getByRole("button", { name: "Re-run attribution with ablation" }).click();
      await heading(p, "Could not start ablation");
    },
  },
  {
    name: "report: FAIL at 390 px with a culprit selected",
    path: `/report/?id=${IDS.FAIL}`,
    setup: phone,
    ready: async (p) => {
      await p.getByRole("button", { name: /Outline culprit 1/ }).click();
      await expect(p.getByTestId("culprit-outline")).toBeVisible();
    },
  },

  // ---- History
  { name: "history: loaded", path: "/history/", ready: (p) => expect(p.getByRole("table")).toBeVisible() },
  {
    name: "history: loaded at 390 px (cards)",
    path: "/history/",
    setup: phone,
    ready: (p) => expect(p.getByRole("list", { name: "Past audits, newest first" })).toBeVisible(),
  },
  {
    name: "history: delete failed",
    path: "/history/",
    setup: (p) => failApi(p, "DELETE", /^\/jobs\/[^/]+$/, 500, { detail: "Disk is read-only." }),
    ready: async (p) => {
      p.once("dialog", (d) => d.accept());
      await p.getByRole("button", { name: /^Delete audit of / }).first().click();
      await heading(p, "Could not delete the audit");
    },
  },
  { name: "history: empty", path: "/history/", mode: "empty", ready: (p) => heading(p, "No audits yet") },
  { name: "history: error", path: "/history/", mode: "error", ready: (p) => heading(p, "Could not load past audits") },
  { name: "history: loading", path: "/history/", mode: "loading", ready: (p) => expect(p.getByRole("status").last()).toContainText("Loading") },
];

const PASSES = [
  { name: "light scheme", colorScheme: "light", reducedMotion: "no-preference" },
  { name: "dark scheme", colorScheme: "dark", reducedMotion: "no-preference" },
  { name: "light scheme, reduced motion", colorScheme: "light", reducedMotion: "reduce" },
] as const;

for (const pass of PASSES) {
  test.describe(`axe, ${pass.name}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ colorScheme: pass.colorScheme, reducedMotion: pass.reducedMotion });
    });

    for (const c of CASES) {
      test(c.name, async ({ page }) => {
        await mockApi(page, { mode: c.mode });
        await c.setup?.(page);
        await page.goto(c.path);
        await c.ready(page);
        await expectNoAxeViolations(page);
      });
    }

    test("report: frame dialog open", async ({ page }) => {
      await mockApi(page);
      await page.goto(`/report/?id=${IDS.FAIL}`);
      await page.getByRole("button", { name: /^Enlarge: .*frame 14 ·/ }).click();
      await expect(page.getByRole("dialog", { name: "Frame viewer" })).toBeVisible();
      await expect(page.getByRole("img", { name: /Captured frame 14 of 600/ })).toBeVisible();
      await expectNoAxeViolations(page);
    });

    test("report: culprit selected", async ({ page }) => {
      await mockApi(page);
      await page.goto(`/report/?id=${IDS.FAIL}`);
      const button = page.getByRole("button", { name: /Outline culprit 1/ });
      await button.click();
      await expect(button).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByTestId("culprit-outline")).toBeVisible();
      await expectNoAxeViolations(page);
    });

    test("report: data table shown", async ({ page }) => {
      await mockApi(page);
      await page.goto(`/report/?id=${IDS.FAIL}`);
      await page.getByRole("button", { name: "Show data table" }).click();
      await expect(page.getByRole("table", { name: /Flashes counted in each whole second/ })).toBeVisible();
      await expectNoAxeViolations(page);
    });
  });
}
