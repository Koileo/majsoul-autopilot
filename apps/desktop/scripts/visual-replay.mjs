import { mkdirSync } from "node:fs";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

const port = 1425;
const url = `http://127.0.0.1:${port}`;
const screenshotDir = "/tmp/majsoul-autopilot-replay";
mkdirSync(screenshotDir, { recursive: true });

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const server = spawn(
  process.platform === "win32" ? "npm.cmd" : "npm",
  ["run", "dev", "--", "--host", "127.0.0.1", "--port", String(port)],
  { stdio: ["ignore", "pipe", "pipe"] },
);

const settings = {
  model_path: "models/mortal-298k",
  ui_language: "zh",
  autoplay_account: { username: "370501542@qq.com", password: "password" },
  autoplay: {
    room_policy: { type: "auto_highest" },
    manual_room: "gold",
    manual_mode: "four_player_south",
    action_interval_ms: { min: 800, max: 1600 },
    max_games: null,
  },
};

const account = {
  refreshing: false,
  username: "370501542@qq.com",
  account_id: 23744444,
  nickname: "SsssssssY",
  level_id: 10301,
  level_score: 683,
  rank_tier: 3,
  target_mode: "four_player_south",
  target_room: "gold",
};

function discards(tiles, riichiIndex = -1) {
  return tiles.map((tile, index) => ({ tile, tsumogiri: index % 4 === 1, riichi: index === riichiIndex }));
}

function p(seat, points, hand, river, melds = [], riichi = false, isSelf = false) {
  return {
    seat,
    points,
    hand: isSelf ? hand : [],
    hand_count: isSelf ? hand.length : hand,
    discards: river,
    melds,
    riichi,
    is_self: isSelf,
  };
}

function table({ seat = 3, bakaze = "E", kyoku = 1, honba = 0, kyotaku = 0, dora = ["6p"], players, last_event = null }) {
  return {
    seat,
    bakaze,
    kyoku,
    honba,
    kyotaku,
    oya: 0,
    dora_markers: dora,
    scores: players.map((player) => player.points),
    players,
    last_event,
  };
}

function model(action, confidence = 0.5) {
  return {
    action_index: 1,
    action_label: action,
    confidence,
    is_greedy: true,
    candidates: [
      { action_index: 1, action_label: action, q_value: 1.2, confidence, legal: true },
      { action_index: 2, action_label: "none", q_value: -0.4, confidence: 0.12, legal: true },
      { action_index: 3, action_label: "discard E", q_value: -0.8, confidence: 0.06, legal: true },
    ],
  };
}

const stages = [
  {
    id: "01-login",
    status: "logging_in",
    events: [
      { type: "runtime_status", status: "logging_in" },
      { type: "account_snapshot", account: { ...account, refreshing: true, nickname: null, account_id: null, level_id: null, level_score: null } },
      { type: "log", level: "info", message: "login begin: username=370501542@qq.com" },
    ],
    expect: ["登录中", "刷新中"],
  },
  {
    id: "02-start-kyoku",
    status: "in_game",
    events: [
      { type: "runtime_status", status: "in_game" },
      { type: "account_snapshot", account },
      {
        type: "table_snapshot",
        table: table({
          players: [
            p(0, 25000, 13, []),
            p(1, 25000, 13, []),
            p(2, 25000, 13, []),
            p(3, 25000, ["1m", "2m", "3m", "5p", "6p", "7p", "2s", "3s", "4s", "E", "S", "P", "F", "C"], [], [], false, true),
          ],
        }),
      },
      { type: "model_decision", decision: model("discard E", 0.54) },
      { type: "game_event", event: { type: "start_kyoku", bakaze: "E", kyoku: 1, honba: 0, kyotaku: 0 } },
    ],
    expect: ["东1", "打出 东", "SsssssssY"],
  },
  {
    id: "03-riichi",
    status: "in_game",
    events: [
      {
        type: "table_snapshot",
        table: table({
          kyoku: 2,
          kyotaku: 1,
          dora: ["3s"],
          players: [
            p(0, 24000, 13, discards(["5p", "E", "9s", "1m"], 3), [], true),
            p(1, 25300, 13, discards(["8p", "2m", "7s"])),
            p(2, 25100, 13, discards(["4m", "9p", "2s", "W"])),
            p(3, 25600, ["2m", "3m", "4m", "5m", "6m", "7p", "8p", "9p", "2s", "3s", "4s", "P", "F", "C"], discards(["1p", "N", "E"]), [], false, true),
          ],
          last_event: { type: "reach", actor: 0 },
        }),
      },
      { type: "model_decision", decision: model("reach 4m", 0.74) },
      { type: "game_event", event: { type: "reach", actor: 0 } },
    ],
    expect: ["东2", "立直", "立直 4万"],
  },
  {
    id: "04-meld-pressure",
    status: "in_game",
    events: [
      {
        type: "table_snapshot",
        table: table({
          bakaze: "S",
          kyoku: 3,
          honba: 2,
          kyotaku: 2,
          dora: ["1m", "5pr"],
          players: [
            p(0, 16000, 7, discards(["5p", "E", "9s", "1m", "3p", "7p", "W", "2m", "4m"], 6), [
              { kind: "pon", target: 2, called_tile: "P", consumed: ["P", "P"] },
            ], true),
            p(1, 41200, 5, discards(["8p", "2m", "7s", "4s", "9m", "1p", "2p", "3p", "4p"], 5), [
              { kind: "chi", target: 2, called_tile: "6s", consumed: ["4s", "5s"] },
              { kind: "pon", target: 0, called_tile: "F", consumed: ["F", "F"] },
            ]),
            p(2, 11200, 6, discards(["4m", "9p", "2s", "W", "6m", "7m", "8m", "9m"], 2), [
              { kind: "daiminkan", target: 1, called_tile: "7p", consumed: ["7p", "7p", "7p"] },
            ], true),
            p(3, 30600, ["1m", "2m", "3m", "4p", "5p", "6p", "2s", "3s"], discards(["1p", "N", "E", "9m", "8s", "7s", "6s", "5s"]), [
              { kind: "ankan", target: null, called_tile: null, consumed: ["9p", "9p", "9p", "9p"] },
              { kind: "pon", target: 0, called_tile: "C", consumed: ["C", "C"] },
            ], false, true),
          ],
        }),
      },
      { type: "model_decision", decision: model("hora", 0.92) },
      { type: "game_event", event: { type: "pon", actor: 3, target: 0, pai: "C", consumed: ["C", "C"] } },
    ],
    expect: ["南3", "和牌", "\"pon\""],
  },
  {
    id: "05-hule",
    status: "in_game",
    events: [
      {
        type: "table_snapshot",
        table: table({
          bakaze: "S",
          kyoku: 3,
          honba: 2,
          kyotaku: 2,
          dora: ["1m", "5pr"],
          players: [
            p(0, 16000, 7, discards(["5p", "E", "9s", "1m", "3p", "7p", "W", "2m", "4m"], 6), [], true),
            p(1, 41200, 5, discards(["8p", "2m", "7s", "4s", "9m", "1p", "2p", "3p", "4p"], 5), []),
            p(2, 11200, 6, discards(["4m", "9p", "2s", "W", "6m", "7m", "8m", "9m"], 2), [], true),
            p(3, 30600, ["1m", "2m", "3m", "4p", "5p", "6p", "2s", "3s"], discards(["1p", "N", "E", "9m", "8s", "7s", "6s", "5s"]), [
              { kind: "ankan", target: null, called_tile: null, consumed: ["9p", "9p", "9p", "9p"] },
              { kind: "pon", target: 0, called_tile: "C", consumed: ["C", "C"] },
            ], false, true),
          ],
          last_event: { type: "hule", actor: 3, target: null, pai: "C", zimo: true, title: "満貫", point_sum: 8000 },
        }),
      },
      { type: "game_event", event: { type: "hule", actor: 3, target: null, pai: "C", zimo: true, title: "満貫", point_sum: 8000 } },
      { type: "log", level: "info", message: "round replay ended with hule" },
    ],
    expect: ["自家 自摸", "8000点", "round replay ended with hule"],
  },
];

function installTauriMock(page) {
  return page.addInitScript(({ settings, stages }) => {
    const callbacks = {};
    let nextCallbackId = 1;
    let cursor = 0;
    let stageIndex = 0;
    let seq = 1;
    const records = [];
    const rebuildRecords = () => {
      records.length = 0;
      seq = 1;
      for (let i = 0; i <= stageIndex; i += 1) {
        for (const event of stages[i].events) {
          records.push({ seq: seq++, event });
        }
      }
    };
    rebuildRecords();
    window.__advanceReplay = (nextStage) => {
      stageIndex = nextStage;
      rebuildRecords();
    };
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
            status: stages[stageIndex].status,
            last_error: null,
            settings_path: "settings.json",
            runtime_log_path: "logs/runtime/gui_autoplay.log",
          };
        }
        if (command === "get_core_event_batch") {
          const after = args.after ?? cursor;
          const events = records.filter((record) => record.seq > after);
          cursor = events.at(-1)?.seq ?? after;
          return { cursor, events };
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
  }, { settings, stages });
}

function assertLayout(metrics, stageId) {
  const errors = [];
  if (metrics.overflowX || metrics.overflowY) errors.push("document overflow");
  if (metrics.tableArea && Math.abs(metrics.tableArea.width - metrics.tableArea.height) > 8) errors.push("table is not square");
  if (metrics.outsideTableTiles.length > 0) errors.push(`tiles outside table: ${JSON.stringify(metrics.outsideTableTiles.slice(0, 3))}`);
  if (metrics.riverCenterOverlaps.length > 0) errors.push(`river overlaps center: ${metrics.riverCenterOverlaps.join(",")}`);
  if (metrics.redArrowClassCount > 0) errors.push("called tile red arrow marker classes should not exist");
  if (metrics.sideMeldOverlaps.length > 0) errors.push(`side meld overlaps: ${metrics.sideMeldOverlaps.join(",")}`);
  if (errors.length > 0) {
    throw new Error(`${stageId}: ${errors.join("; ")}\n${JSON.stringify(metrics, null, 2)}`);
  }
}

async function readMetrics(page) {
  return page.evaluate(() => {
    const rect = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const item = el.getBoundingClientRect();
      return { left: item.left, top: item.top, right: item.right, bottom: item.bottom, width: item.width, height: item.height };
    };
    const tableRect = document.querySelector(".tableArea")?.getBoundingClientRect();
    const center = document.querySelector(".tableCenterAbsolute")?.getBoundingClientRect();
    const tableTiles = Array.from(document.querySelectorAll(".tableArea .tile")).map((node) => node.getBoundingClientRect());
    const riverSelectors = [".riverSelf", ".riverToimen", ".riverKamicha", ".riverShimocha"];
    return {
      tableArea: rect(".tableArea"),
      overflowX: document.documentElement.scrollWidth > window.innerWidth,
      overflowY: document.documentElement.scrollHeight > window.innerHeight,
      outsideTableTiles:
        tableRect == null
          ? []
          : tableTiles.filter((tile) => tile.left < tableRect.left || tile.right > tableRect.right || tile.top < tableRect.top || tile.bottom > tableRect.bottom),
      riverCenterOverlaps: center
        ? riverSelectors.filter((selector) =>
            Array.from(document.querySelectorAll(`${selector} .riverTileSlot`)).some((slot) => {
              const item = slot.getBoundingClientRect();
              return !(item.right < center.left || item.left > center.right || item.bottom < center.top || item.top > center.bottom);
            }),
          )
        : [],
      sideMeldOverlaps: (() => {
        const subjects = Array.from(
          document.querySelectorAll(".playerKamicha .opponentMelds .tile, .playerKamicha .opponentMelds .calledTileWrapper, .playerShimocha .opponentMelds .tile, .playerShimocha .opponentMelds .calledTileWrapper"),
        ).map((node, index) => ({ index, rect: node.getBoundingClientRect() }));
        const targets = Array.from(
          document.querySelectorAll(".riverTileSlot, .playerKamicha .opponentHandTiles .tile, .playerShimocha .opponentHandTiles .tile"),
        ).map((node, index) => ({ index, rect: node.getBoundingClientRect() }));
        const overlaps = [];
        for (const subject of subjects) {
          for (const target of targets) {
            const a = subject.rect;
            const b = target.rect;
            if (!(a.right <= b.left + 2 || b.right <= a.left + 2 || a.bottom <= b.top + 2 || b.bottom <= a.top + 2)) {
              overlaps.push(`${subject.index}-${target.index}`);
            }
          }
        }
        return overlaps.slice(0, 10);
      })(),
      redArrowClassCount: document.querySelectorAll(".tileCalled, [class*='tileCalledFrom']").length,
    };
  });
}

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
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
    await installTauriMock(page);
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForSelector(".appShell");

    for (let index = 0; index < stages.length; index += 1) {
      await page.evaluate((stage) => window.__advanceReplay(stage), index);
      await page.waitForTimeout(850);
      const body = await page.locator("body").innerText();
      for (const expected of stages[index].expect) {
        if (!body.includes(expected)) {
          throw new Error(`${stages[index].id}: missing ${expected}\n${body}`);
        }
      }
      const screenshot = `${screenshotDir}/${stages[index].id}.png`;
      await page.screenshot({ path: screenshot, fullPage: true });
      assertLayout(await readMetrics(page), stages[index].id);
    }
    await browser.close();
    console.log(JSON.stringify({ screenshotDir, stages: stages.map((stage) => `${screenshotDir}/${stage.id}.png`) }, null, 2));
  } finally {
    await browser.close().catch(() => {});
  }
} finally {
  server.kill("SIGTERM");
}
