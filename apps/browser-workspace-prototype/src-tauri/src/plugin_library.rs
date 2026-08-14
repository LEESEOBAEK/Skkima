use crate::atomic_file::{atomic_write, ExclusiveFileLock};
use crate::project_onboarding::user_path_string;
use crate::skill_library::{inspect_skill_source, register_github_skill, SkillRegistrationResult};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::env;
use std::fs;
use std::os::windows::process::CommandExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use wait_timeout::ChildExt;

const CREATE_NO_WINDOW: u32 = 0x08000000;
const REGISTRY_SCHEMA_VERSION: &str = "1.0.0";
const MAX_PLUGIN_FILES: usize = 2_000;
const MAX_PLUGIN_BYTES: u64 = 64 * 1024 * 1024;
const MAX_PLUGIN_SKILLS: usize = 64;
const CLONE_TIMEOUT: Duration = Duration::from_secs(90);
static IMPORT_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PluginSkillRecord {
    relative_path: String,
    skill_id: String,
    name: String,
    description: String,
    source_hash: String,
    valid: bool,
    validation_message: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PluginRecord {
    plugin_id: String,
    name: String,
    owner: String,
    repository: String,
    source_url: String,
    revision: String,
    source_hash: String,
    snapshot_path: String,
    imported_at: u64,
    skills: Vec<PluginSkillRecord>,
}

#[derive(Default, Deserialize, Serialize)]
struct PluginRegistry {
    #[serde(default = "registry_schema_version")]
    schema_version: String,
    #[serde(default)]
    plugins: Vec<PluginRecord>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PluginLibrarySnapshot {
    library_root: String,
    git_available: bool,
    git_version: Option<String>,
    plugins: Vec<PluginRecord>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PluginImportResult {
    state: String,
    plugin: PluginRecord,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PluginRemovalResult {
    state: String,
    plugin_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct GitHubRepository {
    owner: String,
    repository: String,
}

impl GitHubRepository {
    fn parse(value: &str) -> Result<Self, String> {
        let trimmed = value
            .trim()
            .trim_matches(|character| matches!(character, '<' | '>'))
            .trim_end_matches('/');
        let path = if let Some(path) = trimmed.strip_prefix("https://github.com/") {
            path
        } else if trimmed.contains("://") {
            return Err("공개 GitHub HTTPS 저장소 주소만 가져올 수 있습니다.".to_owned());
        } else {
            trimmed
        };
        if path
            .chars()
            .any(|character| matches!(character, '?' | '#' | '@'))
            || path.contains("//")
        {
            return Err("쿼리, 인증 정보가 포함된 GitHub 주소는 사용할 수 없습니다.".to_owned());
        }
        let parts = path.split('/').collect::<Vec<_>>();
        let owner = parts.first().copied().unwrap_or_default();
        let raw_repository = parts.get(1).copied().unwrap_or_default();
        let repository = raw_repository
            .strip_suffix(".git")
            .unwrap_or(raw_repository);
        let repository_view_is_supported = parts.len() == 2
            || (parts.len() >= 4
                && matches!(parts.get(2).copied(), Some("tree" | "blob"))
                && parts[3..]
                    .iter()
                    .all(|segment| !segment.is_empty() && *segment != "." && *segment != ".."));
        if owner.is_empty() || repository.is_empty() || !repository_view_is_supported {
            return Err(
                "GitHub 저장소는 owner/repository, 저장소 URL 또는 tree/blob 링크 형식이어야 합니다."
                    .to_owned(),
            );
        }
        if !valid_github_segment(owner) || !valid_github_segment(repository) {
            return Err(
                "GitHub 소유자 또는 저장소 이름에 지원하지 않는 문자가 있습니다.".to_owned(),
            );
        }
        Ok(Self {
            owner: owner.to_owned(),
            repository: repository.to_owned(),
        })
    }

    fn plugin_id(&self) -> String {
        format!(
            "{}--{}",
            self.owner.to_ascii_lowercase(),
            self.repository.to_ascii_lowercase()
        )
    }

    fn canonical_url(&self) -> String {
        format!("https://github.com/{}/{}", self.owner, self.repository)
    }
}

fn valid_github_segment(value: &str) -> bool {
    value
        .chars()
        .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.'))
        && value != "."
        && value != ".."
}

fn registry_schema_version() -> String {
    REGISTRY_SCHEMA_VERSION.to_owned()
}

fn unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn default_library_root() -> Result<PathBuf, String> {
    if let Some(configured) = env::var_os("SKKIMA_PLUGIN_LIBRARY_ROOT") {
        return Ok(PathBuf::from(configured));
    }
    crate::profile::app_data_root().map(|root| root.join("plugin-library"))
}

fn registry_path(root: &Path) -> PathBuf {
    root.join("registry.json")
}

fn registry_lock_path(root: &Path) -> PathBuf {
    root.join("registry.lock")
}

fn with_registry_lock<T>(
    root: &Path,
    operation: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    let _registry_lock = ExclusiveFileLock::acquire(&registry_lock_path(root))
        .map_err(|error| format!("플러그인 목록 갱신 잠금을 얻지 못했습니다: {error}"))?;
    operation()
}

fn read_registry(root: &Path) -> Result<PluginRegistry, String> {
    let path = registry_path(root);
    if !path.is_file() {
        return Ok(PluginRegistry {
            schema_version: registry_schema_version(),
            plugins: Vec::new(),
        });
    }
    let bytes = fs::read(&path)
        .map_err(|error| format!("플러그인 라이브러리 목록을 읽지 못했습니다: {error}"))?;
    let registry: PluginRegistry = serde_json::from_slice(&bytes)
        .map_err(|error| format!("플러그인 라이브러리 목록 형식이 올바르지 않습니다: {error}"))?;
    if registry.schema_version != REGISTRY_SCHEMA_VERSION {
        return Err(format!(
            "지원하지 않는 플러그인 라이브러리 버전입니다: {}",
            registry.schema_version
        ));
    }
    Ok(registry)
}

fn write_registry(root: &Path, registry: &PluginRegistry) -> Result<(), String> {
    fs::create_dir_all(root)
        .map_err(|error| format!("플러그인 라이브러리 폴더를 만들지 못했습니다: {error}"))?;
    let path = registry_path(root);
    let bytes = serde_json::to_vec_pretty(registry)
        .map_err(|error| format!("플러그인 목록을 직렬화하지 못했습니다: {error}"))?;
    atomic_write(&path, &bytes)
        .map_err(|error| format!("플러그인 목록을 확정하지 못했습니다: {error}"))
}

fn upsert_plugin_record(root: &Path, record: PluginRecord) -> Result<PluginImportResult, String> {
    let mut registry = read_registry(root)?;
    let state = if registry.plugins.iter().any(|plugin| {
        plugin.plugin_id == record.plugin_id && plugin.source_hash == record.source_hash
    }) {
        "already_imported"
    } else {
        "imported"
    };
    registry
        .plugins
        .retain(|plugin| plugin.plugin_id != record.plugin_id);
    registry.plugins.push(record.clone());
    registry.plugins.sort_by(|left, right| {
        left.name
            .to_ascii_lowercase()
            .cmp(&right.name.to_ascii_lowercase())
    });
    write_registry(root, &registry)?;
    Ok(PluginImportResult {
        state: state.to_owned(),
        plugin: record,
    })
}

fn git_version() -> Option<String> {
    let output = Command::new("cmd.exe")
        .args(["/D", "/S", "/C", "git --version"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    (!text.is_empty()).then_some(text)
}

fn clone_public_repository(url: &str, destination: &Path) -> Result<(), String> {
    let mut child = Command::new("git")
        .args([
            "-c",
            "core.hooksPath=NUL",
            "-c",
            "protocol.file.allow=never",
            "clone",
            "--depth",
            "1",
            "--filter=blob:limit=4m",
            "--no-tags",
            "--no-recurse-submodules",
            url,
        ])
        .arg(destination)
        .env("GIT_TERMINAL_PROMPT", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| format!("Git을 시작하지 못했습니다: {error}"))?;
    let status = child
        .wait_timeout(CLONE_TIMEOUT)
        .map_err(|error| format!("GitHub 가져오기 상태를 확인하지 못했습니다: {error}"))?;
    let Some(status) = status else {
        let _ = child.kill();
        let _ = child.wait();
        return Err("GitHub 저장소 가져오기가 90초를 초과해 중단되었습니다.".to_owned());
    };
    if !status.success() {
        return Err(
            "공개 GitHub 저장소를 가져오지 못했습니다. 주소, 공개 상태와 네트워크를 확인하세요."
                .to_owned(),
        );
    }
    Ok(())
}

fn repository_revision(checkout: &Path) -> Result<String, String> {
    let output = Command::new("git")
        .args(["-C"])
        .arg(checkout)
        .args(["rev-parse", "HEAD"])
        .stdin(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map_err(|error| format!("Git revision을 확인하지 못했습니다: {error}"))?;
    if !output.status.success() {
        return Err("가져온 저장소의 revision을 확인하지 못했습니다.".to_owned());
    }
    let revision = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if revision.len() != 40
        || !revision
            .chars()
            .all(|character| character.is_ascii_hexdigit())
    {
        return Err("가져온 저장소의 revision 형식이 올바르지 않습니다.".to_owned());
    }
    Ok(revision)
}

fn collect_repository_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    fn walk(
        root: &Path,
        current: &Path,
        files: &mut Vec<PathBuf>,
        bytes: &mut u64,
    ) -> Result<(), String> {
        for entry in fs::read_dir(current)
            .map_err(|error| format!("가져온 저장소를 읽지 못했습니다: {error}"))?
        {
            let entry = entry.map_err(|error| format!("저장소 항목을 읽지 못했습니다: {error}"))?;
            let path = entry.path();
            if current == root && entry.file_name() == ".git" {
                continue;
            }
            let metadata = fs::symlink_metadata(&path)
                .map_err(|error| format!("저장소 항목 정보를 읽지 못했습니다: {error}"))?;
            if metadata.file_type().is_symlink() {
                return Err(format!(
                    "심볼릭 링크가 포함된 저장소는 가져올 수 없습니다: {}",
                    path.display()
                ));
            }
            if metadata.is_dir() {
                walk(root, &path, files, bytes)?;
            } else if metadata.is_file() {
                *bytes = bytes.saturating_add(metadata.len());
                if *bytes > MAX_PLUGIN_BYTES {
                    return Err("플러그인 스냅샷은 64MB를 초과할 수 없습니다.".to_owned());
                }
                files.push(path.strip_prefix(root).unwrap_or(&path).to_path_buf());
                if files.len() > MAX_PLUGIN_FILES {
                    return Err("플러그인 스냅샷은 파일 2,000개를 초과할 수 없습니다.".to_owned());
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

fn repository_hash(root: &Path, files: &[PathBuf]) -> Result<String, String> {
    let mut digest = Sha256::new();
    for relative in files {
        digest.update(relative.to_string_lossy().replace('\\', "/").as_bytes());
        digest.update([0]);
        digest.update(
            fs::read(root.join(relative))
                .map_err(|error| format!("저장소 파일을 해시하지 못했습니다: {error}"))?,
        );
        digest.update([0]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn copy_repository_snapshot(
    checkout: &Path,
    files: &[PathBuf],
    target: &Path,
) -> Result<(), String> {
    if target.exists() {
        return Ok(());
    }
    let temporary = target.with_extension(format!("tmp-{}", std::process::id()));
    if temporary.exists() {
        fs::remove_dir_all(&temporary)
            .map_err(|error| format!("이전 임시 플러그인을 정리하지 못했습니다: {error}"))?;
    }
    fs::create_dir_all(&temporary)
        .map_err(|error| format!("임시 플러그인 스냅샷을 만들지 못했습니다: {error}"))?;
    for relative in files {
        let destination = temporary.join(relative);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("플러그인 하위 폴더를 만들지 못했습니다: {error}"))?;
        }
        fs::copy(checkout.join(relative), &destination)
            .map_err(|error| format!("플러그인 파일을 복사하지 못했습니다: {error}"))?;
    }
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("플러그인 저장 폴더를 만들지 못했습니다: {error}"))?;
    }
    fs::rename(&temporary, target)
        .map_err(|error| format!("플러그인 스냅샷을 확정하지 못했습니다: {error}"))
}

fn discover_skills(snapshot: &Path, files: &[PathBuf]) -> Result<Vec<PluginSkillRecord>, String> {
    let mut skill_files = files
        .iter()
        .filter(|relative| {
            relative
                .file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|value| value.eq_ignore_ascii_case("SKILL.md"))
        })
        .cloned()
        .collect::<Vec<_>>();
    skill_files.sort();
    if skill_files.len() > MAX_PLUGIN_SKILLS {
        return Err("한 저장소에서 스킬은 최대 64개까지 탐색할 수 있습니다.".to_owned());
    }
    Ok(skill_files
        .into_iter()
        .map(|skill_file| {
            let root = skill_file.parent().unwrap_or(Path::new(""));
            let relative_path = root.to_string_lossy().replace('\\', "/");
            match inspect_skill_source(&snapshot.join(root)) {
                Ok(summary) => PluginSkillRecord {
                    relative_path,
                    skill_id: summary.skill_id,
                    name: summary.name,
                    description: summary.description,
                    source_hash: summary.source_hash,
                    valid: true,
                    validation_message: "사용자 스킬 라이브러리에 등록할 수 있습니다.".to_owned(),
                },
                Err(error) => PluginSkillRecord {
                    relative_path: relative_path.clone(),
                    skill_id: String::new(),
                    name: if relative_path.is_empty() {
                        "저장소 루트 스킬".to_owned()
                    } else {
                        relative_path
                    },
                    description: "SKILL.md 검증이 필요합니다.".to_owned(),
                    source_hash: String::new(),
                    valid: false,
                    validation_message: error,
                },
            }
        })
        .collect())
}

fn import_repository_at(root: &Path, source_url: &str) -> Result<PluginImportResult, String> {
    let repository = GitHubRepository::parse(source_url)?;
    fs::create_dir_all(root)
        .map_err(|error| format!("플러그인 라이브러리를 준비하지 못했습니다: {error}"))?;
    let import_root = root.join(".imports").join(format!(
        "{}-{}-{}-{}",
        repository.plugin_id(),
        std::process::id(),
        unix_timestamp(),
        IMPORT_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    let checkout = import_root.join("checkout");
    fs::create_dir_all(&import_root)
        .map_err(|error| format!("임시 GitHub 가져오기 폴더를 만들지 못했습니다: {error}"))?;

    let result = (|| {
        clone_public_repository(&repository.canonical_url(), &checkout)?;
        let revision = repository_revision(&checkout)?;
        let files = collect_repository_files(&checkout)?;
        let hash = repository_hash(&checkout, &files)?;
        let snapshot = root
            .join("snapshots")
            .join(repository.plugin_id())
            .join(&hash);
        with_registry_lock(root, || {
            copy_repository_snapshot(&checkout, &files, &snapshot)?;
            let skills = discover_skills(&snapshot, &files)?;
            upsert_plugin_record(
                root,
                PluginRecord {
                    plugin_id: repository.plugin_id(),
                    name: repository.repository.clone(),
                    owner: repository.owner.clone(),
                    repository: repository.repository.clone(),
                    source_url: repository.canonical_url(),
                    revision,
                    source_hash: hash,
                    snapshot_path: user_path_string(&snapshot),
                    imported_at: unix_timestamp(),
                    skills,
                },
            )
        })
    })();

    let _ = fs::remove_dir_all(&import_root);
    result
}

fn safe_relative_skill_path(value: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(value);
    if path.components().any(|component| {
        matches!(
            component,
            Component::ParentDir | Component::RootDir | Component::Prefix(_)
        )
    }) {
        return Err("플러그인 스킬 경로가 저장소 밖을 가리킬 수 없습니다.".to_owned());
    }
    Ok(path)
}

fn find_plugin(root: &Path, plugin_id: &str) -> Result<PluginRecord, String> {
    read_registry(root)?
        .plugins
        .into_iter()
        .find(|plugin| plugin.plugin_id == plugin_id)
        .ok_or_else(|| format!("플러그인 라이브러리에서 {plugin_id} 항목을 찾을 수 없습니다."))
}

#[tauri::command]
pub(crate) fn list_plugin_library() -> Result<PluginLibrarySnapshot, String> {
    let root = default_library_root()?;
    let registry = read_registry(&root)?;
    let version = git_version();
    Ok(PluginLibrarySnapshot {
        library_root: user_path_string(&root),
        git_available: version.is_some(),
        git_version: version,
        plugins: registry.plugins,
    })
}

#[tauri::command]
pub(crate) fn import_github_plugin(source_url: String) -> Result<PluginImportResult, String> {
    if git_version().is_none() {
        return Err("GitHub 저장소를 가져오려면 Git CLI가 필요합니다.".to_owned());
    }
    import_repository_at(&default_library_root()?, &source_url)
}

#[tauri::command]
pub(crate) fn register_plugin_skill(
    plugin_id: String,
    relative_path: String,
) -> Result<SkillRegistrationResult, String> {
    let root = default_library_root()?;
    let plugin = find_plugin(&root, &plugin_id)?;
    let skill = plugin
        .skills
        .iter()
        .find(|skill| skill.relative_path == relative_path)
        .ok_or_else(|| "선택한 스킬을 플러그인 목록에서 찾을 수 없습니다.".to_owned())?;
    if !skill.valid {
        return Err(format!(
            "검증되지 않은 스킬은 등록할 수 없습니다: {}",
            skill.validation_message
        ));
    }
    let relative = safe_relative_skill_path(&relative_path)?;
    let snapshot = PathBuf::from(&plugin.snapshot_path);
    let skill_root = snapshot.join(relative);
    let canonical_snapshot = snapshot
        .canonicalize()
        .map_err(|error| format!("플러그인 스냅샷을 찾지 못했습니다: {error}"))?;
    let canonical_skill = skill_root
        .canonicalize()
        .map_err(|error| format!("플러그인 스킬 폴더를 찾지 못했습니다: {error}"))?;
    if !canonical_skill.starts_with(&canonical_snapshot) {
        return Err("플러그인 스킬 경로가 스냅샷 밖을 가리킵니다.".to_owned());
    }
    register_github_skill(&canonical_skill, &plugin.source_url, &relative_path)
}

#[tauri::command]
pub(crate) fn remove_plugin(plugin_id: String) -> Result<PluginRemovalResult, String> {
    remove_plugin_at(&default_library_root()?, &plugin_id)
}

fn remove_plugin_at(root: &Path, plugin_id: &str) -> Result<PluginRemovalResult, String> {
    with_registry_lock(root, || {
        let plugin = find_plugin(root, plugin_id)?;
        let expected_root = root.join("snapshots").join(&plugin.plugin_id);
        if expected_root.exists() {
            fs::remove_dir_all(&expected_root)
                .map_err(|error| format!("플러그인 스냅샷을 제거하지 못했습니다: {error}"))?;
        }
        let mut registry = read_registry(root)?;
        registry
            .plugins
            .retain(|item| item.plugin_id != plugin.plugin_id);
        write_registry(root, &registry)?;
        Ok(PluginRemovalResult {
            state: "removed".to_owned(),
            plugin_id: plugin_id.to_owned(),
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process;
    use std::thread;

    fn test_root(label: &str) -> PathBuf {
        env::temp_dir().join(format!("skkima-plugin-{label}-{}", process::id()))
    }

    fn write_skill(root: &Path, name: &str) {
        fs::create_dir_all(root).unwrap();
        fs::write(
            root.join("SKILL.md"),
            format!("---\nname: {name}\ndescription: Repository skill.\n---\n\n# Instructions\n"),
        )
        .unwrap();
    }

    #[test]
    fn normalizes_common_github_repository_inputs() {
        for source in [
            "LEESEOBAEK/Workflow-Input-Assistant",
            "https://github.com/LEESEOBAEK/Workflow-Input-Assistant",
            "https://github.com/LEESEOBAEK/Workflow-Input-Assistant.git",
            "https://github.com/LEESEOBAEK/Workflow-Input-Assistant/tree/main/skills",
            "https://github.com/LEESEOBAEK/Workflow-Input-Assistant/blob/main/SKILL.md",
            "<https://github.com/LEESEOBAEK/Workflow-Input-Assistant/>",
        ] {
            let parsed = GitHubRepository::parse(source).unwrap();
            assert_eq!(parsed.owner, "LEESEOBAEK");
            assert_eq!(parsed.repository, "Workflow-Input-Assistant");
            assert_eq!(
                parsed.canonical_url(),
                "https://github.com/LEESEOBAEK/Workflow-Input-Assistant"
            );
        }
        assert!(GitHubRepository::parse("http://github.com/a/b").is_err());
        assert!(GitHubRepository::parse("https://github.com/a/b?token=x").is_err());
        assert!(GitHubRepository::parse("https://github.com/a/b/issues/1").is_err());
        assert!(GitHubRepository::parse("https://example.com/a/b").is_err());
    }

    #[test]
    fn discovers_multiple_skills_without_executing_repository_code() {
        let root = test_root("discover");
        write_skill(&root, "Root Skill");
        write_skill(&root.join("skills/first"), "First Skill");
        write_skill(&root.join("packages/nested/second"), "Second Skill");
        fs::write(root.join("README.md"), "read only").unwrap();
        let files = collect_repository_files(&root).unwrap();
        let skills = discover_skills(&root, &files).unwrap();
        assert_eq!(skills.len(), 3);
        assert!(skills.iter().all(|skill| skill.valid));
        assert_eq!(
            skills
                .iter()
                .map(|skill| skill.relative_path.as_str())
                .collect::<Vec<_>>(),
            vec!["", "packages/nested/second", "skills/first"]
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn keeps_invalid_skill_metadata_visible_for_review() {
        let root = test_root("invalid-skill");
        fs::create_dir_all(root.join("broken")).unwrap();
        fs::write(root.join("broken/SKILL.md"), "# Missing frontmatter\n").unwrap();
        let files = collect_repository_files(&root).unwrap();
        let skills = discover_skills(&root, &files).unwrap();
        assert_eq!(skills.len(), 1);
        assert!(!skills[0].valid);
        assert!(skills[0].validation_message.contains("YAML"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_plugin_skill_path_traversal() {
        assert!(safe_relative_skill_path("skills/good").is_ok());
        assert!(safe_relative_skill_path("../outside").is_err());
        assert!(safe_relative_skill_path(r"C:\outside").is_err());
    }

    #[test]
    fn removes_only_the_selected_plugin_snapshot_and_registry_entry() {
        let root = test_root("remove");
        let plugin_id = "example--skills";
        let snapshot_root = root.join("snapshots").join(plugin_id).join("hash");
        fs::create_dir_all(&snapshot_root).unwrap();
        fs::write(snapshot_root.join("SKILL.md"), "read only").unwrap();
        write_registry(
            &root,
            &PluginRegistry {
                schema_version: registry_schema_version(),
                plugins: vec![PluginRecord {
                    plugin_id: plugin_id.to_owned(),
                    name: "skills".to_owned(),
                    owner: "example".to_owned(),
                    repository: "skills".to_owned(),
                    source_url: "https://github.com/example/skills".to_owned(),
                    revision: "a".repeat(40),
                    source_hash: "hash".to_owned(),
                    snapshot_path: user_path_string(&snapshot_root),
                    imported_at: 1,
                    skills: Vec::new(),
                }],
            },
        )
        .unwrap();

        let result = remove_plugin_at(&root, plugin_id).unwrap();

        assert_eq!(result.state, "removed");
        assert!(!root.join("snapshots").join(plugin_id).exists());
        assert!(read_registry(&root).unwrap().plugins.is_empty());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn concurrent_registry_updates_preserve_every_plugin() {
        let root = test_root("concurrent-registry");
        let _ = fs::remove_dir_all(&root);
        let handles = (0..8)
            .map(|index| {
                let root = root.clone();
                thread::spawn(move || {
                    let record = PluginRecord {
                        plugin_id: format!("owner--plugin-{index}"),
                        name: format!("plugin-{index}"),
                        owner: "owner".to_owned(),
                        repository: format!("plugin-{index}"),
                        source_url: format!("https://github.com/owner/plugin-{index}"),
                        revision: format!("revision-{index}"),
                        source_hash: format!("hash-{index}"),
                        snapshot_path: format!("snapshot-{index}"),
                        imported_at: index,
                        skills: Vec::new(),
                    };
                    with_registry_lock(&root, || upsert_plugin_record(&root, record))
                })
            })
            .collect::<Vec<_>>();
        for handle in handles {
            handle.join().unwrap().unwrap();
        }

        let registry = read_registry(&root).unwrap();
        assert_eq!(registry.plugins.len(), 8);
        assert!(!registry_lock_path(&root).exists());
        assert_eq!(
            fs::read_dir(&root)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry.file_name().to_string_lossy().contains("tmp-"))
                .count(),
            0
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    #[ignore = "requires network access to the public portfolio repository"]
    fn imports_the_public_workflow_input_assistant_repository() {
        let root = test_root("public-repository");
        let _ = fs::remove_dir_all(&root);
        let result = import_repository_at(
            &root,
            "https://github.com/LEESEOBAEK/Workflow-Input-Assistant",
        )
        .unwrap();
        assert_eq!(result.plugin.owner, "LEESEOBAEK");
        assert!(!result.plugin.skills.is_empty());
        assert!(result.plugin.skills.iter().any(|skill| skill.valid));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    #[ignore = "requires network access to a public multi-skill repository"]
    fn imports_a_public_tree_link_and_discovers_multiple_skills() {
        let root = test_root("public-multi-skill-repository");
        let _ = fs::remove_dir_all(&root);
        let result = import_repository_at(
            &root,
            "https://github.com/obra/superpowers/tree/main/skills",
        )
        .unwrap();

        assert_eq!(result.plugin.owner, "obra");
        assert_eq!(result.plugin.repository, "superpowers");
        assert!(result.plugin.skills.len() > 1);
        assert!(
            result
                .plugin
                .skills
                .iter()
                .filter(|skill| skill.valid)
                .count()
                > 1
        );
        fs::remove_dir_all(root).unwrap();
    }
}
