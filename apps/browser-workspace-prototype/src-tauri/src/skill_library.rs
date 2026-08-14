use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::atomic_file::{atomic_write, ExclusiveFileLock};
use crate::local_environment::platform_cli_version;
use crate::project_onboarding::user_path_string;

const LIBRARY_SCHEMA_VERSION: &str = "1.0.0";
const INSTALL_OWNER: &str = "skkima-skill-library";
const MAX_SKILL_FILES: usize = 256;
const MAX_SKILL_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SkillRecord {
    skill_id: String,
    name: String,
    description: String,
    source_kind: String,
    source_path: String,
    source_hash: String,
    snapshot_path: String,
    registered_at: u64,
}

#[derive(Default, Deserialize, Serialize)]
struct SkillRegistry {
    #[serde(default = "library_schema_version")]
    schema_version: String,
    #[serde(default)]
    skills: Vec<SkillRecord>,
}

#[derive(Deserialize)]
struct SkillFrontmatter {
    name: String,
    description: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SkillLibrarySnapshot {
    library_root: String,
    skills: Vec<SkillRecord>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SkillRegistrationResult {
    state: String,
    skill: SkillRecord,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SkillSourceSummary {
    pub(crate) skill_id: String,
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) source_hash: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct InstallManifest {
    schema_version: String,
    owner: String,
    platform: String,
    #[serde(default)]
    compatible_platforms: Vec<String>,
    skill_id: String,
    source_hash: String,
    installed_at: u64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SkillInstallationStatus {
    state: String,
    platform: String,
    platform_label: String,
    project_root: String,
    target_path: String,
    skill_id: String,
    source_hash: String,
    shared_platforms: Vec<String>,
    cli_available: bool,
    cli_version: Option<String>,
    compatible: bool,
    structurally_valid: bool,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PlatformAvailability {
    platform: String,
    label: String,
    available: bool,
    version: Option<String>,
    shared_platforms: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ProjectSkillInstallationSnapshot {
    project_root: String,
    platforms: Vec<PlatformAvailability>,
    installations: Vec<SkillInstallationStatus>,
}

#[derive(Clone, Copy)]
struct PlatformSpec {
    id: &'static str,
    label: &'static str,
    skill_root: &'static str,
    shared_platforms: &'static [&'static str],
}

const PLATFORM_SPECS: [PlatformSpec; 3] = [
    PlatformSpec {
        id: "codex",
        label: "Codex",
        skill_root: ".agents/skills",
        shared_platforms: &["codex", "antigravity"],
    },
    PlatformSpec {
        id: "claude",
        label: "Claude Code",
        skill_root: ".claude/skills",
        shared_platforms: &["claude"],
    },
    PlatformSpec {
        id: "antigravity",
        label: "Antigravity",
        skill_root: ".agents/skills",
        shared_platforms: &["codex", "antigravity"],
    },
];

fn library_schema_version() -> String {
    LIBRARY_SCHEMA_VERSION.to_owned()
}

fn unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn default_library_root() -> Result<PathBuf, String> {
    if let Some(configured) = env::var_os("SKKIMA_SKILL_LIBRARY_ROOT") {
        return Ok(PathBuf::from(configured));
    }
    crate::profile::app_data_root().map(|root| root.join("skill-library"))
}

fn registry_path(library_root: &Path) -> PathBuf {
    library_root.join("registry.json")
}

fn registry_lock_path(library_root: &Path) -> PathBuf {
    library_root.join("registry.lock")
}

fn read_registry(library_root: &Path) -> Result<SkillRegistry, String> {
    let path = registry_path(library_root);
    if !path.exists() {
        return Ok(SkillRegistry {
            schema_version: library_schema_version(),
            skills: Vec::new(),
        });
    }
    let bytes = fs::read(&path)
        .map_err(|error| format!("스킬 라이브러리 목록을 읽지 못했습니다: {error}"))?;
    let registry: SkillRegistry = serde_json::from_slice(&bytes)
        .map_err(|error| format!("스킬 라이브러리 목록 형식이 올바르지 않습니다: {error}"))?;
    if registry.schema_version != LIBRARY_SCHEMA_VERSION {
        return Err(format!(
            "지원하지 않는 스킬 라이브러리 버전입니다: {}",
            registry.schema_version
        ));
    }
    Ok(registry)
}

fn write_registry(library_root: &Path, registry: &SkillRegistry) -> Result<(), String> {
    fs::create_dir_all(library_root)
        .map_err(|error| format!("스킬 라이브러리 폴더를 만들지 못했습니다: {error}"))?;
    let path = registry_path(library_root);
    let bytes = serde_json::to_vec_pretty(registry)
        .map_err(|error| format!("스킬 라이브러리 목록을 직렬화하지 못했습니다: {error}"))?;
    atomic_write(&path, &bytes)
        .map_err(|error| format!("스킬 라이브러리 목록을 확정하지 못했습니다: {error}"))
}

fn skill_source_root(source: &Path) -> Result<(PathBuf, String), String> {
    let canonical = source
        .canonicalize()
        .map_err(|error| format!("선택한 스킬 경로를 찾을 수 없습니다: {error}"))?;
    if canonical.is_file() {
        if canonical
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case("md"))
            != Some(true)
        {
            return Err("로컬 스킬 파일은 Markdown(.md) 형식이어야 합니다.".to_owned());
        }
        return Ok((canonical, "local_file".to_owned()));
    }
    if canonical.is_dir() && canonical.join("SKILL.md").is_file() {
        return Ok((canonical, "local_directory".to_owned()));
    }
    Err("선택한 폴더의 최상위에 SKILL.md가 있어야 합니다.".to_owned())
}

fn collect_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    fn walk(
        root: &Path,
        current: &Path,
        files: &mut Vec<PathBuf>,
        bytes: &mut u64,
    ) -> Result<(), String> {
        for entry in fs::read_dir(current)
            .map_err(|error| format!("스킬 폴더를 읽지 못했습니다: {error}"))?
        {
            let entry = entry.map_err(|error| format!("스킬 항목을 읽지 못했습니다: {error}"))?;
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)
                .map_err(|error| format!("스킬 항목 정보를 읽지 못했습니다: {error}"))?;
            if metadata.file_type().is_symlink() {
                return Err(format!(
                    "심볼릭 링크가 포함된 스킬은 등록할 수 없습니다: {}",
                    path.display()
                ));
            }
            if metadata.is_dir() {
                walk(root, &path, files, bytes)?;
            } else if metadata.is_file() {
                *bytes = bytes.saturating_add(metadata.len());
                if *bytes > MAX_SKILL_BYTES {
                    return Err("스킬 스냅샷은 16MB를 초과할 수 없습니다.".to_owned());
                }
                files.push(path.strip_prefix(root).unwrap_or(&path).to_path_buf());
                if files.len() > MAX_SKILL_FILES {
                    return Err("스킬 스냅샷은 파일 256개를 초과할 수 없습니다.".to_owned());
                }
            }
        }
        Ok(())
    }

    let mut files = Vec::new();
    let mut bytes = 0;
    walk(root, root, &mut files, &mut bytes)?;
    files.sort();
    Ok(files)
}

fn source_hash(root: &Path, files: &[PathBuf]) -> Result<String, String> {
    let mut digest = Sha256::new();
    for relative in files {
        digest.update(relative.to_string_lossy().replace('\\', "/").as_bytes());
        digest.update([0]);
        digest.update(
            fs::read(root.join(relative))
                .map_err(|error| format!("스킬 파일을 해시하지 못했습니다: {error}"))?,
        );
        digest.update([0]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn metadata_from_skill(skill_md: &Path) -> Result<(String, String), String> {
    let text = fs::read_to_string(skill_md)
        .map_err(|error| format!("스킬 문서를 UTF-8 텍스트로 읽지 못했습니다: {error}"))?;
    let mut lines = text.lines();
    if lines.next().map(str::trim) != Some("---") {
        return Err("스킬 문서는 YAML 머리말(---)로 시작해야 합니다.".to_owned());
    }
    let mut frontmatter = Vec::new();
    let mut closed = false;
    for line in lines {
        if line.trim() == "---" {
            closed = true;
            break;
        }
        frontmatter.push(line);
    }
    if !closed {
        return Err("스킬 문서 YAML 머리말의 닫는 구분선(---)이 없습니다.".to_owned());
    }
    let parsed: SkillFrontmatter = serde_yaml_ng::from_str(&frontmatter.join("\n"))
        .map_err(|error| format!("스킬 문서 YAML 머리말을 해석하지 못했습니다: {error}"))?;
    let name = parsed.name.trim().to_owned();
    let description = parsed.description.trim().to_owned();
    if name.is_empty() {
        return Err("스킬 문서 YAML 머리말에 비어 있지 않은 name 필드가 필요합니다.".to_owned());
    }
    if description.is_empty() {
        return Err(
            "스킬 문서 YAML 머리말에 비어 있지 않은 description 필드가 필요합니다.".to_owned(),
        );
    }
    Ok((name, description))
}

fn safe_skill_id(name: &str, hash: &str) -> String {
    let mut id = String::new();
    let mut previous_separator = false;
    for character in name.chars() {
        if character.is_ascii_alphanumeric() {
            id.push(character.to_ascii_lowercase());
            previous_separator = false;
        } else if matches!(character, '-' | '_' | ' ') && !id.is_empty() && !previous_separator {
            id.push('-');
            previous_separator = true;
        }
    }
    let trimmed = id.trim_matches('-');
    if trimmed.is_empty() {
        format!("skill-{}", &hash[..8])
    } else {
        trimmed
            .chars()
            .take(64)
            .collect::<String>()
            .trim_matches('-')
            .to_owned()
    }
}

fn prepare_skill_source(
    source: &Path,
) -> Result<(PathBuf, String, Vec<PathBuf>, SkillSourceSummary), String> {
    let (source_root, source_kind) = skill_source_root(source)?;
    let files = if source_kind == "local_file" {
        let metadata = fs::metadata(&source_root)
            .map_err(|error| format!("스킬 파일 정보를 읽지 못했습니다: {error}"))?;
        if metadata.len() > MAX_SKILL_BYTES {
            return Err("스킬 스냅샷은 16MB를 초과할 수 없습니다.".to_owned());
        }
        vec![PathBuf::from("SKILL.md")]
    } else {
        collect_files(&source_root)?
    };
    if !files.iter().any(|path| path == Path::new("SKILL.md")) {
        return Err("스킬 스냅샷에 SKILL.md가 없습니다.".to_owned());
    }
    let hash = if source_kind == "local_file" {
        let mut digest = Sha256::new();
        digest.update(b"SKILL.md");
        digest.update([0]);
        digest.update(
            fs::read(&source_root)
                .map_err(|error| format!("스킬 파일을 해시하지 못했습니다: {error}"))?,
        );
        digest.update([0]);
        format!("{:x}", digest.finalize())
    } else {
        source_hash(&source_root, &files)?
    };
    let skill_document = if source_kind == "local_file" {
        source_root.clone()
    } else {
        source_root.join("SKILL.md")
    };
    let (name, description) = metadata_from_skill(&skill_document)?;
    let skill_id = safe_skill_id(&name, &hash);
    Ok((
        source_root,
        source_kind,
        files,
        SkillSourceSummary {
            skill_id,
            name,
            description,
            source_hash: hash,
        },
    ))
}

pub(crate) fn inspect_skill_source(source: &Path) -> Result<SkillSourceSummary, String> {
    prepare_skill_source(source).map(|(_, _, _, summary)| summary)
}

fn copy_snapshot(source_root: &Path, files: &[PathBuf], target: &Path) -> Result<(), String> {
    if target.exists() {
        return Ok(());
    }
    let temporary = target.with_extension(format!("tmp-{}", std::process::id()));
    if temporary.exists() {
        fs::remove_dir_all(&temporary)
            .map_err(|error| format!("이전 임시 스냅샷을 정리하지 못했습니다: {error}"))?;
    }
    fs::create_dir_all(&temporary)
        .map_err(|error| format!("임시 스킬 스냅샷을 만들지 못했습니다: {error}"))?;
    for relative in files {
        let destination = temporary.join(relative);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("스냅샷 하위 폴더를 만들지 못했습니다: {error}"))?;
        }
        let source_file = if source_root.is_file() {
            source_root.to_path_buf()
        } else {
            source_root.join(relative)
        };
        fs::copy(source_file, &destination)
            .map_err(|error| format!("스킬 파일을 스냅샷으로 복사하지 못했습니다: {error}"))?;
    }
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("스냅샷 저장 폴더를 만들지 못했습니다: {error}"))?;
    }
    fs::rename(&temporary, target)
        .map_err(|error| format!("스킬 스냅샷을 확정하지 못했습니다: {error}"))
}

fn register_skill_at(
    library_root: &Path,
    source: &Path,
) -> Result<SkillRegistrationResult, String> {
    register_skill_at_with_origin(library_root, source, None, None)
}

fn register_skill_at_with_origin(
    library_root: &Path,
    source: &Path,
    source_kind_override: Option<&str>,
    source_path_override: Option<&str>,
) -> Result<SkillRegistrationResult, String> {
    let (source_root, detected_source_kind, files, summary) = prepare_skill_source(source)?;
    let _registry_lock = ExclusiveFileLock::acquire(&registry_lock_path(library_root))
        .map_err(|error| format!("스킬 라이브러리 갱신 잠금을 얻지 못했습니다: {error}"))?;
    let snapshot = library_root
        .join("snapshots")
        .join(&summary.skill_id)
        .join(&summary.source_hash);
    copy_snapshot(&source_root, &files, &snapshot)?;

    let mut registry = read_registry(library_root)?;
    if let Some(existing) = registry
        .skills
        .iter()
        .find(|record| {
            record.skill_id == summary.skill_id && record.source_hash == summary.source_hash
        })
        .cloned()
    {
        return Ok(SkillRegistrationResult {
            state: "already_registered".to_owned(),
            skill: existing,
        });
    }
    let record = SkillRecord {
        skill_id: summary.skill_id.clone(),
        name: summary.name,
        description: summary.description,
        source_kind: source_kind_override
            .unwrap_or(&detected_source_kind)
            .to_owned(),
        source_path: source_path_override
            .map(str::to_owned)
            .unwrap_or_else(|| user_path_string(&source_root)),
        source_hash: summary.source_hash,
        snapshot_path: user_path_string(&snapshot),
        registered_at: unix_timestamp(),
    };
    registry
        .skills
        .retain(|item| item.skill_id != record.skill_id);
    registry.skills.push(record.clone());
    registry
        .skills
        .sort_by(|left, right| left.name.cmp(&right.name));
    write_registry(library_root, &registry)?;
    Ok(SkillRegistrationResult {
        state: "registered".to_owned(),
        skill: record,
    })
}

pub(crate) fn register_github_skill(
    source: &Path,
    source_url: &str,
    relative_path: &str,
) -> Result<SkillRegistrationResult, String> {
    let provenance = format!("{}#{}", source_url.trim_end_matches('/'), relative_path);
    register_skill_at_with_origin(
        &default_library_root()?,
        source,
        Some("github_plugin"),
        Some(&provenance),
    )
}

fn find_skill(library_root: &Path, skill_id: &str) -> Result<SkillRecord, String> {
    read_registry(library_root)?
        .skills
        .into_iter()
        .find(|skill| skill.skill_id == skill_id)
        .ok_or_else(|| format!("스킬 라이브러리에서 {skill_id} 항목을 찾을 수 없습니다."))
}

fn canonical_project_root(project_root: &Path) -> Result<PathBuf, String> {
    let canonical = project_root
        .canonicalize()
        .map_err(|error| format!("프로젝트 폴더를 찾을 수 없습니다: {error}"))?;
    if !canonical.is_dir() {
        return Err("선택한 프로젝트 경로가 폴더가 아닙니다.".to_owned());
    }
    Ok(canonical)
}

fn install_manifest_path(target: &Path) -> PathBuf {
    target.join(".skkima-install.json")
}

fn read_install_manifest(target: &Path) -> Result<Option<InstallManifest>, String> {
    let path = install_manifest_path(target);
    if !path.is_file() {
        return Ok(None);
    }
    let bytes =
        fs::read(path).map_err(|error| format!("스킬 설치 기록을 읽지 못했습니다: {error}"))?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|error| format!("스킬 설치 기록 형식이 올바르지 않습니다: {error}"))
}

fn platform_spec(platform: &str) -> Result<PlatformSpec, String> {
    PLATFORM_SPECS
        .iter()
        .find(|spec| spec.id == platform)
        .copied()
        .ok_or_else(|| format!("지원하지 않는 스킬 플랫폼입니다: {platform}"))
}

fn platform_availability(spec: PlatformSpec) -> PlatformAvailability {
    let version = platform_cli_version(spec.id);
    PlatformAvailability {
        platform: spec.id.to_owned(),
        label: spec.label.to_owned(),
        available: version.is_some(),
        version,
        shared_platforms: spec
            .shared_platforms
            .iter()
            .map(|value| (*value).to_owned())
            .collect(),
    }
}

fn manifest_supports_platform(manifest: &InstallManifest, spec: PlatformSpec) -> bool {
    if manifest
        .compatible_platforms
        .iter()
        .any(|item| item == spec.id)
    {
        return true;
    }
    if manifest.platform == spec.id {
        return true;
    }
    manifest.platform == "codex"
        && spec.shared_platforms.contains(&"codex")
        && spec.shared_platforms.contains(&"antigravity")
}

fn skill_target(project_root: &Path, skill: &SkillRecord, spec: PlatformSpec) -> PathBuf {
    project_root.join(spec.skill_root).join(&skill.skill_id)
}

fn installation_status_at(
    project_root: &Path,
    skill: &SkillRecord,
    spec: PlatformSpec,
    availability: &PlatformAvailability,
) -> Result<SkillInstallationStatus, String> {
    let project_root = canonical_project_root(project_root)?;
    let target = skill_target(&project_root, skill, spec);
    let manifest = read_install_manifest(&target)?;
    let structurally_valid = target.join("SKILL.md").is_file()
        && manifest.as_ref().is_some_and(|value| {
            value.owner == INSTALL_OWNER
                && manifest_supports_platform(value, spec)
                && value.skill_id == skill.skill_id
                && value.source_hash == skill.source_hash
        });
    let state = if structurally_valid {
        "installed"
    } else if target.exists() {
        "conflict"
    } else {
        "not_installed"
    };
    let message = match state {
        "installed" if availability.available && spec.shared_platforms.len() > 1 => {
            "공유 프로젝트 스킬 경로와 CLI를 확인했습니다."
        }
        "installed" if availability.available => "프로젝트 스킬 경로와 CLI를 확인했습니다.",
        "installed" => "스킬은 설치되었지만 해당 플랫폼 CLI를 찾지 못했습니다.",
        "conflict" => "같은 위치에 쓰끼마가 소유하지 않은 파일 또는 다른 버전이 있습니다.",
        _ => "현재 프로젝트에 이 스킬이 설치되지 않았습니다.",
    };
    Ok(SkillInstallationStatus {
        state: state.to_owned(),
        platform: spec.id.to_owned(),
        platform_label: spec.label.to_owned(),
        project_root: user_path_string(&project_root),
        target_path: user_path_string(&target),
        skill_id: skill.skill_id.clone(),
        source_hash: skill.source_hash.clone(),
        shared_platforms: spec
            .shared_platforms
            .iter()
            .map(|value| (*value).to_owned())
            .collect(),
        cli_available: availability.available,
        cli_version: availability.version.clone(),
        compatible: true,
        structurally_valid,
        message: message.to_owned(),
    })
}

fn install_skill_at(
    library_root: &Path,
    project_root: &Path,
    skill_id: &str,
    platform: &str,
) -> Result<SkillInstallationStatus, String> {
    let spec = platform_spec(platform)?;
    let availability = platform_availability(spec);
    let skill = find_skill(library_root, skill_id)?;
    let project_root = canonical_project_root(project_root)?;
    let target = skill_target(&project_root, &skill, spec);
    if target.exists() {
        let current = installation_status_at(&project_root, &skill, spec, &availability)?;
        if current.structurally_valid {
            return Ok(current);
        }
        return Err(format!(
            "기존 스킬 폴더를 자동으로 덮어쓰지 않습니다: {}",
            target.display()
        ));
    }

    let snapshot = PathBuf::from(&skill.snapshot_path);
    let files = collect_files(&snapshot)?;
    let temporary = target.with_extension(format!("tmp-{}", std::process::id()));
    if temporary.exists() {
        fs::remove_dir_all(&temporary)
            .map_err(|error| format!("이전 임시 설치 폴더를 정리하지 못했습니다: {error}"))?;
    }
    copy_snapshot(&snapshot, &files, &temporary)?;
    let manifest = InstallManifest {
        schema_version: LIBRARY_SCHEMA_VERSION.to_owned(),
        owner: INSTALL_OWNER.to_owned(),
        platform: spec.id.to_owned(),
        compatible_platforms: spec
            .shared_platforms
            .iter()
            .map(|value| (*value).to_owned())
            .collect(),
        skill_id: skill.skill_id.clone(),
        source_hash: skill.source_hash.clone(),
        installed_at: unix_timestamp(),
    };
    fs::write(
        install_manifest_path(&temporary),
        serde_json::to_vec_pretty(&manifest)
            .map_err(|error| format!("스킬 설치 기록을 직렬화하지 못했습니다: {error}"))?,
    )
    .map_err(|error| format!("스킬 설치 기록을 쓰지 못했습니다: {error}"))?;
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("프로젝트 스킬 폴더를 만들지 못했습니다: {error}"))?;
    }
    fs::rename(&temporary, &target)
        .map_err(|error| format!("프로젝트 스킬 설치를 확정하지 못했습니다: {error}"))?;
    installation_status_at(&project_root, &skill, spec, &availability)
}

fn uninstall_skill_at(
    library_root: &Path,
    project_root: &Path,
    skill_id: &str,
    platform: &str,
) -> Result<SkillInstallationStatus, String> {
    let spec = platform_spec(platform)?;
    let availability = platform_availability(spec);
    let skill = find_skill(library_root, skill_id)?;
    let project_root = canonical_project_root(project_root)?;
    let target = skill_target(&project_root, &skill, spec);
    if !target.exists() {
        return installation_status_at(&project_root, &skill, spec, &availability);
    }
    let manifest = read_install_manifest(&target)?
        .ok_or_else(|| "쓰끼마 설치 기록이 없어 이 폴더를 자동 삭제하지 않습니다.".to_owned())?;
    if manifest.owner != INSTALL_OWNER
        || manifest.skill_id != skill.skill_id
        || !manifest_supports_platform(&manifest, spec)
    {
        return Err(
            "쓰끼마가 소유한 해당 플랫폼 스킬 설치가 아니므로 삭제하지 않았습니다.".to_owned(),
        );
    }
    fs::remove_dir_all(&target)
        .map_err(|error| format!("프로젝트 스킬을 제거하지 못했습니다: {error}"))?;
    installation_status_at(&project_root, &skill, spec, &availability)
}

#[tauri::command]
pub(crate) fn pick_local_skill() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("스킬 Markdown 파일 선택")
        .add_filter("스킬 Markdown", &["md"])
        .pick_file()
        .map(|path| user_path_string(&path))
}

#[tauri::command]
pub(crate) fn pick_local_skill_folder() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("SKILL.md가 있는 스킬 폴더 선택")
        .pick_folder()
        .map(|path| user_path_string(&path))
}

#[tauri::command]
pub(crate) fn list_skill_library() -> Result<SkillLibrarySnapshot, String> {
    let root = default_library_root()?;
    let registry = read_registry(&root)?;
    Ok(SkillLibrarySnapshot {
        library_root: user_path_string(&root),
        skills: registry.skills,
    })
}

#[tauri::command]
pub(crate) fn register_local_skill(source_path: String) -> Result<SkillRegistrationResult, String> {
    register_skill_at(&default_library_root()?, Path::new(&source_path))
}

#[tauri::command]
pub(crate) fn inspect_codex_skill_installation(
    project_root: String,
    skill_id: String,
) -> Result<SkillInstallationStatus, String> {
    let root = default_library_root()?;
    let skill = find_skill(&root, &skill_id)?;
    let spec = platform_spec("codex")?;
    let availability = platform_availability(spec);
    installation_status_at(Path::new(&project_root), &skill, spec, &availability)
}

#[tauri::command]
pub(crate) fn install_codex_skill(
    project_root: String,
    skill_id: String,
) -> Result<SkillInstallationStatus, String> {
    install_skill_at(
        &default_library_root()?,
        Path::new(&project_root),
        &skill_id,
        "codex",
    )
}

#[tauri::command]
pub(crate) fn uninstall_codex_skill(
    project_root: String,
    skill_id: String,
) -> Result<SkillInstallationStatus, String> {
    uninstall_skill_at(
        &default_library_root()?,
        Path::new(&project_root),
        &skill_id,
        "codex",
    )
}

#[tauri::command]
pub(crate) fn inspect_project_skill_installations(
    project_root: String,
) -> Result<ProjectSkillInstallationSnapshot, String> {
    let library_root = default_library_root()?;
    let registry = read_registry(&library_root)?;
    let project_root = canonical_project_root(Path::new(&project_root))?;
    let platforms = PLATFORM_SPECS
        .iter()
        .copied()
        .map(platform_availability)
        .collect::<Vec<_>>();
    let mut installations = Vec::with_capacity(registry.skills.len() * PLATFORM_SPECS.len());
    for skill in &registry.skills {
        for (spec, availability) in PLATFORM_SPECS.iter().copied().zip(&platforms) {
            installations.push(installation_status_at(
                &project_root,
                skill,
                spec,
                availability,
            )?);
        }
    }
    Ok(ProjectSkillInstallationSnapshot {
        project_root: user_path_string(&project_root),
        platforms,
        installations,
    })
}

#[tauri::command]
pub(crate) fn install_project_skill(
    project_root: String,
    skill_id: String,
    platform: String,
) -> Result<SkillInstallationStatus, String> {
    install_skill_at(
        &default_library_root()?,
        Path::new(&project_root),
        &skill_id,
        &platform,
    )
}

#[tauri::command]
pub(crate) fn uninstall_project_skill(
    project_root: String,
    skill_id: String,
    platform: String,
) -> Result<SkillInstallationStatus, String> {
    uninstall_skill_at(
        &default_library_root()?,
        Path::new(&project_root),
        &skill_id,
        &platform,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process;
    use std::thread;

    fn test_root(label: &str) -> PathBuf {
        env::temp_dir().join(format!("skkima-skill-{label}-{}", process::id()))
    }

    fn write_skill(root: &Path, name: &str) {
        fs::create_dir_all(root).unwrap();
        fs::write(
            root.join("SKILL.md"),
            format!("---\nname: {name}\ndescription: Safe smoke test skill.\n---\n\n# Instructions\n\nCreate only requested text files.\n"),
        )
        .unwrap();
        fs::write(root.join("reference.txt"), "smoke-test\n").unwrap();
    }

    #[test]
    fn registers_a_versioned_local_snapshot() {
        let root = test_root("register");
        let source = root.join("source");
        let library = root.join("library");
        write_skill(&source, "Skkima Smoke Test");

        let first = register_skill_at(&library, &source).unwrap();
        let second = register_skill_at(&library, &source).unwrap();

        assert_eq!(first.state, "registered");
        assert_eq!(second.state, "already_registered");
        assert_eq!(first.skill.skill_id, "skkima-smoke-test");
        assert!(Path::new(&first.skill.snapshot_path)
            .join("SKILL.md")
            .is_file());
        assert_eq!(read_registry(&library).unwrap().skills.len(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn concurrent_registrations_preserve_every_skill() {
        let root = test_root("concurrent-register");
        let _ = fs::remove_dir_all(&root);
        let library = root.join("library");
        let sources = (0..8)
            .map(|index| {
                let source = root.join(format!("source-{index}"));
                write_skill(&source, &format!("Concurrent Skill {index}"));
                source
            })
            .collect::<Vec<_>>();

        let handles = sources
            .into_iter()
            .map(|source| {
                let library = library.clone();
                thread::spawn(move || register_skill_at(&library, &source))
            })
            .collect::<Vec<_>>();
        for handle in handles {
            handle.join().unwrap().unwrap();
        }

        let registry = read_registry(&library).unwrap();
        assert_eq!(registry.skills.len(), 8);
        assert!(!registry_lock_path(&library).exists());
        assert_eq!(
            fs::read_dir(&library)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry.file_name().to_string_lossy().contains("tmp-"))
                .count(),
            0
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn a_skill_file_registration_does_not_copy_sibling_files() {
        let root = test_root("single-file");
        let source = root.join("source");
        let library = root.join("library");
        write_skill(&source, "Single File Skill");
        fs::write(source.join("unrelated.bin"), b"do not snapshot").unwrap();

        let result = register_skill_at(&library, &source.join("SKILL.md")).unwrap();
        let snapshot = Path::new(&result.skill.snapshot_path);

        assert!(snapshot.join("SKILL.md").is_file());
        assert!(!snapshot.join("reference.txt").exists());
        assert!(!snapshot.join("unrelated.bin").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn normalizes_a_valid_markdown_file_to_the_skill_standard() {
        let root = test_root("markdown-normalization");
        let source = root.join("source");
        let library = root.join("library");
        fs::create_dir_all(&source).unwrap();
        let source_file = source.join("workflow-helper.md");
        let contents = "---\nname: Workflow Helper\ndescription: Guides a workflow safely.\n---\n\n# Instructions\n\nReview the request.\n";
        fs::write(&source_file, contents).unwrap();

        let result = register_skill_at(&library, &source_file).unwrap();
        let snapshot = Path::new(&result.skill.snapshot_path);

        assert_eq!(result.skill.source_kind, "local_file");
        assert_eq!(Path::new(&result.skill.source_path), source_file);
        assert_eq!(
            fs::read_to_string(snapshot.join("SKILL.md")).unwrap(),
            contents
        );
        assert!(!snapshot.join("workflow-helper.md").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_an_ordinary_markdown_document_without_skill_metadata() {
        let root = test_root("ordinary-markdown");
        let source = root.join("notes.md");
        let library = root.join("library");
        fs::create_dir_all(&root).unwrap();
        fs::write(&source, "# Project notes\n").unwrap();

        let error = register_skill_at(&library, &source).unwrap_err();

        assert!(error.contains("YAML 머리말"));
        assert!(!library.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn installs_and_removes_only_managed_codex_skills() {
        let root = test_root("install");
        let source = root.join("source");
        let library = root.join("library");
        let project = root.join("project");
        write_skill(&source, "Skkima Smoke Test");
        fs::create_dir_all(&project).unwrap();
        let registered = register_skill_at(&library, &source).unwrap();

        let installed =
            install_skill_at(&library, &project, &registered.skill.skill_id, "codex").unwrap();
        assert!(installed.structurally_valid);
        assert!(Path::new(&installed.target_path).join("SKILL.md").is_file());

        let removed =
            uninstall_skill_at(&library, &project, &registered.skill.skill_id, "codex").unwrap();
        assert_eq!(removed.state, "not_installed");
        assert!(!Path::new(&removed.target_path).exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn refuses_to_overwrite_an_unmanaged_skill_folder() {
        let root = test_root("conflict");
        let source = root.join("source");
        let library = root.join("library");
        let project = root.join("project");
        write_skill(&source, "Skkima Smoke Test");
        fs::create_dir_all(project.join(".agents/skills/skkima-smoke-test")).unwrap();
        let registered = register_skill_at(&library, &source).unwrap();

        let error =
            install_skill_at(&library, &project, &registered.skill.skill_id, "codex").unwrap_err();
        assert!(error.contains("덮어쓰지 않습니다"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn installs_shared_agents_skills_and_separate_claude_skills() {
        let root = test_root("multi-platform-install");
        let source = root.join("source");
        let library = root.join("library");
        let project = root.join("project");
        write_skill(&source, "Cross Platform Skill");
        fs::create_dir_all(&project).unwrap();
        let registered = register_skill_at(&library, &source).unwrap();

        let codex =
            install_skill_at(&library, &project, &registered.skill.skill_id, "codex").unwrap();
        let antigravity_spec = platform_spec("antigravity").unwrap();
        let antigravity = installation_status_at(
            &project,
            &registered.skill,
            antigravity_spec,
            &platform_availability(antigravity_spec),
        )
        .unwrap();
        let claude =
            install_skill_at(&library, &project, &registered.skill.skill_id, "claude").unwrap();

        assert_eq!(codex.target_path, antigravity.target_path);
        assert_eq!(antigravity.state, "installed");
        assert_ne!(codex.target_path, claude.target_path);
        assert!(Path::new(&claude.target_path).join("SKILL.md").is_file());

        uninstall_skill_at(
            &library,
            &project,
            &registered.skill.skill_id,
            "antigravity",
        )
        .unwrap();
        assert!(!Path::new(&codex.target_path).exists());
        assert!(Path::new(&claude.target_path).exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn reports_three_supported_skill_platforms() {
        let platforms = PLATFORM_SPECS
            .iter()
            .map(|spec| spec.id)
            .collect::<Vec<_>>();
        assert_eq!(platforms, vec!["codex", "claude", "antigravity"]);
        assert_eq!(
            platform_spec("codex").unwrap().skill_root,
            platform_spec("antigravity").unwrap().skill_root
        );
        assert_ne!(
            platform_spec("codex").unwrap().skill_root,
            platform_spec("claude").unwrap().skill_root
        );
    }

    #[test]
    fn requires_skill_frontmatter() {
        let root = test_root("invalid");
        let source = root.join("source");
        let library = root.join("library");
        fs::create_dir_all(&source).unwrap();
        fs::write(source.join("SKILL.md"), "# Missing metadata\n").unwrap();

        let error = register_skill_at(&library, &source).unwrap_err();
        assert!(error.contains("YAML 머리말"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn accepts_a_multiline_yaml_description() {
        let root = test_root("multiline-yaml");
        let source = root.join("source");
        let library = root.join("library");
        fs::create_dir_all(&source).unwrap();
        fs::write(
            source.join("SKILL.md"),
            "---\nname: Multiline Skill\ndescription: >-\n  First sentence.\n  Second sentence.\n---\n\n# Instructions\n",
        )
        .unwrap();

        let result = register_skill_at(&library, &source).unwrap();
        assert_eq!(result.skill.description, "First sentence. Second sentence.");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn records_github_plugin_provenance_without_changing_skill_contents() {
        let root = test_root("github-origin");
        let source = root.join("plugin/skills/workflow-input-assistant");
        let library = root.join("library");
        write_skill(&source, "Workflow Input Assistant");

        let result = register_skill_at_with_origin(
            &library,
            &source,
            Some("github_plugin"),
            Some("https://github.com/example/skills#skills/workflow-input-assistant"),
        )
        .unwrap();

        assert_eq!(result.skill.source_kind, "github_plugin");
        assert_eq!(
            result.skill.source_path,
            "https://github.com/example/skills#skills/workflow-input-assistant"
        );
        assert!(Path::new(&result.skill.snapshot_path)
            .join("SKILL.md")
            .is_file());
        fs::remove_dir_all(root).unwrap();
    }
}
