use std::fs;
use std::path::PathBuf;

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![session_roots, list_session_files])
        .run(tauri::generate_context!())
        .expect("error while running Context Hub");
}
