use serde::Serialize;
use std::collections::HashMap;
use std::env;
use std::os::windows::process::CommandExt;
use std::process::{Command, Output, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use wait_timeout::ChildExt;

const CREATE_NO_WINDOW: u32 = 0x08000000;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(5);
const POWERSHELL_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SystemProfile {
    operating_system: String,
    os_version: String,
    architecture: String,
    cpu: String,
    memory_bytes: Option<u64>,
    gpu: Vec<GpuProfile>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct GpuProfile {
    name: String,
    vram_bytes: Option<u64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalEnvironment {
    system: SystemProfile,
    tools: Vec<ToolVersion>,
    checked_at_unix: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ToolVersion {
    id: String,
    label: String,
    installed: bool,
    version: Option<String>,
    status: String,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum ProcessRunError {
    SpawnFailed,
    WaitFailed,
    TimedOut,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum CommandProbeError {
    Unavailable,
    Failed,
    TimedOut,
}

fn run_process_with_timeout(
    process: &mut Command,
    timeout: Duration,
) -> Result<Output, ProcessRunError> {
    process.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = process.spawn().map_err(|_| ProcessRunError::SpawnFailed)?;
    match child
        .wait_timeout(timeout)
        .map_err(|_| ProcessRunError::WaitFailed)?
    {
        Some(_) => child
            .wait_with_output()
            .map_err(|_| ProcessRunError::WaitFailed),
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
            Err(ProcessRunError::TimedOut)
        }
    }
}

fn run_powershell(script: &str) -> Option<String> {
    let mut process = Command::new("powershell.exe");
    process
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .creation_flags(CREATE_NO_WINDOW);
    let output = run_process_with_timeout(&mut process, POWERSHELL_TIMEOUT).ok()?;

    if !output.status.success() {
        return None;
    }

    Some(clean_output(&decode_output(&output.stdout)))
}

fn run_version_command(command: &str) -> Option<String> {
    run_command_output(command).and_then(|output| extract_numeric_version(&output))
}

fn run_command_output(command: &str) -> Option<String> {
    probe_command_output(command).ok()
}

fn read_registered_windows_path() -> Option<String> {
    let script = r#"
$paths = @(
  [Environment]::GetEnvironmentVariable('Path', 'Machine'),
  [Environment]::GetEnvironmentVariable('Path', 'User')
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$paths -join ';'
"#;
    run_powershell(script).filter(|path| !path.trim().is_empty())
}

fn probe_command_output(command: &str) -> Result<String, CommandProbeError> {
    match run_command_output_with_path(command, None) {
        Ok(output) => Ok(output),
        Err(CommandProbeError::TimedOut) => Err(CommandProbeError::TimedOut),
        Err(first_error) => {
            let refreshed_path = read_registered_windows_path().ok_or(first_error)?;
            match run_command_output_with_path(command, Some(&refreshed_path)) {
                Ok(output) => Ok(output),
                Err(CommandProbeError::TimedOut) => Err(CommandProbeError::TimedOut),
                Err(second_error) => Err(second_error),
            }
        }
    }
}

fn run_command_output_with_path(
    command: &str,
    path: Option<&str>,
) -> Result<String, CommandProbeError> {
    let mut process = Command::new("cmd.exe");
    process.args(["/D", "/S", "/C", command]);
    process.creation_flags(CREATE_NO_WINDOW);
    if let Some(path) = path {
        process.env("PATH", path);
    }

    let output = run_process_with_timeout(&mut process, COMMAND_TIMEOUT).map_err(|error| {
        if error == ProcessRunError::TimedOut {
            CommandProbeError::TimedOut
        } else {
            CommandProbeError::Failed
        }
    })?;

    let stdout = clean_output(&decode_output(&output.stdout));
    let stderr = clean_output(&decode_output(&output.stderr));
    let combined = if stdout.is_empty() { stderr } else { stdout };

    if !output.status.success() {
        return Err(CommandProbeError::Unavailable);
    }
    if combined.is_empty() {
        return Err(CommandProbeError::Failed);
    }

    Ok(combined)
}

fn clean_output(value: &str) -> String {
    let mut cleaned = String::with_capacity(value.len());
    let mut in_escape = false;

    for character in value.replace('\0', "").chars() {
        if in_escape {
            if character.is_ascii_alphabetic() {
                in_escape = false;
            }
            continue;
        }

        if character == '\u{1b}' {
            in_escape = true;
            continue;
        }

        cleaned.push(character);
    }

    cleaned
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn decode_output(bytes: &[u8]) -> String {
    let likely_utf16 = bytes.starts_with(&[0xff, 0xfe])
        || (!bytes.is_empty() && bytes.iter().filter(|byte| **byte == 0).count() > bytes.len() / 4);

    if likely_utf16 {
        let mut units = bytes
            .chunks_exact(2)
            .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<_>>();
        if units.first() == Some(&0xfeff) {
            units.remove(0);
        }
        String::from_utf16_lossy(&units)
    } else {
        String::from_utf8_lossy(bytes).into_owned()
    }
}

fn extract_numeric_version(value: &str) -> Option<String> {
    let mut version = String::new();
    let mut started = false;

    for character in value.chars() {
        if !started {
            if character.is_ascii_digit() {
                started = true;
                version.push(character);
            }
            continue;
        }

        if character.is_ascii_digit() || character == '.' {
            version.push(character);
        } else {
            break;
        }
    }

    let version = version.trim_matches('.').to_owned();
    (!version.is_empty()).then_some(version)
}

fn parse_system_probe(output: &str) -> HashMap<String, String> {
    output
        .lines()
        .filter_map(|line| {
            let (key, value) = line.split_once('|')?;
            Some((key.trim().to_owned(), value.trim().to_owned()))
        })
        .collect()
}

fn parse_nvidia_memory(output: &str) -> Vec<(String, u64)> {
    output
        .lines()
        .filter_map(|line| {
            let (name, memory_mib) = line.rsplit_once(',')?;
            let memory_mib = memory_mib.trim().parse::<u64>().ok()?;
            Some((
                name.trim().to_owned(),
                memory_mib.saturating_mul(1024 * 1024),
            ))
        })
        .collect()
}

fn system_profile() -> SystemProfile {
    let fallback_cpu =
        env::var("PROCESSOR_IDENTIFIER").unwrap_or_else(|_| "확인할 수 없음".to_owned());
    let mut profile = SystemProfile {
        operating_system: "Windows".to_owned(),
        os_version: "확인할 수 없음".to_owned(),
        architecture: env::consts::ARCH.to_owned(),
        cpu: fallback_cpu,
        memory_bytes: None,
        gpu: Vec::new(),
    };

    let probe_script = r#"
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
$processor = Get-CimInstance Win32_Processor | Select-Object -First 1
$graphics = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
Write-Output ('OS|' + $os.Caption)
Write-Output ('VERSION|' + $os.Version)
Write-Output ('ARCH|' + $os.OSArchitecture)
Write-Output ('CPU|' + $processor.Name)
Write-Output ('MEMORY|' + $computer.TotalPhysicalMemory)
$gpuIndex = 0
foreach ($graphic in $graphics) {
    Write-Output ('GPU' + $gpuIndex + '|' + $graphic)
    $gpuIndex += 1
}
"#;

    if let Some(output) = run_powershell(probe_script) {
        let values = parse_system_probe(&output);

        if let Some(value) = values.get("OS").filter(|value| !value.is_empty()) {
            profile.operating_system = value.clone();
        }
        if let Some(value) = values.get("VERSION").filter(|value| !value.is_empty()) {
            profile.os_version = value.clone();
        }
        if let Some(value) = values.get("ARCH").filter(|value| !value.is_empty()) {
            profile.architecture = value.clone();
        }
        if let Some(value) = values.get("CPU").filter(|value| !value.is_empty()) {
            profile.cpu = value.clone();
        }
        profile.memory_bytes = values
            .get("MEMORY")
            .and_then(|value| value.parse::<u64>().ok());
        let mut gpu_index = 0;
        while let Some(name) = values.get(&format!("GPU{gpu_index}")) {
            if !name.is_empty() {
                profile.gpu.push(GpuProfile {
                    name: name.clone(),
                    vram_bytes: None,
                });
            }
            gpu_index += 1;
        }
    }

    if let Some(output) =
        run_command_output("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits")
    {
        for (nvidia_name, vram_bytes) in parse_nvidia_memory(&output) {
            let normalized_name = nvidia_name.to_lowercase();
            if let Some(gpu) = profile.gpu.iter_mut().find(|gpu| {
                let candidate = gpu.name.to_lowercase();
                candidate.contains(&normalized_name) || normalized_name.contains(&candidate)
            }) {
                gpu.vram_bytes = Some(vram_bytes);
            } else {
                profile.gpu.push(GpuProfile {
                    name: nvidia_name,
                    vram_bytes: Some(vram_bytes),
                });
            }
        }
    }

    profile
}

fn detect_tool(id: &str, label: &str, commands: &[&str]) -> ToolVersion {
    let mut failure = CommandProbeError::Unavailable;
    for command in commands {
        match probe_command_output(command) {
            Ok(output) => {
                if let Some(version) = extract_numeric_version(&output) {
                    return tool_version(id, label, Some(version));
                }
                failure = CommandProbeError::Failed;
            }
            Err(CommandProbeError::TimedOut) => failure = CommandProbeError::TimedOut,
            Err(CommandProbeError::Failed) if failure != CommandProbeError::TimedOut => {
                failure = CommandProbeError::Failed;
            }
            Err(CommandProbeError::Unavailable) | Err(CommandProbeError::Failed) => {}
        }
    }

    tool_version_with_status(
        id,
        label,
        None,
        match failure {
            CommandProbeError::Unavailable => "missing",
            CommandProbeError::Failed => "error",
            CommandProbeError::TimedOut => "timeout",
        },
    )
}

fn detect_codex_tool() -> ToolVersion {
    let version = detect_npm_codex_version()
        .or_else(|| run_version_command("codex --version"))
        .or_else(detect_bundled_codex_version);

    tool_version("codex", "Codex", version)
}

fn detect_npm_codex_version() -> Option<String> {
    let script = r#"
$shim = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'npm\codex.cmd'
if (Test-Path -LiteralPath $shim) {
  $paths = @(
    (Split-Path -Parent $shim),
    [Environment]::GetEnvironmentVariable('Path', 'Machine'),
    [Environment]::GetEnvironmentVariable('Path', 'User')
  ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  $env:Path = $paths -join ';'
  & $shim --version
}
"#;
    run_powershell(script).and_then(|output| extract_numeric_version(&output))
}

fn detect_bundled_codex_version() -> Option<String> {
    let script = r#"
$root = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
$candidate = Get-ChildItem -LiteralPath $root -Filter 'codex.exe' -File -Recurse -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($candidate) {
  & $candidate.FullName --version
}
"#;
    run_powershell(script).and_then(|output| extract_numeric_version(&output))
}

pub(crate) fn platform_cli_version(platform: &str) -> Option<String> {
    match platform {
        "codex" => detect_npm_codex_version()
            .or_else(|| run_version_command("codex --version"))
            .or_else(detect_bundled_codex_version),
        "claude" => run_version_command("claude --version"),
        "antigravity" => run_version_command("agy --version"),
        _ => None,
    }
}

fn tool_version(id: &str, label: &str, version: Option<String>) -> ToolVersion {
    let status = if version.is_some() {
        "available"
    } else {
        "missing"
    };
    tool_version_with_status(id, label, version, status)
}

fn tool_version_with_status(
    id: &str,
    label: &str,
    version: Option<String>,
    status: &str,
) -> ToolVersion {
    ToolVersion {
        id: id.to_owned(),
        label: label.to_owned(),
        installed: version.is_some(),
        version,
        status: status.to_owned(),
    }
}

fn build_local_environment() -> LocalEnvironment {
    let tools = vec![
        detect_tool("python", "Python", &["python --version", "py --version"]),
        detect_tool("wsl", "WSL", &["wsl --version", "wsl --status"]),
        detect_tool("docker", "Docker", &["docker --version"]),
        detect_tool("git", "Git", &["git --version"]),
        detect_tool("node", "Node.js", &["node --version"]),
        detect_codex_tool(),
        detect_tool("claude", "Claude Code", &["claude --version"]),
        detect_tool("antigravity", "Antigravity", &["agy --version"]),
        detect_tool("cursor", "Cursor", &["cursor --version"]),
        detect_tool("vscode", "VS Code", &["code --version"]),
    ];

    LocalEnvironment {
        system: system_profile(),
        tools,
        checked_at_unix: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
    }
}

#[tauri::command]
pub async fn get_local_environment() -> Result<LocalEnvironment, String> {
    tauri::async_runtime::spawn_blocking(build_local_environment)
        .await
        .map_err(|error| format!("로컬 환경 확인 작업이 중단되었습니다: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{
        clean_output, decode_output, extract_numeric_version, parse_nvidia_memory,
        parse_system_probe, run_command_output_with_path, run_process_with_timeout,
        ProcessRunError,
    };
    use std::process::Command;
    use std::time::{Duration, Instant};
    use std::{env, fs, process};

    #[test]
    fn parses_system_probe_without_losing_values() {
        let values =
            parse_system_probe("OS|Microsoft Windows 11 Pro\nCPU|Example CPU\nGPU|GPU A / GPU B\n");
        assert_eq!(
            values.get("OS").map(String::as_str),
            Some("Microsoft Windows 11 Pro")
        );
        assert_eq!(values.get("GPU").map(String::as_str), Some("GPU A / GPU B"));
    }

    #[test]
    fn strips_terminal_escape_sequences_and_empty_lines() {
        assert_eq!(
            clean_output("\u{1b}[32mtool 1.0\u{1b}[0m\r\n\r\n"),
            "tool 1.0"
        );
    }

    #[test]
    fn extracts_only_the_numeric_version() {
        assert_eq!(
            extract_numeric_version("Docker version 29.1.3, build abc123"),
            Some("29.1.3".to_owned())
        );
        assert_eq!(
            extract_numeric_version("git version 2.52.0.windows.1"),
            Some("2.52.0".to_owned())
        );
        assert_eq!(
            extract_numeric_version("v24.12.0"),
            Some("24.12.0".to_owned())
        );
    }

    #[test]
    fn retries_tools_with_an_explicit_registered_path() {
        let shim_dir = env::temp_dir().join(format!("schema-workflow-path-test-{}", process::id()));
        fs::create_dir_all(&shim_dir).expect("temporary shim directory should be created");
        let shim_path = shim_dir.join("schema-workflow-path-test.cmd");
        fs::write(&shim_path, "@echo codex-cli 9.8.7\r\n")
            .expect("temporary command shim should be written");

        let output =
            run_command_output_with_path("schema-workflow-path-test --version", shim_dir.to_str())
                .expect("registered command should run");

        fs::remove_dir_all(&shim_dir).expect("temporary shim directory should be removed");
        assert_eq!(output, "codex-cli 9.8.7");
    }

    #[test]
    fn stops_a_command_after_its_timeout() {
        let mut command = Command::new("cmd.exe");
        command.args(["/D", "/S", "/C", "ping 127.0.0.1 -n 4 > nul"]);
        let started = Instant::now();
        let output = run_process_with_timeout(&mut command, Duration::from_millis(50));

        assert_eq!(output, Err(ProcessRunError::TimedOut));
        assert!(started.elapsed() < Duration::from_secs(3));
    }

    #[test]
    fn decodes_windows_utf16_command_output() {
        let bytes = "WSL 버전: 2.6.3.0\r\n"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect::<Vec<_>>();
        assert_eq!(decode_output(&bytes), "WSL 버전: 2.6.3.0\r\n");
    }

    #[test]
    fn parses_nvidia_vram_as_bytes() {
        assert_eq!(
            parse_nvidia_memory("NVIDIA GeForce RTX 5070 Ti, 16303\n"),
            vec![("NVIDIA GeForce RTX 5070 Ti".to_owned(), 16303 * 1024 * 1024)]
        );
    }
}
