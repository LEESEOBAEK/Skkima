use crate::cli_execution::{quote_powershell, resolve_platform_executable, write_utf8_bom};
use crate::windows_process::process_is_running;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::os::windows::process::CommandExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

const CREATE_NEW_CONSOLE: u32 = 0x00000010;
const TEST_SCHEMA_VERSION: &str = "1.0.0";
const TEST_OWNER: &str = "skkima-skill-smoke-test";
const TEST_SKILL_ID: &str = "skkima-smoke-test";
const MAX_BASELINE_FILES: usize = 10_000;
const MAX_BASELINE_BYTES: u64 = 256 * 1024 * 1024;
const TEST_SKILL: &str = include_str!("../../tests/fixtures/skkima-smoke-skill/SKILL.md");

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct FileFingerprint {
    path: String,
    sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct StoredSmokeTest {
    schema_version: String,
    test_id: String,
    project_root: String,
    platform: String,
    platform_label: String,
    state: String,
    token: String,
    proof_path: String,
    skill_target: String,
    installed_by_test: bool,
    created_at: u128,
    process_id: Option<u32>,
    log_path: String,
    script_path: String,
    message: String,
    unexpected_changes: Vec<String>,
    baseline: Vec<FileFingerprint>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SmokeTestStatus {
    test_id: String,
    project_root: String,
    platform: String,
    platform_label: String,
    state: String,
    proof_path: String,
    created_at: u128,
    process_id: Option<u32>,
    log_path: String,
    message: String,
    unexpected_changes: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SmokeTestSnapshot {
    project_root: String,
    tests: Vec<SmokeTestStatus>,
}

#[derive(Serialize)]
struct SmokeInstallManifest<'a> {
    schema_version: &'a str,
    owner: &'a str,
    platform: &'a str,
    compatible_platforms: Vec<&'a str>,
    skill_id: &'a str,
    source_hash: String,
    installed_at: u128,
    test_id: &'a str,
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn user_path(path: &Path) -> String {
    path.to_string_lossy()
        .strip_prefix(r"\\?\")
        .unwrap_or(&path.to_string_lossy())
        .to_owned()
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

fn platform_label(platform: &str) -> Result<&'static str, String> {
    match platform {
        "codex" => Ok("Codex"),
        "claude" => Ok("Claude Code"),
        "antigravity" => Ok("Antigravity"),
        _ => Err(format!("지원하지 않는 테스트 플랫폼입니다: {platform}")),
    }
}

fn skill_target(project_root: &Path, platform: &str) -> Result<PathBuf, String> {
    let relative = match platform {
        "codex" | "antigravity" => Path::new(".agents/skills"),
        "claude" => Path::new(".claude/skills"),
        _ => return Err(format!("지원하지 않는 테스트 플랫폼입니다: {platform}")),
    };
    Ok(project_root.join(relative).join(TEST_SKILL_ID))
}

fn test_root(project_root: &Path) -> PathBuf {
    project_root.join(".skkima").join("skill-tests")
}

fn safe_test_id(test_id: &str) -> Result<&str, String> {
    let path = Path::new(test_id);
    if test_id.is_empty()
        || path.components().count() != 1
        || !matches!(path.components().next(), Some(Component::Normal(_)))
    {
        return Err("테스트 ID가 올바르지 않습니다.".to_owned());
    }
    Ok(test_id)
}

fn state_path(project_root: &Path, test_id: &str) -> Result<PathBuf, String> {
    Ok(test_root(project_root).join(format!("{}.json", safe_test_id(test_id)?)))
}

fn proof_path(project_root: &Path, test_id: &str) -> Result<PathBuf, String> {
    Ok(test_root(project_root).join(format!("{}.txt", safe_test_id(test_id)?)))
}

fn write_record(project_root: &Path, record: &StoredSmokeTest) -> Result<(), String> {
    let path = state_path(project_root, &record.test_id)?;
    let temporary = path.with_extension(format!("json.tmp-{}", std::process::id()));
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(record)
            .map_err(|error| format!("스킬 테스트 기록을 직렬화하지 못했습니다: {error}"))?,
    )
    .map_err(|error| format!("스킬 테스트 임시 기록을 쓰지 못했습니다: {error}"))?;
    fs::rename(&temporary, &path)
        .map_err(|error| format!("스킬 테스트 기록을 확정하지 못했습니다: {error}"))
}

fn read_record(project_root: &Path, test_id: &str) -> Result<StoredSmokeTest, String> {
    let path = state_path(project_root, test_id)?;
    let bytes =
        fs::read(&path).map_err(|error| format!("스킬 테스트 기록을 읽지 못했습니다: {error}"))?;
    serde_json::from_slice(&bytes)
        .map_err(|error| format!("스킬 테스트 기록 형식이 올바르지 않습니다: {error}"))
}

fn hash_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn normalized_relative(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

fn excluded_path(relative: &Path, smoke_target: &Path) -> bool {
    relative.starts_with(".git")
        || relative.starts_with(Path::new(".skkima/skill-tests"))
        || relative.starts_with(smoke_target)
}

fn collect_baseline(
    project_root: &Path,
    smoke_target: &Path,
) -> Result<Vec<FileFingerprint>, String> {
    fn visit(
        root: &Path,
        current: &Path,
        smoke_target: &Path,
        files: &mut Vec<FileFingerprint>,
        total_bytes: &mut u64,
    ) -> Result<(), String> {
        for entry in fs::read_dir(current)
            .map_err(|error| format!("프로젝트 기준선을 읽지 못했습니다: {error}"))?
        {
            let entry =
                entry.map_err(|error| format!("프로젝트 항목을 읽지 못했습니다: {error}"))?;
            let path = entry.path();
            let relative = path
                .strip_prefix(root)
                .map_err(|_| "프로젝트 기준선 경로가 루트 밖을 가리킵니다.".to_owned())?;
            if excluded_path(relative, smoke_target) {
                continue;
            }
            let file_type = entry
                .file_type()
                .map_err(|error| format!("프로젝트 항목 형식을 읽지 못했습니다: {error}"))?;
            if file_type.is_symlink() {
                continue;
            }
            if file_type.is_dir() {
                visit(root, &path, smoke_target, files, total_bytes)?;
                continue;
            }
            if !file_type.is_file() {
                continue;
            }
            if files.len() >= MAX_BASELINE_FILES {
                return Err(format!(
                    "안전 비교 대상이 {}개를 초과해 테스트를 시작하지 않았습니다.",
                    MAX_BASELINE_FILES
                ));
            }
            let bytes = fs::read(&path)
                .map_err(|error| format!("기준선 파일을 읽지 못했습니다: {error}"))?;
            *total_bytes = total_bytes.saturating_add(bytes.len() as u64);
            if *total_bytes > MAX_BASELINE_BYTES {
                return Err(
                    "안전 비교 대상이 256MB를 초과해 테스트를 시작하지 않았습니다.".to_owned(),
                );
            }
            files.push(FileFingerprint {
                path: normalized_relative(relative),
                sha256: hash_bytes(&bytes),
            });
        }
        Ok(())
    }

    let mut files = Vec::new();
    let mut total_bytes = 0;
    visit(
        project_root,
        project_root,
        smoke_target,
        &mut files,
        &mut total_bytes,
    )?;
    files.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(files)
}

fn compare_baseline(
    project_root: &Path,
    smoke_target: &Path,
    baseline: &[FileFingerprint],
) -> Result<Vec<String>, String> {
    let current = collect_baseline(project_root, smoke_target)?;
    let before = baseline
        .iter()
        .map(|item| (item.path.as_str(), item.sha256.as_str()))
        .collect::<BTreeMap<_, _>>();
    let after = current
        .iter()
        .map(|item| (item.path.as_str(), item.sha256.as_str()))
        .collect::<BTreeMap<_, _>>();
    let mut changes = Vec::new();
    for path in before.keys().chain(after.keys()) {
        if before.get(path) != after.get(path) && !changes.iter().any(|item| item == *path) {
            changes.push((*path).to_owned());
        }
    }
    Ok(changes)
}

fn install_smoke_skill(
    project_root: &Path,
    platform: &str,
    test_id: &str,
) -> Result<(PathBuf, bool), String> {
    let target = skill_target(project_root, platform)?;
    if target.exists() {
        let current = fs::read_to_string(target.join("SKILL.md")).unwrap_or_default();
        if current == TEST_SKILL {
            let owned_by_test = fs::read(target.join(".skkima-install.json"))
                .ok()
                .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
                .and_then(|value| {
                    value
                        .get("owner")
                        .and_then(|item| item.as_str())
                        .map(str::to_owned)
                })
                .as_deref()
                == Some(TEST_OWNER);
            return Ok((target, owned_by_test));
        }
        return Err(format!(
            "테스트 스킬 경로에 다른 스킬이 있어 덮어쓰지 않았습니다: {}",
            user_path(&target)
        ));
    }
    let temporary = target.with_extension(format!("tmp-{}", std::process::id()));
    if temporary.exists() {
        fs::remove_dir_all(&temporary)
            .map_err(|error| format!("이전 테스트 임시 폴더를 정리하지 못했습니다: {error}"))?;
    }
    fs::create_dir_all(&temporary)
        .map_err(|error| format!("테스트 스킬 폴더를 만들지 못했습니다: {error}"))?;
    fs::write(temporary.join("SKILL.md"), TEST_SKILL)
        .map_err(|error| format!("테스트 SKILL.md를 쓰지 못했습니다: {error}"))?;
    let compatible_platforms = if matches!(platform, "codex" | "antigravity") {
        vec!["codex", "antigravity"]
    } else {
        vec!["claude"]
    };
    let manifest = SmokeInstallManifest {
        schema_version: TEST_SCHEMA_VERSION,
        owner: TEST_OWNER,
        platform,
        compatible_platforms,
        skill_id: TEST_SKILL_ID,
        source_hash: hash_bytes(TEST_SKILL.as_bytes()),
        installed_at: now_millis(),
        test_id,
    };
    fs::write(
        temporary.join(".skkima-install.json"),
        serde_json::to_vec_pretty(&manifest)
            .map_err(|error| format!("테스트 설치 기록을 직렬화하지 못했습니다: {error}"))?,
    )
    .map_err(|error| format!("테스트 설치 기록을 쓰지 못했습니다: {error}"))?;
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("플랫폼 스킬 경로를 만들지 못했습니다: {error}"))?;
    }
    fs::rename(&temporary, &target)
        .map_err(|error| format!("테스트 스킬 설치를 확정하지 못했습니다: {error}"))?;
    Ok((target, true))
}

fn skill_invocation(platform: &str) -> &'static str {
    if platform == "codex" {
        "$skkima-smoke-test"
    } else {
        "/skkima-smoke-test"
    }
}

fn build_prompt(platform: &str, proof_relative: &str, token: &str) -> String {
    format!(
        "{}\n\nRun the explicitly requested Skkima smoke test.\nCreate exactly one proof file at `{}`.\nWrite exactly this token and one trailing newline: `{}`\nDo not modify, delete, execute, or inspect any other project file.\nAfter the proof file is written, respond with TEST_COMPLETE.",
        skill_invocation(platform), proof_relative, token
    )
}

fn platform_command(platform: &str) -> Result<&'static str, String> {
    match platform {
        "codex" => Ok("$prompt | & $cli exec -C $projectRoot --sandbox workspace-write --skip-git-repo-check --ephemeral - 2>&1"),
        "claude" => Ok("& $cli --print --permission-mode acceptEdits --no-session-persistence $prompt 2>&1"),
        "antigravity" => Ok("& $cli --add-dir $projectRoot --mode accept-edits --sandbox --print-timeout 5m --print $prompt 2>&1"),
        _ => Err(format!("지원하지 않는 테스트 플랫폼입니다: {platform}")),
    }
}

fn build_launch_script(
    project_root: &Path,
    executable: &Path,
    platform: &str,
    prompt: &str,
    log_path: &Path,
    platform_log: &Path,
) -> Result<String, String> {
    Ok(format!(
        r#"$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
try {{ chcp.com 65001 | Out-Null }} catch {{}}
$projectRoot = {project_root}
$cli = {cli}
$prompt = {prompt}
$platformLog = {platform_log}
Set-Location -LiteralPath $projectRoot
try {{
  Start-Transcript -LiteralPath {log_path} -Force | Out-Null
  Write-Host '[쓰끼마] 비대화형 스킬 인식 테스트를 시작합니다.' -ForegroundColor Cyan
  $smokeOutput = {command}
  $smokeExitCode = $LASTEXITCODE
  $smokeOutput | Set-Content -LiteralPath $platformLog -Encoding utf8
  if ($smokeExitCode -ne 0) {{ throw "CLI exited with code $smokeExitCode." }}
}} catch {{
  Write-Host ('[쓰끼마] 테스트 실행 실패: {{0}}' -f $_.Exception.Message) -ForegroundColor Red
}} finally {{
  try {{ Stop-Transcript | Out-Null }} catch {{}}
}}
"#,
        project_root = quote_powershell(&user_path(project_root)),
        cli = quote_powershell(&user_path(executable)),
        prompt = quote_powershell(prompt),
        platform_log = quote_powershell(&user_path(platform_log)),
        log_path = quote_powershell(&user_path(log_path)),
        command = platform_command(platform)?,
    ))
}

fn test_identity(platform: &str) -> (String, String) {
    (
        format!("smoke-{}-{}", platform, now_millis()),
        format!("SKKIMA-{}-{}", platform.to_ascii_uppercase(), now_millis()),
    )
}

fn prepare_smoke_test_with_identity(
    project_root: &Path,
    platform: &str,
    test_id: String,
    token: String,
) -> Result<StoredSmokeTest, String> {
    let label = platform_label(platform)?;
    let root = test_root(project_root);
    fs::create_dir_all(&root)
        .map_err(|error| format!("스킬 테스트 폴더를 만들지 못했습니다: {error}"))?;
    let (target, installed_by_test) = install_smoke_skill(project_root, platform, &test_id)?;
    let target_relative = target
        .strip_prefix(project_root)
        .map_err(|_| "테스트 스킬 경로가 프로젝트 밖을 가리킵니다.".to_owned())?;
    let baseline = match collect_baseline(project_root, target_relative) {
        Ok(value) => value,
        Err(error) => {
            if installed_by_test {
                let _ = fs::remove_dir_all(&target);
            }
            return Err(error);
        }
    };
    let proof_path = root.join(format!("{test_id}.txt"));
    let script_path = root.join(format!("{test_id}.ps1"));
    let log_path = root.join(format!("{test_id}.log"));
    Ok(StoredSmokeTest {
        schema_version: TEST_SCHEMA_VERSION.to_owned(),
        test_id,
        project_root: user_path(project_root),
        platform: platform.to_owned(),
        platform_label: label.to_owned(),
        state: "prepared".to_owned(),
        token,
        proof_path: user_path(&proof_path),
        skill_target: user_path(&target),
        installed_by_test,
        created_at: now_millis(),
        process_id: None,
        log_path: user_path(&log_path),
        script_path: user_path(&script_path),
        message: "테스트 CLI 실행을 준비했습니다.".to_owned(),
        unexpected_changes: Vec::new(),
        baseline,
    })
}

#[cfg(test)]
fn prepare_smoke_test(project_root: &Path, platform: &str) -> Result<StoredSmokeTest, String> {
    let (test_id, token) = test_identity(platform);
    prepare_smoke_test_with_identity(project_root, platform, test_id, token)
}

fn preparation_failure_record(
    project_root: &Path,
    platform: &str,
    test_id: String,
    token: String,
    error: &str,
) -> Result<StoredSmokeTest, String> {
    let root = test_root(project_root);
    fs::create_dir_all(&root).map_err(|create_error| {
        format!("스킬 테스트 진단 폴더를 만들지 못했습니다: {create_error}")
    })?;
    let target = skill_target(project_root, platform)?;
    Ok(StoredSmokeTest {
        schema_version: TEST_SCHEMA_VERSION.to_owned(),
        test_id: test_id.clone(),
        project_root: user_path(project_root),
        platform: platform.to_owned(),
        platform_label: platform_label(platform)?.to_owned(),
        state: "failed".to_owned(),
        token,
        proof_path: user_path(&root.join(format!("{test_id}.txt"))),
        skill_target: user_path(&target),
        installed_by_test: false,
        created_at: now_millis(),
        process_id: None,
        log_path: user_path(&root.join(format!("{test_id}.log"))),
        script_path: user_path(&root.join(format!("{test_id}.ps1"))),
        message: format!("테스트 준비 실패: {error}"),
        unexpected_changes: Vec::new(),
        baseline: Vec::new(),
    })
}

fn persist_preparation_failure(
    project_root: &Path,
    platform: &str,
    test_id: String,
    token: String,
    error: &str,
) -> Result<SmokeTestStatus, String> {
    let record = preparation_failure_record(project_root, platform, test_id, token, error)?;
    write_record(project_root, &record)?;
    Ok(public_status(record))
}

fn inspect_record(
    project_root: &Path,
    mut record: StoredSmokeTest,
) -> Result<StoredSmokeTest, String> {
    let target = skill_target(project_root, &record.platform)?;
    let target_relative = target
        .strip_prefix(project_root)
        .map_err(|_| "테스트 스킬 경로가 프로젝트 밖을 가리킵니다.".to_owned())?;
    record.unexpected_changes = compare_baseline(project_root, target_relative, &record.baseline)?;
    let skill_intact =
        fs::read_to_string(target.join("SKILL.md")).is_ok_and(|contents| contents == TEST_SKILL);
    if !skill_intact {
        record
            .unexpected_changes
            .push(normalized_relative(target_relative));
    }
    record.unexpected_changes.sort();
    record.unexpected_changes.dedup();
    let proof = fs::read_to_string(proof_path(project_root, &record.test_id)?).ok();
    let proof_valid = proof
        .as_deref()
        .is_some_and(|contents| contents.trim_end_matches(['\r', '\n']) == record.token);
    let process_running = record.process_id.is_some_and(process_is_running);
    if !record.unexpected_changes.is_empty() {
        record.state = "failed".to_owned();
        record.message = format!(
            "허용하지 않은 프로젝트 변경 {}건을 발견했습니다.",
            record.unexpected_changes.len()
        );
    } else if proof_valid {
        record.state = "passed".to_owned();
        record.message = "스킬 인식과 제한된 증명 파일 생성을 확인했습니다.".to_owned();
    } else if proof.is_some() {
        record.state = "failed".to_owned();
        record.message = "증명 파일의 토큰이 요청값과 일치하지 않습니다.".to_owned();
    } else if record.process_id.is_some() && !process_running {
        record.state = "failed".to_owned();
        record.message = "CLI가 증명 파일을 만들지 않고 종료되었습니다.".to_owned();
    } else if process_running {
        record.state = "running".to_owned();
        record.message = "CLI에서 테스트 스킬을 실행하고 있습니다.".to_owned();
    }
    write_record(project_root, &record)?;
    Ok(record)
}

fn public_status(record: StoredSmokeTest) -> SmokeTestStatus {
    SmokeTestStatus {
        test_id: record.test_id,
        project_root: record.project_root,
        platform: record.platform,
        platform_label: record.platform_label,
        state: record.state,
        proof_path: record.proof_path,
        created_at: record.created_at,
        process_id: record.process_id,
        log_path: record.log_path,
        message: record.message,
        unexpected_changes: record.unexpected_changes,
    }
}

fn list_records(project_root: &Path) -> Result<Vec<StoredSmokeTest>, String> {
    let root = test_root(project_root);
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let mut latest = BTreeMap::<String, StoredSmokeTest>::new();
    for entry in fs::read_dir(&root)
        .map_err(|error| format!("스킬 테스트 목록을 읽지 못했습니다: {error}"))?
        .flatten()
    {
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        let Ok(bytes) = fs::read(&path) else { continue };
        let Ok(record) = serde_json::from_slice::<StoredSmokeTest>(&bytes) else {
            continue;
        };
        let replace = latest
            .get(&record.platform)
            .is_none_or(|current| record.created_at > current.created_at);
        if replace {
            latest.insert(record.platform.clone(), record);
        }
    }
    Ok(latest.into_values().collect())
}

#[tauri::command]
pub(crate) async fn launch_skill_smoke_test(
    project_root: String,
    platform: String,
    approved: bool,
) -> Result<SmokeTestStatus, String> {
    if !approved {
        return Err("실제 CLI 스킬 테스트에는 사용자 확인이 필요합니다.".to_owned());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let root = canonical_project_root(&project_root)?;
        let (test_id, token) = test_identity(&platform);
        let executable = match resolve_platform_executable(&platform) {
            Ok(executable) => executable,
            Err(error) => {
                return persist_preparation_failure(&root, &platform, test_id, token, &error)
            }
        };
        let mut record = match prepare_smoke_test_with_identity(
            &root,
            &platform,
            test_id.clone(),
            token.clone(),
        ) {
            Ok(record) => record,
            Err(error) => {
                return persist_preparation_failure(&root, &platform, test_id, token, &error)
            }
        };
        write_record(&root, &record)?;
        let absolute_proof = proof_path(&root, &record.test_id)?;
        let proof_reference = if platform == "antigravity" {
            user_path(&absolute_proof).replace('\\', "/")
        } else {
            absolute_proof
                .strip_prefix(&root)
                .map_err(|_| "증명 파일 경로가 프로젝트 밖을 가리킵니다.".to_owned())?
                .to_string_lossy()
                .replace('\\', "/")
        };
        let prompt = build_prompt(&platform, &proof_reference, &record.token);
        let script_path = PathBuf::from(&record.script_path);
        let log_path = PathBuf::from(&record.log_path);
        let platform_log = script_path.with_extension("platform.log");
        write_utf8_bom(
            &script_path,
            &build_launch_script(
                &root,
                &executable,
                &platform,
                &prompt,
                &log_path,
                &platform_log,
            )?,
        )?;
        let child = Command::new("powershell.exe")
            .args([
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ])
            .arg(&script_path)
            .current_dir(&root)
            .creation_flags(CREATE_NEW_CONSOLE)
            .stdin(Stdio::null())
            .spawn()
            .map_err(|error| format!("스킬 테스트 PowerShell 창을 열지 못했습니다: {error}"))?;
        record.process_id = Some(child.id());
        record.state = "running".to_owned();
        record.message = "CLI에서 테스트 스킬을 실행하고 있습니다.".to_owned();
        write_record(&root, &record)?;
        Ok(public_status(record))
    })
    .await
    .map_err(|error| format!("스킬 테스트 준비 작업이 중단되었습니다: {error}"))?
}

#[tauri::command]
pub(crate) async fn inspect_skill_smoke_tests(
    project_root: String,
) -> Result<SmokeTestSnapshot, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = canonical_project_root(&project_root)?;
        let tests = list_records(&root)?
            .into_iter()
            .map(|record| inspect_record(&root, record).map(public_status))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(SmokeTestSnapshot {
            project_root: user_path(&root),
            tests,
        })
    })
    .await
    .map_err(|error| format!("스킬 테스트 상태 확인이 중단되었습니다: {error}"))?
}

#[tauri::command]
pub(crate) async fn cleanup_skill_smoke_test(
    project_root: String,
    test_id: String,
) -> Result<SmokeTestSnapshot, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = canonical_project_root(&project_root)?;
        let record = read_record(&root, &test_id)?;
        if record.process_id.is_some_and(process_is_running) {
            return Err("CLI 테스트 창을 먼저 종료한 뒤 정리해 주세요.".to_owned());
        }
        if record.installed_by_test {
            let target = skill_target(&root, &record.platform)?;
            let owner_is_test = fs::read(target.join(".skkima-install.json"))
                .ok()
                .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
                .and_then(|value| {
                    value
                        .get("owner")
                        .and_then(|item| item.as_str())
                        .map(str::to_owned)
                })
                .as_deref()
                == Some(TEST_OWNER);
            if owner_is_test && target.exists() {
                fs::remove_dir_all(&target)
                    .map_err(|error| format!("테스트 스킬을 정리하지 못했습니다: {error}"))?;
            }
        }
        for path in [
            proof_path(&root, &record.test_id)?,
            PathBuf::from(&record.log_path),
            PathBuf::from(&record.script_path),
            PathBuf::from(&record.script_path).with_extension("platform.log"),
            state_path(&root, &record.test_id)?,
        ] {
            if path.is_file() {
                fs::remove_file(&path)
                    .map_err(|error| format!("테스트 파일을 정리하지 못했습니다: {error}"))?;
            }
        }
        let tests = list_records(&root)?
            .into_iter()
            .map(public_status)
            .collect();
        Ok(SmokeTestSnapshot {
            project_root: user_path(&root),
            tests,
        })
    })
    .await
    .map_err(|error| format!("스킬 테스트 정리가 중단되었습니다: {error}"))?
}

#[cfg(test)]
mod tests {
    use super::{
        inspect_record, persist_preparation_failure, platform_command, prepare_smoke_test,
        public_status, read_record, TEST_OWNER, TEST_SKILL,
    };
    use std::{env, fs, process};

    fn test_root(name: &str) -> std::path::PathBuf {
        env::temp_dir().join(format!("skkima-smoke-{name}-{}", process::id()))
    }

    #[test]
    fn passes_only_with_the_exact_proof_and_unchanged_project() {
        let root = test_root("pass");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("source.txt"), "unchanged").unwrap();
        let record = prepare_smoke_test(&root, "codex").unwrap();
        fs::write(&record.proof_path, format!("{}\n", record.token)).unwrap();

        let inspected = inspect_record(&root, record).unwrap();
        fs::remove_dir_all(&root).unwrap();

        assert_eq!(public_status(inspected).state, "passed");
    }

    #[test]
    fn fails_when_the_agent_changes_an_unapproved_file() {
        let root = test_root("changed");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("source.txt"), "before").unwrap();
        let record = prepare_smoke_test(&root, "claude").unwrap();
        fs::write(&record.proof_path, format!("{}\n", record.token)).unwrap();
        fs::write(root.join("source.txt"), "after").unwrap();

        let inspected = inspect_record(&root, record).unwrap();
        fs::remove_dir_all(&root).unwrap();

        assert_eq!(inspected.state, "failed");
        assert_eq!(inspected.unexpected_changes, vec!["source.txt"]);
    }

    #[test]
    fn installs_a_owned_readable_smoke_skill_without_overwriting() {
        let root = test_root("install");
        fs::create_dir_all(&root).unwrap();
        let record = prepare_smoke_test(&root, "antigravity").unwrap();
        let target = std::path::PathBuf::from(record.skill_target);
        let manifest = fs::read_to_string(target.join(".skkima-install.json")).unwrap();

        fs::remove_dir_all(&root).unwrap();
        assert_eq!(fs::read_to_string(target.join("SKILL.md")).ok(), None);
        assert!(manifest.contains(TEST_OWNER));
        assert!(TEST_SKILL.contains("Skkima Smoke Test"));
    }

    #[test]
    fn antigravity_print_flag_consumes_only_the_prompt() {
        let command = platform_command("antigravity").unwrap();

        assert!(command.contains("--add-dir $projectRoot"));
        assert!(command.contains("--mode accept-edits --sandbox --print-timeout 5m"));
        assert!(command.contains("--print $prompt"));
        assert!(!command.contains("--print --mode"));
    }

    #[test]
    fn codex_reads_the_multiline_prompt_from_standard_input() {
        let command = platform_command("codex").unwrap();

        assert!(command.starts_with("$prompt | & $cli exec"));
        assert!(command.contains("--ephemeral - 2>&1"));
        assert!(!command.contains("--ephemeral $prompt"));
    }

    #[test]
    fn persists_preparation_failures_for_later_diagnosis() {
        let root = test_root("preparation-failure");
        fs::create_dir_all(&root).unwrap();
        let test_id = "smoke-codex-preparation-failure".to_owned();

        let status = persist_preparation_failure(
            &root,
            "codex",
            test_id.clone(),
            "SKKIMA-CODEX-PREPARATION-FAILURE".to_owned(),
            "locked baseline file",
        )
        .unwrap();
        let stored = read_record(&root, &test_id).unwrap();

        fs::remove_dir_all(&root).unwrap();
        assert_eq!(status.state, "failed");
        assert_eq!(stored.state, "failed");
        assert!(stored.message.contains("locked baseline file"));
    }
}
