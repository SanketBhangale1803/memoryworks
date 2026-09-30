use serde::Serialize;
use std::path::Path;

#[derive(Serialize)]
struct LocalFile { path: String, bytes: u64 }

#[tauri::command]
fn store_secret(service: String, account: String, secret: String) -> Result<(), String> {
    keyring::Entry::new(&service, &account).map_err(|e| e.to_string())?.set_password(&secret).map_err(|e| e.to_string())
}

#[tauri::command]
fn load_secret(service: String, account: String) -> Result<Option<String>, String> {
    match keyring::Entry::new(&service, &account).map_err(|e| e.to_string())?.get_password() {
        Ok(secret) => Ok(Some(secret)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

#[tauri::command]
fn folder_manifest(path: String) -> Result<Vec<LocalFile>, String> {
    let root = Path::new(&path).canonicalize().map_err(|e| e.to_string())?;
    if !root.is_dir() { return Err("The selected path is not a directory".into()); }
    Ok(walkdir::WalkDir::new(root).follow_links(false).into_iter().filter_map(Result::ok).filter(|entry| entry.file_type().is_file()).take(5_000).filter_map(|entry| {
        let metadata = entry.metadata().ok()?;
        Some(LocalFile { path: entry.path().to_string_lossy().to_string(), bytes: metadata.len() })
    }).collect())
}

#[tauri::command]
async fn probe_endpoint(url: String) -> Result<String, String> {
    let parsed = url::Url::parse(&url).map_err(|e| e.to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.username() != "" || parsed.password().is_some() { return Err("Use an HTTP(S) URL without embedded credentials".into()); }
    let response = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).build().map_err(|e| e.to_string())?.get(parsed).timeout(std::time::Duration::from_secs(10)).send().await.map_err(|e| e.to_string())?;
    Ok(format!("Reachable: HTTP {}", response.status().as_u16()))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![store_secret, load_secret, folder_manifest, probe_endpoint])
        .run(tauri::generate_context!())
        .expect("error while running MemoryWorks desktop bridge");
}
