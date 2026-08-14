use crate::research_sources::{bind_research_sources, ResearchRunBinding};
use serde::Serialize;
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::Duration;
use wait_timeout::ChildExt;

const CREATE_NO_WINDOW: u32 = 0x08000000;
const LAUNCHER_TIMEOUT: Duration = Duration::from_secs(120);
const CONTRACT_RELATIVE_PATH: &str = ".schema-workflow/project-contract.json";

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SkillReadiness {
    platform: String,
    state: String,
    skill_version: Option<String>,
    channel: Option<String>,
    target: String,
    shared_platforms: Vec<String>,
}

pub(crate) fn user_path_string(path: &Path) -> String {
    let value = path.to_string_lossy();
    if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
        return format!(r"\\{rest}");
    }
    value.strip_prefix(r"\\?\").unwrap_or(&value).to_owned()
}

fn validate_project_name(value: &str) -> Result<String, String> {
    let name = value.trim();
    if name.is_empty() {
        return Err("프로젝트 이름을 입력해 주세요.".to_owned());
    }
    if name.chars().count() > 80 {
        return Err("프로젝트 이름은 80자 이하여야 합니다.".to_owned());
    }
    if name == "." || name == ".." {
        return Err("현재 또는 상위 폴더를 프로젝트 이름으로 사용할 수 없습니다.".to_owned());
    }
    if name.ends_with(['.', ' '])
        || name
            .chars()
            .any(|character| character.is_control() || r#"<>:"/\|?*"#.contains(character))
    {
        return Err("Windows 폴더 이름으로 사용할 수 없는 문자가 포함되어 있습니다.".to_owned());
    }

    let device_name = name.split('.').next().unwrap_or(name).to_ascii_uppercase();
    let reserved = matches!(device_name.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || (device_name.len() == 4
            && (device_name.starts_with("COM") || device_name.starts_with("LPT"))
            && matches!(device_name.as_bytes()[3], b'1'..=b'9'));
    if reserved {
        return Err("Windows 예약 장치 이름은 프로젝트 이름으로 사용할 수 없습니다.".to_owned());
    }

    Ok(name.to_owned())
}

fn stable_launcher_path() -> Result<PathBuf, String> {
    if let Some(configured) = env::var_os("SKKIMA_SCHEMA_WORKFLOW_LAUNCHER") {
        let path = PathBuf::from(configured);
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!(
            "지정된 Schema Workflow 실행기를 찾을 수 없습니다: {}",
            path.display()
        ));
    }

    let home = env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)
        .ok_or_else(|| "Windows 사용자 폴더를 확인할 수 없습니다.".to_owned())?;
    let launcher = home
        .join(".schema-workflow")
        .join("bin")
        .join("schema-workflow.ps1");
    if !launcher.is_file() {
        return Err(format!(
            "안정 채널 Schema Workflow 실행기가 설치되어 있지 않습니다: {}",
            launcher.display()
        ));
    }
    Ok(launcher)
}

fn launcher_python_path(launcher: &Path) -> PathBuf {
    if launcher
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("py"))
    {
        return launcher.to_path_buf();
    }
    launcher.with_file_name("schema_workflow_launcher.py")
}

fn wait_for_output(mut command: Command) -> Result<Output, String> {
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|error| format!("Schema Workflow 실행기를 시작하지 못했습니다: {error}"))?;
    match child
        .wait_timeout(LAUNCHER_TIMEOUT)
        .map_err(|error| format!("Schema Workflow 실행 상태를 확인하지 못했습니다: {error}"))?
    {
        Some(_) => child
            .wait_with_output()
            .map_err(|error| format!("Schema Workflow 실행 결과를 읽지 못했습니다: {error}")),
        None => {
            let process_id = child.id().to_string();
            let mut terminate_tree = Command::new("taskkill.exe");
            terminate_tree
                .args(["/PID", &process_id, "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            let _ = terminate_tree.status();
            let _ = child.kill();
            let _ = child.wait();
            Err("Schema Workflow 작업이 120초 안에 끝나지 않아 중단했습니다.".to_owned())
        }
    }
}

fn parse_json_output(output: &[u8]) -> Result<Value, String> {
    let text = String::from_utf8_lossy(output);
    let trimmed = text.trim().trim_start_matches('\u{feff}');
    if let Ok(value) = serde_json::from_str(trimmed) {
        return Ok(value);
    }

    let start = trimmed.find('{');
    let end = trimmed.rfind('}');
    match (start, end) {
        (Some(start), Some(end)) if start <= end => serde_json::from_str(&trimmed[start..=end])
            .map_err(|error| format!("Schema Workflow JSON 결과를 해석하지 못했습니다: {error}")),
        _ => Err("Schema Workflow가 JSON 결과를 반환하지 않았습니다.".to_owned()),
    }
}

fn workflow_json_failure_message(exit_code: Option<i32>, stdout: &[u8], stderr: &[u8]) -> String {
    let combined = format!(
        "{}\n{}",
        String::from_utf8_lossy(stdout),
        String::from_utf8_lossy(stderr)
    );
    let detail = if combined.contains("unrecognized arguments") {
        "작업 입력이 실행 인자로 분리되었습니다. 작업 제목과 현재 상황을 그대로 두고 다시 실행해 보세요."
    } else if combined.contains("the following arguments are required") {
        "실행에 필요한 항목이 누락되었습니다. 프로젝트와 작업 정보를 다시 확인해 주세요."
    } else {
        "실행기가 구조화된 응답을 반환하지 않았습니다. 실행 기록에서 종료 원인을 확인해 주세요."
    };
    let code = exit_code
        .map(|value| value.to_string())
        .unwrap_or_else(|| "알 수 없음".to_owned());
    format!("Schema Workflow 실행 준비에 실패했습니다 (종료 코드: {code}). {detail}")
}

fn run_launcher(arguments: &[String]) -> Result<Value, String> {
    let launcher = stable_launcher_path()?;
    let launcher_python = launcher_python_path(&launcher);
    if !launcher_python.is_file() {
        return Err(format!(
            "Schema Workflow Python 실행기를 찾을 수 없습니다: {}",
            launcher_python.display()
        ));
    }

    let mut command = Command::new("python.exe");
    command
        .arg(&launcher_python)
        .args(arguments)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .creation_flags(CREATE_NO_WINDOW);

    let output = wait_for_output(command)?;
    let payload = parse_json_output(&output.stdout)
        .or_else(|stdout_error| parse_json_output(&output.stderr).map_err(|_| stdout_error))
        .map_err(|_| {
            workflow_json_failure_message(output.status.code(), &output.stdout, &output.stderr)
        })?;
    if !output.status.success() || payload.get("status").and_then(Value::as_str) == Some("blocked")
    {
        let message = payload
            .get("message")
            .and_then(Value::as_str)
            .or_else(|| payload.get("error").and_then(Value::as_str))
            .unwrap_or("Schema Workflow 작업이 차단되었습니다.");
        return Err(message.to_owned());
    }
    Ok(payload)
}

pub(crate) fn ensure_project_platform(project_root: &Path, platform: &str) -> Result<(), String> {
    if !matches!(platform, "codex" | "claude" | "antigravity") {
        return Err("지원하지 않는 AI 플랫폼입니다.".to_owned());
    }
    run_launcher(&[
        "project-init".to_owned(),
        "--project-root".to_owned(),
        user_path_string(project_root),
        "--platform".to_owned(),
        platform.to_owned(),
        "--channel".to_owned(),
        "stable".to_owned(),
        "--output".to_owned(),
        "json".to_owned(),
    ])?;
    Ok(())
}

fn active_release_root() -> Result<PathBuf, String> {
    let launcher = stable_launcher_path()?;
    let install_root = launcher
        .parent()
        .and_then(Path::parent)
        .ok_or_else(|| "Schema Workflow 설치 루트를 확인할 수 없습니다.".to_owned())?;
    let pointer_path = install_root.join("active-release.json");
    let pointer: Value = serde_json::from_slice(
        &fs::read(&pointer_path)
            .map_err(|error| format!("활성 릴리스 정보를 읽을 수 없습니다: {error}"))?,
    )
    .map_err(|error| format!("활성 릴리스 정보가 올바른 JSON이 아닙니다: {error}"))?;
    let release_version = pointer
        .get("release_version")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| "활성 릴리스 버전이 기록되어 있지 않습니다.".to_owned())?;
    let release_root = install_root.join("releases").join(release_version);
    if !release_root.is_dir() {
        return Err(format!(
            "활성 Schema Workflow 릴리스 폴더를 찾을 수 없습니다: {}",
            release_root.display()
        ));
    }
    Ok(release_root)
}

fn run_active_workspace_cli(project_root: &Path, arguments: &[String]) -> Result<Value, String> {
    run_launcher(&[
        "doctor".to_owned(),
        "--project-root".to_owned(),
        user_path_string(project_root),
        "--channel".to_owned(),
        "stable".to_owned(),
        "--output".to_owned(),
        "json".to_owned(),
    ])?;

    let release_root = active_release_root()?;
    let workflow_runner = release_root
        .join("engine")
        .join("python")
        .join("workflow")
        .join("workflow_runner.py");
    if !workflow_runner.is_file() {
        return Err(format!(
            "활성 릴리스의 Workflow 명령을 찾을 수 없습니다: {}",
            workflow_runner.display()
        ));
    }

    let mut command = Command::new("python.exe");
    command
        .arg(&workflow_runner)
        .args(arguments)
        .current_dir(&release_root)
        .env("PYTHONPATH", &release_root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .creation_flags(CREATE_NO_WINDOW);
    let output = wait_for_output(command)?;
    let payload = parse_json_output(&output.stdout)
        .or_else(|stdout_error| parse_json_output(&output.stderr).map_err(|_| stdout_error))?;
    if !output.status.success() {
        let message = payload
            .get("message")
            .and_then(Value::as_str)
            .or_else(|| payload.get("error").and_then(Value::as_str))
            .unwrap_or("Workflow 관계 기록이 차단되었습니다.");
        return Err(message.to_owned());
    }
    Ok(payload)
}

fn validate_operation_input(
    task_title: &str,
    current_situation: &str,
    operation_kind: &str,
    anchor_run_id: Option<&str>,
) -> Result<(), String> {
    if task_title.is_empty() {
        return Err("작업 제목을 입력해 주세요.".to_owned());
    }
    if current_situation.is_empty() {
        return Err("현재 상황을 입력해 주세요.".to_owned());
    }
    if task_title.chars().count() > 120 {
        return Err("작업 제목은 120자 이하여야 합니다.".to_owned());
    }
    if !matches!(operation_kind, "independent" | "continuation" | "branch") {
        return Err("지원하지 않는 작업 방식입니다.".to_owned());
    }
    if matches!(operation_kind, "continuation" | "branch")
        && anchor_run_id.is_none_or(|value| value.trim().is_empty())
    {
        return Err("이어가기와 분기는 기준 Run을 선택해야 합니다.".to_owned());
    }
    Ok(())
}

fn prepare_workflow_operation_sync(
    project_root: String,
    task_title: String,
    current_situation: String,
    operation_kind: String,
    anchor_run_id: Option<String>,
    operation_id: String,
    session_reference: String,
    research_binding: Option<ResearchRunBinding>,
) -> Result<Value, String> {
    let root = PathBuf::from(project_root)
        .canonicalize()
        .map_err(|error| format!("프로젝트 폴더를 찾을 수 없습니다: {error}"))?;
    let title = task_title.trim();
    let situation = current_situation.trim();
    let kind = operation_kind.trim();
    let anchor = anchor_run_id
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    validate_operation_input(title, situation, kind, anchor)?;

    let text = format!("작업 제목: {title}\n\n현재 상황:\n{situation}");
    let mut result = match kind {
        "independent" => run_launcher(&[
            "run".to_owned(),
            "--project-root".to_owned(),
            user_path_string(&root),
            "--text".to_owned(),
            text,
            "--run-name".to_owned(),
            title.to_owned(),
            "--operation-id".to_owned(),
            operation_id,
            "--channel".to_owned(),
            "stable".to_owned(),
            "--output".to_owned(),
            "json".to_owned(),
        ])?,
        "branch" => run_active_workspace_cli(
            &root,
            &[
                "init".to_owned(),
                "--project-root".to_owned(),
                user_path_string(&root),
                "--text".to_owned(),
                text,
                "--run-name".to_owned(),
                title.to_owned(),
                "--operation-id".to_owned(),
                operation_id,
                "--session-reference".to_owned(),
                session_reference,
                "--relation-type".to_owned(),
                "branch".to_owned(),
                "--parent-run-id".to_owned(),
                anchor.expect("validated branch anchor").to_owned(),
            ],
        )?,
        "continuation" => run_active_workspace_cli(
            &root,
            &[
                "continue-run".to_owned(),
                "--project-root".to_owned(),
                user_path_string(&root),
                "--run-id".to_owned(),
                anchor.expect("validated continuation anchor").to_owned(),
                "--operation-id".to_owned(),
                operation_id,
                "--session-reference".to_owned(),
                session_reference,
                "--supplemental-input".to_owned(),
                text,
                "--note".to_owned(),
                title.to_owned(),
            ],
        )?,
        _ => unreachable!("operation kind is validated"),
    };

    if let Some(payload) = result.as_object_mut() {
        let prepared_run_id = payload
            .get("run_id")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .or_else(|| anchor.map(str::to_owned));
        payload.insert("operation_kind".to_owned(), Value::String(kind.to_owned()));
        payload.insert("cli_started".to_owned(), Value::Bool(false));
        payload.insert(
            "anchor_run_id".to_owned(),
            anchor.map_or(Value::Null, |value| Value::String(value.to_owned())),
        );
        payload.insert(
            "prepared_run_id".to_owned(),
            prepared_run_id.map_or(Value::Null, Value::String),
        );
    }
    bind_prepared_research_run(&root, &result, research_binding)?;
    Ok(result)
}

fn bind_prepared_research_run(
    project_root: &Path,
    result: &Value,
    research_binding: Option<ResearchRunBinding>,
) -> Result<(), String> {
    let Some(binding) = research_binding else {
        return Ok(());
    };
    let run_id = result
        .get("prepared_run_id")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| "리서치 Run의 준비된 ID를 확인하지 못했습니다.".to_owned())?;
    bind_research_sources(user_path_string(project_root), run_id.to_owned(), binding)
}

fn skill_readiness(project_root: &Path, platform: &str) -> SkillReadiness {
    let relative = if platform == "claude" {
        Path::new(".claude").join("skills").join("schema-workflow")
    } else {
        Path::new(".agents").join("skills").join("schema-workflow")
    };
    let target = project_root.join(relative);
    let manifest_path = target.join("schema-workflow-skill.json");
    let manifest = fs::read_to_string(&manifest_path)
        .ok()
        .and_then(|contents| serde_json::from_str::<Value>(&contents).ok());
    let shared_platforms = manifest
        .as_ref()
        .and_then(|value| value.get("compatible_platforms"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let owned = manifest
        .as_ref()
        .and_then(|value| value.get("owner"))
        .and_then(Value::as_str)
        == Some("schema-workflow-skill-manager");
    let compatible = shared_platforms.iter().any(|item| item == platform);

    SkillReadiness {
        platform: platform.to_owned(),
        state: if owned && compatible {
            "installed".to_owned()
        } else if target.exists() {
            "unmanaged".to_owned()
        } else {
            "not_installed".to_owned()
        },
        skill_version: manifest
            .as_ref()
            .and_then(|value| value.get("skill_version"))
            .and_then(Value::as_str)
            .map(str::to_owned),
        channel: manifest
            .as_ref()
            .and_then(|value| value.get("channel"))
            .and_then(Value::as_str)
            .map(str::to_owned),
        target: user_path_string(&target),
        shared_platforms,
    }
}

fn inspect_readiness_path(project_root: &Path) -> Result<Value, String> {
    let canonical_root = project_root
        .canonicalize()
        .map_err(|error| format!("프로젝트 폴더를 찾을 수 없습니다: {error}"))?;
    if !canonical_root.is_dir() {
        return Err("선택한 프로젝트 경로가 폴더가 아닙니다.".to_owned());
    }

    let contract_path = canonical_root.join(CONTRACT_RELATIVE_PATH);
    let contract = fs::read_to_string(&contract_path)
        .ok()
        .and_then(|contents| serde_json::from_str::<Value>(&contents).ok());
    let doctor = run_launcher(&[
        "doctor".to_owned(),
        "--project-root".to_owned(),
        user_path_string(&canonical_root),
        "--channel".to_owned(),
        "stable".to_owned(),
        "--output".to_owned(),
        "json".to_owned(),
    ])?;
    let skills = ["codex", "claude", "antigravity"]
        .iter()
        .map(|platform| skill_readiness(&canonical_root, platform))
        .collect::<Vec<_>>();

    Ok(json!({
        "projectRoot": user_path_string(&canonical_root),
        "contractFound": contract.is_some(),
        "contractPath": user_path_string(&contract_path),
        "contractSchemaVersion": contract
            .as_ref()
            .and_then(|value| value.get("schema_version"))
            .and_then(Value::as_str),
        "projectId": contract
            .as_ref()
            .and_then(|value| value.get("project_id"))
            .and_then(Value::as_str),
        "doctor": doctor,
        "skills": skills,
    }))
}

#[tauri::command]
pub fn pick_project_parent_folder() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("새 프로젝트를 만들 상위 폴더 선택")
        .pick_folder()
        .map(|path| path.to_string_lossy().into_owned())
}

#[tauri::command]
pub async fn prepare_new_project(
    parent_root: String,
    project_name: String,
    platforms: Vec<String>,
    approved: bool,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if !approved {
            return Err("프로젝트 폴더와 스킬 준비 작업에 대한 승인이 필요합니다.".to_owned());
        }
        let parent = PathBuf::from(parent_root)
            .canonicalize()
            .map_err(|error| format!("상위 폴더를 찾을 수 없습니다: {error}"))?;
        if !parent.is_dir() {
            return Err("선택한 상위 경로가 폴더가 아닙니다.".to_owned());
        }
        let name = validate_project_name(&project_name)?;
        let target = parent.join(name);
        if target.exists() {
            return Err("같은 이름의 파일 또는 폴더가 이미 있습니다.".to_owned());
        }
        let allowed = ["codex", "claude", "antigravity"];
        let selected = platforms
            .into_iter()
            .filter(|platform| allowed.contains(&platform.as_str()))
            .fold(Vec::<String>::new(), |mut result, platform| {
                if !result.contains(&platform) {
                    result.push(platform);
                }
                result
            });
        if selected.is_empty() {
            return Err("사용할 AI 플랫폼을 하나 이상 선택해 주세요.".to_owned());
        }

        let mut arguments = vec![
            "project-init".to_owned(),
            "--project-root".to_owned(),
            user_path_string(&target),
        ];
        for platform in selected {
            arguments.push("--platform".to_owned());
            arguments.push(platform);
        }
        arguments.extend([
            "--channel".to_owned(),
            "stable".to_owned(),
            "--output".to_owned(),
            "json".to_owned(),
        ]);
        let mut result = run_launcher(&arguments)?;
        if let Some(payload) = result.as_object_mut() {
            payload.insert(
                "project_root".to_owned(),
                Value::String(user_path_string(&target)),
            );
        }
        Ok(result)
    })
    .await
    .map_err(|error| format!("프로젝트 준비 작업이 중단되었습니다: {error}"))?
}

#[tauri::command]
pub async fn inspect_project_readiness(project_root: String) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || inspect_readiness_path(Path::new(&project_root)))
        .await
        .map_err(|error| format!("프로젝트 준비 상태 확인이 중단되었습니다: {error}"))?
}

#[tauri::command]
pub async fn start_first_workflow_run(
    project_root: String,
    task_title: String,
    current_situation: String,
    operation_id: String,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        prepare_workflow_operation_sync(
            project_root,
            task_title,
            current_situation,
            "independent".to_owned(),
            None,
            operation_id,
            "desktop-first-run".to_owned(),
            None,
        )
    })
    .await
    .map_err(|error| format!("첫 Workflow Run 생성이 중단되었습니다: {error}"))?
}

#[tauri::command]
pub async fn prepare_workflow_operation(
    project_root: String,
    task_title: String,
    current_situation: String,
    operation_kind: String,
    anchor_run_id: Option<String>,
    operation_id: String,
    session_reference: String,
    research_binding: Option<ResearchRunBinding>,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        prepare_workflow_operation_sync(
            project_root,
            task_title,
            current_situation,
            operation_kind,
            anchor_run_id,
            operation_id,
            session_reference,
            research_binding,
        )
    })
    .await
    .map_err(|error| format!("Workflow 실행 준비가 중단되었습니다: {error}"))?
}

#[cfg(test)]
mod tests {
    use super::{
        bind_prepared_research_run, launcher_python_path, parse_json_output, skill_readiness,
        user_path_string, validate_operation_input, validate_project_name,
        workflow_json_failure_message,
    };
    use crate::research_sources::{
        preflight_research_run, save_research_sources, ResearchRunBinding, ResearchSource,
    };
    use serde_json::json;
    use std::{env, fs, path::Path, process};

    #[test]
    fn validates_windows_project_names() {
        assert_eq!(validate_project_name("새 프로젝트").unwrap(), "새 프로젝트");
        for invalid in ["", "..", "a/b", "a\\b", "CON", "LPT1", "name."] {
            assert!(validate_project_name(invalid).is_err(), "{invalid}");
        }
    }

    #[test]
    fn binds_research_sources_to_the_prepared_run_before_cli_launch() {
        let root = env::temp_dir().join(format!("skkima-prepared-research-{}", process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("research_sources")).unwrap();
        fs::create_dir_all(root.join("outputs/workflows/2026-08-06_224443__시장_규모_사실_확인"))
            .unwrap();
        let source_path = root.join("research_sources").join("market-note.txt");
        fs::write(&source_path, "시장 메모").unwrap();
        let source = ResearchSource {
            source_id: "market_note".to_owned(),
            source_type: "file".to_owned(),
            title: "시장 메모".to_owned(),
            locator: "research_sources/market-note.txt".to_owned(),
            collected_at: "2026-08-06T22:30:00+09:00".to_owned(),
            sha256: None,
            quote: "테스트 메모".to_owned(),
            purpose: "사실 확인".to_owned(),
            permission_status: "permitted".to_owned(),
        };
        save_research_sources(root.to_string_lossy().into_owned(), vec![source]).unwrap();

        let result = json!({ "prepared_run_id": "2026-08-06_224443__시장_규모_사실_확인" });
        bind_prepared_research_run(
            &root,
            &result,
            Some(ResearchRunBinding {
                claim_kind: "fact".to_owned(),
                source_ids: vec!["market_note".to_owned()],
            }),
        )
        .unwrap();

        let binding_path = root
            .join("outputs/workflows/2026-08-06_224443__시장_규모_사실_확인/research_sources.json");
        assert!(binding_path.is_file());
        assert!(preflight_research_run(&root, "2026-08-06_224443__시장_규모_사실_확인").is_ok());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn converts_windows_verbatim_paths_for_user_display() {
        assert_eq!(
            user_path_string(Path::new(r"\\?\C:\Users\demo\project")),
            r"C:\Users\demo\project"
        );
        assert_eq!(
            user_path_string(Path::new(r"\\?\UNC\server\share\project")),
            r"\\server\share\project"
        );
        assert_eq!(
            user_path_string(Path::new(r"C:\Users\demo\project")),
            r"C:\Users\demo\project"
        );
    }

    #[test]
    fn parses_json_after_a_launcher_prefix() {
        let payload =
            parse_json_output(b"notice\r\n{\"status\":\"normal\",\"run_id\":\"run-1\"}\r\n")
                .expect("launcher JSON should be parsed");
        assert_eq!(payload["run_id"], "run-1");
    }

    #[test]
    fn resolves_the_python_launcher_next_to_the_powershell_wrapper() {
        assert_eq!(
            launcher_python_path(Path::new(
                r"C:\\Users\\demo\\.schema-workflow\\bin\\schema-workflow.ps1"
            )),
            Path::new(r"C:\\Users\\demo\\.schema-workflow\\bin\\schema_workflow_launcher.py")
        );
        assert_eq!(
            launcher_python_path(Path::new(r"C:\\Tools\\schema_workflow_launcher.py")),
            Path::new(r"C:\\Tools\\schema_workflow_launcher.py")
        );
    }

    #[test]
    fn reports_argument_splitting_without_echoing_the_request_body() {
        let message = workflow_json_failure_message(
            Some(2),
            b"",
            b"error: unrecognized arguments: private workflow request body",
        );
        assert!(message.contains("실행 인자로 분리"));
        assert!(message.contains("종료 코드: 2"));
        assert!(!message.contains("private workflow request body"));
    }

    #[test]
    fn validates_operation_relationship_inputs() {
        assert!(validate_operation_input("새 작업", "현재 상황", "independent", None).is_ok());
        for kind in ["continuation", "branch"] {
            assert!(validate_operation_input("후속 작업", "추가 요청", kind, None).is_err());
            assert!(
                validate_operation_input("후속 작업", "추가 요청", kind, Some("run-1")).is_ok()
            );
        }
        assert!(validate_operation_input("작업", "상황", "automatic", None).is_err());
    }

    #[test]
    fn identifies_shared_agent_skill_compatibility() {
        let root = env::temp_dir().join(format!("skkima-skill-readiness-test-{}", process::id()));
        let skill_root = root.join(".agents/skills/schema-workflow");
        fs::create_dir_all(&skill_root).expect("skill directory should be created");
        fs::write(
            skill_root.join("schema-workflow-skill.json"),
            serde_json::to_vec_pretty(&json!({
                "owner": "schema-workflow-skill-manager",
                "compatible_platforms": ["codex", "antigravity"],
                "skill_version": "1.0.0",
                "channel": "stable"
            }))
            .unwrap(),
        )
        .unwrap();

        assert_eq!(skill_readiness(&root, "codex").state, "installed");
        assert_eq!(skill_readiness(&root, "antigravity").state, "installed");
        assert_eq!(skill_readiness(&root, "claude").state, "not_installed");
        let _ = fs::remove_dir_all(root);
    }
}
