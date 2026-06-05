import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  Bot,
  CircleDot,
  Gauge,
  KeyRound,
  Languages,
  Play,
  RadioTower,
  Save,
  Shield,
  Square,
  TimerReset,
} from "lucide-react";
import { GameTable } from "./GameTable";
import { copy, languageNames } from "./i18n";
import { useAppStore } from "./store";
import type { CoreEventBatch, Language, ModeChoice, RoomChoice, RuntimeSnapshot, Settings } from "./types";

function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window;
}

const BASE_WIDTH = 1440;
const BASE_HEIGHT = 900;

function useViewportScale() {
  const [scale, setScale] = useState(() => {
    if (typeof window === "undefined") return 1;
    return measureScale();
  });

  useEffect(() => {
    const update = () => setScale(measureScale());
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return scale;
}

function measureScale() {
  const widthScale = window.innerWidth / BASE_WIDTH;
  const heightScale = window.innerHeight / BASE_HEIGHT;
  return Math.max(0.55, Math.min(widthScale, heightScale));
}

function normalizeSettings(settings: Settings): Settings {
  return {
    ...settings,
    ui_language: settings.ui_language ?? "zh",
    autoplay: {
      ...settings.autoplay,
      max_games: settings.autoplay.max_games ?? null,
    },
  };
}

export function App() {
  const {
    settings,
    setSettings,
    status,
    table,
    modelDecision,
    logs,
    events,
    account,
    stopScheduled,
    ingest,
  } = useAppStore();

  const language = settings.ui_language;
  const t = copy[language];
  const viewportScale = useViewportScale();
  const [settingsReady, setSettingsReady] = useState(() => !isTauriRuntime());
  const [launching, setLaunching] = useState(false);
  const [runtimeRunning, setRuntimeRunning] = useState(false);
  const [emergencyConfirmOpen, setEmergencyConfirmOpen] = useState(false);
  const lastSavedSettings = useRef(JSON.stringify(normalizeSettings(settings)));
  const lastSnapshotError = useRef<string | null>(null);
  const coreEventCursor = useRef(0);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    invoke<Settings>("load_settings")
      .then((loaded) => {
        const normalized = normalizeSettings(loaded);
        lastSavedSettings.current = JSON.stringify(normalized);
        setSettings(normalized);
        setSettingsReady(true);
      })
      .catch((error) => {
        ingest({ type: "log", level: "warn", message: `${copy.zh.readFailed}: ${error}` });
        setSettingsReady(true);
      });
  }, [ingest, setSettings]);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    let disposed = false;
    const syncCoreEvents = () => {
      invoke<CoreEventBatch>("get_core_event_batch", { after: coreEventCursor.current })
        .then((batch) => {
          if (disposed) return;
          for (const record of batch.events) {
            ingest(record.event);
          }
          coreEventCursor.current = batch.cursor;
        })
        .catch((error) => {
          if (!disposed) {
            ingest({ type: "log", level: "warn", message: `core event sync failed: ${String(error)}` });
          }
        });
    };
    syncCoreEvents();
    const timer = window.setInterval(syncCoreEvents, 700);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [ingest]);

  useEffect(() => {
    if (!isTauriRuntime()) {
      return;
    }
    let disposed = false;
    const syncRuntimeSnapshot = () => {
      invoke<RuntimeSnapshot>("get_runtime_snapshot")
        .then((snapshot) => {
          if (disposed) return;
          setRuntimeRunning(snapshot.running);
          if (snapshot.status !== status) {
            ingest({ type: "runtime_status", status: snapshot.status });
          }
          if (!snapshot.running && snapshot.last_error && snapshot.last_error !== lastSnapshotError.current) {
            lastSnapshotError.current = snapshot.last_error;
            ingest({ type: "runtime_error", message: snapshot.last_error });
          }
          if (snapshot.running || !snapshot.last_error) {
            lastSnapshotError.current = null;
          }
        })
        .catch((error) => {
          if (!disposed) {
            ingest({ type: "log", level: "warn", message: `runtime snapshot failed: ${String(error)}` });
          }
        });
    };
    syncRuntimeSnapshot();
    const timer = window.setInterval(syncRuntimeSnapshot, 1000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [ingest, status]);

  useEffect(() => {
    if (isTauriRuntime()) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.get("demo") !== "running") {
      return;
    }
    ingest({ type: "runtime_status", status: "in_game" });
  }, [ingest]);

  useEffect(() => {
    if (!settingsReady || !isTauriRuntime()) {
      return;
    }
    const normalized = normalizeSettings(settings);
    const raw = JSON.stringify(normalized);
    if (raw === lastSavedSettings.current) {
      return;
    }
    const timer = window.setTimeout(() => {
      invoke("save_settings", { settings: normalized })
        .then(() => {
          lastSavedSettings.current = raw;
        })
        .catch((error) => {
          ingest({ type: "log", level: "error", message: `${t.saveFailed}: ${String(error)}` });
        });
    }, 450);
    return () => window.clearTimeout(timer);
  }, [ingest, settings, settingsReady, t.saveFailed]);

  const canStart =
    !launching && !runtimeRunning && (status === "idle" || status === "stopped" || status === "error");
  const inGame = ["logging_in", "matching", "reconnecting", "in_game", "stopping_after_game"].includes(status);
  const stopAlreadyScheduled = stopScheduled || status === "stopping_after_game";
  const statusText = t.status[status] ?? status;
  const accountName = account?.nickname || account?.username || settings.autoplay_account.username || t.accountWaiting;
  const accountTarget =
    account?.target_room && account?.target_mode
      ? `${t.rooms[account.target_room]} ${t.modes[account.target_mode]}`
      : "-";

  async function saveSettings(nextSettings = settings) {
    const normalized = normalizeSettings(nextSettings);
    setSettings(normalized);
    if (!isTauriRuntime()) {
      ingest({ type: "log", level: "info", message: t.previewSave });
      return;
    }
    try {
      await invoke("save_settings", { settings: normalized });
      lastSavedSettings.current = JSON.stringify(normalized);
      ingest({ type: "log", level: "info", message: t.saveOk });
    } catch (error) {
      ingest({ type: "log", level: "error", message: `${t.saveFailed}: ${String(error)}` });
      throw error;
    }
  }

  async function start() {
    if (!canStart) {
      return;
    }
    const normalized = normalizeSettings(settings);
    setLaunching(true);
    ingest({
      type: "account_snapshot",
      account: {
        refreshing: true,
        username: normalized.autoplay_account.username,
        account_id: null,
        nickname: null,
        level_id: null,
        level_score: null,
        rank_tier: null,
        target_mode: null,
        target_room: null,
      },
    });
    try {
      await saveSettings(normalized);
      if (!isTauriRuntime()) return;
      await invoke("start_autoplay", { settings: normalized });
      setRuntimeRunning(true);
      ingest({ type: "runtime_status", status: "logging_in" });
    } catch (error) {
      if (String(error).includes("autoplay is already running")) {
        setRuntimeRunning(true);
        ingest({ type: "runtime_status", status: "logging_in" });
      }
      ingest({ type: "log", level: "error", message: `${t.startFailed}: ${String(error)}` });
    } finally {
      setLaunching(false);
    }
  }

  async function stopAfterCurrentGame() {
    if (!isTauriRuntime()) return;
    try {
      await invoke("stop_after_current_game");
      ingest({ type: "stop_scheduled", after_current_game: true });
      ingest({ type: "runtime_status", status: "stopping_after_game" });
    } catch (error) {
      ingest({ type: "log", level: "error", message: `${t.stopFailed}: ${String(error)}` });
    }
  }

  async function emergencyStop() {
    if (!isTauriRuntime()) return;
    try {
      await invoke("emergency_stop");
      setEmergencyConfirmOpen(false);
      setRuntimeRunning(false);
      ingest({ type: "runtime_status", status: "stopped" });
    } catch (error) {
      ingest({ type: "log", level: "error", message: `${t.emergencyFailed}: ${String(error)}` });
    }
  }

  function updateSettings(next: Settings) {
    setSettings(normalizeSettings(next));
  }

  function updateLanguage(nextLanguage: Language) {
    updateSettings({ ...settings, ui_language: nextLanguage });
  }

  return (
    <div className="viewportFrame">
      <div
        className="appShell"
        lang={language}
        style={{
          width: BASE_WIDTH,
          height: BASE_HEIGHT,
          transform: `translate(-50%, -50%) scale(${viewportScale})`,
        }}
      >
      <header className="commandBar">
        <div className="brandCluster">
          <div className="brandMark">雀</div>
          <div>
            <h1>{t.appTitle}</h1>
            <p>{t.subtitle}</p>
          </div>
        </div>

        <div className="languageSwitch" data-testid="language-switch" aria-label={t.settings}>
          <Languages size={16} />
          {(Object.keys(languageNames) as Language[]).map((lang) => (
            <button
              key={lang}
              className={language === lang ? "active" : ""}
              onClick={() => updateLanguage(lang)}
              type="button"
            >
              {languageNames[lang]}
            </button>
          ))}
        </div>

        <div className={`statusBeacon status-${status}`}>
          <CircleDot size={15} />
          <span>{statusText}</span>
        </div>

        <div className="commandActions">
          <button className="primary" disabled={!canStart} onClick={start} type="button">
            <Play size={16} /> {t.launch}
          </button>
          <button disabled={!inGame || stopAlreadyScheduled} onClick={stopAfterCurrentGame} type="button">
            <TimerReset size={16} /> {stopAlreadyScheduled ? t.stopScheduled : t.stopAfterGame}
          </button>
          <button
            className="danger"
            disabled={!inGame}
            onClick={() => setEmergencyConfirmOpen(true)}
            type="button"
          >
            <Square size={16} /> {t.emergencyStop}
          </button>
        </div>
      </header>

      <main className="cockpit">
        <aside className="controlDeck">
          <PanelTitle icon={<KeyRound size={17} />} label={t.account} />
          <Field label={t.username}>
            <input
              value={settings.autoplay_account.username}
              onChange={(event) =>
                updateSettings({
                  ...settings,
                  autoplay_account: {
                    ...settings.autoplay_account,
                    username: event.target.value,
                  },
                })
              }
            />
          </Field>
          <Field label={t.password}>
            <input
              type="password"
              value={settings.autoplay_account.password}
              onChange={(event) =>
                updateSettings({
                  ...settings,
                  autoplay_account: {
                    ...settings.autoplay_account,
                    password: event.target.value,
                  },
                })
              }
            />
          </Field>
          <PanelTitle icon={<Bot size={17} />} label={t.model} />
          <Field label={t.modelPath}>
            <input
              value={settings.model_path}
              onChange={(event) => updateSettings({ ...settings, model_path: event.target.value })}
            />
          </Field>

          <PanelTitle icon={<RadioTower size={17} />} label={t.match} />
          <label className="toggleRow">
            <input
              type="checkbox"
              checked={settings.autoplay.room_policy.type === "auto_highest"}
              onChange={(event) =>
                updateSettings({
                  ...settings,
                  autoplay: {
                    ...settings.autoplay,
                    room_policy: event.target.checked ? { type: "auto_highest" } : { type: "manual" },
                  },
                })
              }
            />
            <span>{t.autoHighest}</span>
          </label>
          <Field label={t.manualRoom}>
            <select
              value={settings.autoplay.manual_room}
              disabled={settings.autoplay.room_policy.type === "auto_highest"}
              onChange={(event) =>
                updateSettings({
                  ...settings,
                  autoplay: {
                    ...settings.autoplay,
                    manual_room: event.target.value as RoomChoice,
                  },
                })
              }
            >
              {Object.entries(t.rooms).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t.mode}>
            <select
              value={settings.autoplay.manual_mode}
              disabled={settings.autoplay.room_policy.type === "auto_highest"}
              onChange={(event) =>
                updateSettings({
                  ...settings,
                  autoplay: {
                    ...settings.autoplay,
                    manual_mode: event.target.value as ModeChoice,
                  },
                })
              }
            >
              {Object.entries(t.modes).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>

          <PanelTitle icon={<Gauge size={17} />} label={t.tempo} />
          <div className="fieldGrid">
            <Field label={t.minMs}>
              <input
                type="number"
                value={settings.autoplay.action_interval_ms.min}
                onChange={(event) =>
                  updateSettings({
                    ...settings,
                    autoplay: {
                      ...settings.autoplay,
                      action_interval_ms: {
                        ...settings.autoplay.action_interval_ms,
                        min: Number(event.target.value),
                      },
                    },
                  })
                }
              />
            </Field>
            <Field label={t.maxMs}>
              <input
                type="number"
                value={settings.autoplay.action_interval_ms.max}
                onChange={(event) =>
                  updateSettings({
                    ...settings,
                    autoplay: {
                      ...settings.autoplay,
                      action_interval_ms: {
                        ...settings.autoplay.action_interval_ms,
                        max: Number(event.target.value),
                      },
                    },
                  })
                }
              />
            </Field>
          </div>
          <Field label={t.maxGames}>
            <input
              type="number"
              placeholder={t.maxGamesPlaceholder}
              value={settings.autoplay.max_games ?? ""}
              onChange={(event) =>
                updateSettings({
                  ...settings,
                  autoplay: {
                    ...settings.autoplay,
                    max_games: event.target.value === "" ? null : Number(event.target.value),
                  },
                })
              }
            />
          </Field>

          <button className="saveButton" onClick={() => void saveSettings()} type="button">
            <Save size={16} /> {t.save}
          </button>
        </aside>

        <section className="arenaDeck">
          <GameTable
            table={table}
            labels={{
              self: t.self,
              noTable: t.noTable,
              riichi: t.riichi,
              tsumoWin: t.tsumoWin,
              ronWin: t.ronWin,
              dealIn: t.dealIn,
              winningHand: t.winningHand,
              exhaustiveDraw: t.exhaustiveDraw,
              abortiveDraw: t.abortiveDraw,
              winTile: t.winTile,
              points: t.points,
              han: t.han,
              fu: t.fu,
              allPlayersPay: t.allPlayersPay,
              winningMelds: t.winningMelds,
              formatTileName: (tile) => formatTileName(tile, language),
              topPlayer: t.topPlayer,
              leftPlayer: t.leftPlayer,
              rightPlayer: t.rightPlayer,
              dora: t.dora,
              honba: t.honba,
              deposit: t.deposit,
              roundEast: t.roundEast,
              roundSouth: t.roundSouth,
              roundWest: t.roundWest,
              roundNorth: t.roundNorth,
            }}
          />

          <div className="modelStrip">
            <div className="recommendation">
              <span>{t.modelDecision}</span>
              <strong>{modelDecision ? formatActionLabel(modelDecision.action_label, t, language) : "-"}</strong>
              <small>
                {modelDecision
                  ? `${Math.round(modelDecision.confidence * 100)}% ${t.relativeConfidence}`
                  : "-"}
              </small>
            </div>
            <div className="candidateTrack">
              {(modelDecision?.candidates ?? []).slice(0, 3).map((candidate) => (
                <div className="candidateRow" key={candidate.action_index}>
                  <span>{formatActionLabel(candidate.action_label, t, language)}</span>
                  <div className="confidenceBar">
                    <i style={{ width: `${Math.max(4, candidate.confidence * 100)}%` }} />
                  </div>
                  <code>{candidate.q_value.toFixed(2)}</code>
                </div>
              ))}
            </div>
          </div>
        </section>

        <aside className="telemetryRail">
          <div className={`phaseBanner status-${status}`} data-testid="phase-banner">
            <div>
              <span>{t.statusLabel}</span>
              <strong>{statusText}</strong>
            </div>
            <p>
              {account?.refreshing
                ? `${t.accountRefreshing}: ${accountName}`
                : account?.nickname
                  ? `${account.nickname} · ${accountTarget}`
                  : t.accountWaiting}
            </p>
          </div>

          <TelemetryBlock title={t.players} icon={<Shield size={16} />} className="playersBlock">
            <div className="accountSnapshot liveAccount" data-testid="account-snapshot">
              <div>
                <span>{t.accountInfo}</span>
                <strong>{accountName}</strong>
              </div>
              <small className={account?.refreshing ? "isRefreshing" : ""}>
                {account?.refreshing
                  ? t.accountRefreshing
                  : account?.account_id
                    ? `${t.accountId} ${account.account_id}`
                    : t.accountWaiting}
              </small>
              <dl>
                <div>
                  <dt>{t.rank}</dt>
                  <dd>{formatRank(account?.rank_tier, account?.level_id, account?.level_score, language)}</dd>
                </div>
                <div>
                  <dt>{t.target}</dt>
                  <dd>{accountTarget}</dd>
                </div>
              </dl>
            </div>
            {(table?.players ?? []).map((player) => (
              <div className="playerLine" key={player.seat}>
                <span>{playerRelation(player.seat, table?.seat, t)}</span>
                <strong>{player.points}</strong>
                <em>#{player.seat}</em>
                <small>{player.riichi ? t.riichi : ""}</small>
              </div>
            ))}
          </TelemetryBlock>

          <TelemetryBlock title={t.eventStream} icon={<RadioTower size={16} />} className="eventsBlock">
            <div className="eventStream">
              {events.slice(0, 10).map((event, index) => (
                <code key={index}>{JSON.stringify(event)}</code>
              ))}
            </div>
          </TelemetryBlock>

          <TelemetryBlock title={t.logs} icon={<AlertTriangle size={16} />} className="logsBlock">
            <div className="logList">
              {logs.slice(0, 7).map((log, index) => (
                <p key={index} className={`log-${log.level}`}>
                  {log.level === "error" ? <AlertTriangle size={14} /> : null}
                  {new Date(log.at).toLocaleTimeString()} {formatLogMessage(log.message, language)}
                </p>
              ))}
            </div>
          </TelemetryBlock>
        </aside>
      </main>
      {emergencyConfirmOpen ? (
        <div className="confirmOverlay" role="dialog" aria-modal="true" aria-labelledby="emergency-title">
          <div className="confirmDialog">
            <div className="confirmIcon">
              <AlertTriangle size={22} />
            </div>
            <div>
              <h2 id="emergency-title">{t.emergencyStop}</h2>
              <p>{t.emergencyConfirm}</p>
            </div>
            <div className="confirmActions">
              <button type="button" onClick={() => setEmergencyConfirmOpen(false)}>
                {t.emergencyCancel}
              </button>
              <button className="danger" type="button" onClick={() => void emergencyStop()}>
                {t.emergencyConfirmAction}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      </div>
    </div>
  );
}

function formatRank(
  tier: number | null | undefined,
  levelId: number | null | undefined,
  levelScore: number | null | undefined,
  language: Language,
) {
  if (!tier) return levelId ? String(levelId) : "-";
  const zh = ["", "初心", "雀士", "雀杰", "雀豪", "魂天"];
  const en = ["", "Novice", "Adept", "Expert", "Master", "Celestial"];
  const ja = ["", "初心", "雀士", "雀傑", "雀豪", "魂天"];
  const names = language === "en" ? en : language === "ja" ? ja : zh;
  const base = names[tier] ?? `Tier ${tier}`;
  const star = levelId ? levelId % 100 : null;
  const score = levelScore ?? null;
  if (!star) return score == null ? base : `${base} ${score}${language === "en" ? " pts" : "分"}`;
  if (language === "en") {
    return score == null ? `${base} ${star}` : `${base} ${star} · ${score} pts`;
  }
  const starText = language === "ja" ? `${toDisplayNumber(star, language)}星` : `${toDisplayNumber(star, language)}星`;
  return score == null ? `${base}${starText}` : `${base}${starText} ${score}分`;
}

function toDisplayNumber(value: number, language: Language) {
  if (language === "zh") return ["", "一", "二", "三", "四"][value] ?? String(value);
  if (language === "ja") return ["", "一", "二", "三", "四"][value] ?? String(value);
  return String(value);
}

function playerRelation(
  seat: number,
  selfSeat: number | undefined,
  labels: { self: string; topPlayer: string; leftPlayer: string; rightPlayer: string },
) {
  if (selfSeat === undefined) return `#${seat}`;
  const relative = (seat - selfSeat + 4) % 4;
  if (relative === 0) return labels.self;
  if (relative === 1) return labels.rightPlayer;
  if (relative === 2) return labels.topPlayer;
  return labels.leftPlayer;
}

function formatActionLabel(
  label: string,
  copyText: {
    modelDiscard: string;
    modelReach: string;
    modelChiLow: string;
    modelChiMid: string;
    modelChiHigh: string;
    modelPon: string;
    modelKan: string;
    modelHora: string;
    modelRyukyoku: string;
    modelNone: string;
  },
  language: Language,
) {
  const discard = label.match(/^discard (.+)$/);
  if (discard) return `${copyText.modelDiscard} ${formatTileName(discard[1], language)}`;
  const reach = label.match(/^reach(?: (.+))?$/);
  if (reach) {
    return reach[1] ? `${copyText.modelReach} ${formatTileName(reach[1], language)}` : copyText.modelReach;
  }
  const map: Record<string, string> = {
    "chi low": copyText.modelChiLow,
    "chi mid": copyText.modelChiMid,
    "chi high": copyText.modelChiHigh,
    pon: copyText.modelPon,
    kan: copyText.modelKan,
    hora: copyText.modelHora,
    ryukyoku: copyText.modelRyukyoku,
    none: copyText.modelNone,
  };
  return map[label] ?? label;
}

function formatLogMessage(message: string, language: Language) {
  const discardAccepted = message.match(/^discard accepted: (.+)$/);
  if (discardAccepted) {
    const tile = formatTileName(discardAccepted[1], language);
    if (language === "en") return `Discarded ${tile}`;
    if (language === "ja") return `${tile}を打牌`;
    return `已打出 ${tile}`;
  }

  if (message.includes("discard refused without discard operation window")) {
    if (language === "en") return "No discard window; ignored one discard request";
    if (language === "ja") return "打牌できない状態のため、打牌要求を1回無視しました";
    return "当前没有出牌窗口，已忽略一次出牌请求";
  }

  if (message.startsWith("game connected:")) {
    if (language === "en") return "Game connected";
    if (language === "ja") return "対局に接続しました";
    return "已进入牌局";
  }

  if (message.includes("match queued successfully")) {
    if (language === "en") return "Queued for matchmaking";
    if (language === "ja") return "マッチング待機に入りました";
    return "已进入匹配队列";
  }

  const loginBegin = message.match(/^login begin: username=(.+)$/);
  if (loginBegin) {
    if (language === "en") return `Logging in as ${loginBegin[1]}`;
    if (language === "ja") return `${loginBegin[1]} でログイン中`;
    return `正在登录 ${loginBegin[1]}`;
  }

  const matchFound = message.match(/^match found:.*game_uuid=([^\\s]+).*/);
  if (matchFound) {
    if (language === "en") return `Match found (${matchFound[1]})`;
    if (language === "ja") return `対局が見つかりました (${matchFound[1]})`;
    return `匹配成功 (${matchFound[1]})`;
  }

  if (message.startsWith("runtime stopped after current game:")) {
    if (language === "en") return "Stopped after the current game";
    if (language === "ja") return "本局後に停止しました";
    return "已在本局结束后停止";
  }

  if (message.startsWith("core event sync failed:")) {
    if (language === "en") return "Failed to refresh runtime events";
    if (language === "ja") return "実行イベントの更新に失敗しました";
    return "刷新运行事件失败";
  }

  if (message.startsWith("runtime snapshot failed:")) {
    if (language === "en") return "Failed to refresh runtime status";
    if (language === "ja") return "実行状態の更新に失敗しました";
    return "刷新运行状态失败";
  }

  return message;
}

function formatTileName(tile: string, language: Language) {
  const honorNames: Record<Language, Record<string, string>> = {
    zh: { E: "东", S: "南", W: "西", N: "北", P: "白", F: "发", C: "中" },
    en: { E: "East", S: "South", W: "West", N: "North", P: "White", F: "Green", C: "Red" },
    ja: { E: "東", S: "南", W: "西", N: "北", P: "白", F: "發", C: "中" },
  };
  const honors = honorNames[language];
  if (honors[tile]) return honors[tile];
  const normalized = tile.replace(/r$/, "");
  const suited = normalized.match(/^([1-9])([mps])$/);
  if (!suited) return tile;
  const suitNames: Record<Language, Record<string, string>> = {
    zh: { m: "万", p: "筒", s: "索" },
    en: { m: "m", p: "p", s: "s" },
    ja: { m: "萬", p: "筒", s: "索" },
  };
  const suit = suitNames[language][suited[2]];
  const red = tile.endsWith("r") ? (language === "en" ? "red " : "赤") : "";
  if (language === "en") return `${red}${suited[1]}${suit}`;
  return `${red}${suited[1]}${suit}`;
}

function PanelTitle({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <div className="panelTitle">
      {icon}
      <h2>{label}</h2>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function TelemetryBlock({
  title,
  icon,
  children,
  className,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`telemetryBlock ${className ?? ""}`}>
      <div className="telemetryTitle">
        {icon}
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}
