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

/// Lightweight session metadata for the list view — derived WITHOUT reading the
/// full transcript. Reading the body of every session (hundreds of MB, files up
/// to 33 MB) on startup is the load-time bottleneck; the list only needs this.
#[derive(serde::Serialize)]
struct SessionMeta {
    id: String,
    tool: String,
    project: String,
    title: String,
    preview: String,
    #[serde(rename = "startedAt")]
    started_at: String,
    #[serde(rename = "filePath")]
    file_path: String,
    mtime: u64,
    size: u64,
}

const HEAD_BYTES: u64 = 64 * 1024;

/// Read at most `n` bytes from the start of a file (UTF-8 lossy).
fn read_head(path: &std::path::Path, n: u64) -> std::io::Result<String> {
    use std::io::Read;
    let f = fs::File::open(path)?;
    let mut buf = Vec::new();
    f.take(n).read_to_end(&mut buf)?;
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

/// Pull the first textual content out of a Claude/Codex message `content`
/// (which is either a string or an array of `{text|input_text|output_text}`).
fn content_text(v: &serde_json::Value) -> String {
    if let Some(s) = v.as_str() {
        return s.to_string();
    }
    if let Some(arr) = v.as_array() {
        for b in arr {
            for k in ["text", "input_text", "output_text"] {
                if let Some(t) = b.get(k).and_then(|x| x.as_str()) {
                    if !t.is_empty() {
                        return t.to_string();
                    }
                }
            }
        }
    }
    String::new()
}

/// Decode a Claude project dir like `-Users-me-ionet-repos-yuno` to a project
/// name. Lossy (hyphens in real names can't be recovered), so prefer a `cwd`
/// field when the head provides one.
fn project_from_claude_dir(file_path: &str) -> String {
    file_path
        .replace('\\', "/")
        .split('/')
        .rev()
        .nth(1) // parent dir of the file
        .map(|d| d.trim_start_matches('-').rsplit('-').next().unwrap_or(d).to_string())
        .unwrap_or_default()
}

fn basename(p: &str) -> String {
    p.replace('\\', "/").rsplit('/').next().unwrap_or(p).to_string()
}

/// Extract (title, preview, project, started_at) from the head of a transcript.
fn extract_meta(tool: &str, head: &str, file_path: &str) -> (String, String, String, String) {
    let mut cwd = String::new();
    let mut started_at = String::new();
    let mut first_user = String::new();

    for line in head.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue, // a truncated final line in the head window
        };
        // timestamps live at the top level (Claude) or under payload (Codex)
        if started_at.is_empty() {
            if let Some(ts) = v.get("timestamp").and_then(|x| x.as_str()) {
                started_at = ts.to_string();
            } else if let Some(ts) = v.pointer("/payload/timestamp").and_then(|x| x.as_str()) {
                started_at = ts.to_string();
            }
        }
        if cwd.is_empty() {
            if let Some(c) = v.get("cwd").and_then(|x| x.as_str()) {
                cwd = c.to_string();
            } else if let Some(c) = v.pointer("/payload/cwd").and_then(|x| x.as_str()) {
                cwd = c.to_string();
            }
        }
        if first_user.is_empty() {
            let t = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
            let cand = if tool == "codex" && t == "response_item" {
                let p = v.get("payload");
                let is_msg = p.and_then(|p| p.get("type")).and_then(|x| x.as_str()) == Some("message");
                let role = p.and_then(|p| p.get("role")).and_then(|x| x.as_str()).unwrap_or("");
                if is_msg && role == "user" {
                    p.and_then(|p| p.get("content")).map(content_text).unwrap_or_default()
                } else {
                    String::new()
                }
            } else if t == "user" {
                v.pointer("/message/content").map(content_text).unwrap_or_default()
            } else {
                String::new()
            };
            // Skip system-injected wrappers (Codex prepends an <environment_context>
            // user turn) and tool/hook noise — we want the human's real first prompt.
            let trimmed = cand.trim_start();
            if !trimmed.is_empty() && !trimmed.starts_with("<environment_context") && !trimmed.starts_with('<') {
                first_user = cand;
            }
        }
    }

    let project = if !cwd.is_empty() {
        basename(&cwd)
    } else if tool == "claude-code" {
        project_from_claude_dir(file_path)
    } else {
        String::from("unknown")
    };
    let title: String = first_user.lines().next().unwrap_or("").chars().take(80).collect();
    let preview: String = first_user.chars().take(160).collect();
    (title, preview, project, started_at)
}

/// Scan all session roots and return lightweight metadata for the list view,
/// reading only each file's head (not its body). One IPC call; native speed.
#[tauri::command]
fn scan_session_meta() -> Vec<SessionMeta> {
    let mut out = Vec::new();
    for root in session_roots() {
        let norm = root.replace('\\', "/");
        let tool = if norm.contains("/.claude/") {
            "claude-code"
        } else if norm.contains("/.codex/") {
            "codex"
        } else {
            continue; // Kilo handled by the existing JS path for now
        };
        let mut files = Vec::new();
        let _ = walk(PathBuf::from(&root), &["jsonl".to_string()], &mut files);
        for fp in files {
            let p = PathBuf::from(&fp);
            let meta = match fs::metadata(&p) {
                Ok(m) => m,
                Err(_) => continue,
            };
            let mtime = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let head = match read_head(&p, HEAD_BYTES) {
                Ok(h) => h,
                Err(_) => continue,
            };
            let (title, preview, project, started_at) = extract_meta(tool, &head, &fp);
            let id = p
                .file_stem()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            out.push(SessionMeta {
                id,
                tool: tool.to_string(),
                project,
                title,
                preview,
                started_at,
                file_path: fp,
                mtime,
                size: meta.len(),
            });
        }
    }
    out
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
            scan_session_meta,
            start_watching,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Freshet");
}
