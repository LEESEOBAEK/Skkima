#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::SystemTime;

const MAX_JSON_BYTES: u64 = 16 * 1024 * 1024;

mod atomic_file;
mod browser_evidence;
mod browser_shell;
mod chrome_bridge;
mod chrome_devtools_mcp;
mod cli_execution;
mod external_connections;
mod local_environment;
mod plugin_library;
mod profile;
mod project_onboarding;
mod research_sources;
mod skill_library;
mod skill_smoke_test;
mod windows_process;

#[derive(Clone)]
struct CachedRunManifest {
    file_len: u64,
    modified_at: Option<SystemTime>,
    parsed: Result<WorkflowRunSummary, String>,
}

static RUN_MANIFEST_CACHE: OnceLock<Mutex<HashMap<PathBuf, CachedRunManifest>>> = OnceLock::new();

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct WorkflowDeliverable {
    path: String,
    role: Option<String>,
    file_count: Option<u64>,
    total_bytes: Option<u64>,
    recorded_at: Option<String>,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct WorkflowInputEntry {
    text: String,
    recorded_at: Option<String>,
    operation_id: Option<String>,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct WorkflowLayerSummary {
    id: String,
    status: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct WorkflowRevisionEntry {
    timestamp: Option<String>,
    event: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct WorkflowErrorSurface {
    version: String,
    category: String,
    stage: String,
    code: String,
    data_validation_status: String,
    retryable: bool,
    recovery: Vec<String>,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct WorkflowRunSummary {
    run_id: String,
    short_id: String,
    display_title: String,
    created_at: Option<String>,
    updated_at: Option<String>,
    status: String,
    workflow_state: Option<String>,
    request_completed: bool,
    validation_valid: Option<bool>,
    evidence_status: Option<String>,
    validation_needed: Vec<String>,
    error_surface: Option<WorkflowErrorSurface>,
    failure_reason: Option<String>,
    recovery_action: Option<String>,
    next_required_action: Option<String>,
    final_deliverable: Option<String>,
    deliverables: Vec<WorkflowDeliverable>,
    operation_id: Option<String>,
    session_reference: Option<String>,
    relation_type: Option<String>,
    parent_run_id: Option<String>,
    source_text: Option<String>,
    supplemental_inputs: Vec<WorkflowInputEntry>,
    layers: Vec<WorkflowLayerSummary>,
    revision_history: Vec<WorkflowRevisionEntry>,
    quality_gate_reason: Option<String>,
}

#[derive(Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct WorkflowProjectSnapshot {
    project_name: String,
    project_root: String,
    workspace_id: Option<String>,
    schema_version: Option<String>,
    runs_root: String,
    contract_found: bool,
    runs: Vec<WorkflowRunSummary>,
    warnings: Vec<String>,
}

#[tauri::command]
fn pick_project_folder() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("쓰끼마 프로젝트 폴더 선택")
        .pick_folder()
        .map(|path| path.to_string_lossy().into_owned())
}

#[tauri::command]
fn open_path_in_explorer(path: String) -> Result<(), String> {
    let canonical = PathBuf::from(path)
        .canonicalize()
        .map_err(|error| format!("선택한 경로를 찾을 수 없습니다: {error}"))?;
    let mut command = Command::new("explorer.exe");
    if canonical.is_file() {
        command.arg("/select,").arg(&canonical);
    } else {
        command.arg(&canonical);
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Windows 파일 탐색기를 열 수 없습니다: {error}"))
}

fn read_json(path: &Path) -> Result<Value, String> {
    let metadata = fs::metadata(path)
        .map_err(|error| format!("{} 파일을 읽을 수 없습니다: {error}", path.display()))?;
    if metadata.len() > MAX_JSON_BYTES {
        return Err(format!(
            "{} 파일이 허용된 크기를 초과했습니다.",
            path.display()
        ));
    }

    let contents = fs::read_to_string(path)
        .map_err(|error| format!("{} 파일을 읽을 수 없습니다: {error}", path.display()))?;
    serde_json::from_str(&contents)
        .map_err(|error| format!("{} JSON 형식이 올바르지 않습니다: {error}", path.display()))
}

fn nested_value<'a>(value: &'a Value, path: &[&str]) -> Option<&'a Value> {
    path.iter().try_fold(value, |current, key| current.get(key))
}

fn nested_string(value: &Value, path: &[&str]) -> Option<String> {
    nested_value(value, path)
        .and_then(Value::as_str)
        .map(str::to_owned)
}

fn nested_bool(value: &Value, path: &[&str]) -> Option<bool> {
    nested_value(value, path).and_then(Value::as_bool)
}

fn nested_string_list(value: &Value, path: &[&str]) -> Vec<String> {
    nested_value(value, path)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .collect()
}

fn safe_relative_path(value: &str) -> Option<PathBuf> {
    let path = Path::new(value);
    if path.as_os_str().is_empty() {
        return None;
    }

    let is_safe = path
        .components()
        .all(|component| matches!(component, Component::Normal(_) | Component::CurDir));
    is_safe.then(|| path.to_path_buf())
}

fn run_title(manifest: &Value, run_id: &str) -> String {
    nested_string(manifest, &["trace", "original_run_name"])
        .or_else(|| nested_string(manifest, &["trace", "run_name"]))
        .filter(|title| !title.trim().is_empty())
        .unwrap_or_else(|| {
            let parts = run_id.split("__").collect::<Vec<_>>();
            parts
                .get(1)
                .map(|title| title.replace('_', " "))
                .filter(|title| !title.trim().is_empty())
                .unwrap_or_else(|| run_id.to_owned())
        })
}

fn parse_deliverables(manifest: &Value) -> Vec<WorkflowDeliverable> {
    nested_value(manifest, &["deliverable_paths"])
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let path = item
                .get("path_relative")
                .and_then(Value::as_str)
                .or_else(|| item.get("path").and_then(Value::as_str))?;
            Some(WorkflowDeliverable {
                path: path.to_owned(),
                role: item.get("role").and_then(Value::as_str).map(str::to_owned),
                file_count: item.get("file_count").and_then(Value::as_u64),
                total_bytes: item.get("total_bytes").and_then(Value::as_u64),
                recorded_at: item
                    .get("recorded_at")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
            })
        })
        .collect()
}

fn parse_supplemental_inputs(manifest: &Value) -> Vec<WorkflowInputEntry> {
    manifest
        .get("supplemental_inputs")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let text = item.get("text")?.as_str()?.trim();
            if text.is_empty() {
                return None;
            }
            Some(WorkflowInputEntry {
                text: text.to_owned(),
                recorded_at: item
                    .get("recorded_at")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                operation_id: item
                    .get("operation_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
            })
        })
        .collect()
}

fn parse_layers(manifest: &Value) -> Vec<WorkflowLayerSummary> {
    manifest
        .get("layers")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            Some(WorkflowLayerSummary {
                id: item.get("id")?.as_str()?.to_owned(),
                status: item
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown")
                    .to_owned(),
            })
        })
        .collect()
}

fn parse_revision_history(manifest: &Value) -> Vec<WorkflowRevisionEntry> {
    manifest
        .get("revision_history")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            Some(WorkflowRevisionEntry {
                timestamp: item
                    .get("timestamp")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                event: item.get("event")?.as_str()?.to_owned(),
            })
        })
        .collect()
}

fn parse_error_surface(manifest: &Value) -> Option<WorkflowErrorSurface> {
    let value = nested_value(manifest, &["summary", "error_surface"])?;
    Some(WorkflowErrorSurface {
        version: value.get("version")?.as_str()?.to_owned(),
        category: value.get("category")?.as_str()?.to_owned(),
        stage: value.get("stage")?.as_str()?.to_owned(),
        code: value.get("code")?.as_str()?.to_owned(),
        data_validation_status: value.get("data_validation_status")?.as_str()?.to_owned(),
        retryable: value.get("retryable")?.as_bool()?,
        recovery: value
            .get("recovery")?
            .as_array()?
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect(),
    })
}

fn parse_run_summary(manifest: &Value) -> Result<WorkflowRunSummary, String> {
    let run_id = manifest
        .get("run_id")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| "run_id가 없습니다.".to_owned())?
        .to_owned();
    let short_id = run_id
        .rsplit("__")
        .next()
        .filter(|value| *value != run_id)
        .unwrap_or(&run_id)
        .to_owned();
    let status = manifest
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_owned();
    let is_running = status == "running";
    let has_active_continuation = manifest
        .get("active_continuation_operation_id")
        .and_then(Value::as_str)
        .is_some_and(|value| !value.trim().is_empty());
    let stored_workflow_state = nested_string(manifest, &["summary", "workflow_state"]);
    let workflow_state = if is_running && has_active_continuation {
        Some("continuation_running".to_owned())
    } else {
        stored_workflow_state.clone()
    };
    let stored_request_completed = nested_bool(
        manifest,
        &["summary", "human_report_quality_gate", "request_completed"],
    )
    .unwrap_or_else(|| stored_workflow_state.as_deref() == Some("request_completed"));
    let request_completed = status == "completed" && stored_request_completed;
    let validation_valid = if is_running {
        None
    } else {
        nested_bool(manifest, &["summary", "fulfillment_validation_valid"])
    };
    let next_required_action = if is_running {
        Some("CLI 결과와 현재 요청 검증 대기".to_owned())
    } else {
        nested_string(manifest, &["summary", "next_required_action"])
    };
    let quality_gate_reason = if is_running {
        Some(
            "현재 요청의 CLI 결과와 이행 검증을 기다리고 있습니다. 기존 산출물과 이전 검증은 현재 요청의 완료 판정으로 사용하지 않습니다."
                .to_owned(),
        )
    } else {
        nested_string(
            manifest,
            &["summary", "human_report_quality_gate", "reason"],
        )
    };

    Ok(WorkflowRunSummary {
        display_title: run_title(manifest, &run_id),
        created_at: manifest
            .get("created_at")
            .and_then(Value::as_str)
            .map(str::to_owned),
        updated_at: manifest
            .get("updated_at")
            .and_then(Value::as_str)
            .map(str::to_owned),
        status,
        workflow_state,
        request_completed,
        validation_valid,
        evidence_status: nested_string(manifest, &["summary", "evidence_status"]),
        validation_needed: nested_string_list(manifest, &["summary", "validation_needed"]),
        error_surface: parse_error_surface(manifest),
        failure_reason: nested_string(manifest, &["summary", "failure_reason"]),
        recovery_action: nested_string(manifest, &["summary", "recovery_action"]),
        next_required_action,
        final_deliverable: nested_string(manifest, &["summary", "final_deliverable", "path"]),
        deliverables: parse_deliverables(manifest),
        operation_id: manifest
            .get("operation_id")
            .and_then(Value::as_str)
            .map(str::to_owned),
        session_reference: manifest
            .get("session_reference")
            .and_then(Value::as_str)
            .map(str::to_owned),
        relation_type: manifest
            .get("relation_type")
            .and_then(Value::as_str)
            .map(str::to_owned),
        parent_run_id: manifest
            .get("parent_run_id")
            .and_then(Value::as_str)
            .map(str::to_owned),
        source_text: nested_string(manifest, &["source", "raw_text"]),
        supplemental_inputs: parse_supplemental_inputs(manifest),
        layers: parse_layers(manifest),
        revision_history: parse_revision_history(manifest),
        quality_gate_reason,
        run_id,
        short_id,
    })
}

fn run_manifest_cache() -> &'static Mutex<HashMap<PathBuf, CachedRunManifest>> {
    RUN_MANIFEST_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn read_run_summary_cached(manifest_path: &Path) -> Result<WorkflowRunSummary, String> {
    let metadata = fs::metadata(manifest_path)
        .map_err(|error| format!("파일 정보를 읽을 수 없습니다: {error}"))?;
    let file_len = metadata.len();
    let modified_at = metadata.modified().ok();

    if let Ok(cache) = run_manifest_cache().lock() {
        if let Some(entry) = cache.get(manifest_path) {
            if entry.file_len == file_len && entry.modified_at == modified_at {
                return entry.parsed.clone();
            }
        }
    }

    let parsed = read_json(manifest_path).and_then(|value| parse_run_summary(&value));
    if let Ok(mut cache) = run_manifest_cache().lock() {
        cache.insert(
            manifest_path.to_path_buf(),
            CachedRunManifest {
                file_len,
                modified_at,
                parsed: parsed.clone(),
            },
        );
    }
    parsed
}

fn prune_run_manifest_cache(runs_root: &Path, visible_manifests: &[PathBuf]) {
    let visible = visible_manifests
        .iter()
        .collect::<std::collections::HashSet<_>>();
    if let Ok(mut cache) = run_manifest_cache().lock() {
        cache.retain(|path, _| !path.starts_with(runs_root) || visible.contains(path));
    }
}

fn inspect_workflow_project_path(project_root: &Path) -> Result<WorkflowProjectSnapshot, String> {
    let canonical_root = project_root
        .canonicalize()
        .map_err(|error| format!("프로젝트 폴더를 찾을 수 없습니다: {error}"))?;
    if !canonical_root.is_dir() {
        return Err("선택한 경로가 폴더가 아닙니다.".to_owned());
    }

    let fallback_name = canonical_root
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("프로젝트")
        .to_owned();
    let canonical_contract_path = canonical_root
        .join(".schema-workflow")
        .join("project-contract.json");
    let legacy_contract_path = canonical_root.join(".schema-workflow.json");
    let contract_path = if canonical_contract_path.is_file() {
        canonical_contract_path
    } else {
        legacy_contract_path
    };
    if !contract_path.is_file() {
        return Ok(WorkflowProjectSnapshot {
            project_name: fallback_name,
            project_root: canonical_root.to_string_lossy().into_owned(),
            workspace_id: None,
            schema_version: None,
            runs_root: "outputs/workflows".to_owned(),
            contract_found: false,
            runs: Vec::new(),
            warnings: vec![
                ".schema-workflow/project-contract.json 계약 파일이 없습니다.".to_owned(),
            ],
        });
    }

    let contract = read_json(&contract_path)?;
    let runs_root_value = contract
        .get("runs_root")
        .and_then(Value::as_str)
        .unwrap_or("outputs/workflows");
    let runs_relative = safe_relative_path(runs_root_value)
        .ok_or_else(|| "runs_root는 프로젝트 내부의 상대 경로여야 합니다.".to_owned())?;
    let runs_root = canonical_root.join(&runs_relative);
    let mut warnings = Vec::new();
    let mut runs = Vec::new();
    let mut visible_manifests = Vec::new();

    if runs_root.is_dir() {
        let entries = fs::read_dir(&runs_root)
            .map_err(|error| format!("Run 폴더를 읽을 수 없습니다: {error}"))?;
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let manifest_path = path.join("workflow_manifest.json");
            if !manifest_path.is_file() {
                continue;
            }
            visible_manifests.push(manifest_path.clone());

            match read_run_summary_cached(&manifest_path) {
                Ok(run) => runs.push(run),
                Err(error) => warnings.push(format!(
                    "{} Run을 표시하지 못했습니다: {error}",
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .unwrap_or("알 수 없는 Run")
                )),
            }
        }
        prune_run_manifest_cache(&runs_root, &visible_manifests);
    } else {
        warnings.push(format!("Run 폴더가 아직 없습니다: {runs_root_value}"));
    }

    runs.sort_by(|left, right| {
        right
            .created_at
            .cmp(&left.created_at)
            .then_with(|| right.run_id.cmp(&left.run_id))
    });

    Ok(WorkflowProjectSnapshot {
        project_name: contract
            .get("project_slug")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .unwrap_or(&fallback_name)
            .to_owned(),
        project_root: canonical_root.to_string_lossy().into_owned(),
        workspace_id: contract
            .get("project_id")
            .or_else(|| contract.get("workspace_id"))
            .and_then(Value::as_str)
            .map(str::to_owned),
        schema_version: contract
            .get("schema_version")
            .and_then(Value::as_str)
            .map(str::to_owned),
        runs_root: runs_relative.to_string_lossy().replace('\\', "/"),
        contract_found: true,
        runs,
        warnings,
    })
}

#[tauri::command]
async fn inspect_workflow_project(project_root: String) -> Result<WorkflowProjectSnapshot, String> {
    tauri::async_runtime::spawn_blocking(move || {
        inspect_workflow_project_path(Path::new(&project_root))
    })
    .await
    .map_err(|error| format!("Workflow 프로젝트 읽기 작업이 중단되었습니다: {error}"))?
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            chrome_bridge::start(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            pick_project_folder,
            open_path_in_explorer,
            inspect_workflow_project,
            browser_shell::create_browser_workspace,
            browser_shell::set_browser_workspace_bounds,
            browser_shell::navigate_browser_workspace,
            browser_shell::reload_browser_workspace,
            browser_shell::browser_workspace_history,
            browser_shell::set_browser_workspace_visible,
            browser_shell::focus_browser_workspace,
            browser_shell::keep_browser_workspace_on_top,
            browser_shell::browser_workspace_state,
            browser_shell::inspect_browser_workspace,
            browser_shell::execute_browser_click,
            browser_shell::set_browser_workspace_zoom,
            browser_shell::set_browser_workspace_viewport,
            browser_shell::open_browser_workspace_in_chrome,
            browser_evidence::save_browser_web_evidence,
            browser_evidence::save_browser_action_record,
            browser_evidence::list_browser_web_evidence,
            browser_evidence::clear_browser_web_evidence,
            local_environment::get_local_environment,
            project_onboarding::pick_project_parent_folder,
            project_onboarding::prepare_new_project,
            project_onboarding::inspect_project_readiness,
            project_onboarding::start_first_workflow_run,
            project_onboarding::prepare_workflow_operation,
            research_sources::list_research_sources,
            research_sources::save_research_sources,
            research_sources::bind_research_sources,
            skill_library::pick_local_skill,
            skill_library::pick_local_skill_folder,
            skill_library::list_skill_library,
            skill_library::register_local_skill,
            skill_library::inspect_codex_skill_installation,
            skill_library::install_codex_skill,
            skill_library::uninstall_codex_skill,
            skill_library::inspect_project_skill_installations,
            skill_library::install_project_skill,
            skill_library::uninstall_project_skill,
            skill_smoke_test::launch_skill_smoke_test,
            skill_smoke_test::inspect_skill_smoke_tests,
            skill_smoke_test::cleanup_skill_smoke_test,
            plugin_library::list_plugin_library,
            plugin_library::import_github_plugin,
            plugin_library::register_plugin_skill,
            plugin_library::remove_plugin,
            cli_execution::launch_workflow_cli,
            cli_execution::inspect_workflow_cli_launch,
            cli_execution::stop_workflow_cli,
            cli_execution::list_workflow_cli_launches,
            external_connections::inspect_chrome_devtools_connection,
            chrome_devtools_mcp::launch_chrome_debug_session,
            chrome_devtools_mcp::start_chrome_devtools_mcp,
            chrome_devtools_mcp::stop_chrome_devtools_mcp,
            chrome_devtools_mcp::read_chrome_devtools_mcp,
            chrome_devtools_mcp::save_chrome_devtools_mcp_evidence,
            chrome_bridge::get_chrome_bridge_snapshot,
            chrome_bridge::get_chrome_bridge_history,
            chrome_bridge::inspect_chrome_bridge_connection,
            chrome_bridge::delete_chrome_bridge_context_record,
            chrome_bridge::clear_chrome_bridge_context_history
        ])
        .run(tauri::generate_context!())
        .expect("failed to run 쓰끼마 desktop app");
}

#[cfg(test)]
mod tests {
    use super::{
        inspect_workflow_project_path, parse_run_summary, read_run_summary_cached,
        safe_relative_path,
    };
    use serde_json::json;
    use std::{env, fs, process};

    #[test]
    fn invalidates_a_cached_manifest_when_the_file_changes() {
        let manifest_dir = env::temp_dir().join(format!(
            "schema-workflow-manifest-cache-test-{}",
            process::id()
        ));
        fs::create_dir_all(&manifest_dir).expect("temporary manifest directory should be created");
        let manifest_path = manifest_dir.join("workflow_manifest.json");
        fs::write(
            &manifest_path,
            r#"{"run_id":"run-short","status":"completed"}"#,
        )
        .expect("first manifest should be written");

        let first = read_run_summary_cached(&manifest_path).expect("first manifest should parse");
        assert_eq!(first.run_id, "run-short");
        assert_eq!(first.status, "completed");

        fs::write(
            &manifest_path,
            r#"{"run_id":"run-with-a-different-length","status":"failed"}"#,
        )
        .expect("updated manifest should be written");
        let updated =
            read_run_summary_cached(&manifest_path).expect("updated manifest should parse");

        fs::remove_dir_all(&manifest_dir).expect("temporary manifest directory should be removed");
        assert_eq!(updated.run_id, "run-with-a-different-length");
        assert_eq!(updated.status, "failed");
    }

    #[test]
    fn converts_manifest_to_read_only_run_summary() {
        let manifest = json!({
            "run_id": "2026-07-28_151437__sample__95d41016",
            "created_at": "2026-07-28T15:14:37",
            "updated_at": "2026-07-28T16:05:53+09:00",
            "status": "completed",
            "trace": { "original_run_name": "AI 도구 블로그 운영안 설계" },
            "source": { "raw_text": "최초 문제 상황" },
            "layers": [
                { "id": "01_input_structuring", "status": "valid" }
            ],
            "summary": {
                "workflow_state": "request_completed",
                "human_report_quality_gate": {
                    "request_completed": true,
                    "reason": "요청이 충족되었습니다."
                },
                "fulfillment_validation_valid": true,
                "evidence_status": "sufficient",
                "validation_needed": [
                    "실사용 처리시간 비교"
                ],
                "error_surface": {
                    "version": "0.1.0",
                    "category": "presentation_failure",
                    "stage": "cli_output",
                    "code": "CLI_OUTPUT_ENCODING_FAILED",
                    "data_validation_status": "not_implied",
                    "retryable": true,
                    "recovery": ["Retry after configuring CLI stdout/stderr as UTF-8."]
                },
                "failure_reason": "입력 파일을 찾지 못했습니다.",
                "recovery_action": "입력 경로를 교정합니다.",
                "next_required_action": "none",
                "final_deliverable": { "path": "deliverables/blog_planning_v3.md" }
            },
            "deliverable_paths": [
                {
                    "path_relative": "deliverables/blog_planning_v3.md",
                    "role": "requested_output",
                    "file_count": 1,
                    "total_bytes": 3937,
                    "recorded_at": "2026-07-28T16:05:34+09:00"
                }
            ],
            "supplemental_inputs": [
                {
                    "text": "기능을 추가해줘.",
                    "recorded_at": "2026-07-28T15:30:34+09:00",
                    "operation_id": "op_followup"
                }
            ],
            "revision_history": [
                {
                    "timestamp": "2026-07-28T15:30:34+09:00",
                    "event": "completed_run_continuation_started"
                }
            ],
            "operation_id": "op_dashboard_example",
            "session_reference": "session_example",
            "relation_type": "branch",
            "parent_run_id": "2026-07-28_120000__source__parent123"
        });

        let run = parse_run_summary(&manifest).expect("manifest should parse");
        assert_eq!(run.display_title, "AI 도구 블로그 운영안 설계");
        assert_eq!(run.short_id, "95d41016");
        assert!(run.request_completed);
        assert_eq!(run.validation_valid, Some(true));
        assert_eq!(run.evidence_status.as_deref(), Some("sufficient"));
        assert_eq!(run.validation_needed, vec!["실사용 처리시간 비교"]);
        assert_eq!(
            run.error_surface.as_ref().map(|surface| surface.code.as_str()),
            Some("CLI_OUTPUT_ENCODING_FAILED")
        );
        assert_eq!(
            run.failure_reason.as_deref(),
            Some("입력 파일을 찾지 못했습니다.")
        );
        assert_eq!(
            run.recovery_action.as_deref(),
            Some("입력 경로를 교정합니다.")
        );
        assert_eq!(
            run.final_deliverable.as_deref(),
            Some("deliverables/blog_planning_v3.md")
        );
        assert_eq!(run.deliverables.len(), 1);
        assert_eq!(run.source_text.as_deref(), Some("최초 문제 상황"));
        assert_eq!(run.supplemental_inputs.len(), 1);
        assert_eq!(run.layers.len(), 1);
        assert_eq!(run.revision_history.len(), 1);
        assert_eq!(run.relation_type.as_deref(), Some("branch"));
        assert_eq!(
            run.parent_run_id.as_deref(),
            Some("2026-07-28_120000__source__parent123")
        );
        assert_eq!(
            run.quality_gate_reason.as_deref(),
            Some("요청이 충족되었습니다.")
        );
    }

    #[test]
    fn active_continuation_does_not_reuse_previous_completion_projection() {
        let manifest = json!({
            "run_id": "2026-07-30_142957__sample__165e6def",
            "status": "running",
            "active_continuation_operation_id": "op_continuation",
            "summary": {
                "workflow_state": "request_completed",
                "human_report_quality_gate": {
                    "request_completed": true,
                    "reason": "이전 요청이 충족되었습니다."
                },
                "fulfillment_validation_valid": true,
                "next_required_action": "none"
            }
        });

        let run = parse_run_summary(&manifest).expect("continuation manifest should parse");
        assert_eq!(run.status, "running");
        assert_eq!(run.workflow_state.as_deref(), Some("continuation_running"));
        assert!(!run.request_completed);
        assert_eq!(run.validation_valid, None);
        assert_eq!(
            run.next_required_action.as_deref(),
            Some("CLI 결과와 현재 요청 검증 대기")
        );
        assert!(run
            .quality_gate_reason
            .as_deref()
            .is_some_and(|reason| reason.contains("이전 검증")));
    }

    #[test]
    fn loads_independent_and_branch_runs_from_one_project() {
        let project_root = env::temp_dir().join(format!(
            "schema-workflow-branch-project-test-{}",
            process::id()
        ));
        let runs_root = project_root.join("outputs").join("workflows");
        let source_run_id = "2026-07-30_003737__source__sim01";
        let branch_run_id = "2026-07-30_095317__branch__sim03";
        fs::create_dir_all(runs_root.join(source_run_id))
            .expect("source run directory should be created");
        fs::create_dir_all(runs_root.join(branch_run_id))
            .expect("branch run directory should be created");
        fs::write(
            project_root.join(".schema-workflow.json"),
            serde_json::to_vec(&json!({
                "schema_version": "1.0.0",
                "workspace_id": "ws_branch_test",
                "project_slug": "분기 테스트",
                "runs_root": "outputs/workflows"
            }))
            .expect("contract should serialize"),
        )
        .expect("contract should be written");
        fs::write(
            runs_root.join(source_run_id).join("workflow_manifest.json"),
            serde_json::to_vec(&json!({
                "run_id": source_run_id,
                "created_at": "2026-07-30T00:37:37+09:00",
                "status": "completed",
                "relation_type": "independent"
            }))
            .expect("source manifest should serialize"),
        )
        .expect("source manifest should be written");
        fs::write(
            runs_root.join(branch_run_id).join("workflow_manifest.json"),
            serde_json::to_vec(&json!({
                "run_id": branch_run_id,
                "created_at": "2026-07-30T09:53:17+09:00",
                "status": "completed",
                "relation_type": "branch",
                "parent_run_id": source_run_id
            }))
            .expect("branch manifest should serialize"),
        )
        .expect("branch manifest should be written");

        let snapshot =
            inspect_workflow_project_path(&project_root).expect("project should be inspected");
        assert_eq!(snapshot.runs.len(), 2);
        let branch = snapshot
            .runs
            .iter()
            .find(|run| run.run_id == branch_run_id)
            .expect("branch run should be returned");
        assert_eq!(branch.relation_type.as_deref(), Some("branch"));
        assert_eq!(branch.parent_run_id.as_deref(), Some(source_run_id));

        fs::remove_dir_all(&project_root).expect("temporary project should be removed");
    }

    #[test]
    fn keeps_valid_runs_visible_when_one_manifest_is_invalid() {
        let project_root = env::temp_dir().join(format!(
            "schema-workflow-partial-project-test-{}",
            process::id()
        ));
        let runs_root = project_root.join("outputs").join("workflows");
        let valid_run_id = "2026-07-30_120000__valid__bundle04";
        let invalid_run_id = "2026-07-30_120100__invalid__bundle04";
        fs::create_dir_all(runs_root.join(valid_run_id))
            .expect("valid run directory should be created");
        fs::create_dir_all(runs_root.join(invalid_run_id))
            .expect("invalid run directory should be created");
        fs::write(
            project_root.join(".schema-workflow.json"),
            serde_json::to_vec(&json!({
                "schema_version": "1.0.0",
                "workspace_id": "ws_partial_test",
                "project_slug": "부분 오류 테스트",
                "runs_root": "outputs/workflows"
            }))
            .expect("contract should serialize"),
        )
        .expect("contract should be written");
        fs::write(
            runs_root.join(valid_run_id).join("workflow_manifest.json"),
            serde_json::to_vec(&json!({
                "run_id": valid_run_id,
                "created_at": "2026-07-30T12:00:00+09:00",
                "status": "completed"
            }))
            .expect("valid manifest should serialize"),
        )
        .expect("valid manifest should be written");
        fs::write(
            runs_root
                .join(invalid_run_id)
                .join("workflow_manifest.json"),
            b"{not-json",
        )
        .expect("invalid manifest should be written");

        let snapshot =
            inspect_workflow_project_path(&project_root).expect("project should be inspected");

        fs::remove_dir_all(&project_root).expect("temporary project should be removed");
        assert_eq!(snapshot.runs.len(), 1);
        assert_eq!(snapshot.runs[0].run_id, valid_run_id);
        assert_eq!(snapshot.warnings.len(), 1);
        assert!(snapshot.warnings[0].contains(invalid_run_id));
    }

    #[test]
    fn reads_the_canonical_split_project_contract() {
        let project_root = env::temp_dir().join(format!(
            "schema-workflow-canonical-contract-test-{}",
            process::id()
        ));
        let contract_root = project_root.join(".schema-workflow");
        fs::create_dir_all(&contract_root).expect("contract directory should be created");
        fs::write(
            contract_root.join("project-contract.json"),
            serde_json::to_vec(&json!({
                "schema_version": "1.0.0",
                "project_id": "project_canonical_test",
                "project_root": ".",
                "contract_owner": "project-contract-gateway",
                "revision": 1
            }))
            .expect("contract should serialize"),
        )
        .expect("contract should be written");

        let snapshot =
            inspect_workflow_project_path(&project_root).expect("project should be inspected");
        assert!(snapshot.contract_found);
        assert_eq!(
            snapshot.workspace_id.as_deref(),
            Some("project_canonical_test")
        );
        assert_eq!(snapshot.runs_root, "outputs/workflows");

        fs::remove_dir_all(&project_root).expect("temporary project should be removed");
    }

    #[test]
    fn rejects_runs_root_that_can_escape_the_project() {
        assert!(safe_relative_path("outputs/workflows").is_some());
        assert!(safe_relative_path("../outside").is_none());
        assert!(safe_relative_path("C:\\outside").is_none());
    }
}
