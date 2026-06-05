import { spawn } from "node:child_process";
import { chromium } from "playwright";

const port = 1426;
const url = `http://127.0.0.1:${port}`;
const server = spawn(
  process.platform === "win32" ? "npm.cmd" : "npm",
  ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(port)],
  { cwd: new URL("..", import.meta.url), stdio: ["ignore", "pipe", "pipe"] },
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
  if (!ready) throw new Error("Vite dev server did not become ready");

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 920 } });
  await page.addInitScript(() => {
    const callbacks = {};
    let nextCallbackId = 1;
    const settings = {
      model_path: "models/mortal-298k",
      ui_language: "zh",
      autoplay_account: { username: "test@example.com", password: "password" },
      autoplay: {
        room_policy: { type: "manual" },
        manual_room: "gold",
        manual_mode: "four_player_south",
        action_interval_ms: { min: 800, max: 1600 },
        max_games: null,
      },
    };
    const table = {
      seat: 3,
      bakaze: "S",
      kyoku: 1,
      honba: 0,
      kyotaku: 0,
      oya: 0,
      dora_markers: ["1p"],
      scores: [25000, 25000, 25000, 25000],
      players: [
        { seat: 0, points: 25000, hand: [], hand_count: 13, discards: [], melds: [], riichi: false, is_self: false },
        { seat: 1, points: 25000, hand: [], hand_count: 13, discards: [], melds: [], riichi: false, is_self: false },
        { seat: 2, points: 25000, hand: [], hand_count: 13, discards: [], melds: [], riichi: false, is_self: false },
        {
          seat: 3,
          points: 25000,
          hand: ["1m", "2m", "3m", "4m", "5s", "6s", "7s"],
          hand_count: 10,
          discards: [],
          melds: [
            { kind: "pon", called: "F", consumed: ["F", "F"], target: 2 },
          ],
          riichi: false,
          is_self: true,
        },
      ],
      last_event: {
        type: "hule",
        actor: 3,
        target: 3,
        pai: "1m",
        zimo: true,
        title: "1番",
        count: 2,
        fu: 40,
        fans: [],
        point_sum: 10700,
        hand: ["1m", "2m", "3m", "4m", "5s", "6s", "7s"],
      },
    };
    const coreEvents = [
      {
        seq: 1,
        event: {
          type: "account_snapshot",
          account: {
            refreshing: false,
            username: "test@example.com",
            account_id: 123456,
            nickname: "HuleTester",
            level_id: 10301,
            level_score: 542,
            rank_tier: 3,
            target_mode: "four_player_south",
            target_room: "gold",
          },
        },
      },
      { seq: 2, event: { type: "table_snapshot", table } },
    ];
    window.__TAURI_INTERNALS__ = {
      callbacks,
      transformCallback(callback) {
        const id = nextCallbackId;
        nextCallbackId += 1;
        callbacks[id] = callback;
        return id;
      },
      unregisterCallback(id) {
        delete callbacks[id];
      },
      runCallback(id, payload) {
        callbacks[id]?.(payload);
      },
      async invoke(command, args = {}) {
        if (command === "load_settings") return settings;
        if (command === "save_settings") return null;
        if (command === "get_runtime_snapshot") {
          return {
            running: true,
            status: "in_game",
            last_error: null,
            settings_path: "settings.json",
            runtime_log_path: "logs/runtime/gui_autoplay.log",
          };
        }
        if (command === "get_core_event_batch") {
          const after = args.after ?? 0;
          const events = coreEvents.filter((record) => record.seq > after);
          return { cursor: events.at(-1)?.seq ?? after, events };
        }
        if (command === "plugin:event|listen") return 1;
        if (command === "plugin:event|unlisten") return null;
        return null;
      },
      convertFileSrc(path) {
        return path;
      },
    };
    window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
  });

  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector(".roundResultWin");
  const text = await page.locator(".roundResultWin").innerText();
  if (text.includes("2番 · 40符 · 10700点") || text.includes("2番・40符・10700点")) {
    throw new Error(`hule summary still uses settlement point_sum as base score: ${text}`);
  }
  if (!text.includes("700点/1300点")) {
    throw new Error(`hule summary did not show computed 2han40fu child tsumo score: ${text}`);
  }
  if (text.includes("番种")) {
    throw new Error(`hule banner should not show yaku details: ${text}`);
  }
  await page.screenshot({ path: "/tmp/majsoul-autopilot-hule-display.png", fullPage: true });
  await browser.close();
  console.log("hule display check passed");
} finally {
  server.kill("SIGTERM");
}
