use anyhow::{anyhow, Context, Result};
use autoplay::action::{
    ActionState, BotAction, Operation, OperationContext, PendingAction, RpcPlan, OP_DISCARD,
    OP_LIQI,
};
use clap::{Parser, Subcommand};
use liqi::pb;
use mjai::bridge;
use mortal::{
    candle_engine::CandleMortalEngine,
    native::{NativeBot, NativeEngine, Observation},
};
use protocol::session::{self, StartMatchResult};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs,
    path::PathBuf,
    time::{Duration, Instant},
};

#[derive(Parser)]
#[command(name = "majsoul-autopilot-rs")]
#[command(about = "Pure Liqi protocol Mahjong Soul autopilot in Rust")]
struct Cli {
    #[arg(long, default_value = "settings.json")]
    settings: PathBuf,

    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    CheckLogin,
    CheckModel,
    ReplayFixture {
        fixture: PathBuf,
    },
    Run {
        #[arg(long)]
        max_games: Option<u32>,
    },
}

#[derive(Debug, Deserialize)]
struct Settings {
    model_path: String,
    autoplay_account: Account,
}

#[derive(Debug, Deserialize)]
struct Account {
    username: String,
    password: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let settings = read_settings(&cli.settings)?;

    match cli.command {
        Command::CheckLogin => {
            let summary = session::check_login(
                &settings.autoplay_account.username,
                &settings.autoplay_account.password,
                "rust-cli-device",
            )
            .await?;
            println!(
                "login ok: account_id={} nickname={} level={} tier={} target={:?}/{:?}",
                summary.account_id,
                summary.nickname,
                summary.level_id,
                summary.rank_tier,
                summary.target_mode,
                summary.target_room
            );
        }
        Command::CheckModel => {
            let path = PathBuf::from(&settings.model_path);
            if !path.exists() {
                return Err(anyhow!("model path not found: {}", path.display()));
            }
            let mut engine = CandleMortalEngine::load(&path)
                .with_context(|| format!("load exported Mortal model from {}", path.display()))?;
            let mut mask = vec![false; 46];
            mask[0] = true;
            let decisions = engine.react_batch(&[Observation {
                values: vec![0.0; 1012 * 34],
                mask,
                channels: 1012,
                width: 34,
            }])?;
            println!(
                "model ok: {} action={} version={}",
                path.display(),
                decisions[0].action,
                engine.version()
            );
        }
        Command::ReplayFixture { fixture } => {
            if !fixture.exists() {
                return Err(anyhow!("fixture not found: {}", fixture.display()));
            }
            println!("fixture path ok: {}", fixture.display());
        }
        Command::Run { max_games } => {
            let engine = CandleMortalEngine::load(&settings.model_path).with_context(|| {
                format!("load exported Mortal model from {}", settings.model_path)
            })?;
            let mut client = session::ProtocolClient::login(
                &settings.autoplay_account.username,
                &settings.autoplay_account.password,
                "rust-cli-device",
            )
            .await?;
            let (mode, room) = (
                client.summary.target_mode.clone(),
                client.summary.target_room.clone(),
            );
            let existing = client.fetch_existing_game().await?;
            let (game, initial_events) = if let Some(existing) = existing {
                println!(
                    "existing game found: account_id={} target={:?}/{:?} game_uuid={} location={} max_games={:?}",
                    client.summary.account_id,
                    mode,
                    room,
                    existing.game_uuid,
                    existing.location,
                    max_games
                );
                client.connect_existing_game(&existing).await?
            } else {
                match client.start_match().await? {
                    StartMatchResult::Queued(match_sid) => {
                        println!(
                            "match queued: account_id={} target={:?}/{:?} match_sid={} max_games={:?}",
                            client.summary.account_id, mode, room, match_sid, max_games
                        );
                        let start = client.wait_for_match_start().await?;
                        println!(
                            "match found: mode_id={} game_uuid={} location={} url={}",
                            start.match_mode_id, start.game_uuid, start.location, start.game_url
                        );
                        client.connect_game(&start).await?
                    }
                    StartMatchResult::Busy => {
                        if let Some(existing) = client.fetch_existing_game().await? {
                            println!(
                                "account busy; reconnecting existing game: account_id={} game_uuid={} location={}",
                                client.summary.account_id, existing.game_uuid, existing.location
                            );
                            client.connect_existing_game(&existing).await?
                        } else {
                            println!(
                                "account busy but fetchGamingInfo returned no game; leaving queue/game control alone"
                            );
                            return Ok(());
                        }
                    }
                }
            };
            run_game_loop(game, initial_events, engine, max_games).await?;
        }
    }

    Ok(())
}

async fn run_game_loop(
    mut game: session::GameSession,
    initial_events: Vec<bridge::Event>,
    engine: CandleMortalEngine,
    max_games: Option<u32>,
) -> Result<()> {
    println!(
        "game connected: initial_events={} operation_window={}",
        initial_events.len(),
        game.bridge.last_operation_list().len()
    );

    let mut bot = NativeBot::new(game.bridge.seat() as u8, engine);
    let mut queue = VecDeque::new();
    let mut games_done = 0u32;
    if !initial_events.is_empty() {
        let mut last_action_json = None;
        for event in &initial_events {
            println!("restore event: {:?}", event);
            if matches!(event, bridge::Event::EndGame) {
                games_done += 1;
            }
            last_action_json = bot.react(event)?;
        }
        if max_games.is_some_and(|limit| games_done >= limit) {
            println!("max games reached in restore: {games_done}");
            return Ok(());
        }
        if let Some(action_json) = last_action_json {
            let ack_events = handle_bot_action_json(&mut game, &mut bot, action_json).await?;
            queue.extend(ack_events);
        }
    }

    loop {
        let event = if let Some(event) = queue.pop_front() {
            event
        } else {
            let events = game.next_events().await?;
            queue.extend(events);
            continue;
        };

        println!("event: {:?}", event);
        if matches!(event, bridge::Event::EndGame) {
            games_done += 1;
            if max_games.is_some_and(|limit| games_done >= limit) {
                println!("max games reached: {games_done}");
                break;
            }
        }

        let ack_events = handle_bot_event(&mut game, &mut bot, &event).await?;
        queue.extend(ack_events);
    }

    Ok(())
}

fn read_settings(path: &PathBuf) -> Result<Settings> {
    let raw = fs::read_to_string(path)
        .with_context(|| format!("read settings from {}", path.display()))?;
    let settings: Settings = serde_json::from_str(&raw)
        .with_context(|| format!("parse settings from {}", path.display()))?;
    if settings.autoplay_account.username.trim().is_empty()
        || settings.autoplay_account.password.is_empty()
    {
        return Err(anyhow!(
            "settings autoplay_account username/password are required"
        ));
    }
    Ok(settings)
}

async fn handle_bot_event(
    game: &mut session::GameSession,
    bot: &mut NativeBot<CandleMortalEngine>,
    event: &bridge::Event,
) -> Result<Vec<bridge::Event>> {
    let Some(action_json) = bot.react(event)? else {
        return Ok(Vec::new());
    };
    handle_bot_action_json(game, bot, action_json).await
}

async fn handle_bot_action_json(
    game: &mut session::GameSession,
    bot: &mut NativeBot<CandleMortalEngine>,
    action_json: String,
) -> Result<Vec<bridge::Event>> {
    let action_json = resolve_riichi_action(game, bot, action_json)?;
    let Some(pending) = pending_action_from_json(&action_json, &game.bridge)? else {
        return Ok(Vec::new());
    };
    execute_pending_action(game, pending).await
}

fn resolve_riichi_action(
    game: &session::GameSession,
    bot: &mut NativeBot<CandleMortalEngine>,
    action_json: String,
) -> Result<String> {
    let mut action: Value = serde_json::from_str(&action_json)?;
    if action.get("type").and_then(Value::as_str) != Some("reach")
        || action.get("pai").is_some()
    {
        return Ok(action_json);
    }

    let actor = action
        .get("actor")
        .and_then(Value::as_u64)
        .unwrap_or(game.bridge.seat() as u64) as u32;
    let reach_event = bridge::Event::Reach { actor };
    if let Some(dahai_json) = bot.react(&reach_event)? {
        let dahai: Value = serde_json::from_str(&dahai_json)?;
        if dahai.get("type").and_then(Value::as_str) == Some("dahai") {
            action["pai"] = dahai.get("pai").cloned().unwrap_or(Value::String(String::new()));
            action["tsumogiri"] = dahai
                .get("tsumogiri")
                .cloned()
                .unwrap_or(Value::Bool(false));
            println!(
                "riichi: model chose {} tsumogiri={}",
                action.get("pai").and_then(Value::as_str).unwrap_or(""),
                action
                    .get("tsumogiri")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
            );
            return Ok(serde_json::to_string(&action)?);
        }
    }

    if let Some(tile) = game.bridge.my_tsumohai() {
        action["pai"] = json!(tile);
        action["tsumogiri"] = json!(true);
        println!("riichi: model did not return dahai, using last tsumo {tile}");
    } else {
        println!("riichi: requested but no tile available");
    }
    Ok(serde_json::to_string(&action)?)
}

fn pending_action_from_json(
    action_json: &str,
    bridge: &bridge::Bridge,
) -> Result<Option<PendingAction>> {
    let action: Value = serde_json::from_str(action_json)?;
    let Some(action_type) = action.get("type").and_then(Value::as_str) else {
        return Ok(Some(PendingAction {
            action: BotAction::None,
            context: operation_context(bridge),
        }));
    };

    let bot_action = match action_type {
        "none" => BotAction::None,
        "dahai" => BotAction::Dahai {
            tile: string_field(&action, "pai"),
            tsumogiri: bool_field(&action, "tsumogiri"),
        },
        "reach" => BotAction::Reach {
            tile: string_field(&action, "pai"),
            tsumogiri: bool_field(&action, "tsumogiri"),
        },
        "chi" => BotAction::Chi {
            consumed: string_array_field(&action, "consumed"),
        },
        "pon" => BotAction::Pon {
            consumed: string_array_field(&action, "consumed"),
        },
        "daiminkan" => BotAction::Daiminkan {
            consumed: string_array_field(&action, "consumed"),
        },
        "ankan" => BotAction::Ankan {
            consumed: string_array_field(&action, "consumed"),
        },
        "kakan" => BotAction::Kakan {
            tile: string_field(&action, "pai"),
        },
        "hora" => {
            let actor = action.get("actor").and_then(Value::as_u64);
            let target = action.get("target").and_then(Value::as_u64).or(actor);
            BotAction::Hora {
                tsumo: actor == target,
            }
        }
        "ryukyoku" => BotAction::Ryukyoku,
        other => return Err(anyhow!("unknown MJAI action type: {other}")),
    };

    Ok(Some(PendingAction {
        action: bot_action,
        context: operation_context(bridge),
    }))
}

async fn execute_pending_action(
    game: &mut session::GameSession,
    pending: PendingAction,
) -> Result<Vec<bridge::Event>> {
    tokio::time::sleep(Duration::from_millis(300)).await;
    let state = action_state(&game.bridge);
    let plan = autoplay::action::plan_action(&pending, &state);
    match plan {
        RpcPlan::IgnoreStale => {
            println!("stale action ignored: {:?}", pending.action);
            Ok(Vec::new())
        }
        RpcPlan::RefuseNoDiscardWindow => {
            println!("discard refused without discard operation window");
            Ok(Vec::new())
        }
        RpcPlan::Skip => {
            check_common_error(game.skip().await?, "inputChiPengGang skip")?;
            Ok(Vec::new())
        }
        RpcPlan::ChiPengGang {
            r#type,
            index,
            timeuse,
        } => {
            check_common_error(
                game.input_chi_peng_gang(pb::ReqChiPengGang {
                    r#type,
                    index,
                    timeuse,
                    ..Default::default()
                })
                .await?,
                "inputChiPengGang",
            )?;
            Ok(game.next_events().await?)
        }
        RpcPlan::InputOperation {
            r#type,
            tile,
            mut moqie,
            timeuse,
        } => {
            let before_discard = game.bridge.discard_counter();
            let before_round = game.bridge.round_end_counter();
            let expected_discard = tile.as_deref().map(ms_tile_to_mjai);
            if r#type == OP_DISCARD {
                if let Some(op_context) = game.bridge.last_operation_context().cloned() {
                    if op_context.source == "ActionNewRound" {
                        if moqie {
                            println!("opening-round discard uses hand-discard mode instead of moqie");
                            moqie = false;
                        }
                        wait_opening_round_discard_window(&op_context).await;
                        let still_same_window = game
                            .bridge
                            .last_operation_context()
                            .is_some_and(|current| {
                                current.source == op_context.source
                                    && current.seat == op_context.seat
                                    && current.received_key == op_context.received_key
                            })
                            && game
                                .bridge
                                .last_operation_list()
                                .iter()
                                .any(|op| op.r#type == OP_DISCARD);
                        if !still_same_window {
                            println!("opening-round discard window changed before submit");
                            return Ok(Vec::new());
                        }
                    }
                }
            }
            check_common_error(
                game.input_operation(pb::ReqSelfOperation {
                    r#type,
                    tile: tile.clone().unwrap_or_default(),
                    moqie,
                    timeuse,
                    ..Default::default()
                })
                .await?,
                "inputOperation",
            )?;
            if matches!(r#type, OP_DISCARD | OP_LIQI) {
                wait_for_discard_ack(game, before_discard, before_round, expected_discard).await
            } else {
                Ok(game.next_events().await?)
            }
        }
    }
}

async fn wait_opening_round_discard_window(op_context: &bridge::OperationContext) {
    const OPENING_ROUND_DISCARD_MIN_DELAY: f64 = 12.0;
    if op_context.source != "ActionNewRound" {
        return;
    }
    let passed_waiting = op_context.passed_waiting_time as f64;
    let elapsed = op_context.received_at.elapsed().as_secs_f64();
    let wait_time = OPENING_ROUND_DISCARD_MIN_DELAY - passed_waiting.max(elapsed).max(0.0);
    if wait_time <= 0.0 {
        return;
    }
    println!("opening-round discard waits {wait_time:.1}s before submit");
    tokio::time::sleep(Duration::from_secs_f64(wait_time)).await;
}

async fn wait_for_discard_ack(
    game: &mut session::GameSession,
    before_discard: u64,
    before_round: u64,
    expected_pai: Option<String>,
) -> Result<Vec<bridge::Event>> {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(anyhow!("discard RPC accepted but no matching broadcast ACK arrived"));
        }
        let events = tokio::time::timeout(remaining, game.next_events()).await??;
        if game.bridge.round_end_counter() > before_round {
            return Ok(events);
        }
        if game.bridge.discard_counter() <= before_discard {
            continue;
        }

        let self_discard = events.iter().find_map(|event| match event {
            bridge::Event::Dahai { actor, pai, .. } if *actor == game.bridge.seat() => {
                Some(pai.as_str())
            }
            _ => None,
        });
        match (expected_pai.as_deref(), self_discard) {
            (None, Some(_)) => return Ok(events),
            (Some(expected), Some(actual)) if expected == actual => return Ok(events),
            (Some(expected), Some(actual)) => {
                return Err(anyhow!(
                    "no matching broadcast ACK: expected self discard {expected}, got {actual}"
                ));
            }
            (_, None) => continue,
        }
    }
}

fn check_common_error(response: pb::ResCommon, label: &str) -> Result<()> {
    if let Some(error) = response.error {
        if error.code != 0 {
            return Err(anyhow!("{label} failed: code={}", error.code));
        }
    }
    Ok(())
}

fn action_state(bridge: &bridge::Bridge) -> ActionState {
    ActionState {
        current_context: operation_context(bridge),
        operations: bridge
            .last_operation_list()
            .iter()
            .map(|op| Operation {
                r#type: op.r#type,
                combination: op.combination.clone(),
            })
            .collect(),
    }
}

fn operation_context(bridge: &bridge::Bridge) -> Option<OperationContext> {
    bridge.last_operation_context().map(|ctx| OperationContext {
        source: ctx.source.clone(),
        seat: ctx.seat,
        received_key: ctx.received_key,
    })
}

fn string_field(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn bool_field(value: &Value, key: &str) -> bool {
    value.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn string_array_field(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn ms_tile_to_mjai(tile: &str) -> String {
    match tile {
        "0m" => "5mr",
        "0p" => "5pr",
        "0s" => "5sr",
        "1z" => "E",
        "2z" => "S",
        "3z" => "W",
        "4z" => "N",
        "5z" => "P",
        "6z" => "F",
        "7z" => "C",
        other => other,
    }
    .to_string()
}
