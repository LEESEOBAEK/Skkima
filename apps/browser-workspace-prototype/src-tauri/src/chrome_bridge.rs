use crate::atomic_file::atomic_write;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager};

const BRIDGE_PORT: u16 = 3217;
const MAX_REQUEST_BYTES: usize = 96 * 1024;
const MAX_SELECTED_TEXT_BYTES: usize = 8 * 1024;
const MAX_BODY_EXCERPT_BYTES: usize = 6 * 1024;
const MAX_HEADINGS: usize = 24;
const MAX_LINKS: usize = 24;
const MAX_HISTORY_ENTRIES: usize = 20;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeBridgeLink {
    pub text: String,
    pub url: String,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeBridgeContext {
    pub schema_version: String,
    pub source: String,
    pub captured_at: String,
    pub page_url: String,
    pub page_title: String,
    pub selected_text: String,
    pub headings: Vec<String>,
    pub links: Vec<ChromeBridgeLink>,
    pub body_excerpt: Option<String>,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeBridgeSnapshot {
    pub received_at: String,
    pub context: ChromeBridgeContext,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeBridgeConnection {
    pub endpoint: String,
    pub status: String,
    pub detail: String,
    pub browser: Option<String>,
    pub websocket_debugger_url: Option<String>,
    pub checked_at: String,
}

static SERVER_STARTED: OnceLock<()> = OnceLock::new();
static HISTORY_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

fn now_iso_like() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_owned())
}

fn snapshot_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("Unable to resolve local app storage: {error}"))?
        .join("chrome-bridge")
        .join("latest-context.json"))
}

fn history_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("Unable to resolve local app storage: {error}"))?
        .join("chrome-bridge")
        .join("context-history.json"))
}

fn ensure_storage_parent(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("Unable to prepare Chrome Bridge storage: {error}"))?;
    }
    Ok(())
}

fn read_history(
    history_path: &Path,
    latest_path: &Path,
) -> Result<Vec<ChromeBridgeSnapshot>, String> {
    if history_path.exists() {
        let bytes = fs::read(history_path)
            .map_err(|error| format!("Unable to read Chrome Bridge history: {error}"))?;
        return serde_json::from_slice(&bytes)
            .map_err(|error| format!("Chrome Bridge history is invalid: {error}"));
    }

    // Migrate the existing single-record store the first time history is used.
    if latest_path.exists() {
        let bytes = fs::read(latest_path)
            .map_err(|error| format!("Unable to read Chrome Bridge snapshot: {error}"))?;
        let snapshot = serde_json::from_slice(&bytes)
            .map_err(|error| format!("Chrome Bridge snapshot is invalid: {error}"))?;
        return Ok(vec![snapshot]);
    }

    Ok(Vec::new())
}

fn prepend_history(history: &mut Vec<ChromeBridgeSnapshot>, snapshot: ChromeBridgeSnapshot) {
    history.insert(0, snapshot);
    history.truncate(MAX_HISTORY_ENTRIES);
}

fn remove_history_record(
    history: &mut Vec<ChromeBridgeSnapshot>,
    received_at: &str,
    captured_at: &str,
    page_url: &str,
) -> bool {
    let Some(index) = history.iter().position(|snapshot| {
        snapshot.received_at == received_at
            && snapshot.context.captured_at == captured_at
            && snapshot.context.page_url == page_url
    }) else {
        return false;
    };

    history.remove(index);
    true
}

fn write_history_storage(
    history_path: &Path,
    latest_path: &Path,
    history: &[ChromeBridgeSnapshot],
) -> Result<(), String> {
    ensure_storage_parent(history_path)?;
    let history_bytes = serde_json::to_vec_pretty(history)
        .map_err(|error| format!("Unable to serialize Chrome Bridge history: {error}"))?;
    atomic_write(history_path, &history_bytes)
        .map_err(|error| format!("Unable to store Chrome Bridge history: {error}"))?;

    if let Some(snapshot) = history.first() {
        let latest_bytes = serde_json::to_vec_pretty(snapshot)
            .map_err(|error| format!("Unable to serialize Chrome Bridge payload: {error}"))?;
        atomic_write(latest_path, &latest_bytes)
            .map_err(|error| format!("Unable to store Chrome Bridge payload: {error}"))?;
    } else if latest_path.exists() {
        fs::remove_file(latest_path)
            .map_err(|error| format!("Unable to clear Chrome Bridge snapshot: {error}"))?;
    }
    Ok(())
}

fn validate_context(context: &ChromeBridgeContext) -> Result<(), String> {
    if context.schema_version != "0.1.0" {
        return Err("Unsupported Chrome Bridge schema version".to_owned());
    }
    if context.source != "skkima-chrome-bridge" {
        return Err("Unknown Chrome Bridge source".to_owned());
    }
    if !(context.page_url.starts_with("http://") || context.page_url.starts_with("https://")) {
        return Err("Only http and https page URLs are accepted".to_owned());
    }
    if context.page_title.len() > 512
        || context.selected_text.len() > MAX_SELECTED_TEXT_BYTES
        || context
            .body_excerpt
            .as_ref()
            .is_some_and(|value| value.len() > MAX_BODY_EXCERPT_BYTES)
        || context.headings.len() > MAX_HEADINGS
        || context.links.len() > MAX_LINKS
    {
        return Err("Chrome Bridge payload exceeds the read-only size limits".to_owned());
    }
    Ok(())
}

fn write_response(stream: &mut TcpStream, status: &str, body: &str) {
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: content-type\r\nConnection: close\r\n\r\n{body}",
        body.as_bytes().len()
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.shutdown(Shutdown::Both);
}

fn write_empty_response(stream: &mut TcpStream, status: &str) {
    let response = format!(
        "HTTP/1.1 {status}\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: content-type\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.shutdown(Shutdown::Both);
}

fn json_response(message: &str) -> String {
    serde_json::json!({ "status": "error", "message": message }).to_string()
}

fn read_request(stream: &mut TcpStream) -> Result<(String, String, Vec<u8>), String> {
    stream
        .set_read_timeout(Some(REQUEST_TIMEOUT))
        .map_err(|error| format!("Unable to set bridge read timeout: {error}"))?;
    let mut request = Vec::new();
    let mut header_end = None;
    while header_end.is_none() && request.len() < MAX_REQUEST_BYTES {
        let mut chunk = [0_u8; 4096];
        let read = stream
            .read(&mut chunk)
            .map_err(|error| format!("Unable to read bridge request: {error}"))?;
        if read == 0 {
            break;
        }
        request.extend_from_slice(&chunk[..read]);
        header_end = request.windows(4).position(|window| window == b"\r\n\r\n");
    }
    let header_end = header_end.ok_or_else(|| "Invalid bridge request headers".to_owned())?;
    let header_text = String::from_utf8_lossy(&request[..header_end]);
    let mut request_parts = header_text
        .lines()
        .next()
        .unwrap_or_default()
        .split_whitespace();
    let method = request_parts.next().unwrap_or_default().to_owned();
    let path = request_parts.next().unwrap_or_default().to_owned();
    let content_length = header_text
        .lines()
        .find_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .unwrap_or(0);
    let body_start = header_end + 4;
    let total_length = body_start
        .checked_add(content_length)
        .ok_or_else(|| "Invalid bridge request size".to_owned())?;
    if total_length > MAX_REQUEST_BYTES {
        return Err("Bridge request is too large".to_owned());
    }
    while request.len() < total_length {
        let mut chunk = [0_u8; 4096];
        let read = stream
            .read(&mut chunk)
            .map_err(|error| format!("Unable to read bridge request body: {error}"))?;
        if read == 0 {
            break;
        }
        request.extend_from_slice(&chunk[..read]);
    }
    if request.len() < total_length {
        return Err("Bridge request body was incomplete".to_owned());
    }
    Ok((method, path, request[body_start..total_length].to_vec()))
}

fn handle_connection(mut stream: TcpStream, app: AppHandle) {
    let _ = stream.set_write_timeout(Some(REQUEST_TIMEOUT));
    let request = match read_request(&mut stream) {
        Ok(request) => request,
        Err(error) => {
            write_response(&mut stream, "400 Bad Request", &json_response(&error));
            return;
        }
    };
    let (method, path, body) = request;
    if method == "GET" && path == "/api/chrome-context/health" {
        write_response(
            &mut stream,
            "200 OK",
            &serde_json::json!({
                "status": "ready",
                "service": "skkima-chrome-bridge",
                "schemaVersion": "0.1.0",
                "readOnly": true,
                "port": BRIDGE_PORT
            })
            .to_string(),
        );
        return;
    }
    if method == "OPTIONS" && path == "/api/chrome-context" {
        write_empty_response(&mut stream, "204 No Content");
        return;
    }
    if method != "POST" || path != "/api/chrome-context" {
        write_response(
            &mut stream,
            "404 Not Found",
            &json_response("Bridge endpoint not found"),
        );
        return;
    }
    let context = match serde_json::from_slice::<ChromeBridgeContext>(&body) {
        Ok(context) => context,
        Err(error) => {
            write_response(
                &mut stream,
                "400 Bad Request",
                &json_response(&format!("Invalid Chrome Bridge payload: {error}")),
            );
            return;
        }
    };
    if let Err(error) = validate_context(&context) {
        write_response(
            &mut stream,
            "422 Unprocessable Entity",
            &json_response(&error),
        );
        return;
    }
    let snapshot = ChromeBridgeSnapshot {
        received_at: now_iso_like(),
        context,
    };
    let path = match snapshot_path(&app) {
        Ok(path) => path,
        Err(error) => {
            write_response(
                &mut stream,
                "500 Internal Server Error",
                &json_response(&error),
            );
            return;
        }
    };
    let history_path = match history_path(&app) {
        Ok(path) => path,
        Err(error) => {
            write_response(
                &mut stream,
                "500 Internal Server Error",
                &json_response(&error),
            );
            return;
        }
    };
    if let Err(error) = ensure_storage_parent(&path) {
        write_response(
            &mut stream,
            "500 Internal Server Error",
            &json_response(&error),
        );
        return;
    }
    let _history_guard = match HISTORY_LOCK.get_or_init(|| Mutex::new(())).lock() {
        Ok(guard) => guard,
        Err(_) => {
            write_response(
                &mut stream,
                "500 Internal Server Error",
                &json_response("Chrome Bridge history lock is unavailable"),
            );
            return;
        }
    };
    let mut history = match read_history(&history_path, &path) {
        Ok(history) => history,
        Err(error) => {
            write_response(
                &mut stream,
                "500 Internal Server Error",
                &json_response(&error),
            );
            return;
        }
    };
    prepend_history(&mut history, snapshot.clone());
    if let Err(error) = write_history_storage(&history_path, &path, &history) {
        write_response(
            &mut stream,
            "500 Internal Server Error",
            &json_response(&error),
        );
        return;
    }
    write_response(
        &mut stream,
        "200 OK",
        &serde_json::json!({ "status": "accepted", "receivedAt": snapshot.received_at })
            .to_string(),
    );
}

pub fn start(app: AppHandle) {
    if SERVER_STARTED.set(()).is_err() {
        return;
    }
    thread::spawn(move || {
        let listener = match TcpListener::bind(("127.0.0.1", BRIDGE_PORT)) {
            Ok(listener) => listener,
            Err(_) => return,
        };
        for stream in listener.incoming().flatten() {
            let app = app.clone();
            thread::spawn(move || handle_connection(stream, app));
        }
    });
}

#[tauri::command]
pub fn inspect_chrome_bridge_connection() -> Result<ChromeBridgeConnection, String> {
    Ok(ChromeBridgeConnection {
        endpoint: format!("http://127.0.0.1:{BRIDGE_PORT}"),
        status: "connected".to_owned(),
        detail:
            "Skkima Chrome Bridge is ready and waiting for read-only Chrome extension handoffs."
                .to_owned(),
        browser: None,
        websocket_debugger_url: None,
        checked_at: now_iso_like(),
    })
}

#[tauri::command]
pub fn get_chrome_bridge_snapshot(app: AppHandle) -> Result<Option<ChromeBridgeSnapshot>, String> {
    let path = snapshot_path(&app)?;
    if !path.exists() {
        return Ok(None);
    }
    let bytes = fs::read(&path)
        .map_err(|error| format!("Unable to read Chrome Bridge snapshot: {error}"))?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|error| format!("Chrome Bridge snapshot is invalid: {error}"))
}

#[tauri::command]
pub fn get_chrome_bridge_history(app: AppHandle) -> Result<Vec<ChromeBridgeSnapshot>, String> {
    let history = history_path(&app)?;
    let latest = snapshot_path(&app)?;
    read_history(&history, &latest)
}

#[tauri::command]
pub fn delete_chrome_bridge_context_record(
    app: AppHandle,
    received_at: String,
    captured_at: String,
    page_url: String,
) -> Result<Vec<ChromeBridgeSnapshot>, String> {
    if received_at.trim().is_empty() || captured_at.trim().is_empty() || page_url.trim().is_empty()
    {
        return Err("Chrome Bridge record key is required".to_owned());
    }

    let history_path = history_path(&app)?;
    let latest_path = snapshot_path(&app)?;
    let _history_guard = HISTORY_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .map_err(|_| "Chrome Bridge history lock is unavailable".to_owned())?;
    let mut history = read_history(&history_path, &latest_path)?;
    if !remove_history_record(&mut history, &received_at, &captured_at, &page_url) {
        return Err("Chrome Bridge record was not found".to_owned());
    }
    write_history_storage(&history_path, &latest_path, &history)?;
    Ok(history)
}

#[tauri::command]
pub fn clear_chrome_bridge_context_history(app: AppHandle) -> Result<(), String> {
    let history_path = history_path(&app)?;
    let latest_path = snapshot_path(&app)?;
    let _history_guard = HISTORY_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .map_err(|_| "Chrome Bridge history lock is unavailable".to_owned())?;
    write_history_storage(&history_path, &latest_path, &[])
}

#[cfg(test)]
mod tests {
    use super::{
        prepend_history, remove_history_record, validate_context, ChromeBridgeContext,
        ChromeBridgeLink, ChromeBridgeSnapshot, MAX_HISTORY_ENTRIES,
    };

    fn context() -> ChromeBridgeContext {
        ChromeBridgeContext {
            schema_version: "0.1.0".to_owned(),
            source: "skkima-chrome-bridge".to_owned(),
            captured_at: "2026-08-02T00:00:00Z".to_owned(),
            page_url: "https://example.com/".to_owned(),
            page_title: "Example".to_owned(),
            selected_text: "Selected".to_owned(),
            headings: vec!["Example Domain".to_owned()],
            links: vec![ChromeBridgeLink {
                text: "More".to_owned(),
                url: "https://example.com/more".to_owned(),
            }],
            body_excerpt: None,
        }
    }

    #[test]
    fn accepts_read_only_http_context() {
        assert!(validate_context(&context()).is_ok());
    }

    #[test]
    fn rejects_non_web_pages() {
        let mut value = context();
        value.page_url = "chrome://settings".to_owned();
        assert!(validate_context(&value).is_err());
    }

    #[test]
    fn rejects_unknown_sources() {
        let mut value = context();
        value.source = "unknown".to_owned();
        assert!(validate_context(&value).is_err());
    }

    #[test]
    fn history_is_newest_first_and_bounded() {
        let mut history = Vec::new();
        for index in 0..(MAX_HISTORY_ENTRIES + 3) {
            let mut value = context();
            value.page_title = format!("Page {index}");
            prepend_history(
                &mut history,
                ChromeBridgeSnapshot {
                    received_at: index.to_string(),
                    context: value,
                },
            );
        }

        assert_eq!(history.len(), MAX_HISTORY_ENTRIES);
        assert_eq!(
            history[0].received_at,
            (MAX_HISTORY_ENTRIES + 2).to_string()
        );
        assert_eq!(history[MAX_HISTORY_ENTRIES - 1].received_at, "3");
    }

    #[test]
    fn removes_only_the_matching_history_record() {
        let mut first = context();
        first.page_title = "First".to_owned();
        let mut second = context();
        second.page_title = "Second".to_owned();
        second.captured_at = "2026-08-02T00:01:00Z".to_owned();
        second.page_url = "https://example.com/second".to_owned();
        let mut history = vec![
            ChromeBridgeSnapshot {
                received_at: "received-2".to_owned(),
                context: second,
            },
            ChromeBridgeSnapshot {
                received_at: "received-1".to_owned(),
                context: first,
            },
        ];

        assert!(remove_history_record(
            &mut history,
            "received-2",
            "2026-08-02T00:01:00Z",
            "https://example.com/second"
        ));
        assert_eq!(history.len(), 1);
        assert_eq!(history[0].received_at, "received-1");
        assert!(!remove_history_record(
            &mut history,
            "received-2",
            "2026-08-02T00:01:00Z",
            "https://example.com/second"
        ));
    }
}
