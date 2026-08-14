use serde::Serialize;
use serde_json::Value;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const CONNECTION_TIMEOUT: Duration = Duration::from_millis(700);
const MAX_RESPONSE_BYTES: usize = 128 * 1024;

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChromeDevtoolsConnection {
    pub endpoint: String,
    pub status: String,
    pub detail: String,
    pub browser: Option<String>,
    pub websocket_debugger_url: Option<String>,
    pub checked_at: String,
}

fn checked_at() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_owned())
}

fn parse_endpoint(endpoint: &str) -> Result<(String, u16, String), String> {
    let value = endpoint.trim().trim_end_matches('/');
    let authority = value
        .strip_prefix("http://")
        .ok_or_else(|| "Chrome DevTools 연결은 http:// 로컬 주소만 지원합니다.".to_owned())?;
    let (host_port, path) = authority.split_once('/').map_or(
        (authority, "/json/version".to_owned()),
        |(host_port, path)| (host_port, format!("/{path}")),
    );

    let (host, port) = if let Some(value) = host_port.strip_prefix('[') {
        let (host, port) = value
            .split_once(']')
            .and_then(|(host, rest)| rest.strip_prefix(':').map(|port| (host, port)))
            .ok_or_else(|| "연결 주소의 포트 형식이 올바르지 않습니다.".to_owned())?;
        (format!("[{host}]"), port)
    } else {
        let (host, port) = host_port
            .rsplit_once(':')
            .ok_or_else(|| "연결 주소에 포트가 필요합니다. 예: http://127.0.0.1:9222".to_owned())?;
        (host.to_owned(), port)
    };

    let host_without_brackets = host.trim_matches(['[', ']']);
    if !matches!(host_without_brackets, "127.0.0.1" | "localhost" | "::1") {
        return Err("보안을 위해 localhost, 127.0.0.1 또는 ::1만 확인할 수 있습니다.".to_owned());
    }

    let port = port
        .parse::<u16>()
        .map_err(|_| "연결 주소의 포트가 올바르지 않습니다.".to_owned())?;
    if port == 0 {
        return Err("연결 주소의 포트가 올바르지 않습니다.".to_owned());
    }

    let path = if path.is_empty() {
        "/json/version".to_owned()
    } else {
        path
    };
    if path != "/json/version" {
        return Err("Chrome DevTools 연결 확인은 /json/version 주소만 지원합니다.".to_owned());
    }
    Ok((host, port, path.to_owned()))
}

fn parse_version_response(
    response: &[u8],
) -> Result<(String, Option<String>, Option<String>), String> {
    let response = String::from_utf8_lossy(response);
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| "Chrome DevTools 응답 형식을 읽지 못했습니다.".to_owned())?;
    let status_line = headers.lines().next().unwrap_or_default();
    if !status_line.contains(" 200 ") {
        return Err(format!(
            "Chrome DevTools가 응답하지 않았습니다: {status_line}"
        ));
    }
    let value: Value = serde_json::from_str(body)
        .map_err(|_| "Chrome DevTools 응답이 JSON 형식이 아닙니다.".to_owned())?;
    let browser = value
        .get("Browser")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let websocket_debugger_url = value
        .get("webSocketDebuggerUrl")
        .and_then(Value::as_str)
        .map(str::to_owned);
    Ok((status_line.to_owned(), browser, websocket_debugger_url))
}

fn response_body_length(response: &[u8]) -> Option<usize> {
    let header_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")?;
    let headers = String::from_utf8_lossy(&response[..header_end]);
    headers.lines().find_map(|line| {
        let (name, value) = line.split_once(':')?;
        if name.trim().eq_ignore_ascii_case("content-length") {
            value.trim().parse::<usize>().ok()
        } else {
            None
        }
    })
}

fn read_http_response(stream: &mut TcpStream) -> Result<Vec<u8>, String> {
    let mut response = Vec::new();
    let mut chunk = [0_u8; 4096];

    while response.len() < MAX_RESPONSE_BYTES {
        let read = stream
            .read(&mut chunk)
            .map_err(|_| "Chrome DevTools 응답을 읽지 못했습니다.".to_owned())?;
        if read == 0 {
            break;
        }
        response.extend_from_slice(&chunk[..read]);

        let Some(header_end) = response.windows(4).position(|window| window == b"\r\n\r\n") else {
            continue;
        };
        let body_start = header_end + 4;
        if let Some(expected_length) = response_body_length(&response) {
            if response.len().saturating_sub(body_start) >= expected_length {
                break;
            }
        } else if serde_json::from_slice::<Value>(&response[body_start..]).is_ok() {
            // Chrome normally sends Content-Length, but accepting a complete
            // JSON body also handles compatible local DevTools endpoints.
            break;
        }
    }

    if response.is_empty() {
        Err("Chrome DevTools 응답을 읽지 못했습니다.".to_owned())
    } else {
        Ok(response)
    }
}

#[tauri::command]
pub fn inspect_chrome_devtools_connection(
    endpoint: String,
) -> Result<ChromeDevtoolsConnection, String> {
    let (host, port, path) = parse_endpoint(&endpoint)?;
    let address = format!("{host}:{port}");
    let socket = address
        .to_socket_addrs()
        .map_err(|_| "로컬 Chrome DevTools 주소를 확인하지 못했습니다.".to_owned())?
        .next()
        .ok_or_else(|| "로컬 Chrome DevTools 주소를 확인하지 못했습니다.".to_owned())?;
    let mut stream = TcpStream::connect_timeout(&socket, CONNECTION_TIMEOUT).map_err(|_| {
        "연결되지 않았습니다. Chrome을 원격 디버깅 모드로 실행했는지 확인하세요.".to_owned()
    })?;
    stream
        .set_read_timeout(Some(CONNECTION_TIMEOUT))
        .map_err(|_| "Chrome DevTools 응답 대기 시간을 설정하지 못했습니다.".to_owned())?;
    stream
        .set_write_timeout(Some(CONNECTION_TIMEOUT))
        .map_err(|_| "Chrome DevTools 요청 시간을 설정하지 못했습니다.".to_owned())?;

    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|_| "Chrome DevTools 요청을 보내지 못했습니다.".to_owned())?;

    let response = read_http_response(&mut stream)?;

    let (_, browser, websocket_debugger_url) = parse_version_response(&response)?;
    let detail = if websocket_debugger_url.is_some() {
        "별도 Chrome의 DevTools 연결을 확인했습니다. 현재는 읽기 전용 연결 상태만 확인합니다."
            .to_owned()
    } else {
        "Chrome DevTools 응답은 받았지만 디버거 세션 주소가 없습니다.".to_owned()
    };
    let status = if websocket_debugger_url.is_some() {
        "connected"
    } else {
        "needs_setup"
    };

    Ok(ChromeDevtoolsConnection {
        endpoint: format!("http://{host}:{port}"),
        status: status.to_owned(),
        detail,
        browser,
        websocket_debugger_url,
        checked_at: checked_at(),
    })
}

#[cfg(test)]
mod tests {
    use super::{parse_endpoint, parse_version_response};

    #[test]
    fn accepts_loopback_devtools_endpoint() {
        assert_eq!(
            parse_endpoint("http://127.0.0.1:9222").unwrap(),
            ("127.0.0.1".to_owned(), 9222, "/json/version".to_owned())
        );
    }

    #[test]
    fn rejects_remote_or_non_http_endpoint() {
        assert!(parse_endpoint("https://127.0.0.1:9222").is_err());
        assert!(parse_endpoint("http://example.com:9222").is_err());
    }

    #[test]
    fn parses_chrome_version_response() {
        let response = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"Browser\":\"Chrome/128.0\",\"webSocketDebuggerUrl\":\"ws://127.0.0.1:9222/devtools/browser/abc\"}";
        let parsed = parse_version_response(response).unwrap();
        assert_eq!(parsed.1.as_deref(), Some("Chrome/128.0"));
        assert!(parsed.2.is_some());
    }
}
