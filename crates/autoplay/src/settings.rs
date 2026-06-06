use anyhow::{anyhow, Context, Result};
use protocol::config::{Mode, Room};
use serde::{Deserialize, Serialize};
use std::{fs, io::ErrorKind, path::Path};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    pub model_path: String,
    #[serde(default = "default_ui_language")]
    pub ui_language: UiLanguage,
    pub autoplay_account: Account,
    #[serde(default)]
    pub autoplay: AutoplaySettings,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Account {
    pub username: String,
    pub password: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AutoplaySettings {
    #[serde(default)]
    pub room_policy: RoomPolicy,
    #[serde(default = "default_manual_room")]
    pub manual_room: RoomChoice,
    #[serde(default = "default_manual_mode")]
    pub manual_mode: ModeChoice,
    #[serde(default)]
    pub action_interval_ms: ActionInterval,
    #[serde(default)]
    pub max_games: Option<u32>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum RoomPolicy {
    #[default]
    AutoHighest,
    Manual,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RoomChoice {
    Bronze,
    Silver,
    Gold,
    Jade,
    Throne,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModeChoice {
    FourPlayerEast,
    FourPlayerSouth,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UiLanguage {
    Zh,
    En,
    Ja,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct ActionInterval {
    pub min: u64,
    pub max: u64,
}

impl Default for AutoplaySettings {
    fn default() -> Self {
        Self {
            room_policy: RoomPolicy::default(),
            manual_room: default_manual_room(),
            manual_mode: default_manual_mode(),
            action_interval_ms: ActionInterval::default(),
            max_games: None,
        }
    }
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            model_path: "models/mortal".to_string(),
            ui_language: default_ui_language(),
            autoplay_account: Account::default(),
            autoplay: AutoplaySettings::default(),
        }
    }
}

impl Default for ActionInterval {
    fn default() -> Self {
        Self {
            min: 800,
            max: 1600,
        }
    }
}

pub fn read_settings(path: &Path) -> Result<Settings> {
    let settings = read_settings_unchecked(path)?;
    validate_settings(&settings)?;
    Ok(settings)
}

pub fn read_settings_unchecked(path: &Path) -> Result<Settings> {
    let raw = match fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(Settings::default()),
        Err(err) => {
            return Err(err).with_context(|| format!("read settings from {}", path.display()))
        }
    };
    let settings: Settings = serde_json::from_str(&raw)
        .with_context(|| format!("parse settings from {}", path.display()))?;
    Ok(settings)
}

pub fn write_settings(path: &Path, settings: &Settings) -> Result<()> {
    let raw = serde_json::to_string_pretty(settings)?;
    fs::write(path, format!("{raw}\n"))
        .with_context(|| format!("write settings to {}", path.display()))?;
    Ok(())
}

pub fn validate_settings(settings: &Settings) -> Result<()> {
    if settings.autoplay_account.username.trim().is_empty()
        || settings.autoplay_account.password.is_empty()
    {
        return Err(anyhow!(
            "settings autoplay_account username/password are required"
        ));
    }
    if settings.model_path.trim().is_empty() {
        return Err(anyhow!("settings model_path is required"));
    }
    if settings.autoplay.action_interval_ms.min > settings.autoplay.action_interval_ms.max {
        return Err(anyhow!("action_interval_ms min must be <= max"));
    }
    Ok(())
}

pub fn manual_target(settings: &AutoplaySettings) -> Option<(Mode, Room)> {
    match settings.room_policy {
        RoomPolicy::AutoHighest => None,
        RoomPolicy::Manual => Some((settings.manual_mode.into(), settings.manual_room.into())),
    }
}

fn default_manual_room() -> RoomChoice {
    RoomChoice::Bronze
}

fn default_manual_mode() -> ModeChoice {
    ModeChoice::FourPlayerEast
}

fn default_ui_language() -> UiLanguage {
    UiLanguage::Zh
}

impl From<RoomChoice> for Room {
    fn from(value: RoomChoice) -> Self {
        match value {
            RoomChoice::Bronze => Room::Bronze,
            RoomChoice::Silver => Room::Silver,
            RoomChoice::Gold => Room::Gold,
            RoomChoice::Jade => Room::Jade,
            RoomChoice::Throne => Room::Throne,
        }
    }
}

impl From<ModeChoice> for Mode {
    fn from(value: ModeChoice) -> Self {
        match value {
            ModeChoice::FourPlayerEast => Mode::FourPlayerEast,
            ModeChoice::FourPlayerSouth => Mode::FourPlayerSouth,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_settings_path(name: &str) -> std::path::PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("majsoul-autopilot-{name}-{stamp}.json"))
    }

    #[test]
    fn missing_settings_file_loads_default_without_validation() {
        let path = temp_settings_path("missing");
        let settings = read_settings_unchecked(&path).unwrap();
        assert_eq!(settings.model_path, "models/mortal");
        assert!(settings.autoplay_account.username.is_empty());
        assert!(settings.autoplay_account.password.is_empty());
    }

    #[test]
    fn strict_settings_require_credentials_for_runtime() {
        let settings = Settings::default();
        let err = validate_settings(&settings).unwrap_err().to_string();
        assert!(err.contains("username/password"));
    }

    #[test]
    fn settings_can_save_plaintext_password() {
        let path = temp_settings_path("plaintext");
        let mut settings = Settings::default();
        settings.autoplay_account.username = "user@example.com".to_string();
        settings.autoplay_account.password = "secret-password".to_string();
        write_settings(&path, &settings).unwrap();
        let raw = fs::read_to_string(&path).unwrap();
        assert!(raw.contains("secret-password"));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn settings_preserve_ui_language_across_save() {
        let path = temp_settings_path("language");
        fs::write(
            &path,
            r#"{
  "model_path": "models/mortal",
  "ui_language": "ja",
  "autoplay_account": {
    "username": "user@example.com",
    "password": "secret-password"
  }
}"#,
        )
        .unwrap();
        let settings = read_settings_unchecked(&path).unwrap();
        write_settings(&path, &settings).unwrap();
        let raw = fs::read_to_string(&path).unwrap();
        assert!(raw.contains(r#""ui_language": "ja""#));
        let _ = fs::remove_file(path);
    }
}
