use anyhow::{anyhow, Context, Result};
use autoplay::{
    events::stdout_sink,
    runtime::{resolve_model_path, run_autoplay, RuntimeOptions},
    settings::read_settings,
};
use clap::{Parser, Subcommand};
use mortal::{
    candle_engine::CandleMortalEngine,
    native::{NativeEngine, Observation},
};
use protocol::session;
use std::{
    path::PathBuf,
    sync::{atomic::AtomicBool, Arc},
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
            let path = resolve_model_path(&settings.model_path, Some(&cli.settings))?;
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
            if max_games.is_some() {
                settings.autoplay.max_games = max_games;
            }
            run_autoplay(
                RuntimeOptions {
                    settings,
                    settings_path: Some(cli.settings),
                    device_id: "rust-cli-device".to_string(),
                },
                stdout_sink(),
                Arc::new(AtomicBool::new(false)),
            )
            .await?;
        }
    }

    Ok(())
}
