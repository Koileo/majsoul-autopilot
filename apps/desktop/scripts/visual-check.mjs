import { spawn } from "node:child_process";
import { chromium } from "playwright";

const port = 1420;
const url = `http://127.0.0.1:${port}/?demo=running`;
const server = spawn(
  process.platform === "win32" ? "npm.cmd" : "npm",
  ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(port)],
  { stdio: ["ignore", "pipe", "pipe"] },
);

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  let ready = false;
  for (let i = 0; i < 80; i += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {
      await wait(250);
    }
  }
  if (!ready) {
    throw new Error("Vite dev server did not become ready");
  }

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector(".gameTable");
  await page.waitForSelector(".tableArea");
  await page.waitForSelector(".handTiles .tile");
  await page.waitForSelector('[data-testid="language-switch"]');
  await page.getByRole("button", { name: "日本語" }).click();
  await page.waitForTimeout(500);

  const metrics = await page.evaluate(() => {
    const table = document.querySelector(".gameTable")?.getBoundingClientRect();
    const tableArea = document.querySelector(".tableArea")?.getBoundingClientRect();
    const tableTiles = Array.from(document.querySelectorAll(".tableArea .tile")).map((node) => {
      const rect = node.getBoundingClientRect();
      return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, title: node.getAttribute("title") };
    });
    const handTitles = Array.from(document.querySelectorAll(".handTiles .tile")).map((node) =>
      node.getAttribute("title"),
    );
    const tsumoTitle = document.querySelector(".tsumoContainer .tile")?.getAttribute("title") ?? null;
    const hand = document.querySelector(".handTiles")?.getBoundingClientRect();
    const tsumo = document.querySelector(".tsumoContainer")?.getBoundingClientRect();
    return {
      overflowX: document.documentElement.scrollWidth > window.innerWidth,
      overflowY: document.documentElement.scrollHeight > window.innerHeight,
      table: table ? { width: table.width, height: table.height, x: table.x, y: table.y } : null,
      tableArea: tableArea
        ? { width: tableArea.width, height: tableArea.height, x: tableArea.x, y: tableArea.y, right: tableArea.right, bottom: tableArea.bottom }
        : null,
      outsideTableTiles: tableArea
        ? tableTiles.filter(
            (tile) => tile.x < tableArea.x || tile.right > tableArea.right || tile.y < tableArea.y || tile.bottom > tableArea.bottom,
          )
        : [],
      handTitles,
      tsumoTitle,
      tsumoGap: hand && tsumo ? tsumo.x - hand.right : null,
      bodyText: document.body.innerText,
    };
  });

  if (metrics.overflowX || metrics.overflowY) {
    throw new Error(`layout overflow: ${JSON.stringify(metrics)}`);
  }
  if (!metrics.table || metrics.table.width < 640 || metrics.table.height < 520) {
    throw new Error(`table is undersized: ${JSON.stringify(metrics.table)}`);
  }
  if (!metrics.tableArea || metrics.tableArea.width < 440 || metrics.tableArea.height < 440) {
    throw new Error(`table area is undersized: ${JSON.stringify(metrics.tableArea)}`);
  }
  if (Math.abs(metrics.tableArea.width - metrics.tableArea.height) > 8) {
    throw new Error(`table area is not square: ${JSON.stringify(metrics.tableArea)}`);
  }
  if (metrics.outsideTableTiles.length > 0) {
    throw new Error(`table tiles overflowed: ${JSON.stringify(metrics.outsideTableTiles.slice(0, 5))}`);
  }
  const expectedHand = ["6m", "6m", "7m", "8m", "7p", "8p", "9p", "2s", "3s", "4s", "E", "S", "P"];
  if (metrics.handTitles.join("|") !== expectedHand.join("|")) {
    throw new Error(`hand sorting changed: ${JSON.stringify(metrics.handTitles)}`);
  }
  if (metrics.tsumoTitle !== "3s" || metrics.tsumoGap < 12) {
    throw new Error(`tsumo tile was not separated: ${JSON.stringify({ title: metrics.tsumoTitle, gap: metrics.tsumoGap })}`);
  }
  if (!metrics.bodyText.includes("Majsoul Autopilot")) {
    throw new Error("main app text did not render");
  }
  if (!metrics.bodyText.includes("自動対局")) {
    throw new Error("Japanese labels did not render after language switch");
  }
  if (consoleErrors.length > 0) {
    throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
  }

  await page.setViewportSize({ width: 1180, height: 760 });
  await page.waitForTimeout(400);
  const compactMetrics = await page.evaluate(() => {
    const table = document.querySelector(".gameTable")?.getBoundingClientRect();
    return {
      overflowX: document.documentElement.scrollWidth > window.innerWidth,
      overflowY: document.documentElement.scrollHeight > window.innerHeight,
      table: table ? { width: table.width, height: table.height, x: table.x, y: table.y } : null,
    };
  });
  if (compactMetrics.overflowX || compactMetrics.overflowY) {
    throw new Error(`compact layout overflow: ${JSON.stringify(compactMetrics)}`);
  }
  if (
    !compactMetrics.table ||
    compactMetrics.table.width < 520 ||
    compactMetrics.table.height < 390 ||
    compactMetrics.table.x < 0 ||
    compactMetrics.table.y < 0
  ) {
    throw new Error(`compact table is not scaled into view: ${JSON.stringify(compactMetrics.table)}`);
  }

  await page.setViewportSize({ width: 2560, height: 1440 });
  await page.waitForTimeout(400);
  const fullscreenMetrics = await page.evaluate(() => {
    const shell = document.querySelector(".appShell")?.getBoundingClientRect();
    return shell ? { width: shell.width, height: shell.height, x: shell.x, y: shell.y } : null;
  });
  if (!fullscreenMetrics || fullscreenMetrics.height < 1360 || fullscreenMetrics.y > 30) {
    throw new Error(`fullscreen layout did not scale up: ${JSON.stringify(fullscreenMetrics)}`);
  }

  await browser.close();
  console.log("visual check passed");
} finally {
  server.kill("SIGTERM");
}
