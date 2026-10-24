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
  // Wait for every <img> to finish loading so frames appear in the shot.
  await page.waitForFunction(() => Array.from(document.images).every((img) => img.complete));
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
        await page.evaluate(() => Promise.all(Array.from(document.images).map((img) => img.decode().catch(() => null))));
        await settle(page);
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
