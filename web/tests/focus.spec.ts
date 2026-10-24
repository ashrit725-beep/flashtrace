// Focus after actions, and Retry on every action error. When the control a
// keyboard user pressed disappears (or becomes disabled), the browser drops
// focus on <body> and the next Tab starts again at the skip link. These tests
// pin where focus goes instead.

import { expect, test, type Page } from "@playwright/test";
import { IDS, failApi, mockApi, readFixture, recordRequests, replaceJson } from "./mock-api";

const activeTag = (page: Page) => page.evaluate(() => document.activeElement?.tagName ?? "none");

test("Retry that fails again moves focus to the new error's heading", async ({ page }) => {
  await mockApi(page, { mode: "error" });
  await page.goto("/history/");
  const retry = page.getByRole("button", { name: "Retry" });
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Could not load past audits" })).toBeFocused();
  // and Tab goes on to its Retry, not back to the skip link
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Retry" })).toBeFocused();
});

test("an error on first load does not steal focus", async ({ page }) => {
  await mockApi(page, { mode: "error" });
  await page.goto("/history/");
  await expect(page.getByRole("heading", { name: "Could not load past audits" })).toBeVisible();
  expect(await activeTag(page)).toBe("BODY");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
});

test("a failed submit keeps focus on Run audit; its Retry sends the same values again", async ({ page }) => {
  await mockApi(page);
  await failApi(page, "POST", /^\/jobs$/, 409, { detail: "Another audit is starting; try again." });
  const requests = recordRequests(page);
  await page.goto("/");
  await page.getByLabel("Page URL or local file path").fill("https://example.com/");
  const run = page.getByRole("button", { name: "Run audit" });
  await run.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "The audit could not be started" })).toBeVisible();
  await expect(page.getByText("Another audit is starting; try again.")).toBeVisible();
  await expect(run).toBeFocused();
  await expect(run).not.toHaveAttribute("aria-disabled", "true");

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: "The audit could not be started" })).toBeFocused();
  const posts = requests.filter((c) => c.method === "POST" && c.path === "/jobs");
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toEqual(posts[0].body);
});

test("Cancel moves focus to the 'Audit cancelled' heading", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/run/?id=${IDS.RUNNING}`);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Cancel audit" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Audit cancelled" })).toBeFocused();
});

test("a failed cancel keeps focus on Cancel, and its Retry cancels without asking again", async ({ page }) => {
  await mockApi(page);
  await failApi(page, "DELETE", new RegExp(`^/jobs/${IDS.RUNNING}$`), 500);
  const requests = recordRequests(page);
  await page.goto(`/run/?id=${IDS.RUNNING}`);
  page.once("dialog", (d) => d.accept());
  const cancel = page.getByRole("button", { name: "Cancel audit" });
  await cancel.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Could not cancel the audit" })).toBeVisible();
  await expect(cancel).toBeFocused();
  // No confirm dialog this time: an unexpected one would fail the test (Playwright dismisses it and the DELETE never happens).
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: "Could not cancel the audit" })).toBeFocused();
  expect(requests.filter((c) => c.method === "DELETE")).toHaveLength(2);
});

test("deleting the last audit moves focus to 'No audits yet'", async ({ page }) => {
  await mockApi(page);
  const one = readFixture<Record<string, unknown>[]>("jobs.json").filter((j) => j.id === IDS.PASS);
  let list = one;
  await replaceJson(page, /^\/jobs$/, () => list);
  await page.route(
    (url) => url.pathname === `/jobs/${IDS.PASS}`,
    (route) => {
      if (route.request().method() !== "DELETE") return route.fallback();
      list = [];
      return route.fulfill({ status: 204, body: "" });
    },
  );
  await page.goto("/history/");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /^Delete audit of / }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "No audits yet" })).toBeFocused();
});

test("deleting the bottom row moves focus to the row above", async ({ page }) => {
  await mockApi(page);
  await page.goto("/history/");
  page.once("dialog", (d) => d.accept());
  const last = page.getByRole("button", { name: /^Delete audit of / }).last();
  await expect(last).toHaveAccessibleName(/gif-hero/); // oldest audit, bottom row
  await last.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("row")).toHaveCount(5);
  await expect(page.getByRole("button", { name: /^Delete audit of http:\/\/127.0.0.1:8765\/calm\// })).toBeFocused();
});

test("a failed delete keeps focus and offers Retry for the same audit", async ({ page }) => {
  await mockApi(page);
  await failApi(page, "DELETE", /^\/jobs\/[^/]+$/, 500, { detail: "Disk is read-only." });
  const requests = recordRequests(page);
  await page.goto("/history/");
  page.once("dialog", (d) => d.accept());
  const del = page.getByRole("button", { name: /Delete audit of https:\/\/example.test\/broken/ });
  await del.focus();
  await page.keyboard.press("Enter");
  const box = page.getByRole("alert").filter({ has: page.getByRole("heading", { name: "Could not delete the audit" }) });
  await expect(box).toContainText("Disk is read-only.");
  await expect(del).toBeFocused();
  await box.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: "Could not delete the audit" })).toBeFocused();
  const deletes = requests.filter((c) => c.method === "DELETE").map((c) => c.path);
  expect(deletes).toEqual([`/jobs/${IDS.ERROR}`, `/jobs/${IDS.ERROR}`]);
});

test("a failed ablation start keeps focus and offers Retry", async ({ page }) => {
  await mockApi(page);
  await failApi(page, "POST", /\/ablate$/, 409, { detail: "The job is busy." });
  const requests = recordRequests(page);
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const ablate = page.getByRole("button", { name: "Re-run attribution with ablation" });
  await ablate.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Could not start ablation" })).toBeVisible();
  await expect(ablate).toBeFocused();
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: "Could not start ablation" })).toBeFocused();
  expect(requests.filter((c) => c.path.endsWith("/ablate"))).toHaveLength(2);
});

test("a failed verify keeps focus on its button and says why", async ({ page }) => {
  await mockApi(page);
  await failApi(page, "POST", /\/fixes\/\d+\/verify$/, 409, { detail: "The job is busy." });
  await page.goto(`/report/?id=${IDS.FAIL}`);
  const fix1 = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: /^Fix 1:/ }) });
  const verify = fix1.getByRole("button", { name: "Verify this fix" });
  await verify.focus();
  await page.keyboard.press("Enter");
  await expect(fix1.getByRole("status")).toHaveText("Verification failed: The job is busy.");
  await expect(verify).toBeFocused();
});

test("every error state has a Retry button, 404s included", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/report/?id=${IDS.ERROR}`);
  await expect(page.getByRole("heading", { name: "No report for this audit" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await page.goto("/run/?id=nope");
  await expect(page.getByRole("heading", { name: "No audit with this ID" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  // A failed job's Retry reports its own failure inside the same box.
  await failApi(page, "POST", /^\/jobs$/, 409, { detail: "The queue is full." });
  await page.goto(`/run/?id=${IDS.ERROR}`);
  const retry = page.getByRole("button", { name: "Retry" });
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Retry failed:")).toBeVisible();
  await expect(page.getByText("The queue is full.")).toBeVisible();
  await expect(retry).toBeFocused();
});
