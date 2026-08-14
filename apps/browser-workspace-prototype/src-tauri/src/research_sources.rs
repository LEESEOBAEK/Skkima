use crate::atomic_file::atomic_write;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Component, Path, PathBuf};

const MANIFEST_RELATIVE_PATH: &str = "research_sources/sources.manifest.json";
const BINDING_FILE: &str = "research_sources.json";

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ResearchSource {
    pub source_id: String,
    pub source_type: String,
    pub title: String,
    pub locator: String,
    pub collected_at: String,
    pub sha256: Option<String>,
    pub quote: String,
    pub purpose: String,
    pub permission_status: String,
}

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct ResearchSourceManifest {
    schema_version: String,
    sources: Vec<ResearchSource>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResearchRunBinding {
    pub claim_kind: String,
    pub source_ids: Vec<String>,
}

fn root(path: &str) -> Result<PathBuf, String> {
    let root = PathBuf::from(path)
        .canonicalize()
        .map_err(|error| format!("프로젝트 폴더를 찾을 수 없습니다: {error}"))?;
    if !root.is_dir() {
        return Err("프로젝트 경로가 폴더가 아닙니다.".to_owned());
    }
    Ok(root)
}

fn manifest_path(root: &Path) -> PathBuf {
    root.join(MANIFEST_RELATIVE_PATH)
}

fn load_manifest(root: &Path) -> Result<ResearchSourceManifest, String> {
    let path = manifest_path(root);
    if !path.is_file() {
        return Ok(ResearchSourceManifest {
            schema_version: "1.0.0".to_owned(),
            sources: Vec::new(),
        });
    }
    let manifest: ResearchSourceManifest = serde_json::from_slice(
        &fs::read(&path).map_err(|error| format!("리서치 자료 목록을 읽지 못했습니다: {error}"))?,
    )
    .map_err(|error| format!("리서치 자료 목록 JSON이 올바르지 않습니다: {error}"))?;
    if manifest.schema_version != "1.0.0" {
        return Err("지원하지 않는 리서치 자료 목록 버전입니다.".to_owned());
    }
    Ok(manifest)
}

fn safe_id(value: &str) -> bool {
    !value.trim().is_empty()
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
}

fn safe_run_id(value: &str) -> bool {
    let path = Path::new(value.trim());
    !value.trim().is_empty()
        && path.components().count() == 1
        && matches!(path.components().next(), Some(Component::Normal(_)))
}

fn safe_url(value: &str) -> bool {
    let lower = value.trim().to_ascii_lowercase();
    (lower.starts_with("https://") || lower.starts_with("http://"))
        && !lower.contains('@')
        && !lower.contains('?')
        && !lower.contains('#')
}

fn inside_root(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let candidate = Path::new(relative);
    if candidate.is_absolute()
        || candidate
            .components()
            .any(|part| matches!(part, Component::ParentDir))
    {
        return Err("리서치 원문 경로는 ProjectRoot 내부의 상대 경로여야 합니다.".to_owned());
    }
    let resolved = root
        .join(candidate)
        .canonicalize()
        .map_err(|error| format!("리서치 원문을 찾을 수 없습니다: {error}"))?;
    if !resolved.starts_with(root) {
        return Err("리서치 원문 경로가 ProjectRoot 밖을 가리킵니다.".to_owned());
    }
    Ok(resolved)
}

fn hash(path: &Path) -> Result<String, String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(
            fs::read(path).map_err(|error| format!("리서치 원문을 읽지 못했습니다: {error}"))?
        )
    ))
}

fn validate(source: &ResearchSource, root: &Path) -> Result<(), String> {
    if !safe_id(&source.source_id) {
        return Err("리서치 source ID는 영문·숫자·-·_만 사용할 수 있습니다.".to_owned());
    }
    if source.title.trim().is_empty()
        || source.collected_at.trim().is_empty()
        || source.quote.trim().is_empty()
        || source.purpose.trim().is_empty()
    {
        return Err(
            "리서치 자료에는 제목, 수집 시각, 인용 또는 요약, 사용 목적이 필요합니다.".to_owned(),
        );
    }
    if !matches!(source.source_type.as_str(), "file" | "url" | "note") {
        return Err("지원하지 않는 리서치 자료 유형입니다.".to_owned());
    }
    if source.permission_status != "permitted" {
        return Err("보관 권한이 확인된 자료만 Run 근거로 등록할 수 있습니다.".to_owned());
    }
    match source.source_type.as_str() {
        "file" => {
            let path = inside_root(root, &source.locator)?;
            let expected = source
                .sha256
                .as_deref()
                .ok_or_else(|| "파일 자료에는 SHA-256이 필요합니다.".to_owned())?;
            if hash(&path)? != expected {
                return Err(format!(
                    "리서치 원문이 변경되었습니다: {}",
                    source.source_id
                ));
            }
        }
        "url" if !safe_url(&source.locator) => {
            return Err(
                "URL 자료에는 자격 증명·쿼리·프래그먼트 없는 http/https URL만 등록할 수 있습니다."
                    .to_owned(),
            )
        }
        _ => {}
    }
    Ok(())
}

fn preflight(root: &Path, binding: &ResearchRunBinding) -> Result<(), String> {
    let root = root
        .canonicalize()
        .map_err(|error| format!("프로젝트 폴더를 찾을 수 없습니다: {error}"))?;
    if !matches!(binding.claim_kind.as_str(), "fact" | "comparative") {
        return Err("리서치 판단 유형은 fact 또는 comparative여야 합니다.".to_owned());
    }
    let required = if binding.claim_kind == "comparative" {
        2
    } else {
        1
    };
    if binding.source_ids.len() < required {
        return Err(format!(
            "{} 판단에는 독립 출처 {}개 이상이 필요합니다.",
            if binding.claim_kind == "comparative" {
                "비교·권고·효과"
            } else {
                "사실"
            },
            required
        ));
    }
    let manifest = load_manifest(&root)?;
    let mut locators = std::collections::HashSet::new();
    for id in &binding.source_ids {
        let source = manifest
            .sources
            .iter()
            .find(|source| source.source_id == *id)
            .ok_or_else(|| format!("등록되지 않은 리서치 source ID입니다: {id}"))?;
        validate(source, &root)?;
        if !locators.insert(source.locator.clone()) {
            return Err("같은 원문 또는 URL을 독립 출처로 중복 사용할 수 없습니다.".to_owned());
        }
    }
    Ok(())
}

#[tauri::command]
pub fn list_research_sources(project_root: String) -> Result<Vec<ResearchSource>, String> {
    Ok(load_manifest(&root(&project_root)?)?.sources)
}

#[tauri::command]
pub fn save_research_sources(
    project_root: String,
    mut sources: Vec<ResearchSource>,
) -> Result<Vec<ResearchSource>, String> {
    let root = root(&project_root)?;
    let mut ids = std::collections::HashSet::new();
    for source in &mut sources {
        if source.source_type == "file" && source.sha256.is_none() {
            source.sha256 = Some(hash(&inside_root(&root, &source.locator)?)?);
        }
        if !ids.insert(source.source_id.clone()) {
            return Err(format!(
                "중복된 리서치 source ID입니다: {}",
                source.source_id
            ));
        }
        validate(source, &root)?;
    }
    let bytes = serde_json::to_vec_pretty(&ResearchSourceManifest {
        schema_version: "1.0.0".to_owned(),
        sources: sources.clone(),
    })
    .map_err(|error| format!("리서치 자료 목록을 만들지 못했습니다: {error}"))?;
    let path = manifest_path(&root);
    fs::create_dir_all(path.parent().expect("manifest parent"))
        .map_err(|error| format!("리서치 자료 폴더를 만들지 못했습니다: {error}"))?;
    atomic_write(&path, &bytes)
        .map_err(|error| format!("리서치 자료 목록을 저장하지 못했습니다: {error}"))?;
    Ok(sources)
}

#[tauri::command]
pub fn bind_research_sources(
    project_root: String,
    run_id: String,
    binding: ResearchRunBinding,
) -> Result<(), String> {
    let root = root(&project_root)?;
    if !safe_run_id(&run_id) {
        return Err("Run ID가 올바르지 않습니다.".to_owned());
    }
    preflight(&root, &binding)?;
    let path = root
        .join("outputs")
        .join("workflows")
        .join(run_id)
        .join(BINDING_FILE);
    if !path.parent().is_some_and(Path::is_dir) {
        return Err("준비된 Run을 찾을 수 없습니다.".to_owned());
    }
    atomic_write(
        &path,
        &serde_json::to_vec_pretty(&binding)
            .map_err(|error| format!("리서치 Run 연결을 만들지 못했습니다: {error}"))?,
    )
    .map_err(|error| format!("리서치 Run 연결을 저장하지 못했습니다: {error}"))
}

pub fn preflight_research_run(project_root: &Path, run_id: &str) -> Result<(), String> {
    let path = project_root
        .join("outputs")
        .join("workflows")
        .join(run_id)
        .join(BINDING_FILE);
    if !path.is_file() {
        return Ok(());
    }
    let binding: ResearchRunBinding = serde_json::from_slice(
        &fs::read(&path).map_err(|error| format!("리서치 Run 연결을 읽지 못했습니다: {error}"))?,
    )
    .map_err(|error| format!("리서치 Run 연결 JSON이 올바르지 않습니다: {error}"))?;
    preflight(project_root, &binding)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process;
    #[test]
    fn preflight_rejects_changed_files_and_requires_two_comparative_sources() {
        let root = std::env::temp_dir().join(format!("skkima-research-{}", process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("research_sources")).unwrap();
        let file = root.join("research_sources/source.txt");
        fs::write(&file, "original").unwrap();
        let source = ResearchSource {
            source_id: "source_1".to_owned(),
            source_type: "file".to_owned(),
            title: "자료".to_owned(),
            locator: "research_sources/source.txt".to_owned(),
            collected_at: "2026-08-06".to_owned(),
            sha256: Some(hash(&file).unwrap()),
            quote: "인용".to_owned(),
            purpose: "검증".to_owned(),
            permission_status: "permitted".to_owned(),
        };
        save_research_sources(root.to_string_lossy().into_owned(), vec![source]).unwrap();
        assert!(preflight(
            &root,
            &ResearchRunBinding {
                claim_kind: "fact".to_owned(),
                source_ids: vec!["source_1".to_owned()]
            }
        )
        .is_ok());
        assert!(preflight(
            &root,
            &ResearchRunBinding {
                claim_kind: "comparative".to_owned(),
                source_ids: vec!["source_1".to_owned()]
            }
        )
        .is_err());
        fs::write(&file, "changed").unwrap();
        assert!(preflight(
            &root,
            &ResearchRunBinding {
                claim_kind: "fact".to_owned(),
                source_ids: vec!["source_1".to_owned()]
            }
        )
        .is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn accepts_a_korean_run_id_but_rejects_path_traversal() {
        assert!(safe_run_id("2026-08-06_223000__시장_규모_확인__abc12345"));
        assert!(!safe_run_id("../run"));
        assert!(!safe_run_id("run/child"));
    }
}
