use anyhow::{anyhow, Context, Result};
use autoplay::{
    events::{CoreEvent, EventSink, LogLevel},
    runtime::{resolve_model_path, run_autoplay, RuntimeOptions},
    settings::{read_settings, Account, AutoplaySettings, Settings},
};
use clap::{Parser, Subcommand};
use mortal::{
    candle_engine::CandleMortalEngine,
    model_import::{ensure_exported_model_dir, import_model_file},
    native::{NativeEngine, Observation},
};
use protocol::session;
use serde::Deserialize;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{atomic::AtomicBool, Arc},
};
use tokio::sync::Semaphore;

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
    #[command(about = "Check Mahjong Soul email/password login and target room")]
    CheckLogin,
    #[command(about = "Check the model configured in settings.json")]
    CheckModel,
    #[command(about = "Model utilities for checking and importing safetensors models")]
    Model {
        #[command(subcommand)]
        command: ModelCommand,
    },
    #[command(about = "Validate a replay fixture path")]
    ReplayFixture { fixture: PathBuf },
    #[command(about = "Run one account from settings.json")]
    Run {
        #[arg(long)]
        max_games: Option<u32>,
    },
    #[command(about = "Run several accounts concurrently from an accounts JSON file")]
    RunMany {
        #[arg(long, help = "Path to accounts.json")]
        accounts: PathBuf,

        #[arg(long, help = "Override max_games for every account")]
        max_games: Option<u32>,

        #[arg(
            long,
            default_value_t = 0,
            help = "Maximum parallel accounts; 0 means all"
        )]
        concurrency: usize,
    },
}

#[derive(Subcommand)]
enum ModelCommand {
    #[command(about = "Check an exported Mortal model directory")]
    Check {
        #[arg(long, help = "Model directory; defaults to settings.json model_path")]
        model: Option<PathBuf>,
    },
    #[command(about = "Import a .safetensors model file into model directory form")]
    Import {
        #[arg(long, help = "Input .safetensors file")]
        input: PathBuf,

        #[arg(
            long,
            help = "Output directory containing model.safetensors and model_config.json"
        )]
        output: PathBuf,

        #[arg(long, help = "Replace output directory if it already exists")]
        force: bool,
    },
}

#[derive(Debug, Deserialize)]
struct MultiAccountFile {
    #[serde(default)]
    model_path: Option<String>,
    #[serde(default)]
    autoplay: Option<AutoplaySettings>,
    accounts: Vec<MultiAccountEntry>,
}

#[derive(Debug, Deserialize)]
struct MultiAccountEntry {
    username: String,
    password: String,
    #[serde(default)]
    label: Option<String>,
    #[serde(default)]
    device_id: Option<String>,
    #[serde(default)]
    model_path: Option<String>,
    #[serde(default)]
    autoplay: Option<AutoplaySettings>,
}

#[derive(Debug, Clone)]
struct AccountRun {
    label: String,
    device_id: String,
    settings: Settings,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let mut settings = read_settings(&cli.settings)?;

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
            check_model(&settings.model_path, Some(&cli.settings))?;
        }
        Command::Model { command } => match command {
            ModelCommand::Check { model } => {
                let raw = model
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| settings.model_path.clone());
                check_model(&raw, Some(&cli.settings))?;
            }
            ModelCommand::Import {
                input,
                output,
                force,
            } => {
                import_model(&input, &output, force)?;
            }
        },
        Command::ReplayFixture { fixture } => {
            if !fixture.exists() {
                return Err(anyhow!("fixture not found: {}", fixture.display()));
            }
            println!("fixture path ok: {}", fixture.display());
        }
        Command::Run { max_games } => {
            if max_games.is_some() {
                settings.autoplay.max_games = max_games;
            }
            run_autoplay(
                RuntimeOptions {
                    settings,
                    settings_path: Some(cli.settings),
                    device_id: "rust-cli-device".to_string(),
                },
                prefixed_stdout_sink("main".to_string()),
                Arc::new(AtomicBool::new(false)),
            )
            .await?;
        }
        Command::RunMany {
            accounts,
            max_games,
            concurrency,
        } => {
            let runs = load_multi_account_runs(&settings, &accounts, max_games)
                .with_context(|| format!("load accounts from {}", accounts.display()))?;
            run_many(runs, cli.settings, concurrency).await?;
        }
    }

    Ok(())
}

fn check_model(raw_path: &str, settings_path: Option<&Path>) -> Result<()> {
    let path = resolve_model_path(raw_path, settings_path)?;
    if !path.exists() {
        return Err(anyhow!("model path not found: {}", path.display()));
    }
    ensure_exported_model_dir(&path)?;
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
    Ok(())
}

fn import_model(input: &Path, output: &Path, force: bool) -> Result<()> {
    if output.exists() {
        if !force {
            return Err(anyhow!(
                "output already exists: {}; pass --force to replace it",
                output.display()
            ));
        }
        fs::remove_dir_all(output).with_context(|| format!("remove {}", output.display()))?;
    }
    let result = import_model_file(input, output)?;
    println!(
        "model imported: input={} output={}",
        input.display(),
        result.model_path.display()
    );
    Ok(())
}

fn load_multi_account_runs(
    base: &Settings,
    accounts_path: &Path,
    max_games: Option<u32>,
) -> Result<Vec<AccountRun>> {
    let raw = fs::read_to_string(accounts_path)
        .with_context(|| format!("read accounts from {}", accounts_path.display()))?;
    let file: MultiAccountFile = serde_json::from_str(&raw)
        .with_context(|| format!("parse accounts from {}", accounts_path.display()))?;
    if file.accounts.is_empty() {
        return Err(anyhow!("accounts file must contain at least one account"));
    }

    let mut runs = Vec::with_capacity(file.accounts.len());
    for (index, account) in file.accounts.into_iter().enumerate() {
        let mut settings = base.clone();
        if let Some(model_path) = file.model_path.clone() {
            settings.model_path = model_path;
        }
        if let Some(autoplay) = file.autoplay.clone() {
            settings.autoplay = autoplay;
        }
        if let Some(model_path) = account.model_path {
            settings.model_path = model_path;
        }
        if let Some(autoplay) = account.autoplay {
            settings.autoplay = autoplay;
        }
        if max_games.is_some() {
            settings.autoplay.max_games = max_games;
        }
        settings.autoplay_account = Account {
            username: account.username,
            password: account.password,
        };
        let label = account
            .label
            .unwrap_or_else(|| format!("{}:{}", index + 1, settings.autoplay_account.username));
        let device_id = account
            .device_id
            .unwrap_or_else(|| format!("rust-cli-{}-{}", index + 1, sanitize_device_id(&label)));
        runs.push(AccountRun {
            label,
            device_id,
            settings,
        });
    }
    Ok(runs)
}

async fn run_many(runs: Vec<AccountRun>, settings_path: PathBuf, concurrency: usize) -> Result<()> {
    let limit = if concurrency == 0 {
        runs.len()
    } else {
        concurrency.min(runs.len())
    };
    let semaphore = Arc::new(Semaphore::new(limit));
    let stop_flags = runs
        .iter()
        .map(|_| Arc::new(AtomicBool::new(false)))
        .collect::<Vec<_>>();
    let mut handles = Vec::with_capacity(runs.len());

    for (run, stop_flag) in runs.into_iter().zip(stop_flags) {
        let permit = semaphore.clone().acquire_owned().await?;
        let settings_path = settings_path.clone();
        handles.push(tokio::spawn(async move {
            let _permit = permit;
            let label = run.label.clone();
            let result = run_autoplay(
                RuntimeOptions {
                    settings: run.settings,
                    settings_path: Some(settings_path),
                    device_id: run.device_id,
                },
                prefixed_stdout_sink(label.clone()),
                stop_flag,
            )
            .await;
            (label, result)
        }));
    }

    let mut failures = Vec::new();
    for handle in handles {
        let (label, result) = handle.await?;
        match result {
            Ok(games) => println!("[{label}] finished: games={games}"),
            Err(err) => {
                eprintln!("[{label}] failed: {err}");
                failures.push(format!("{label}: {err}"));
            }
        }
    }

    if failures.is_empty() {
        Ok(())
    } else {
        Err(anyhow!(
            "{} account(s) failed: {}",
            failures.len(),
            failures.join("; ")
        ))
    }
}

fn prefixed_stdout_sink(label: String) -> EventSink {
    Arc::new(move |event| match event {
        CoreEvent::Log { level, message } => {
            let level = match level {
                LogLevel::Info => "INFO",
                LogLevel::Warn => "WARN",
                LogLevel::Error => "ERROR",
            };
            println!("[{label}] {level}: {message}");
        }
        CoreEvent::GameEvent { event } => println!("[{label}] event: {event:?}"),
        CoreEvent::GameCompleted { games_done } => {
            println!("[{label}] game completed: total_games={games_done}");
        }
        CoreEvent::RuntimeError { message } => eprintln!("[{label}] ERROR: {message}"),
        _ => {}
    })
}

fn sanitize_device_id(raw: &str) -> String {
    raw.chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '-'
            }
        })
        .collect::<String>()
        .trim_matches('-')
        .chars()
        .take(48)
        .collect::<String>()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_file(name: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("majsoul-cli-{name}-{stamp}.json"))
    }

    #[test]
    fn multi_account_file_merges_shared_defaults_and_overrides() {
        let path = temp_file("accounts");
        fs::write(
            &path,
            r#"{
  "model_path": "models/shared",
  "autoplay": {
    "room_policy": {"type": "manual"},
    "manual_room": "silver",
    "manual_mode": "four_player_south",
    "action_interval_ms": {"min": 1000, "max": 1200},
    "max_games": 9
  },
  "accounts": [
    {"username": "a@example.com", "password": "pa", "label": "alpha"},
    {
      "username": "b@example.com",
      "password": "pb",
      "model_path": "models/b",
      "autoplay": {
        "room_policy": {"type": "manual"},
        "manual_room": "gold",
        "manual_mode": "four_player_east",
        "action_interval_ms": {"min": 800, "max": 900},
        "max_games": 2
      }
    }
  ]
}"#,
        )
        .unwrap();
        let base = Settings::default();

        let runs = load_multi_account_runs(&base, &path, Some(1)).unwrap();

        assert_eq!(runs.len(), 2);
        assert_eq!(runs[0].label, "alpha");
        assert_eq!(runs[0].settings.model_path, "models/shared");
        assert_eq!(runs[0].settings.autoplay.max_games, Some(1));
        assert_eq!(runs[1].settings.model_path, "models/b");
        assert_eq!(runs[1].settings.autoplay.max_games, Some(1));
        assert!(runs[1].device_id.starts_with("rust-cli-2-"));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn multi_account_file_requires_accounts() {
        let path = temp_file("empty-accounts");
        fs::write(&path, r#"{"accounts":[]}"#).unwrap();

        let error = load_multi_account_runs(&Settings::default(), &path, None)
            .unwrap_err()
            .to_string();

        assert!(error.contains("at least one account"));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn device_id_sanitizer_keeps_ids_short_and_ascii() {
        assert_eq!(
            sanitize_device_id("账号 alpha@example.com"),
            "alpha-example-com"
        );
        assert!(sanitize_device_id(&"x".repeat(80)).len() <= 48);
    }
}
