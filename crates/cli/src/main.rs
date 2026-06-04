use anyhow::{anyhow, Context, Result};
use clap::{Parser, Subcommand};
use protocol::session;
use serde::Deserialize;
use std::{fs, path::PathBuf};

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
                return Err(anyhow!("model file not found: {}", path.display()));
            }
            println!("model path ok: {}", path.display());
        }
        Command::ReplayFixture { fixture } => {
            if !fixture.exists() {
                return Err(anyhow!("fixture not found: {}", fixture.display()));
            }
            println!("fixture path ok: {}", fixture.display());
        }
        Command::Run { max_games } => {
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
            let match_sid = client.start_match().await?;
            println!(
                "match queued: account_id={} target={:?}/{:?} match_sid={} max_games={:?}",
                client.summary.account_id, mode, room, match_sid, max_games
            );
        }
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
