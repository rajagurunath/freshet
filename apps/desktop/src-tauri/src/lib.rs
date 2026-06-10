use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use notify_debouncer_mini::{new_debouncer, notify::RecursiveMode, DebounceEventResult};
use tauri::{AppHandle, Emitter, Manager};

/// Return common AI-assistant session roots that exist on this machine.
#[tauri::command]
fn session_roots() -> Vec<String> {
    let home = dirs_home();
    let mut roots = Vec::new();
    let candidates = vec![
        home.join(".claude").join("projects"),
        home.join(".codex").join("sessions"),
        // VS Code globalStorage (Kilo Code) — mac & linux & win paths.
        home.join("Library")
            .join("Application Support")
            .join("Code")
            .join("User")
            .join("globalStorage")
            .join("kilocode.kilo-code"),
        home.join(".config")
            .join("Code")
            .join("User")
            .join("globalStorage")
            .join("kilocode.kilo-code"),
    ];
    for c in candidates {
        if c.exists() {
            roots.push(c.to_string_lossy().to_string());
        }
    }
    roots
}

/// Recursively list files under `dir` matching any of the given extensions.
#[tauri::command]
fn list_session_files(dir: String, exts: Vec<String>) -> Result<Vec<String>, String> {
    let mut out = Vec::new();
    walk(PathBuf::from(dir), &exts, &mut out).map_err(|e| e.to_string())?;
    Ok(out)
}

fn walk(dir: PathBuf, exts: &[String], out: &mut Vec<String>) -> std::io::Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() {
            walk(path, exts, out)?;
        } else if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            if exts.iter().any(|e| e == ext) {
                out.push(path.to_string_lossy().to_string());
            }
        }
    }
    Ok(())
}

fn dirs_home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// State that holds the active file watcher so it stays alive as long as the
/// app is running.  Wrapped in Arc<Mutex<>> so it can be stored in Tauri's
/// managed state.
struct WatcherState {
    /// Keeping the debouncer alive (dropping it stops the watcher).
    #[allow(dead_code)]
    debouncer: notify_debouncer_mini::Debouncer<
        notify_debouncer_mini::notify::RecommendedWatcher,
    >,
}

/// Start watching `roots` for file-system changes and emit a
/// `session-file-changed` Tauri event (payload: `{ path: String }`) whenever a
/// session file is created or modified.  Changes are debounced for 2 seconds so
/// rapid sequential writes from a running agent only produce a single event.
///
/// Calling this command a second time replaces the previous watcher.
#[tauri::command]
fn start_watching(
    app: AppHandle,
    roots: Vec<String>,
) -> Result<(), String> {
    let app_clone = app.clone();

    // Build the debounced watcher.  The callback runs on a background thread.
    let mut debouncer = new_debouncer(Duration::from_secs(2), move |res: DebounceEventResult| {
        match res {
            Ok(events) => {
                for event in events {
                    let path = event.path.to_string_lossy().to_string();
                    // Only forward .jsonl and .json session files
                    let is_session_file = path.ends_with(".jsonl")
                        || path.ends_with(".json");
                    if is_session_file {
                        let _ = app_clone.emit(
                            "session-file-changed",
                            serde_json::json!({ "path": path }),
                        );
                    }
                }
            }
            Err(e) => {
                eprintln!("[context-hub] watcher error: {:?}", e);
            }
        }
    })
    .map_err(|e| e.to_string())?;

    for root in roots {
        let p = PathBuf::from(&root);
        if p.exists() {
            debouncer
                .watcher()
                .watch(&p, RecursiveMode::Recursive)
                .map_err(|e| format!("watch {root}: {e}"))?;
        }
    }

    // Store in managed state — this keeps the debouncer (and thus the watcher
    // thread) alive.  Any previous watcher is dropped when we replace it.
    app.manage(Arc::new(Mutex::new(WatcherState { debouncer })));

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            session_roots,
            list_session_files,
            start_watching,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Context Hub");
}
