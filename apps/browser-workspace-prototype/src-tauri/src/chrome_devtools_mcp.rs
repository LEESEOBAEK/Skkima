use crate::atomic_file::atomic_write;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const MCP_REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_MESSAGE_BYTES: usize = 4 * 1024 * 1024;
const MAX_SNAPSHOT_BYTES: usize = 512 * 1024;
const MCP_CLIENT_VERSION: &str = "0.1.1";
const READ_ONLY_TOOLS: [&str; 2] = ["list_pages", "take_snapshot"];

#[derive(Debug)]
struct McpMessage {
    value: Value,
}

struct McpSession {
    child: Child,
    stdin: ChildStdin,
    messages: Receiver<Result<McpMessage, String>>,
    next_id: u64,
    endpoint: String,
    server_info: Option<Value>,
    tools: Vec<McpToolSummary>,
}

impl Drop for McpSession {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

static MCP_SESSION: OnceLock<Mutex<Option<McpSession>>> = OnceLock::new();

fn session_store() -> &'static Mutex<Option<McpSession>> {
    MCP_SESSION.get_or_init(|| Mutex::new(None))
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct McpToolSummary {
    pub name: String,
    pub description: Option<String>,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeDevtoolsMcpConnection {
    pub status: String,
    pub endpoint: String,
    pub server_info: Option<Value>,
    pub tools: Vec<McpToolSummary>,
    pub read_only_tools: Vec<String>,
    pub detail: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeDevtoolsMcpReadResult {
    pub status: String,
    pub endpoint: String,
    pub captured_at: String,
    pub pages: Value,
    pub snapshot_text: String,
    pub detail: String,
    pub source: String,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeDevtoolsMcpEvidence {
    pub schema_version: String,
    pub evidence_id: String,
    pub captured_at: String,
    pub endpoint: String,
    pub pages: Value,
    pub snapshot_text: String,
    pub project_id: Option<String>,
    pub project_name: String,
    pub session_id: Option<String>,
    pub session_name: String,
    pub source: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeDevtoolsMcpEvidenceSaveResult {
    pub status: String,
    pub evidence_id: String,
    pub relative_path: String,
    pub sha256: String,
    pub saved_at: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeDebugSessionLaunchResult {
    pub status: String,
    pub executable: String,
    pub profile_path: String,
    pub endpoint: String,
    pub port: u16,
    pub detail: String,
}

const CHROME_DEBUG_PORT: u16 = 9222;

#[cfg(windows)]
fn chrome_executable_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA") {
        candidates.push(
            PathBuf::from(local_app_data)
                .join("Google")
                .join("Chrome")
                .join("Application")
                .join("chrome.exe"),
        );
    }
    if let Some(program_files) = std::env::var_os("PROGRAMFILES") {
        candidates.push(
            PathBuf::from(program_files)
                .join("Google")
                .join("Chrome")
                .join("Application")
                .join("chrome.exe"),
        );
    }
    if let Some(program_files_x86) = std::env::var_os("PROGRAMFILES(X86)") {
        candidates.push(
            PathBuf::from(program_files_x86)
                .join("Google")
                .join("Chrome")
                .join("Application")
                .join("chrome.exe"),
        );
    }
    candidates
}

#[cfg(not(windows))]
fn chrome_executable_candidates() -> Vec<PathBuf> {
    Vec::new()
}

#[cfg(windows)]
fn locate_chrome_executable() -> Result<PathBuf, String> {
    chrome_executable_candidates()
        .into_iter()
        .find(|candidate| candidate.is_file())
        .ok_or_else(|| {
            "Google Chrome를 찾지 못했습니다. Chrome을 설치한 뒤 다시 시도해 주세요.".to_owned()
        })
}

#[cfg(not(windows))]
fn locate_chrome_executable() -> Result<PathBuf, String> {
    Err("디버깅 Chrome 시작은 현재 Windows 앱에서만 지원합니다.".to_owned())
}

fn chrome_debug_profile_path() -> Result<PathBuf, String> {
    Ok(crate::profile::app_data_root()?.join("chrome-devtools-profile"))
}

fn chrome_debug_endpoint() -> String {
    format!("http://127.0.0.1:{CHROME_DEBUG_PORT}")
}

fn chrome_debug_port_is_open() -> bool {
    std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], CHROME_DEBUG_PORT)),
        Duration::from_millis(200),
    )
    .is_ok()
}

fn chrome_debug_arguments(profile_path: &Path) -> Vec<String> {
    vec![
        format!("--remote-debugging-port={CHROME_DEBUG_PORT}"),
        format!("--user-data-dir={}", profile_path.display()),
        "--no-first-run".to_owned(),
        "--no-default-browser-check".to_owned(),
        "--new-window".to_owned(),
        "about:blank".to_owned(),
    ]
}

#[tauri::command]
pub fn launch_chrome_debug_session() -> Result<ChromeDebugSessionLaunchResult, String> {
    let executable = locate_chrome_executable()?;
    let profile_path = chrome_debug_profile_path()?;
    let endpoint = chrome_debug_endpoint();

    fs::create_dir_all(&profile_path)
        .map_err(|error| format!("Skkima 전용 Chrome 프로필을 만들지 못했습니다: {error}"))?;

    if chrome_debug_port_is_open() {
        return Ok(ChromeDebugSessionLaunchResult {
            status: "already_running".to_owned(),
            executable: executable.display().to_string(),
            profile_path: profile_path.display().to_string(),
            endpoint,
            port: CHROME_DEBUG_PORT,
            detail: "디버깅 Chrome이 이미 실행 중입니다. 기존 창을 그대로 사용합니다.".to_owned(),
        });
    }

    let mut command = Command::new(&executable);
    command.args(chrome_debug_arguments(&profile_path));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
        .spawn()
        .map_err(|error| format!("디버깅 Chrome을 시작하지 못했습니다: {error}"))?;

    Ok(ChromeDebugSessionLaunchResult {
        status: "started".to_owned(),
        executable: executable.display().to_string(),
        profile_path: profile_path.display().to_string(),
        endpoint,
        port: CHROME_DEBUG_PORT,
        detail: "Skkima 전용 디버깅 Chrome을 열었습니다. 로그인 후 브라우저 연결을 확인하세요."
            .to_owned(),
    })
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn now_iso() -> String {
    // The desktop application already treats captured_at as an opaque audit value.
    // Keep this dependency-free and sortable for the local evidence archive.
    now_millis().to_string()
}

fn validate_endpoint(endpoint: &str) -> Result<String, String> {
    let trimmed = endpoint.trim().trim_end_matches('/');
    let authority = trimmed
        .strip_prefix("http://")
        .ok_or_else(|| "MCP 연결은 http:// 로컬 주소만 지원합니다.".to_owned())?;
    if authority.contains('/') || authority.contains('?') || authority.contains('#') {
        return Err("MCP 연결 주소에는 호스트와 포트만 입력해 주세요.".to_owned());
    }
    let (host, port, display_host) = if let Some(value) = authority.strip_prefix('[') {
        let (host, port) = value
            .split_once("]:")
            .ok_or_else(|| "연결 주소에 포트가 필요합니다. 예: http://[::1]:9222".to_owned())?;
        (host, port, format!("[{host}]"))
    } else {
        let (host, port) = authority
            .rsplit_once(':')
            .ok_or_else(|| "연결 주소에 포트가 필요합니다. 예: http://127.0.0.1:9222".to_owned())?;
        (host, port, host.to_owned())
    };
    if !matches!(host, "127.0.0.1" | "localhost" | "::1") {
        return Err("보안을 위해 localhost, 127.0.0.1 또는 ::1만 연결할 수 있습니다.".to_owned());
    }
    let port = port
        .parse::<u16>()
        .map_err(|_| "연결 주소의 포트가 올바르지 않습니다.".to_owned())?;
    if port == 0 {
        return Err("연결 주소의 포트가 올바르지 않습니다.".to_owned());
    }
    Ok(format!("http://{display_host}:{port}"))
}

fn spawn_reader(stdout: ChildStdout) -> Receiver<Result<McpMessage, String>> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) => {
                    let _ = sender.send(Err(
                        "Chrome DevTools MCP 프로세스가 종료되었습니다.".to_owned()
                    ));
                    break;
                }
                Ok(_) => {
                    if line.len() > MAX_MESSAGE_BYTES {
                        let _ =
                            sender.send(Err("MCP 응답이 허용된 크기를 초과했습니다.".to_owned()));
                        break;
                    }
                    if line.trim().is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<Value>(line.trim()) {
                        Ok(value) => {
                            if sender.send(Ok(McpMessage { value })).is_err() {
                                break;
                            }
                        }
                        Err(error) => {
                            let _ = sender
                                .send(Err(format!("MCP JSON-RPC 응답을 읽지 못했습니다: {error}")));
                            break;
                        }
                    }
                }
                Err(error) => {
                    let _ = sender.send(Err(format!("MCP 응답을 읽지 못했습니다: {error}")));
                    break;
                }
            }
        }
    });
    receiver
}

fn spawn_stderr(stderr: impl Read + Send + 'static) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut buffer = String::new();
        let _ = reader.read_to_string(&mut buffer);
    });
}

fn spawn_mcp_process(
    endpoint: &str,
) -> Result<(Child, ChildStdin, Receiver<Result<McpMessage, String>>), String> {
    let executable = if cfg!(windows) { "npx.cmd" } else { "npx" };
    let mut command = Command::new(executable);
    command
        .arg("--yes")
        .arg("chrome-devtools-mcp@latest")
        .arg(format!("--browser-url={endpoint}"))
        .arg("--no-category-emulation")
        .arg("--no-category-performance")
        .arg("--no-category-network")
        .arg("--no-usage-statistics")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|error| {
        format!("Chrome DevTools MCP를 시작하지 못했습니다. npx 설치와 PATH를 확인하세요: {error}")
    })?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "MCP 입력 스트림을 만들지 못했습니다.".to_owned())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "MCP 출력 스트림을 만들지 못했습니다.".to_owned())?;
    if let Some(stderr) = child.stderr.take() {
        spawn_stderr(stderr);
    }
    let messages = spawn_reader(stdout);
    Ok((child, stdin, messages))
}

fn response_for(value: &Value, id: u64) -> Option<Result<Value, String>> {
    if value.get("id").and_then(Value::as_u64) != Some(id) {
        return None;
    }
    if let Some(error) = value.get("error") {
        return Some(Err(format!("MCP 요청이 실패했습니다: {error}")));
    }
    Some(Ok(value.get("result").cloned().unwrap_or(Value::Null)))
}

impl McpSession {
    fn send_request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id;
        self.next_id = self.next_id.saturating_add(1);
        let request = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        let mut bytes = serde_json::to_vec(&request)
            .map_err(|error| format!("MCP 요청을 직렬화하지 못했습니다: {error}"))?;
        bytes.push(b'\n');
        self.stdin
            .write_all(&bytes)
            .and_then(|_| self.stdin.flush())
            .map_err(|error| format!("MCP 요청을 보내지 못했습니다: {error}"))?;

        loop {
            let message = self
                .messages
                .recv_timeout(MCP_REQUEST_TIMEOUT)
                .map_err(|error| {
                    format!("MCP 응답을 기다리는 중 시간 초과 또는 연결 종료: {error}")
                })??;
            if let Some(response) = response_for(&message.value, id) {
                return response;
            }
        }
    }

    fn send_notification(&mut self, method: &str, params: Value) -> Result<(), String> {
        let request = json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        });
        let mut bytes = serde_json::to_vec(&request)
            .map_err(|error| format!("MCP 알림을 직렬화하지 못했습니다: {error}"))?;
        bytes.push(b'\n');
        self.stdin
            .write_all(&bytes)
            .and_then(|_| self.stdin.flush())
            .map_err(|error| format!("MCP 초기화 알림을 보내지 못했습니다: {error}"))
    }
}

fn tool_summaries(result: &Value) -> Vec<McpToolSummary> {
    result
        .get("tools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|tool| {
            Some(McpToolSummary {
                name: tool.get("name")?.as_str()?.to_owned(),
                description: tool
                    .get("description")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
            })
        })
        .collect()
}

fn require_read_only_tool(session: &McpSession, name: &str) -> Result<(), String> {
    if !READ_ONLY_TOOLS.contains(&name) {
        return Err(format!(
            "현재 단계에서 허용하지 않는 MCP 도구입니다: {name}"
        ));
    }
    if !session.tools.iter().any(|tool| tool.name == name) {
        return Err(format!(
            "MCP 서버가 필요한 읽기 도구를 제공하지 않습니다: {name}"
        ));
    }
    Ok(())
}

fn tool_text(result: &Value) -> String {
    result
        .get("content")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            if item.get("type").and_then(Value::as_str) == Some("text") {
                item.get("text").and_then(Value::as_str)
            } else {
                None
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn initialize_session(endpoint: &str) -> Result<(McpSession, ChromeDevtoolsMcpConnection), String> {
    let endpoint = validate_endpoint(endpoint)?;
    let (child, stdin, messages) = spawn_mcp_process(&endpoint)?;
    let mut session = McpSession {
        child,
        stdin,
        messages,
        next_id: 1,
        endpoint: endpoint.clone(),
        server_info: None,
        tools: Vec::new(),
    };

    let initialized = session.send_request(
        "initialize",
        json!({
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "skkima", "version": MCP_CLIENT_VERSION}
        }),
    );
    let initialized = match initialized {
        Ok(value) => value,
        Err(error) => {
            drop(session);
            return Err(error);
        }
    };
    session.server_info = initialized.get("serverInfo").cloned();
    session.send_notification("notifications/initialized", json!({}))?;
    let tools = session.send_request("tools/list", json!({}))?;
    session.tools = tool_summaries(&tools);
    let available_read_only = READ_ONLY_TOOLS
        .iter()
        .filter(|name| session.tools.iter().any(|tool| &tool.name == *name))
        .map(|name| (*name).to_owned())
        .collect::<Vec<_>>();
    let connection = ChromeDevtoolsMcpConnection {
        status: "connected".to_owned(),
        endpoint: session.endpoint.clone(),
        server_info: session.server_info.clone(),
        tools: session.tools.clone(),
        read_only_tools: available_read_only,
        detail: "Chrome DevTools MCP가 연결되었습니다. 현재는 읽기 도구만 허용합니다.".to_owned(),
    };
    Ok((session, connection))
}

#[tauri::command]
pub async fn start_chrome_devtools_mcp(
    endpoint: String,
) -> Result<ChromeDevtoolsMcpConnection, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let (session, connection) = initialize_session(&endpoint)?;
        let mut store = session_store()
            .lock()
            .map_err(|_| "MCP 세션 잠금이 손상되었습니다.".to_owned())?;
        store.take();
        *store = Some(session);
        Ok(connection)
    })
    .await
    .map_err(|error| format!("MCP 시작 작업이 중단되었습니다: {error}"))?
}

#[tauri::command]
pub fn stop_chrome_devtools_mcp() -> Result<(), String> {
    let mut store = session_store()
        .lock()
        .map_err(|_| "MCP 세션 잠금이 손상되었습니다.".to_owned())?;
    store.take();
    Ok(())
}

#[tauri::command]
pub async fn read_chrome_devtools_mcp() -> Result<ChromeDevtoolsMcpReadResult, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let mut store = session_store()
            .lock()
            .map_err(|_| "MCP 세션 잠금이 손상되었습니다.".to_owned())?;
        let session = store
            .as_mut()
            .ok_or_else(|| "Chrome DevTools MCP가 연결되어 있지 않습니다.".to_owned())?;
        require_read_only_tool(session, "list_pages")?;
        require_read_only_tool(session, "take_snapshot")?;
        let pages =
            session.send_request("tools/call", json!({"name": "list_pages", "arguments": {}}))?;
        let snapshot = session.send_request(
            "tools/call",
            json!({"name": "take_snapshot", "arguments": {}}),
        )?;
        let snapshot_text = tool_text(&snapshot);
        if snapshot_text.len() > MAX_SNAPSHOT_BYTES {
            return Err("페이지 읽기 결과가 허용된 크기를 초과했습니다.".to_owned());
        }
        Ok(ChromeDevtoolsMcpReadResult {
            status: "read".to_owned(),
            endpoint: session.endpoint.clone(),
            captured_at: now_iso(),
            pages,
            snapshot_text,
            detail: "현재 Chrome 탭 목록과 선택된 페이지의 접근성 스냅샷을 읽었습니다.".to_owned(),
            source: "chrome-devtools-mcp-read-only".to_owned(),
        })
    })
    .await
    .map_err(|error| format!("MCP 읽기 작업이 중단되었습니다: {error}"))?
}

fn safe_component(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
        .take(80)
        .collect()
}

#[tauri::command]
pub fn save_chrome_devtools_mcp_evidence(
    project_root: String,
    evidence: ChromeDevtoolsMcpEvidence,
) -> Result<ChromeDevtoolsMcpEvidenceSaveResult, String> {
    if evidence.schema_version != "1.0.0" || evidence.source != "chrome-devtools-mcp-read-only" {
        return Err("Chrome DevTools MCP 근거 계약이 올바르지 않습니다.".to_owned());
    }
    if evidence.snapshot_text.len() > MAX_SNAPSHOT_BYTES {
        return Err("Chrome DevTools MCP 근거가 허용된 크기를 초과했습니다.".to_owned());
    }
    validate_endpoint(&evidence.endpoint)
        .map_err(|_| "로컬 Chrome DevTools MCP 근거만 저장할 수 있습니다.".to_owned())?;
    let root = fs::canonicalize(Path::new(&project_root))
        .map_err(|error| format!("프로젝트 경로를 확인하지 못했습니다: {error}"))?;
    if !root.is_dir() {
        return Err("프로젝트 경로가 폴더가 아닙니다.".to_owned());
    }
    let directory = root.join("outputs").join("web_evidence").join("mcp");
    fs::create_dir_all(&directory)
        .map_err(|error| format!("MCP 근거 폴더를 만들지 못했습니다: {error}"))?;
    let bytes = serde_json::to_vec_pretty(&evidence)
        .map_err(|error| format!("MCP 근거를 직렬화하지 못했습니다: {error}"))?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    let filename = format!(
        "{}_{}.json",
        now_millis(),
        safe_component(&evidence.evidence_id)
    );
    if filename.ends_with("_.json") {
        return Err("MCP 근거 ID가 올바르지 않습니다.".to_owned());
    }
    atomic_write(&directory.join(&filename), &bytes)
        .map_err(|error| format!("MCP 근거를 저장하지 못했습니다: {error}"))?;
    Ok(ChromeDevtoolsMcpEvidenceSaveResult {
        status: "saved".to_owned(),
        evidence_id: evidence.evidence_id,
        relative_path: format!("outputs/web_evidence/mcp/{filename}"),
        sha256,
        saved_at: now_millis().to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::{chrome_debug_arguments, chrome_debug_endpoint, tool_summaries, validate_endpoint};
    use serde_json::json;
    use std::path::Path;

    #[test]
    fn only_accepts_loopback_http_endpoint() {
        assert_eq!(
            validate_endpoint("http://127.0.0.1:9222").unwrap(),
            "http://127.0.0.1:9222"
        );
        assert_eq!(
            validate_endpoint("http://[::1]:9222").unwrap(),
            "http://[::1]:9222"
        );
        assert!(validate_endpoint("https://127.0.0.1:9222").is_err());
        assert!(validate_endpoint("http://example.com:9222").is_err());
        assert!(validate_endpoint("http://127.0.0.1:9222/path").is_err());
    }

    #[test]
    fn only_exposes_named_tools_from_tools_list() {
        let tools = tool_summaries(&json!({
            "tools": [
                {"name": "list_pages", "description": "pages"},
                {"name": "click", "description": "interaction"}
            ]
        }));
        assert_eq!(tools.len(), 2);
        assert_eq!(tools[0].name, "list_pages");
    }

    #[test]
    fn debug_chrome_uses_a_dedicated_profile_and_loopback_port() {
        let arguments = chrome_debug_arguments(Path::new(r"C:\Users\test\Skkima\chrome-profile"));
        assert_eq!(chrome_debug_endpoint(), "http://127.0.0.1:9222");
        assert!(arguments.contains(&"--remote-debugging-port=9222".to_owned()));
        assert!(arguments
            .iter()
            .any(|value| { value == r"--user-data-dir=C:\Users\test\Skkima\chrome-profile" }));
        assert!(arguments.contains(&"--no-first-run".to_owned()));
        assert!(arguments.contains(&"--new-window".to_owned()));
    }
}
