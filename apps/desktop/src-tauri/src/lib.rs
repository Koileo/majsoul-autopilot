use autoplay::{
    events::{CoreEvent, EventSink},
    runtime::{AutoplayController, RuntimeOptions},
    settings::{read_settings_unchecked, validate_settings, write_settings, Settings},
};
use serde::Serialize;
use std::{
    collections::VecDeque,
    fs::{File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{Emitter, Manager, State};

struct GuiState {
    settings_path: PathBuf,
    controller: AutoplayController,
    runtime_log_path: PathBuf,
    core_events: Arc<CoreEventBuffer>,
}

struct CoreEventBuffer {
    next_seq: AtomicU64,
    events: Mutex<VecDeque<CoreEventRecord>>,
}

#[derive(Debug, Clone, Serialize)]
struct RuntimeSnapshot {
    running: bool,
    status: autoplay::events::RuntimeStatus,
    last_error: Option<String>,
    settings_path: String,
    runtime_log_path: String,
}

#[derive(Debug, Clone, Serialize)]
struct CoreEventRecord {
    seq: u64,
    event: CoreEvent,
}

#[derive(Debug, Clone, Serialize)]
struct CoreEventBatch {
    cursor: u64,
    events: Vec<CoreEventRecord>,
}

#[tauri::command]
fn load_settings(state: State<'_, GuiState>) -> Result<Settings, String> {
    read_settings_unchecked(&state.settings_path).map_err(|err| err.to_string())
}

#[tauri::command]
fn save_settings(state: State<'_, GuiState>, settings: Settings) -> Result<(), String> {
    write_settings(&state.settings_path, &settings).map_err(|err| err.to_string())
}

#[tauri::command]
async fn start_autoplay(state: State<'_, GuiState>, settings: Settings) -> Result<(), String> {
    validate_settings(&settings).map_err(|err| err.to_string())?;
    state
        .controller
        .start(RuntimeOptions {
            settings,
            settings_path: Some(state.settings_path.clone()),
            device_id: "rust-gui-device".to_string(),
        })
        .await
        .map_err(|err| err.to_string())
}

#[tauri::command]
fn stop_after_current_game(state: State<'_, GuiState>) -> Result<(), String> {
    state.controller.stop_after_current_game();
    Ok(())
}

#[tauri::command]
async fn emergency_stop(state: State<'_, GuiState>) -> Result<(), String> {
    state.controller.emergency_stop().await;
    Ok(())
}

#[tauri::command]
fn get_runtime_snapshot(state: State<'_, GuiState>) -> RuntimeSnapshot {
    let snapshot = state.controller.snapshot();
    RuntimeSnapshot {
        running: snapshot.running,
        status: snapshot.status,
        last_error: snapshot.last_error,
        settings_path: state.settings_path.display().to_string(),
        runtime_log_path: state.runtime_log_path.display().to_string(),
    }
}

#[tauri::command]
fn get_core_event_batch(state: State<'_, GuiState>, after: u64) -> CoreEventBatch {
    state.core_events.batch_after(after)
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let app_handle = app.handle().clone();
            let settings_path = resolve_settings_path(app)?;
            let runtime_log_path = runtime_log_path_for_settings(&settings_path);
            let runtime_log = Arc::new(Mutex::new(open_runtime_log(&runtime_log_path)?));
            let core_events = Arc::new(CoreEventBuffer::new());
            let sink_core_events = core_events.clone();
            write_runtime_log_header(&runtime_log, &settings_path, &runtime_log_path);
            let sink: EventSink = Arc::new(move |event: CoreEvent| {
                write_runtime_event(&runtime_log, &event);
                sink_core_events.push(event.clone());
                let _ = app_handle.emit("core-event", event);
            });
            app.manage(GuiState {
                settings_path,
                controller: AutoplayController::new(sink),
                runtime_log_path,
                core_events,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            load_settings,
            save_settings,
            start_autoplay,
            stop_after_current_game,
            emergency_stop,
            get_runtime_snapshot,
            get_core_event_batch
        ])
        .run(tauri::generate_context!())
        .expect("error while running Tauri app");
}

fn resolve_settings_path(app: &tauri::App) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let current_exe = std::env::current_exe().ok();

    let config_dir = app.path().app_config_dir()?;
    std::fs::create_dir_all(&config_dir)?;
    Ok(resolve_settings_path_from(
        &current_dir,
        current_exe.as_deref(),
        &config_dir,
    ))
}

fn resolve_settings_path_from(
    current_dir: &Path,
    current_exe: Option<&Path>,
    fallback_config_dir: &Path,
) -> PathBuf {
    let mut bases = Vec::new();
    push_unique_base(&mut bases, current_dir.to_path_buf());
    if let Some(exe_parent) = current_exe.and_then(Path::parent) {
        for ancestor in exe_parent.ancestors() {
            push_unique_base(&mut bases, ancestor.to_path_buf());
        }
    }

    for base in bases {
        let settings = base.join("settings.json");
        if settings.exists() || base.join("settings.example.json").exists() {
            return settings;
        }
    }

    fallback_config_dir.join("settings.json")
}

fn push_unique_base(bases: &mut Vec<PathBuf>, base: PathBuf) {
    if !bases.iter().any(|existing| existing == &base) {
        bases.push(base);
    }
}

fn runtime_log_path_for_settings(settings_path: &Path) -> PathBuf {
    settings_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("logs/runtime/gui_autoplay.log")
}

fn open_runtime_log(path: &Path) -> Result<File, Box<dyn std::error::Error>> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(OpenOptions::new().create(true).append(true).open(path)?)
}

fn write_runtime_log_header(
    runtime_log: &Arc<Mutex<File>>,
    settings_path: &Path,
    runtime_log_path: &Path,
) {
    let ts_ms = now_ms();
    if let Ok(mut file) = runtime_log.lock() {
        let _ = writeln!(
            file,
            "{{\"ts_ms\":{ts_ms},\"event\":{{\"type\":\"gui_session_start\",\"settings_path\":{},\"runtime_log_path\":{}}}}}",
            serde_json::to_string(&settings_path.display().to_string()).unwrap_or_else(|_| "\"\"".to_string()),
            serde_json::to_string(&runtime_log_path.display().to_string()).unwrap_or_else(|_| "\"\"".to_string())
        );
    }
}

fn write_runtime_event(runtime_log: &Arc<Mutex<File>>, event: &CoreEvent) {
    let Ok(payload) = serde_json::to_string(event) else {
        return;
    };
    let ts_ms = now_ms();
    if let Ok(mut file) = runtime_log.lock() {
        let _ = writeln!(file, "{{\"ts_ms\":{ts_ms},\"event\":{payload}}}");
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

impl CoreEventBuffer {
    fn new() -> Self {
        Self {
            next_seq: AtomicU64::new(1),
            events: Mutex::new(VecDeque::with_capacity(600)),
        }
    }

    fn push(&self, event: CoreEvent) {
        let seq = self.next_seq.fetch_add(1, Ordering::Relaxed);
        let mut events = self.events.lock().expect("core event buffer poisoned");
        events.push_back(CoreEventRecord { seq, event });
        while events.len() > 600 {
            events.pop_front();
        }
    }

    fn batch_after(&self, after: u64) -> CoreEventBatch {
        let events = self.events.lock().expect("core event buffer poisoned");
        let batch = events
            .iter()
            .filter(|record| record.seq > after)
            .cloned()
            .collect::<Vec<_>>();
        let cursor = batch
            .last()
            .map(|record| record.seq)
            .unwrap_or_else(|| self.next_seq.load(Ordering::Relaxed).saturating_sub(1));
        CoreEventBatch {
            cursor,
            events: batch,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    fn temp_dir(name: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("majsoul-autopilot-tauri-{name}-{stamp}"));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn settings_path_prefers_executable_ancestor_over_app_config_dir() {
        let root = temp_dir("ancestor");
        fs::write(root.join("settings.example.json"), "{}").unwrap();
        let exe = root.join("target/release/bundle/macos/Majsoul Autopilot.app/Contents/MacOS/app");
        fs::create_dir_all(exe.parent().unwrap()).unwrap();
        fs::write(&exe, "").unwrap();
        let app_config = temp_dir("config");

        let resolved = resolve_settings_path_from(Path::new("/"), Some(&exe), &app_config);

        assert_eq!(resolved, root.join("settings.json"));
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(app_config);
    }

    #[test]
    fn settings_path_keeps_current_directory_when_project_settings_exist() {
        let root = temp_dir("cwd");
        fs::write(root.join("settings.json"), "{}").unwrap();
        let app_config = temp_dir("config");

        let resolved = resolve_settings_path_from(&root, None, &app_config);

        assert_eq!(resolved, root.join("settings.json"));
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(app_config);
    }

    #[test]
    fn runtime_log_path_lives_next_to_project_settings() {
        let root = temp_dir("runtime-log");
        let path = runtime_log_path_for_settings(&root.join("settings.json"));

        assert_eq!(path, root.join("logs/runtime/gui_autoplay.log"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn core_event_buffer_returns_only_events_after_cursor() {
        let buffer = CoreEventBuffer::new();
        buffer.push(CoreEvent::RuntimeStatus {
            status: autoplay::events::RuntimeStatus::LoggingIn,
        });
        buffer.push(CoreEvent::RuntimeStatus {
            status: autoplay::events::RuntimeStatus::Matching,
        });

        let first = buffer.batch_after(0);
        assert_eq!(first.events.len(), 2);
        assert_eq!(first.cursor, 2);

        buffer.push(CoreEvent::RuntimeStatus {
            status: autoplay::events::RuntimeStatus::InGame,
        });
        let second = buffer.batch_after(first.cursor);
        assert_eq!(second.events.len(), 1);
        assert_eq!(second.cursor, 3);
    }
}
