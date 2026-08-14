use serde::{Deserialize, Serialize};
use std::{env, fs, path::PathBuf, process::Command};
#[cfg(windows)]
use std::{sync::mpsc, time::Duration};
use tauri::{
    webview::{NewWindowResponse, WebviewBuilder},
    AppHandle, LogicalPosition, LogicalSize, Manager, Url, WebviewUrl,
};
#[cfg(windows)]
use webview2_com::{CallDevToolsProtocolMethodCompletedHandler, CoTaskMemPWSTR};
#[cfg(windows)]
use windows_sys::Win32::UI::WindowsAndMessaging::{
    SetWindowPos, HWND_TOP, SWP_ASYNCWINDOWPOS, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
};

const BROWSER_LABEL: &str = "browser-workspace";

#[cfg(windows)]
fn raise_browser_webview(webview: &tauri::Webview) -> Result<(), String> {
    let (sender, receiver) = mpsc::sync_channel::<Result<(), String>>(1);
    webview
        .with_webview(move |platform| {
            let result = (|| -> Result<(), String> {
                let mut hwnd = windows::Win32::Foundation::HWND::default();
                unsafe {
                    platform
                        .controller()
                        .ParentWindow(&mut hwnd)
                        .map_err(|error| {
                            format!("브라우저 창 핸들을 가져오지 못했습니다: {error}")
                        })?;
                    if hwnd.0.is_null() {
                        return Err("브라우저 창 핸들이 비어 있습니다.".to_string());
                    }
                    if SetWindowPos(
                        hwnd.0 as _,
                        HWND_TOP,
                        0,
                        0,
                        0,
                        0,
                        SWP_ASYNCWINDOWPOS | SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE,
                    ) == 0
                    {
                        return Err("브라우저 창을 전면으로 올리지 못했습니다.".to_string());
                    }
                }
                Ok(())
            })();
            let _ = sender.send(result);
        })
        .map_err(|error| error.to_string())?;
    receiver
        .recv_timeout(Duration::from_secs(2))
        .map_err(|_| "브라우저 창 순서 갱신이 시간 초과되었습니다.".to_string())?
}

#[cfg(not(windows))]
fn raise_browser_webview(_webview: &tauri::Webview) -> Result<(), String> {
    Ok(())
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BrowserBounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BrowserState {
    created: bool,
    visible: bool,
    url: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExternalBrowserResult {
    browser: String,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserPageCounts {
    buttons: u32,
    links: u32,
    inputs: u32,
    forms: u32,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserPageControl {
    order: u32,
    kind: String,
    label: String,
    input_type: String,
    disabled: bool,
    href: String,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserPageSnapshot {
    schema_version: String,
    captured_at: String,
    title: String,
    url: String,
    counts: BrowserPageCounts,
    has_password_field: bool,
    controls: Vec<BrowserPageControl>,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserClickResult {
    status: String,
    title: String,
    url: String,
    control_index: u32,
    reason: Option<String>,
}

const READ_ONLY_PAGE_EXPRESSION: &str = r#"
(() => {
  const compact = (value, max = 120) => String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
  const safeUrl = (value) => {
    try {
      const url = new URL(String(value ?? ""), location.href);
      if (!/^https?:$/.test(url.protocol)) return "";
      url.username = "";
      url.password = "";
      url.search = "";
      url.hash = "";
      return url.toString();
    } catch {
      return "";
    }
  };
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" &&
      rect.width > 0 && rect.height > 0;
  };
  const labelFor = (element) => {
    const labelledBy = compact(element.getAttribute("aria-labelledby"));
    const labelledText = labelledBy
      ? labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent || "").join(" ")
      : "";
    const nativeLabel = element.labels?.length
      ? Array.from(element.labels).map((label) => label.textContent || "").join(" ")
      : "";
    return compact(
      element.getAttribute("aria-label") ||
      labelledText || nativeLabel || element.getAttribute("title") ||
      element.textContent || element.getAttribute("placeholder") ||
      element.getAttribute("name") || "이름 없음"
    );
  };
  const nodes = Array.from(document.querySelectorAll(
    "button, a[href], input:not([type='hidden']), select, textarea, [role='button']"
  )).filter(visible).slice(0, 60);
  const controls = nodes.map((element, order) => {
    const tag = element.tagName.toLowerCase();
    const kind = tag === "a" ? "link"
      : tag === "input" ? "input"
      : tag === "select" ? "select"
      : tag === "textarea" ? "textarea" : "button";
    return {
      order,
      kind,
      label: labelFor(element),
      inputType: kind === "input" ? compact(element.type || "text", 32) : "",
      disabled: element.disabled === true || element.getAttribute("aria-disabled") === "true",
      href: kind === "link" ? safeUrl(element.href) : ""
    };
  });
  return {
    schemaVersion: "1.0.0",
    capturedAt: new Date().toISOString(),
    title: compact(document.title, 160) || "제목 없음",
    url: safeUrl(location.href),
    counts: {
      buttons: controls.filter((item) => item.kind === "button").length,
      links: controls.filter((item) => item.kind === "link").length,
      inputs: controls.filter((item) => ["input", "select", "textarea"].includes(item.kind)).length,
      forms: document.forms.length
    },
    hasPasswordField: Boolean(document.querySelector("input[type='password']")),
    controls
  };
})()
"#;

fn browser_click_expression(
    control_index: u32,
    expected_kind: &str,
    expected_label: &str,
    expected_url: &str,
) -> Result<String, String> {
    let expected_kind = serde_json::to_string(expected_kind)
        .map_err(|error| format!("클릭 대상 종류를 준비하지 못했습니다: {error}"))?;
    let expected_label = serde_json::to_string(expected_label)
        .map_err(|error| format!("클릭 대상 이름을 준비하지 못했습니다: {error}"))?;
    let expected_url = serde_json::to_string(expected_url)
        .map_err(|error| format!("클릭 대상 주소를 준비하지 못했습니다: {error}"))?;

    Ok(format!(
        r#"
(() => {{
  const expectedIndex = {control_index};
  const expectedKind = {expected_kind};
  const expectedLabel = {expected_label};
  const expectedUrl = {expected_url};
  const compact = (value, max = 120) => String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
  const safeUrl = (value) => {{
    try {{
      const url = new URL(String(value ?? ""), location.href);
      if (!/^https?:$/.test(url.protocol)) return "";
      url.username = "";
      url.password = "";
      url.search = "";
      url.hash = "";
      return url.toString();
    }} catch {{
      return "";
    }}
  }};
  const visible = (element) => {{
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" &&
      rect.width > 0 && rect.height > 0;
  }};
  const labelFor = (element) => {{
    const labelledBy = compact(element.getAttribute("aria-labelledby"));
    const labelledText = labelledBy
      ? labelledBy.split(/\\s+/).map((id) => document.getElementById(id)?.textContent || "").join(" ")
      : "";
    const nativeLabel = element.labels?.length
      ? Array.from(element.labels).map((label) => label.textContent || "").join(" ")
      : "";
    return compact(
      element.getAttribute("aria-label") ||
      labelledText || nativeLabel || element.getAttribute("title") ||
      element.textContent || element.getAttribute("placeholder") ||
      element.getAttribute("name") || "이름 없음"
    );
  }};
  const nodes = Array.from(document.querySelectorAll(
    "button, a[href], input:not([type='hidden']), select, textarea, [role='button']"
  )).filter(visible).slice(0, 60);
  const currentUrl = safeUrl(location.href);
  const blocked = (reason) => ({{
    status: "blocked",
    title: compact(document.title, 160),
    url: currentUrl,
    controlIndex: expectedIndex,
    reason
  }});
  if (currentUrl !== expectedUrl) return blocked("페이지 주소가 읽을 당시와 달라졌습니다.");
  const element = nodes[expectedIndex];
  if (!element) return blocked("승인한 조작 요소를 현재 페이지에서 찾지 못했습니다.");
  const tag = element.tagName.toLowerCase();
  const kind = tag === "a" ? "link" : "button";
  if (kind !== expectedKind) return blocked("조작 요소 종류가 승인 당시와 달라졌습니다.");
  if (element.disabled === true || element.getAttribute("aria-disabled") === "true") {{
    return blocked("현재 조작 요소가 비활성화되어 있습니다.");
  }}
  if (labelFor(element) !== expectedLabel) return blocked("조작 요소 이름이 승인 당시와 달라졌습니다.");
  element.scrollIntoView({{ block: "center", inline: "center" }});
  element.focus({{ preventScroll: true }});
  element.click();
  return {{
    status: "succeeded",
    title: compact(document.title, 160),
    url: safeUrl(location.href),
    controlIndex: expectedIndex,
    reason: null
  }};
}})()
"#
    ))
}

fn parse_cdp_page_snapshot(raw: &str) -> Result<BrowserPageSnapshot, String> {
    let envelope: serde_json::Value = serde_json::from_str(raw)
        .map_err(|error| format!("페이지 읽기 응답이 JSON이 아닙니다: {error}"))?;
    if envelope.get("exceptionDetails").is_some() {
        return Err("페이지 스크립트 실행 중 오류가 발생했습니다.".to_string());
    }
    let value = envelope
        .pointer("/result/value")
        .cloned()
        .ok_or_else(|| "페이지 읽기 응답에 결과 값이 없습니다.".to_string())?;
    serde_json::from_value(value)
        .map_err(|error| format!("페이지 읽기 결과 계약이 올바르지 않습니다: {error}"))
}

fn parse_browser_url(input: &str) -> Result<Url, String> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return Err("이동할 주소를 입력해 주세요.".to_string());
    }
    let candidate = if trimmed.contains("://") {
        trimmed.to_string()
    } else {
        format!("https://{trimmed}")
    };
    let url = Url::parse(&candidate).map_err(|_| "올바른 웹 주소가 아닙니다.".to_string())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("HTTP 또는 HTTPS 주소만 열 수 있습니다.".to_string());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("주소에 계정 정보를 포함할 수 없습니다.".to_string());
    }
    Ok(url)
}

fn checked_bounds(
    bounds: BrowserBounds,
) -> Result<(LogicalPosition<f64>, LogicalSize<f64>), String> {
    let values = [bounds.x, bounds.y, bounds.width, bounds.height];
    if values.iter().any(|value| !value.is_finite()) || bounds.width < 80.0 || bounds.height < 80.0
    {
        return Err("브라우저 표시 영역이 올바르지 않습니다.".to_string());
    }
    Ok((
        LogicalPosition::new(bounds.x.max(0.0), bounds.y.max(0.0)),
        LogicalSize::new(bounds.width, bounds.height),
    ))
}

#[tauri::command]
pub async fn create_browser_workspace(
    app: AppHandle,
    url: String,
    bounds: BrowserBounds,
) -> Result<BrowserState, String> {
    let target = parse_browser_url(&url)?;
    let (position, size) = checked_bounds(bounds)?;

    if let Some(webview) = app.get_webview(BROWSER_LABEL) {
        webview
            .set_position(position)
            .map_err(|error| error.to_string())?;
        webview.set_size(size).map_err(|error| error.to_string())?;
        webview
            .navigate(target)
            .map_err(|error| error.to_string())?;
        webview.show().map_err(|error| error.to_string())?;
        raise_browser_webview(&webview)?;
        webview.set_focus().map_err(|error| error.to_string())?;
        return browser_workspace_state(app);
    }

    let window = app
        .get_window("main")
        .ok_or_else(|| "메인 창을 찾지 못했습니다.".to_string())?;
    let profile_root = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("browser-profile");
    fs::create_dir_all(&profile_root).map_err(|error| error.to_string())?;

    let builder = WebviewBuilder::new(BROWSER_LABEL, WebviewUrl::External(target))
        .data_directory(profile_root)
        .focused(true)
        .on_navigation(|url| matches!(url.scheme(), "http" | "https"))
        .on_new_window({
            let app_for_new_window = app.clone();
            move |url, _| {
                if let Ok(target) = parse_browser_url(url.as_str()) {
                    if let Some(webview) = app_for_new_window.get_webview(BROWSER_LABEL) {
                        let _ = webview.navigate(target);
                        let _ = raise_browser_webview(&webview);
                    }
                }
                NewWindowResponse::Deny
            }
        })
        .on_download(|_, _| false);

    let webview = window
        .add_child(builder, position, size)
        .map_err(|error| error.to_string())?;
    raise_browser_webview(&webview)?;
    webview.set_focus().map_err(|error| error.to_string())?;
    browser_workspace_state(app)
}

#[tauri::command]
pub fn focus_browser_workspace(app: AppHandle) -> Result<(), String> {
    app.get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?
        .set_focus()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn set_browser_workspace_bounds(app: AppHandle, bounds: BrowserBounds) -> Result<(), String> {
    let (position, size) = checked_bounds(bounds)?;
    let webview = app
        .get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?;
    webview
        .set_position(position)
        .map_err(|error| error.to_string())?;
    webview.set_size(size).map_err(|error| error.to_string())?;
    raise_browser_webview(&webview)
}

#[tauri::command]
pub fn keep_browser_workspace_on_top(app: AppHandle) -> Result<(), String> {
    let webview = app
        .get_webview(BROWSER_LABEL)
        .ok_or_else(|| "browser workspace is not ready".to_string())?;
    raise_browser_webview(&webview)
}

#[tauri::command]
pub fn navigate_browser_workspace(app: AppHandle, url: String) -> Result<BrowserState, String> {
    let target = parse_browser_url(&url)?;
    let webview = app
        .get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?;
    webview
        .navigate(target)
        .map_err(|error| error.to_string())?;
    browser_workspace_state(app)
}

#[tauri::command]
pub fn reload_browser_workspace(app: AppHandle) -> Result<(), String> {
    app.get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?
        .reload()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn browser_workspace_history(app: AppHandle, direction: String) -> Result<(), String> {
    let script = match direction.as_str() {
        "back" => "history.back()",
        "forward" => "history.forward()",
        _ => return Err("지원하지 않는 이동 방향입니다.".to_string()),
    };
    app.get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?
        .eval(script)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn set_browser_workspace_visible(app: AppHandle, visible: bool) -> Result<(), String> {
    if let Some(webview) = app.get_webview(BROWSER_LABEL) {
        if visible {
            webview.show()
        } else {
            webview.hide()
        }
        .map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn browser_workspace_state(app: AppHandle) -> Result<BrowserState, String> {
    let Some(webview) = app.get_webview(BROWSER_LABEL) else {
        return Ok(BrowserState {
            created: false,
            visible: false,
            url: None,
        });
    };
    let url = webview.url().ok().map(|value| value.to_string());
    Ok(BrowserState {
        created: true,
        visible: true,
        url,
    })
}

#[tauri::command]
pub async fn inspect_browser_workspace(app: AppHandle) -> Result<BrowserPageSnapshot, String> {
    let webview = app
        .get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?;

    #[cfg(windows)]
    {
        let (sender, receiver) = mpsc::sync_channel::<Result<String, String>>(1);
        webview
            .with_webview(move |platform| {
                let setup_sender = sender.clone();
                let setup_result = (|| -> Result<(), String> {
                    let controller = platform.controller();
                    let core = unsafe { controller.CoreWebView2() }
                        .map_err(|error| format!("WebView2를 읽을 수 없습니다: {error}"))?;
                    let method = CoTaskMemPWSTR::from("Runtime.evaluate");
                    let params = serde_json::json!({
                        "expression": READ_ONLY_PAGE_EXPRESSION,
                        "returnByValue": true,
                        "awaitPromise": true,
                    })
                    .to_string();
                    let params = CoTaskMemPWSTR::from(params.as_str());
                    let callback_sender = sender.clone();
                    let callback = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(
                        move |error_code, result| {
                            let response = if error_code.is_ok() {
                                Ok(result)
                            } else {
                                Err(format!("DevTools 읽기 요청이 실패했습니다: {error_code:?}"))
                            };
                            let _ = callback_sender.send(response);
                            Ok(())
                        },
                    ));
                    unsafe {
                        core.CallDevToolsProtocolMethod(
                            *method.as_ref().as_pcwstr(),
                            *params.as_ref().as_pcwstr(),
                            &callback,
                        )
                    }
                    .map_err(|error| {
                        format!("DevTools 읽기 요청을 시작하지 못했습니다: {error}")
                    })?;
                    Ok(())
                })();
                if let Err(error) = setup_result {
                    let _ = setup_sender.send(Err(error));
                }
            })
            .map_err(|error| format!("브라우저 읽기 작업을 예약하지 못했습니다: {error}"))?;

        let raw = tauri::async_runtime::spawn_blocking(move || {
            receiver
                .recv_timeout(Duration::from_secs(5))
                .map_err(|_| "페이지 읽기 시간이 초과되었습니다.".to_string())?
        })
        .await
        .map_err(|error| format!("페이지 읽기 작업을 완료하지 못했습니다: {error}"))??;
        return parse_cdp_page_snapshot(&raw);
    }

    #[cfg(not(windows))]
    {
        let _ = webview;
        Err("페이지 읽기 어댑터는 현재 Windows WebView2만 지원합니다.".to_string())
    }
}

#[tauri::command]
pub async fn execute_browser_click(
    app: AppHandle,
    expected_url: String,
    control_index: u32,
    expected_kind: String,
    expected_label: String,
) -> Result<BrowserClickResult, String> {
    if control_index >= 60 {
        return Err("승인 가능한 조작 요소 범위를 벗어났습니다.".to_string());
    }
    if !matches!(expected_kind.as_str(), "button" | "link") {
        return Err("현재 단계에서는 버튼과 링크만 클릭할 수 있습니다.".to_string());
    }
    let expected_url = parse_browser_url(&expected_url)?.to_string();
    let expected_label = expected_label.trim().to_string();
    if expected_label.is_empty() || expected_label.chars().count() > 120 {
        return Err("클릭 대상 이름이 올바르지 않습니다.".to_string());
    }
    let webview = app
        .get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?;

    #[cfg(windows)]
    {
        let expression = browser_click_expression(
            control_index,
            &expected_kind,
            &expected_label,
            &expected_url,
        )?;
        let (sender, receiver) = mpsc::sync_channel::<Result<String, String>>(1);
        webview
            .with_webview(move |platform| {
                let setup_sender = sender.clone();
                let setup_result = (|| -> Result<(), String> {
                    let controller = platform.controller();
                    let core = unsafe { controller.CoreWebView2() }
                        .map_err(|error| format!("WebView2 클릭을 준비하지 못했습니다: {error}"))?;
                    let method = CoTaskMemPWSTR::from("Runtime.evaluate");
                    let params = serde_json::json!({
                        "expression": expression,
                        "returnByValue": true,
                        "awaitPromise": true,
                    })
                    .to_string();
                    let params = CoTaskMemPWSTR::from(params.as_str());
                    let callback_sender = sender.clone();
                    let callback = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(
                        move |error_code, result| {
                            let response = if error_code.is_ok() {
                                Ok(result)
                            } else {
                                Err(format!("DevTools 클릭 요청이 실패했습니다: {error_code:?}"))
                            };
                            let _ = callback_sender.send(response);
                            Ok(())
                        },
                    ));
                    unsafe {
                        core.CallDevToolsProtocolMethod(
                            *method.as_ref().as_pcwstr(),
                            *params.as_ref().as_pcwstr(),
                            &callback,
                        )
                    }
                    .map_err(|error| {
                        format!("DevTools 클릭 요청을 시작하지 못했습니다: {error}")
                    })?;
                    Ok(())
                })();
                if let Err(error) = setup_result {
                    let _ = setup_sender.send(Err(error));
                }
            })
            .map_err(|error| format!("브라우저 클릭 작업을 예약하지 못했습니다: {error}"))?;

        let raw = tauri::async_runtime::spawn_blocking(move || {
            receiver
                .recv_timeout(Duration::from_secs(5))
                .map_err(|_| "브라우저 클릭 시간이 초과되었습니다.".to_string())?
        })
        .await
        .map_err(|error| format!("브라우저 클릭 작업을 완료하지 못했습니다: {error}"))??;
        let envelope: serde_json::Value = serde_json::from_str(&raw)
            .map_err(|error| format!("브라우저 클릭 응답이 JSON이 아닙니다: {error}"))?;
        if envelope.get("exceptionDetails").is_some() {
            return Err("페이지 클릭 스크립트 실행 중 오류가 발생했습니다.".to_string());
        }
        let value = envelope
            .pointer("/result/value")
            .cloned()
            .ok_or_else(|| "브라우저 클릭 응답에 결과 값이 없습니다.".to_string())?;
        return serde_json::from_value(value)
            .map_err(|error| format!("브라우저 클릭 결과 계약이 올바르지 않습니다: {error}"));
    }

    #[cfg(not(windows))]
    {
        let _ = webview;
        Err("브라우저 클릭 어댑터는 현재 Windows WebView2만 지원합니다.".to_string())
    }
}

#[tauri::command]
pub fn set_browser_workspace_zoom(app: AppHandle, zoom: f64) -> Result<(), String> {
    if !zoom.is_finite() || !(0.5..=1.25).contains(&zoom) {
        return Err("브라우저 페이지 배율은 50%에서 125% 사이여야 합니다.".to_string());
    }
    app.get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?
        .set_zoom(zoom)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn set_browser_workspace_viewport(
    app: AppHandle,
    mode: String,
    viewport_width: u32,
    panel_width: f64,
    panel_height: f64,
) -> Result<(), String> {
    if !panel_width.is_finite()
        || !panel_height.is_finite()
        || panel_width < 80.0
        || panel_height < 80.0
    {
        return Err("브라우저 화면 영역의 크기가 올바르지 않습니다.".to_string());
    }
    if !(960..=1920).contains(&viewport_width) {
        return Err("데스크톱 화면 폭은 960px에서 1920px 사이여야 합니다.".to_string());
    }

    let (method_name, params_value) = match mode.as_str() {
        // Keep native WebView2 coordinates for both modes. Desktop fitting is
        // applied with WebView2 zoom in the controller so mouse hit-testing
        // remains aligned with the rendered page.
        "responsive" | "desktop" => (
            "Emulation.clearDeviceMetricsOverride",
            serde_json::json!({}),
        ),
        _ => return Err("지원하지 않는 브라우저 화면 폭 모드입니다.".to_string()),
    };

    let webview = app
        .get_webview(BROWSER_LABEL)
        .ok_or_else(|| "브라우저가 아직 준비되지 않았습니다.".to_string())?;

    #[cfg(windows)]
    {
        let (sender, receiver) = mpsc::sync_channel::<Result<String, String>>(1);
        let method_name = method_name.to_string();
        let params_value = params_value.to_string();
        webview
            .with_webview(move |platform| {
                let setup_sender = sender.clone();
                let setup_result = (|| -> Result<(), String> {
                    let controller = platform.controller();
                    let core = unsafe { controller.CoreWebView2() }
                        .map_err(|error| format!("WebView2를 읽을 수 없습니다: {error}"))?;
                    let method = CoTaskMemPWSTR::from(method_name.as_str());
                    let params = CoTaskMemPWSTR::from(params_value.as_str());
                    let callback_sender = sender.clone();
                    let callback = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(
                        move |error_code, result| {
                            let response = if error_code.is_ok() {
                                Ok(result)
                            } else {
                                Err(format!("화면 폭 모드 적용에 실패했습니다: {error_code:?}"))
                            };
                            let _ = callback_sender.send(response);
                            Ok(())
                        },
                    ));
                    unsafe {
                        core.CallDevToolsProtocolMethod(
                            *method.as_ref().as_pcwstr(),
                            *params.as_ref().as_pcwstr(),
                            &callback,
                        )
                    }
                    .map_err(|error| format!("화면 폭 모드를 시작하지 못했습니다: {error}"))?;
                    Ok(())
                })();
                if let Err(error) = setup_result {
                    let _ = setup_sender.send(Err(error));
                }
            })
            .map_err(|error| format!("브라우저 화면 폭 작업을 예약하지 못했습니다: {error}"))?;

        tauri::async_runtime::spawn_blocking(move || {
            receiver
                .recv_timeout(Duration::from_secs(5))
                .map_err(|_| "브라우저 화면 폭 적용 시간이 초과되었습니다.".to_string())?
        })
        .await
        .map_err(|error| format!("브라우저 화면 폭 작업을 완료하지 못했습니다: {error}"))??;
        return Ok(());
    }

    #[cfg(not(windows))]
    {
        let _ = (webview, method_name, params_value);
        Err("데스크톱 화면 폭 모드는 현재 Windows WebView2만 지원합니다.".to_string())
    }
}

fn chrome_candidates() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Some(local) = env::var_os("LOCALAPPDATA") {
        paths.push(PathBuf::from(local).join("Google/Chrome/Application/chrome.exe"));
    }
    for name in ["PROGRAMFILES", "PROGRAMFILES(X86)"] {
        if let Some(root) = env::var_os(name) {
            paths.push(PathBuf::from(root).join("Google/Chrome/Application/chrome.exe"));
        }
    }
    paths
}

#[tauri::command]
pub fn open_browser_workspace_in_chrome(url: String) -> Result<ExternalBrowserResult, String> {
    let target = parse_browser_url(&url)?;
    if let Some(chrome) = chrome_candidates().into_iter().find(|path| path.is_file()) {
        Command::new(chrome)
            .arg("--new-window")
            .arg(target.as_str())
            .spawn()
            .map_err(|error| format!("Chrome을 열지 못했습니다: {error}"))?;
        return Ok(ExternalBrowserResult {
            browser: "Chrome".to_string(),
        });
    }

    Command::new("explorer.exe")
        .arg(target.as_str())
        .spawn()
        .map_err(|error| format!("외부 브라우저를 열지 못했습니다: {error}"))?;
    Ok(ExternalBrowserResult {
        browser: "기본 브라우저".to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::{
        browser_click_expression, parse_browser_url, parse_cdp_page_snapshot,
        READ_ONLY_PAGE_EXPRESSION,
    };

    #[test]
    fn adds_https_to_plain_hosts() {
        assert_eq!(
            parse_browser_url("example.com").unwrap().as_str(),
            "https://example.com/"
        );
    }

    #[test]
    fn rejects_local_files_and_credentials() {
        assert!(parse_browser_url("file:///C:/secret.txt").is_err());
        assert!(parse_browser_url("https://user:password@example.com").is_err());
    }

    #[test]
    fn parses_read_only_devtools_snapshot() {
        let raw = serde_json::json!({
            "result": {
                "type": "object",
                "value": {
                    "schemaVersion": "1.0.0",
                    "capturedAt": "2026-08-01T12:00:00.000Z",
                    "title": "Example",
                    "url": "https://example.com/",
                    "counts": { "buttons": 1, "links": 1, "inputs": 0, "forms": 0 },
                    "hasPasswordField": false,
                    "controls": [
                        { "order": 0, "kind": "button", "label": "Continue", "inputType": "", "disabled": false, "href": "" }
                    ]
                }
            }
        })
        .to_string();

        let snapshot = parse_cdp_page_snapshot(&raw).unwrap();
        assert_eq!(snapshot.title, "Example");
        assert_eq!(snapshot.controls[0].label, "Continue");
        assert!(!snapshot.has_password_field);
    }

    #[test]
    fn rejects_devtools_responses_without_a_value() {
        assert!(parse_cdp_page_snapshot(r#"{"result":{"type":"undefined"}}"#).is_err());
    }

    #[test]
    fn read_only_expression_avoids_sensitive_browser_state() {
        assert!(!READ_ONLY_PAGE_EXPRESSION.contains("document.cookie"));
        assert!(!READ_ONLY_PAGE_EXPRESSION.contains("localStorage"));
        assert!(!READ_ONLY_PAGE_EXPRESSION.contains("sessionStorage"));
        assert!(!READ_ONLY_PAGE_EXPRESSION.contains(".value"));
    }

    #[test]
    fn click_expression_is_bounded_and_contains_no_arbitrary_script_input() {
        let expression =
            browser_click_expression(2, "button", "Continue", "https://example.com/").unwrap();
        assert!(expression.contains("const expectedIndex = 2;"));
        assert!(expression.contains("element.click()"));
        assert!(!expression.contains("document.cookie"));
        assert!(!expression.contains("localStorage"));
    }
}
