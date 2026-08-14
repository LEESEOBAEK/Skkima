use crate::atomic_file::atomic_write;
use crate::project_onboarding::{ensure_project_platform, user_path_string};
use crate::research_sources::preflight_research_run;
use crate::windows_process::{process_is_running, terminate_process_tree};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::os::windows::process::CommandExt;
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

const CREATE_NO_WINDOW: u32 = 0x08000000;
const EXECUTION_SCHEMA_VERSION: &str = "1.0.0";
const STARTING_GRACE_MILLISECONDS: u128 = 5_000;

#[derive(Clone, Serialize, Deserialize)]
struct CliExecutionRecord {
    schema_version: String,
    launch_id: String,
    project_root: String,
    run_id: String,
    operation_id: Option<String>,
    platform: String,
    #[serde(default = "default_approval_mode")]
    approval_mode: String,
    status: String,
    process_id: Option<u32>,
    created_at: String,
    started_at: Option<String>,
    finished_at: Option<String>,
    prompt_path: String,
    log_path: String,
    status_path: String,
    error: Option<String>,
}

fn default_approval_mode() -> String {
    "review".to_owned()
}

fn now_marker() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .to_string()
}

fn starting_record_is_stale(record: &CliExecutionRecord, now_milliseconds: u128) -> bool {
    if record.status != "starting" || record.process_id.is_some() {
        return false;
    }
    let Ok(created_at) = record.created_at.parse::<u128>() else {
        return false;
    };
    now_milliseconds.saturating_sub(created_at) >= STARTING_GRACE_MILLISECONDS
}

fn safe_run_id(value: &str) -> Result<&str, String> {
    let value = value.trim();
    let path = Path::new(value);
    if value.is_empty()
        || path.components().count() != 1
        || !matches!(path.components().next(), Some(Component::Normal(_)))
    {
        return Err("Run ID가 올바르지 않습니다.".to_owned());
    }
    Ok(value)
}

fn canonical_project_root(project_root: &str) -> Result<PathBuf, String> {
    let root = PathBuf::from(project_root)
        .canonicalize()
        .map_err(|error| format!("프로젝트 폴더를 찾을 수 없습니다: {error}"))?;
    if !root.is_dir() {
        return Err("선택한 프로젝트 경로가 폴더가 아닙니다.".to_owned());
    }
    Ok(root)
}

fn run_manifest_path(project_root: &Path, run_id: &str) -> Result<PathBuf, String> {
    let run_id = safe_run_id(run_id)?;
    let path = project_root
        .join("outputs")
        .join("workflows")
        .join(run_id)
        .join("workflow_manifest.json");
    if !path.is_file() {
        return Err(format!("준비된 Run manifest를 찾을 수 없습니다: {run_id}"));
    }
    Ok(path)
}

fn read_manifest(project_root: &Path, run_id: &str) -> Result<Value, String> {
    let path = run_manifest_path(project_root, run_id)?;
    let bytes =
        fs::read(&path).map_err(|error| format!("Run manifest를 읽을 수 없습니다: {error}"))?;
    serde_json::from_slice(&bytes)
        .map_err(|error| format!("Run manifest JSON이 올바르지 않습니다: {error}"))
}

fn manifest_is_launchable(manifest: &Value) -> bool {
    let status = manifest.get("status").and_then(Value::as_str);
    if status != Some("running") {
        return false;
    }

    let has_active_continuation = manifest
        .get("active_continuation_operation_id")
        .and_then(Value::as_str)
        .is_some_and(|value| !value.trim().is_empty());
    if has_active_continuation {
        return true;
    }

    let request_completed = manifest
        .get("summary")
        .and_then(|summary| summary.get("human_report_quality_gate"))
        .and_then(|gate| gate.get("request_completed"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    !request_completed
}

fn nested_string<'a>(value: &'a Value, path: &[&str]) -> Option<&'a str> {
    path.iter()
        .try_fold(value, |current, key| current.get(key))?
        .as_str()
}

fn latest_request_text(manifest: &Value) -> Option<&str> {
    manifest
        .get("supplemental_inputs")
        .and_then(Value::as_array)
        .and_then(|items| items.last())
        .and_then(|item| item.get("text"))
        .and_then(Value::as_str)
        .or_else(|| nested_string(manifest, &["source", "raw_text"]))
}

fn skill_invocation(platform: &str) -> &'static str {
    if platform == "codex" {
        "@schema-workflow"
    } else {
        "/schema-workflow"
    }
}

fn build_prompt(project_root: &Path, run_id: &str, platform: &str, manifest: &Value) -> String {
    let operation_id = manifest
        .get("operation_id")
        .and_then(Value::as_str)
        .unwrap_or("not_recorded");
    let session_reference = manifest
        .get("session_reference")
        .and_then(Value::as_str)
        .unwrap_or("not_recorded");
    let relation_type = manifest
        .get("relation_type")
        .and_then(Value::as_str)
        .unwrap_or("independent");
    let parent_run_id = manifest
        .get("parent_run_id")
        .and_then(Value::as_str)
        .unwrap_or("none");
    let request = latest_request_text(manifest)
        .unwrap_or("준비된 Run의 source 및 supplemental_inputs를 확인하여 기록된 요청을 수행한다.");
    format!(
        "{}\n\nProjectRoot:\n{}\n\nPreparedRunId:\n{}\n\nOperationId:\n{}\n\nSessionReference:\n{}\n\n작업 관계:\n- relation_type: {}\n- parent_run_id: {}\n\n문제 상황:\n{}\n\n실행 규칙:\n- 이 Run은 쓰끼마에서 이미 준비되었다. init 또는 continue-run을 다시 실행하거나 새 Run을 만들지 않는다.\n- PreparedRunId의 workflow_manifest.json을 먼저 읽고 정확히 같은 Run과 OperationId 안에서 작업한다.\n- 프로젝트의 schema-workflow 스킬과 활성 stable 엔진을 사용한다.\n- 사용자 원문, 관계, 근거, 산출물, fulfillment를 01~07 레이어에 맞게 유지한다.\n- 대표 사용자 산출물은 final_output으로 등록하고 보조 파일은 Run 내부 generated_output으로 보존한다.\n- 근거가 부족하면 임의로 확정하지 않고 validation_needed로 남긴다.\n- 완료 조건이 충족되면 검증 후 request_completed를 기록하고, 충족되지 않으면 실제 다음 행동을 남긴다.\n",
        skill_invocation(platform),
        user_path_string(project_root),
        run_id,
        operation_id,
        session_reference,
        relation_type,
        parent_run_id,
        request.trim()
    )
}

fn executable_names(platform: &str) -> Result<&'static [&'static str], String> {
    match platform {
        // Command shims remain fallbacks. Codex resolves its native npm binary
        // first so a multiline prompt is not reparsed by cmd.exe.
        "codex" => Ok(&["codex.cmd", "codex", "codex.exe"]),
        "claude" => Ok(&["claude.cmd", "claude", "claude.exe"]),
        "antigravity" => Ok(&["agy.exe", "agy"]),
        _ => Err("지원하지 않는 AI 플랫폼입니다.".to_owned()),
    }
}

fn where_executable(names: &[&str]) -> Option<PathBuf> {
    for name in names {
        let output = Command::new("where.exe")
            .arg(name)
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .ok()?;
        if !output.status.success() {
            continue;
        }
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            let candidate = PathBuf::from(line.trim());
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

fn find_file_limited(root: &Path, file_name: &str, depth: usize) -> Option<PathBuf> {
    if depth == 0 || !root.is_dir() {
        return None;
    }
    let entries = fs::read_dir(root).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file()
            && path
                .file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|value| value.eq_ignore_ascii_case(file_name))
        {
            return Some(path);
        }
        if path.is_dir() {
            if let Some(found) = find_file_limited(&path, file_name, depth - 1) {
                return Some(found);
            }
        }
    }
    None
}

fn find_codex_native_in_package(package_root: &Path) -> Option<PathBuf> {
    find_file_limited(package_root, "codex.exe", 10)
}

fn find_npm_codex_native() -> Option<PathBuf> {
    let app_data = env::var_os("APPDATA")?;
    let package_root = PathBuf::from(app_data)
        .join("npm")
        .join("node_modules")
        .join("@openai")
        .join("codex");
    find_codex_native_in_package(&package_root)
}

pub(crate) fn resolve_platform_executable(platform: &str) -> Result<PathBuf, String> {
    if platform == "codex" {
        if let Some(path) = find_npm_codex_native() {
            return Ok(path);
        }
    }
    let names = executable_names(platform)?;
    if let Some(path) = where_executable(names) {
        return Ok(path);
    }
    if platform == "codex" {
        if let Some(app_data) = env::var_os("APPDATA") {
            let shim = PathBuf::from(app_data).join("npm").join("codex.cmd");
            if shim.is_file() {
                return Ok(shim);
            }
        }
        if let Some(local_app_data) = env::var_os("LOCALAPPDATA") {
            let root = PathBuf::from(local_app_data)
                .join("OpenAI")
                .join("Codex")
                .join("bin");
            if let Some(path) = find_file_limited(&root, "codex.exe", 5) {
                return Ok(path);
            }
        }
    }
    Err(format!("{} CLI 실행 파일을 찾을 수 없습니다.", platform))
}

pub(crate) fn quote_powershell(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

fn normalize_approval_mode(value: &str) -> Result<&str, String> {
    match value.trim() {
        "review" => Ok("review"),
        "auto" => Ok("auto"),
        _ => Err("지원하지 않는 권한 처리 방식입니다.".to_owned()),
    }
}

fn platform_command(platform: &str, approval_mode: &str) -> &'static str {
    match (platform, approval_mode) {
        // Windows PowerShell can split a multiline Korean prompt when it is
        // forwarded as a native CLI argument. Codex exec accepts `-` as a
        // stdin prompt sentinel, preserving the prepared Run verbatim.
        ("codex", "review") => {
            "Invoke-Codex -Arguments @('-C', $projectRoot, '--ask-for-approval', 'on-request', '--sandbox', 'workspace-write', '--no-alt-screen', $prompt)"
        }
        ("codex", "auto") => {
            "Invoke-Codex -Arguments @('-C', $projectRoot, '--ask-for-approval', 'never', '--sandbox', 'workspace-write', '--no-alt-screen', $prompt)"
        }
        ("claude", "review") => "& $cli --permission-mode manual $prompt",
        ("claude", "auto") => "& $cli --permission-mode auto $prompt",
        ("antigravity", "review") => {
            "& $cli --mode accept-edits --log-file $platformLog --prompt-interactive $prompt"
        }
        ("antigravity", "auto") => {
            "& $cli --dangerously-skip-permissions --log-file $platformLog --prompt-interactive $prompt"
        }
        _ => unreachable!("platform is validated"),
    }
}

fn build_launch_script(
    executable: &Path,
    platform_log_path: &Path,
    record: &CliExecutionRecord,
) -> String {
    format!(
        r#"$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
try {{ chcp.com 65001 | Out-Null }} catch {{}}
$projectRoot = {project_root}
$cli = {cli}
$promptPath = {prompt_path}
$logPath = {log_path}
$platformLog = {platform_log}
$statusPath = {status_path}
$state = [ordered]@{{
  schema_version = '1.0.0'
  launch_id = {launch_id}
  project_root = $projectRoot
  run_id = {run_id}
  operation_id = {operation_id}
  platform = {platform}
  approval_mode = {approval_mode}
  status = 'running'
  process_id = $PID
  created_at = {created_at}
  started_at = (Get-Date).ToString('o')
  finished_at = $null
  prompt_path = $promptPath
  log_path = $logPath
  status_path = $statusPath
  error = $null
}}
function Quote-NativeArgument {{
  param([AllowEmptyString()][string]$Value)
  if ($Value.Length -eq 0) {{ return '""' }}
  if ($Value -notmatch '[\s"]') {{ return $Value }}
  $escaped = $Value -replace '(\\*)"', '$1$1\"'
  $escaped = $escaped -replace '(\\+)$', '$1$1'
  return '"' + $escaped + '"'
}}
function Invoke-Codex {{
  param([string[]]$Arguments)
  $argumentLine = (($Arguments | ForEach-Object {{ Quote-NativeArgument $_ }}) -join ' ')
  $process = Start-Process -FilePath $cli -ArgumentList $argumentLine -WorkingDirectory $projectRoot -NoNewWindow -Wait -PassThru
  $global:LASTEXITCODE = $process.ExitCode
}}
function Save-State {{
  $temporary = "$statusPath.tmp_$PID"
  $backup = "$statusPath.bak_$PID"
  $json = $state | ConvertTo-Json -Depth 4
  [System.IO.File]::WriteAllText($temporary, $json, $utf8)
  $startedAt = [DateTime]::UtcNow
  $delayMilliseconds = 10
  try {{
    while ($true) {{
      try {{
        if ([System.IO.File]::Exists($statusPath)) {{
          if ([System.IO.File]::Exists($backup)) {{
            [System.IO.File]::Delete($backup)
          }}
          [System.IO.File]::Replace($temporary, $statusPath, $backup, $true)
        }} else {{
          [System.IO.File]::Move($temporary, $statusPath)
        }}
        return
      }} catch [System.IO.IOException], [System.UnauthorizedAccessException] {{
        if (([DateTime]::UtcNow - $startedAt).TotalMilliseconds -ge 2000) {{ throw }}
        Start-Sleep -Milliseconds $delayMilliseconds
        $delayMilliseconds = [Math]::Min($delayMilliseconds * 2, 200)
      }}
    }}
  }} finally {{
    if ([System.IO.File]::Exists($temporary)) {{
      Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }}
    if ([System.IO.File]::Exists($backup)) {{
      Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    }}
  }}
}}
Save-State
try {{
  Start-Transcript -LiteralPath $logPath -Force | Out-Null
  Set-Location -LiteralPath $projectRoot
  $prompt = Get-Content -Raw -LiteralPath $promptPath -Encoding UTF8
  Write-Host '[쓰끼마] 준비된 Workflow Run을 시작합니다.' -ForegroundColor Cyan
  Write-Host ('[쓰끼마] Run ID: {{0}}' -f $state.run_id) -ForegroundColor DarkGray
  {command}
  if ($LASTEXITCODE -ne 0) {{ throw "CLI exited with code $LASTEXITCODE." }}
  $state.status = 'completed'
}} catch {{
  $state.status = 'failed'
  $state.error = $_.Exception.Message
  Write-Host ('[쓰끼마] 실행 실패: {{0}}' -f $state.error) -ForegroundColor Red
}} finally {{
  $state.finished_at = (Get-Date).ToString('o')
  Save-State
  try {{ Stop-Transcript | Out-Null }} catch {{}}
}}
Write-Host ''
Write-Host '[쓰끼마] 실행 기록이 저장되었습니다. 앱에서 상태를 확인할 수 있습니다.' -ForegroundColor Green
Read-Host 'Enter 키를 누르면 이 창을 닫습니다'
exit
"#,
        project_root = quote_powershell(&record.project_root),
        cli = quote_powershell(&user_path_string(executable)),
        prompt_path = quote_powershell(&record.prompt_path),
        log_path = quote_powershell(&record.log_path),
        platform_log = quote_powershell(&user_path_string(platform_log_path)),
        status_path = quote_powershell(&record.status_path),
        launch_id = quote_powershell(&record.launch_id),
        run_id = quote_powershell(&record.run_id),
        operation_id = record
            .operation_id
            .as_deref()
            .map(quote_powershell)
            .unwrap_or_else(|| "$null".to_owned()),
        platform = quote_powershell(&record.platform),
        approval_mode = quote_powershell(&record.approval_mode),
        created_at = quote_powershell(&record.created_at),
        command = platform_command(&record.platform, &record.approval_mode),
    )
}

fn build_powershell_bootstrap(script_path: &Path, working_directory: &Path) -> String {
    format!(
        r#"$ErrorActionPreference = 'Stop'
$target = {script_path}
$quotedTarget = "'" + $target.Replace("'", "''") + "'"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("& $quotedTarget"))
$process = Start-Process -FilePath 'powershell.exe' -WorkingDirectory {working_directory} -ArgumentList @('-NoLogo', '-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -PassThru
$process.Id
"#,
        script_path = quote_powershell(&user_path_string(script_path)),
        working_directory = quote_powershell(&user_path_string(working_directory)),
    )
}

fn launch_visible_powershell(script_path: &Path, working_directory: &Path) -> Result<u32, String> {
    let output = Command::new("powershell.exe")
        .args([
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
        ])
        .arg(build_powershell_bootstrap(script_path, working_directory))
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map_err(|error| format!("PowerShell 실행 도우미를 시작하지 못했습니다: {error}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        return Err(if stderr.is_empty() {
            format!(
                "PowerShell 실행 도우미가 종료 코드 {:?}로 중단되었습니다.",
                output.status.code()
            )
        } else {
            format!("PowerShell 실행 도우미가 중단되었습니다: {stderr}")
        });
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .rev()
        .find_map(|line| line.trim().parse::<u32>().ok())
        .ok_or_else(|| "PowerShell 실행 창의 프로세스 ID를 확인하지 못했습니다.".to_owned())
}

pub(crate) fn write_utf8_bom(path: &Path, contents: &str) -> Result<(), String> {
    let mut bytes = Vec::with_capacity(3 + contents.len());
    bytes.extend_from_slice(&[0xEF, 0xBB, 0xBF]);
    bytes.extend_from_slice(contents.as_bytes());
    fs::write(path, bytes).map_err(|error| format!("UTF-8 스크립트를 저장할 수 없습니다: {error}"))
}

fn write_record(path: &Path, record: &CliExecutionRecord) -> Result<(), String> {
    let bytes = serde_json::to_vec_pretty(record)
        .map_err(|error| format!("CLI 상태 JSON을 만들 수 없습니다: {error}"))?;
    atomic_write(path, &bytes).map_err(|error| format!("CLI 상태를 저장할 수 없습니다: {error}"))
}

fn execution_root(project_root: &Path, launch_id: &str) -> PathBuf {
    project_root
        .join(".schema-workflow")
        .join("desktop-launch")
        .join("requests")
        .join(launch_id)
}

fn inspect_record(status_path: &Path) -> Result<CliExecutionRecord, String> {
    let bytes = fs::read(status_path)
        .map_err(|error| format!("CLI 실행 상태를 읽을 수 없습니다: {error}"))?;
    let json_bytes = bytes
        .strip_prefix(&[0xEF, 0xBB, 0xBF])
        .unwrap_or(bytes.as_slice());
    let mut record: CliExecutionRecord = serde_json::from_slice(json_bytes)
        .map_err(|error| format!("CLI 실행 상태 JSON이 올바르지 않습니다: {error}"))?;
    let now_milliseconds = now_marker().parse::<u128>().unwrap_or_default();
    let stale_starting = starting_record_is_stale(&record, now_milliseconds);
    let stopped_process = matches!(record.status.as_str(), "starting" | "running")
        && record.process_id.is_some_and(|id| !process_is_running(id));
    if stale_starting || stopped_process {
        record.status = "interrupted".to_owned();
        record.finished_at = Some(now_marker());
        record.error = Some(if stale_starting {
            "CLI 실행 창이 시작 기록을 남기지 못했습니다.".to_owned()
        } else {
            "CLI 실행 창이 결과 기록 없이 종료되었습니다.".to_owned()
        });
        write_record(status_path, &record)?;
    }
    Ok(record)
}

fn list_execution_records(project_root: &Path) -> Result<Vec<CliExecutionRecord>, String> {
    let requests_root = project_root
        .join(".schema-workflow")
        .join("desktop-launch")
        .join("requests");
    if !requests_root.is_dir() {
        return Ok(Vec::new());
    }

    let mut records = Vec::new();
    let entries = fs::read_dir(&requests_root)
        .map_err(|error| format!("CLI 실행 기록 폴더를 읽을 수 없습니다: {error}"))?;
    for entry in entries.flatten() {
        let status_path = entry.path().join("execution.json");
        if !status_path.is_file() {
            continue;
        }
        match inspect_record(&status_path) {
            Ok(record) => records.push(record),
            Err(error) => eprintln!(
                "Skipping unreadable CLI execution record {}: {error}",
                status_path.display()
            ),
        }
    }
    records.sort_by(|left, right| left.created_at.cmp(&right.created_at));
    Ok(records)
}

fn launch_workflow_cli_sync(
    project_root: String,
    run_id: String,
    platform: String,
    approval_mode: String,
    approved: bool,
) -> Result<Value, String> {
    if !approved {
        return Err("CLI 실행에 대한 사용자 확인이 필요합니다.".to_owned());
    }
    let root = canonical_project_root(&project_root)?;
    let run_id = safe_run_id(&run_id)?.to_owned();
    let platform = platform.trim().to_owned();
    let approval_mode = normalize_approval_mode(&approval_mode)?.to_owned();
    let manifest = read_manifest(&root, &run_id)?;
    if !manifest_is_launchable(&manifest) {
        return Err(
            "완료·실패·검토 대기 상태의 Run은 다시 실행할 수 없습니다. 새 작업에서 실행용 Run을 준비하세요."
                .to_owned(),
        );
    }
    preflight_research_run(&root, &run_id)
        .map_err(|error| format!("리서치 사전 검증을 통과하지 못했습니다: {error}"))?;
    ensure_project_platform(&root, &platform)?;
    let executable = resolve_platform_executable(&platform)?;
    let launch_id = format!("launch_{}_{}", now_marker(), std::process::id());
    let request_root = execution_root(&root, &launch_id);
    fs::create_dir_all(&request_root)
        .map_err(|error| format!("CLI 실행 기록 폴더를 만들 수 없습니다: {error}"))?;
    let prompt_path = request_root.join("prompt.txt");
    let script_path = request_root.join("launch.ps1");
    let log_path = request_root.join("execution.log");
    let platform_log_path = request_root.join("platform.log");
    let status_path = request_root.join("execution.json");
    fs::write(
        &prompt_path,
        build_prompt(&root, &run_id, &platform, &manifest),
    )
    .map_err(|error| format!("CLI 전달 프롬프트를 저장할 수 없습니다: {error}"))?;
    let created_at = now_marker();
    let mut record = CliExecutionRecord {
        schema_version: EXECUTION_SCHEMA_VERSION.to_owned(),
        launch_id,
        project_root: user_path_string(&root),
        run_id,
        operation_id: manifest
            .get("operation_id")
            .and_then(Value::as_str)
            .map(str::to_owned),
        platform: platform.clone(),
        approval_mode,
        status: "starting".to_owned(),
        process_id: None,
        created_at,
        started_at: None,
        finished_at: None,
        prompt_path: user_path_string(&prompt_path),
        log_path: user_path_string(&log_path),
        status_path: user_path_string(&status_path),
        error: None,
    };
    write_record(&status_path, &record)?;
    write_utf8_bom(
        &script_path,
        &build_launch_script(&executable, &platform_log_path, &record),
    )
    .map_err(|error| format!("CLI 실행 스크립트를 저장할 수 없습니다: {error}"))?;

    record.process_id = Some(launch_visible_powershell(&script_path, &root)?);
    record.status = "running".to_owned();
    // The child script owns execution.json after launch. Rewriting the stale
    // pre-spawn record here can erase its started, failed, or completed state.
    Ok(json!(record))
}

#[tauri::command]
pub async fn launch_workflow_cli(
    project_root: String,
    run_id: String,
    platform: String,
    approval_mode: String,
    approved: bool,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        launch_workflow_cli_sync(project_root, run_id, platform, approval_mode, approved)
    })
    .await
    .map_err(|error| format!("CLI launch preparation was interrupted: {error}"))?
}

#[tauri::command]
fn stop_workflow_cli_sync(
    project_root: String,
    launch_id: String,
    approved: bool,
) -> Result<Value, String> {
    if !approved {
        return Err("CLI stop requires explicit user approval.".to_owned());
    }
    let root = canonical_project_root(&project_root)?;
    let launch_id = safe_run_id(&launch_id)?;
    let status_path = execution_root(&root, launch_id).join("execution.json");
    let mut record = inspect_record(&status_path)?;
    if matches!(
        record.status.as_str(),
        "completed" | "failed" | "interrupted" | "aborted"
    ) {
        return Ok(json!(record));
    }

    let process_id = record
        .process_id
        .ok_or_else(|| "The CLI process ID has not been recorded yet.".to_owned())?;
    terminate_process_tree(process_id)?;
    for _ in 0..20 {
        if !process_is_running(process_id) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    if process_is_running(process_id) {
        return Err("The CLI process did not terminate.".to_owned());
    }

    record.status = "aborted".to_owned();
    record.finished_at = Some(now_marker());
    record.error = Some("CLI execution was stopped by the user.".to_owned());
    write_record(&status_path, &record)?;
    Ok(json!(record))
}

#[tauri::command]
pub async fn stop_workflow_cli(
    project_root: String,
    launch_id: String,
    approved: bool,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        stop_workflow_cli_sync(project_root, launch_id, approved)
    })
    .await
    .map_err(|error| format!("CLI stop failed: {error}"))?
}

#[tauri::command]
pub async fn inspect_workflow_cli_launch(
    project_root: String,
    launch_id: String,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = canonical_project_root(&project_root)?;
        let launch_id = safe_run_id(&launch_id)?;
        let status_path = execution_root(&root, launch_id).join("execution.json");
        Ok(json!(inspect_record(&status_path)?))
    })
    .await
    .map_err(|error| format!("CLI execution inspection failed: {error}"))?
}

#[tauri::command]
pub async fn list_workflow_cli_launches(project_root: String) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = canonical_project_root(&project_root)?;
        Ok(json!(list_execution_records(&root)?))
    })
    .await
    .map_err(|error| format!("CLI execution listing failed: {error}"))?
}
#[cfg(test)]
mod tests {
    use super::{
        build_launch_script, build_powershell_bootstrap, build_prompt, executable_names,
        find_codex_native_in_package, inspect_record, list_execution_records,
        manifest_is_launchable, normalize_approval_mode, platform_command, quote_powershell,
        safe_run_id, starting_record_is_stale, stop_workflow_cli_sync, write_record,
        write_utf8_bom, CliExecutionRecord, EXECUTION_SCHEMA_VERSION,
    };
    use serde_json::json;
    use std::{env, fs, path::Path, process};

    #[test]
    fn stop_requires_explicit_approval() {
        let error = stop_workflow_cli_sync(
            "C:\\does-not-matter".to_owned(),
            "launch-test".to_owned(),
            false,
        )
        .expect_err("stop should require approval");
        assert_eq!(error, "CLI stop requires explicit user approval.");
    }

    #[test]
    fn rejects_run_path_traversal() {
        assert!(safe_run_id("run-1").is_ok());
        for invalid in ["", "../run", "a/b", "a\\b"] {
            assert!(safe_run_id(invalid).is_err(), "{invalid}");
        }
    }

    #[test]
    fn escapes_powershell_literal_values() {
        assert_eq!(quote_powershell("a'b"), "'a''b'");
    }

    #[test]
    fn maps_review_and_auto_modes_to_platform_specific_flags() {
        let codex_review = platform_command("codex", "review");
        let codex_auto = platform_command("codex", "auto");
        assert!(codex_review.starts_with("Invoke-Codex -Arguments"));
        assert!(codex_review.contains("'on-request'"));
        assert!(codex_auto.contains("'never'"));
        assert!(codex_review.contains("'workspace-write'"));
        assert!(codex_review.contains("'--no-alt-screen'"));
        assert!(!codex_review.contains(" exec "));
        assert!(platform_command("claude", "auto").contains("permission-mode auto"));
        assert!(platform_command("antigravity", "auto").contains("dangerously-skip-permissions"));
        assert!(normalize_approval_mode("unknown").is_err());
    }

    #[test]
    fn prefers_user_command_shims_over_packaged_executables() {
        assert_eq!(
            executable_names("codex").expect("Codex names should resolve")[0],
            "codex.cmd"
        );
        assert_eq!(
            executable_names("claude").expect("Claude names should resolve")[0],
            "claude.cmd"
        );
    }

    #[test]
    fn finds_the_native_codex_binary_inside_an_npm_package() {
        let test_root = env::temp_dir().join(format!(
            "skkima-native-codex-resolution-test-{}",
            process::id()
        ));
        let binary = test_root
            .join("node_modules")
            .join("@openai")
            .join("codex-win32-x64")
            .join("vendor")
            .join("x86_64-pc-windows-msvc")
            .join("bin")
            .join("codex.exe");
        fs::create_dir_all(binary.parent().expect("binary should have a parent"))
            .expect("temporary package should be created");
        fs::write(&binary, b"test").expect("temporary binary should be written");

        let found =
            find_codex_native_in_package(&test_root).expect("native Codex binary should be found");

        fs::remove_dir_all(&test_root).expect("temporary package should be removed");
        assert_eq!(found, binary);
    }

    #[test]
    fn powershell_bootstrap_opens_a_detached_encoded_script() {
        let bootstrap = build_powershell_bootstrap(
            Path::new(r"C:\work folder\launch.ps1"),
            Path::new(r"C:\work folder"),
        );
        assert!(bootstrap.contains("Start-Process"));
        assert!(bootstrap.contains("-NoExit"));
        assert!(bootstrap.contains("-EncodedCommand"));
        assert!(bootstrap.contains("$process.Id"));
        assert!(!bootstrap.contains("CREATE_NEW_CONSOLE"));
    }

    #[test]
    fn starting_record_without_a_process_becomes_stale_after_the_grace_period() {
        let record = CliExecutionRecord {
            schema_version: EXECUTION_SCHEMA_VERSION.to_owned(),
            launch_id: "launch-starting".to_owned(),
            project_root: r"C:\work".to_owned(),
            run_id: "run-starting".to_owned(),
            operation_id: None,
            platform: "codex".to_owned(),
            approval_mode: "review".to_owned(),
            status: "starting".to_owned(),
            process_id: None,
            created_at: "1000".to_owned(),
            started_at: None,
            finished_at: None,
            prompt_path: r"C:\work\prompt.txt".to_owned(),
            log_path: r"C:\work\execution.log".to_owned(),
            status_path: r"C:\work\execution.json".to_owned(),
            error: None,
        };
        assert!(!starting_record_is_stale(&record, 5_999));
        assert!(starting_record_is_stale(&record, 6_000));
    }

    #[test]
    fn writes_powershell_scripts_with_a_utf8_bom() {
        let path =
            env::temp_dir().join(format!("skkima-cli-utf8-script-test-{}.ps1", process::id()));
        write_utf8_bom(&path, "Write-Host '쓰끼마 실행'").expect("UTF-8 script should be written");
        let bytes = fs::read(&path).expect("script should be readable");
        fs::remove_file(&path).expect("temporary script should be removed");
        assert_eq!(&bytes[..3], &[0xEF, 0xBB, 0xBF]);
        assert_eq!(
            std::str::from_utf8(&bytes[3..]).expect("script should stay UTF-8"),
            "Write-Host '쓰끼마 실행'"
        );
    }

    #[test]
    fn launch_script_writes_status_with_explicit_utf8() {
        let record = CliExecutionRecord {
            schema_version: EXECUTION_SCHEMA_VERSION.to_owned(),
            launch_id: "launch-1".to_owned(),
            project_root: r"C:\work".to_owned(),
            run_id: "run-한글".to_owned(),
            operation_id: Some("operation-1".to_owned()),
            platform: "codex".to_owned(),
            approval_mode: "review".to_owned(),
            status: "starting".to_owned(),
            process_id: None,
            created_at: "0".to_owned(),
            started_at: None,
            finished_at: None,
            prompt_path: r"C:\work\prompt.txt".to_owned(),
            log_path: r"C:\work\execution.log".to_owned(),
            status_path: r"C:\work\execution.json".to_owned(),
            error: None,
        };

        let script = build_launch_script(
            Path::new(r"C:\tools\codex.cmd"),
            Path::new(r"C:\work\platform.log"),
            &record,
        );

        assert!(script.contains("[System.IO.File]::WriteAllText($temporary, $json, $utf8)"));
        assert!(
            script.contains("[System.IO.File]::Replace($temporary, $statusPath, $backup, $true)")
        );
        assert!(!script.contains("[System.IO.File]::Replace($temporary, $statusPath, $null"));
        assert!(script.contains("TotalMilliseconds -ge 2000"));
        assert!(!script.contains("Set-Content"));
        assert!(script.contains("function Quote-NativeArgument"));
        assert!(script.contains("Start-Process -FilePath $cli"));
        assert!(script.contains("run-한글"));
    }

    #[test]
    fn prompt_reuses_the_prepared_run_contract() {
        let prompt = build_prompt(
            Path::new(r"C:\work"),
            "run-1",
            "codex",
            &json!({
                "operation_id": "op-1",
                "session_reference": "session-1",
                "relation_type": "branch",
                "parent_run_id": "run-0",
                "source": { "raw_text": "작업 제목: 검증" }
            }),
        );
        assert!(prompt.contains("@schema-workflow"));
        assert!(prompt.contains("PreparedRunId:\nrun-1"));
        assert!(prompt.contains("init 또는 continue-run을 다시 실행하거나 새 Run을 만들지 않는다"));
    }

    #[test]
    fn only_running_unfinished_manifests_are_launchable() {
        assert!(manifest_is_launchable(&json!({
            "status": "running",
            "summary": {"human_report_quality_gate": {"request_completed": false}}
        })));
        assert!(manifest_is_launchable(&json!({
            "status": "running",
            "active_continuation_operation_id": "operation-continuation",
            "summary": {"human_report_quality_gate": {"request_completed": true}}
        })));
        assert!(!manifest_is_launchable(&json!({
            "status": "completed",
            "summary": {"human_report_quality_gate": {"request_completed": true}}
        })));
        assert!(!manifest_is_launchable(&json!({
            "status": "waiting_user",
            "summary": {"human_report_quality_gate": {"request_completed": false}}
        })));
    }

    #[test]
    fn marks_an_orphaned_cli_process_as_interrupted() {
        let test_root = env::temp_dir().join(format!(
            "schema-workflow-cli-interruption-test-{}",
            process::id()
        ));
        fs::create_dir_all(&test_root).expect("temporary launch directory should be created");
        let status_path = test_root.join("execution.json");
        let record = CliExecutionRecord {
            schema_version: EXECUTION_SCHEMA_VERSION.to_owned(),
            launch_id: "launch-orphaned".to_owned(),
            project_root: test_root.to_string_lossy().into_owned(),
            run_id: "run-orphaned".to_owned(),
            operation_id: Some("operation-orphaned".to_owned()),
            platform: "codex".to_owned(),
            approval_mode: "review".to_owned(),
            status: "running".to_owned(),
            process_id: Some(u32::MAX),
            created_at: "0".to_owned(),
            started_at: Some("0".to_owned()),
            finished_at: None,
            prompt_path: test_root.join("prompt.txt").to_string_lossy().into_owned(),
            log_path: test_root
                .join("execution.log")
                .to_string_lossy()
                .into_owned(),
            status_path: status_path.to_string_lossy().into_owned(),
            error: None,
        };
        let serialized =
            serde_json::to_string_pretty(&record).expect("execution record should serialize");
        write_utf8_bom(&status_path, &serialized)
            .expect("PowerShell-compatible execution record should be written");

        let interrupted = inspect_record(&status_path).expect("record should be inspected");

        fs::remove_dir_all(&test_root).expect("temporary launch directory should be removed");
        assert_eq!(interrupted.status, "interrupted");
        assert!(interrupted.finished_at.is_some());
        assert!(interrupted.error.is_some());
    }

    #[test]
    fn recovers_project_execution_records_from_disk() {
        let test_root = env::temp_dir().join(format!(
            "schema-workflow-cli-record-list-test-{}",
            process::id()
        ));
        let request_root = test_root
            .join(".schema-workflow")
            .join("desktop-launch")
            .join("requests")
            .join("launch-orphaned");
        fs::create_dir_all(&request_root).expect("temporary launch directory should be created");
        let status_path = request_root.join("execution.json");
        let record = CliExecutionRecord {
            schema_version: EXECUTION_SCHEMA_VERSION.to_owned(),
            launch_id: "launch-orphaned".to_owned(),
            project_root: test_root.to_string_lossy().into_owned(),
            run_id: "run-orphaned".to_owned(),
            operation_id: Some("operation-orphaned".to_owned()),
            platform: "claude".to_owned(),
            approval_mode: "auto".to_owned(),
            status: "running".to_owned(),
            process_id: Some(u32::MAX),
            created_at: "1".to_owned(),
            started_at: Some("1".to_owned()),
            finished_at: None,
            prompt_path: request_root
                .join("prompt.txt")
                .to_string_lossy()
                .into_owned(),
            log_path: request_root
                .join("execution.log")
                .to_string_lossy()
                .into_owned(),
            status_path: status_path.to_string_lossy().into_owned(),
            error: None,
        };
        write_record(&status_path, &record).expect("execution record should be written");

        let records =
            list_execution_records(&test_root).expect("project records should be recovered");

        fs::remove_dir_all(&test_root).expect("temporary launch directory should be removed");
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].status, "interrupted");
        assert_eq!(records[0].platform, "claude");
        assert_eq!(records[0].approval_mode, "auto");
    }
}
