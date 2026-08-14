use crate::atomic_file::atomic_write;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const MAX_EVIDENCE_BYTES: usize = 256 * 1024;
const MAX_ACTION_BYTES: usize = 64 * 1024;
const MAX_CONTROLS: usize = 60;
const MAX_TEXT_LENGTH: usize = 512;
const DYNAMIC_LINK_THRESHOLD: u32 = 25;
const DYNAMIC_CONTROL_THRESHOLD: usize = 40;
const MAX_HISTORY_RECORDS: usize = 200;

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserEvidenceCounts {
    pub buttons: u32,
    pub links: u32,
    pub inputs: u32,
    pub forms: u32,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserEvidenceControl {
    pub kind: String,
    pub label: String,
    pub input_type: String,
    pub disabled: bool,
    pub href: String,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserWebEvidence {
    pub schema_version: String,
    pub evidence_id: String,
    pub captured_at: String,
    pub title: String,
    pub url: String,
    pub counts: BrowserEvidenceCounts,
    pub has_password_field: bool,
    pub controls: Vec<BrowserEvidenceControl>,
    pub project_id: Option<String>,
    pub project_name: String,
    pub session_id: Option<String>,
    pub session_name: String,
    pub source: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserEvidenceSaveResult {
    pub status: String,
    pub evidence_id: String,
    pub relative_path: String,
    pub sha256: String,
    pub saved_at: String,
    pub observation_count: u64,
    pub revision: u32,
}

#[derive(Clone, Deserialize, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserActionRecord {
    pub schema_version: String,
    pub action_id: String,
    pub created_at: String,
    pub action_type: String,
    pub status: String,
    pub risk: String,
    pub page_title: String,
    pub page_url: String,
    pub control_index: u32,
    pub control_kind: String,
    pub control_label: String,
    pub approval_scope: String,
    pub approved_at: String,
    pub executed_at: Option<String>,
    pub result_url: Option<String>,
    pub reason: Option<String>,
    pub project_id: Option<String>,
    pub project_name: String,
    pub session_id: Option<String>,
    pub session_name: String,
    pub source: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserActionSaveResult {
    pub status: String,
    pub action_id: String,
    pub relative_path: String,
    pub sha256: String,
    pub saved_at: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserEvidenceHistoryRecord {
    #[serde(flatten)]
    pub evidence: BrowserWebEvidence,
    pub relative_path: String,
    pub normalized_url: String,
    pub first_captured_at: String,
    pub last_captured_at: String,
    pub observation_count: u64,
    pub revision: u32,
    pub comparison_mode: String,
}

#[derive(Clone, Serialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BrowserEvidenceClearResult {
    pub status: String,
    pub deleted_records: usize,
    pub preserved_action_records: bool,
}

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
struct BrowserWebEvidenceDocument {
    #[serde(flatten)]
    evidence: BrowserWebEvidence,
    normalized_url: String,
    content_hash: String,
    first_captured_at: String,
    last_captured_at: String,
    observation_count: u64,
    revision: u32,
    comparison_mode: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct StoredBrowserWebEvidence {
    #[serde(flatten)]
    evidence: BrowserWebEvidence,
    #[serde(default)]
    normalized_url: Option<String>,
    #[serde(default)]
    content_hash: Option<String>,
    #[serde(default)]
    first_captured_at: Option<String>,
    #[serde(default)]
    last_captured_at: Option<String>,
    #[serde(default)]
    observation_count: Option<u64>,
    #[serde(default)]
    revision: Option<u32>,
    #[serde(default)]
    comparison_mode: Option<String>,
}

#[derive(Serialize)]
struct BrowserEvidenceFingerprint<'a> {
    normalized_url: &'a str,
    title: &'a str,
    counts: &'a BrowserEvidenceCounts,
    has_password_field: bool,
    controls: &'a [BrowserEvidenceControl],
}

#[derive(Serialize)]
struct BrowserEvidenceControlShape {
    kind: String,
    input_type: String,
    disabled: bool,
}

#[derive(Serialize)]
struct BrowserEvidenceStructureFingerprint<'a> {
    normalized_url: &'a str,
    counts: &'a BrowserEvidenceCounts,
    has_password_field: bool,
    controls: Vec<BrowserEvidenceControlShape>,
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn safe_component(value: &str, fallback: &str) -> String {
    let result: String = value
        .chars()
        .filter(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
        .take(80)
        .collect();
    if result.is_empty() {
        fallback.to_owned()
    } else {
        result
    }
}

fn normalize_url(url: &str) -> String {
    let without_query_or_fragment = url
        .split(|character| character == '?' || character == '#')
        .next()
        .unwrap_or(url)
        .trim_end_matches('/');
    if without_query_or_fragment.is_empty() {
        url.to_owned()
    } else {
        without_query_or_fragment.to_owned()
    }
}

fn comparison_mode(evidence: &BrowserWebEvidence) -> &'static str {
    if evidence.counts.links >= DYNAMIC_LINK_THRESHOLD
        || evidence.controls.len() >= DYNAMIC_CONTROL_THRESHOLD
    {
        "structure"
    } else {
        "snapshot"
    }
}

fn control_shapes(evidence: &BrowserWebEvidence) -> Vec<BrowserEvidenceControlShape> {
    let mut controls = evidence
        .controls
        .iter()
        .map(|control| BrowserEvidenceControlShape {
            kind: control.kind.clone(),
            input_type: control.input_type.clone(),
            disabled: control.disabled,
        })
        .collect::<Vec<_>>();
    controls.sort_by(|left, right| {
        (&left.kind, &left.input_type, left.disabled).cmp(&(
            &right.kind,
            &right.input_type,
            right.disabled,
        ))
    });
    controls
}

fn content_hash(evidence: &BrowserWebEvidence, normalized_url: &str) -> String {
    let bytes = if comparison_mode(evidence) == "structure" {
        serde_json::to_vec(&BrowserEvidenceStructureFingerprint {
            normalized_url,
            counts: &evidence.counts,
            has_password_field: evidence.has_password_field,
            controls: control_shapes(evidence),
        })
        .expect("browser evidence structure fingerprint is serializable")
    } else {
        serde_json::to_vec(&BrowserEvidenceFingerprint {
            normalized_url,
            title: &evidence.title,
            counts: &evidence.counts,
            has_password_field: evidence.has_password_field,
            controls: &evidence.controls,
        })
        .expect("browser evidence fingerprint is serializable")
    };
    format!("{:x}", Sha256::digest(&bytes))
}

fn validate_text(value: &str, field: &str) -> Result<(), String> {
    if value.len() > MAX_TEXT_LENGTH {
        return Err(format!("{field} is too long"));
    }
    Ok(())
}

fn validate_evidence(evidence: &BrowserWebEvidence) -> Result<(), String> {
    if evidence.schema_version != "1.0.0" {
        return Err("Unsupported browser evidence schema version".to_owned());
    }
    if evidence.source != "webview2-devtools-read-only" {
        return Err("Unknown browser evidence source".to_owned());
    }
    if evidence.evidence_id.trim().is_empty()
        || evidence.evidence_id.contains(['/', '\\'])
        || evidence.evidence_id.contains("..")
    {
        return Err("Browser evidence id is invalid".to_owned());
    }
    if !(evidence.url.starts_with("http://") || evidence.url.starts_with("https://")) {
        return Err("Only http and https evidence URLs are accepted".to_owned());
    }
    validate_text(&evidence.evidence_id, "evidence id")?;
    validate_text(&evidence.captured_at, "capturedAt")?;
    validate_text(&evidence.title, "title")?;
    validate_text(&evidence.url, "url")?;
    validate_text(&evidence.project_name, "projectName")?;
    validate_text(&evidence.session_name, "sessionName")?;
    if evidence.controls.len() > MAX_CONTROLS {
        return Err("Browser evidence contains too many controls".to_owned());
    }
    for control in &evidence.controls {
        if !matches!(
            control.kind.as_str(),
            "button" | "link" | "input" | "select" | "textarea"
        ) {
            return Err("Browser evidence contains an unsupported control kind".to_owned());
        }
        validate_text(&control.kind, "control kind")?;
        validate_text(&control.label, "control label")?;
        validate_text(&control.input_type, "control inputType")?;
        validate_text(&control.href, "control href")?;
        if !control.href.is_empty()
            && !(control.href.starts_with("http://") || control.href.starts_with("https://"))
        {
            return Err("Only http and https control links are accepted".to_owned());
        }
    }
    Ok(())
}

fn validate_action(action: &BrowserActionRecord) -> Result<(), String> {
    if action.schema_version != "1.0.0" {
        return Err("Unsupported browser action schema version".to_owned());
    }
    if action.source != "webview2-devtools-click" || action.action_type != "click" {
        return Err("Unknown browser action source or type".to_owned());
    }
    if !matches!(
        action.status.as_str(),
        "succeeded" | "failed" | "blocked" | "cancelled"
    ) {
        return Err("Browser action status is invalid".to_owned());
    }
    if !matches!(action.risk.as_str(), "interaction" | "navigation") {
        return Err("Browser action risk is invalid".to_owned());
    }
    if !matches!(action.approval_scope.as_str(), "once" | "session") {
        return Err("Browser action approval scope is invalid".to_owned());
    }
    if action.action_id.trim().is_empty()
        || action.action_id.contains(['/', '\\'])
        || action.action_id.contains("..")
    {
        return Err("Browser action id is invalid".to_owned());
    }
    if action.control_index >= MAX_CONTROLS as u32 {
        return Err("Browser action control index is invalid".to_owned());
    }
    if !matches!(action.control_kind.as_str(), "button" | "link") {
        return Err("Browser action control kind is invalid".to_owned());
    }
    if !(action.page_url.starts_with("http://") || action.page_url.starts_with("https://")) {
        return Err("Only http and https browser action URLs are accepted".to_owned());
    }
    validate_text(&action.action_id, "action id")?;
    validate_text(&action.created_at, "createdAt")?;
    validate_text(&action.page_title, "pageTitle")?;
    validate_text(&action.page_url, "pageUrl")?;
    validate_text(&action.control_label, "controlLabel")?;
    validate_text(&action.approved_at, "approvedAt")?;
    if let Some(executed_at) = &action.executed_at {
        validate_text(executed_at, "executedAt")?;
    }
    if let Some(reason) = &action.reason {
        validate_text(reason, "reason")?;
    }
    validate_text(&action.project_name, "projectName")?;
    validate_text(&action.session_name, "sessionName")?;
    Ok(())
}

fn evidence_directory(project_root: &Path) -> Result<(PathBuf, PathBuf), String> {
    let canonical_root = fs::canonicalize(project_root)
        .map_err(|error| format!("Unable to access project root: {error}"))?;
    if !canonical_root.is_dir() {
        return Err("Project root is not a directory".to_owned());
    }
    let directory = canonical_root.join("outputs").join("web_evidence");
    fs::create_dir_all(&directory)
        .map_err(|error| format!("Unable to prepare web evidence directory: {error}"))?;
    Ok((canonical_root, directory))
}

fn existing_evidence_directory(project_root: &Path) -> Result<Option<PathBuf>, String> {
    let canonical_root = fs::canonicalize(project_root)
        .map_err(|error| format!("Unable to access project root: {error}"))?;
    if !canonical_root.is_dir() {
        return Err("Project root is not a directory".to_owned());
    }
    let directory = canonical_root.join("outputs").join("web_evidence");
    if directory.is_dir() {
        Ok(Some(directory))
    } else {
        Ok(None)
    }
}

fn load_documents(directory: &Path) -> Vec<(PathBuf, BrowserWebEvidenceDocument)> {
    let Ok(entries) = fs::read_dir(directory) else {
        return Vec::new();
    };
    entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let path = entry.path();
            if path.extension().and_then(|extension| extension.to_str()) != Some("json") {
                return None;
            }
            let bytes = fs::read(&path).ok()?;
            let stored = serde_json::from_slice::<StoredBrowserWebEvidence>(&bytes).ok()?;
            let normalized_url = stored
                .normalized_url
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| normalize_url(&stored.evidence.url));
            let stored_comparison_mode = stored.comparison_mode.clone();
            let resolved_comparison_mode = stored_comparison_mode
                .clone()
                .filter(|value| value == "snapshot" || value == "structure")
                .unwrap_or_else(|| comparison_mode(&stored.evidence).to_owned());
            let content_hash = if stored_comparison_mode.is_some() {
                stored
                    .content_hash
                    .filter(|value| !value.is_empty())
                    .unwrap_or_else(|| content_hash(&stored.evidence, &normalized_url))
            } else {
                content_hash(&stored.evidence, &normalized_url)
            };
            Some((
                path,
                BrowserWebEvidenceDocument {
                    evidence: stored.evidence.clone(),
                    normalized_url,
                    content_hash,
                    first_captured_at: stored
                        .first_captured_at
                        .unwrap_or_else(|| stored.evidence.captured_at.clone()),
                    last_captured_at: stored
                        .last_captured_at
                        .unwrap_or_else(|| stored.evidence.captured_at.clone()),
                    observation_count: stored.observation_count.unwrap_or(1).max(1),
                    revision: stored.revision.unwrap_or(1).max(1),
                    comparison_mode: resolved_comparison_mode,
                },
            ))
        })
        .collect()
}

fn document_to_history_record(
    path: PathBuf,
    document: BrowserWebEvidenceDocument,
) -> BrowserEvidenceHistoryRecord {
    BrowserEvidenceHistoryRecord {
        evidence: document.evidence,
        relative_path: format!(
            "outputs/web_evidence/{}",
            path.file_name()
                .map(|name| name.to_string_lossy())
                .unwrap_or_default()
        ),
        normalized_url: document.normalized_url,
        first_captured_at: document.first_captured_at,
        last_captured_at: document.last_captured_at,
        observation_count: document.observation_count,
        revision: document.revision,
        comparison_mode: document.comparison_mode,
    }
}

fn document_bytes(document: &BrowserWebEvidenceDocument) -> Result<Vec<u8>, String> {
    let bytes = serde_json::to_vec_pretty(document)
        .map_err(|error| format!("Unable to serialize browser evidence: {error}"))?;
    if bytes.len() > MAX_EVIDENCE_BYTES {
        return Err("Browser evidence exceeds the storage size limit".to_owned());
    }
    Ok(bytes)
}

fn save_document(path: &Path, document: &BrowserWebEvidenceDocument) -> Result<String, String> {
    let bytes = document_bytes(document)?;
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    atomic_write(path, &bytes)
        .map_err(|error| format!("Unable to store browser evidence: {error}"))?;
    Ok(sha256)
}

fn save_action(path: &Path, action: &BrowserActionRecord) -> Result<String, String> {
    let bytes = serde_json::to_vec_pretty(action)
        .map_err(|error| format!("Unable to serialize browser action: {error}"))?;
    if bytes.len() > MAX_ACTION_BYTES {
        return Err("Browser action exceeds the storage size limit".to_owned());
    }
    let sha256 = format!("{:x}", Sha256::digest(&bytes));
    atomic_write(path, &bytes)
        .map_err(|error| format!("Unable to store browser action: {error}"))?;
    Ok(sha256)
}

fn clear_history_documents(directory: &Path) -> Result<usize, String> {
    let entries = fs::read_dir(directory)
        .map_err(|error| format!("Unable to read browser evidence directory: {error}"))?;
    let mut deleted_records = 0;

    for entry in entries {
        let entry =
            entry.map_err(|error| format!("Unable to inspect browser evidence: {error}"))?;
        let path = entry.path();
        if path.extension().and_then(|extension| extension.to_str()) != Some("json") {
            continue;
        }
        fs::remove_file(&path).map_err(|error| {
            format!(
                "Unable to remove browser evidence {}: {error}",
                path.display()
            )
        })?;
        deleted_records += 1;
    }

    Ok(deleted_records)
}

#[tauri::command]
pub fn save_browser_web_evidence(
    project_root: String,
    evidence: BrowserWebEvidence,
) -> Result<BrowserEvidenceSaveResult, String> {
    validate_evidence(&evidence)?;
    let (_, directory) = evidence_directory(Path::new(&project_root))?;
    let documents = load_documents(&directory);
    let normalized_url = normalize_url(&evidence.url);
    let current_content_hash = content_hash(&evidence, &normalized_url);

    if let Some((path, previous)) = documents
        .iter()
        .find(|(_, document)| document.content_hash == current_content_hash)
    {
        let mut updated = previous.clone();
        updated.last_captured_at = evidence.captured_at.clone();
        updated.observation_count = updated.observation_count.saturating_add(1);
        let sha256 = save_document(path, &updated)?;
        return Ok(BrowserEvidenceSaveResult {
            status: "deduplicated".to_owned(),
            evidence_id: updated.evidence.evidence_id,
            relative_path: format!(
                "outputs/web_evidence/{}",
                path.file_name().unwrap().to_string_lossy()
            ),
            sha256,
            saved_at: now_millis().to_string(),
            observation_count: updated.observation_count,
            revision: updated.revision,
        });
    }

    let revision = documents
        .iter()
        .filter(|(_, document)| document.normalized_url == normalized_url)
        .map(|(_, document)| document.revision)
        .max()
        .unwrap_or(0)
        .saturating_add(1);
    let document = BrowserWebEvidenceDocument {
        first_captured_at: evidence.captured_at.clone(),
        last_captured_at: evidence.captured_at.clone(),
        observation_count: 1,
        revision,
        normalized_url,
        content_hash: current_content_hash,
        comparison_mode: comparison_mode(&evidence).to_owned(),
        evidence,
    };
    let file_name = format!(
        "{}_{}.json",
        now_millis(),
        safe_component(&document.evidence.evidence_id, "browser-read")
    );
    let path = directory.join(file_name);
    let sha256 = save_document(&path, &document)?;

    Ok(BrowserEvidenceSaveResult {
        status: "saved".to_owned(),
        evidence_id: document.evidence.evidence_id,
        relative_path: format!(
            "outputs/web_evidence/{}",
            path.file_name().unwrap().to_string_lossy()
        ),
        sha256,
        saved_at: now_millis().to_string(),
        observation_count: document.observation_count,
        revision: document.revision,
    })
}

#[tauri::command]
pub fn save_browser_action_record(
    project_root: String,
    action: BrowserActionRecord,
) -> Result<BrowserActionSaveResult, String> {
    validate_action(&action)?;
    let (_, directory) = evidence_directory(Path::new(&project_root))?;
    let actions_directory = directory.join("actions");
    fs::create_dir_all(&actions_directory)
        .map_err(|error| format!("Unable to prepare browser action directory: {error}"))?;
    let file_name = format!(
        "{}_{}.json",
        now_millis(),
        safe_component(&action.action_id, "browser-action")
    );
    let path = actions_directory.join(file_name);
    let sha256 = save_action(&path, &action)?;
    Ok(BrowserActionSaveResult {
        status: "saved".to_owned(),
        action_id: action.action_id,
        relative_path: format!(
            "outputs/web_evidence/actions/{}",
            path.file_name().unwrap().to_string_lossy()
        ),
        sha256,
        saved_at: now_millis().to_string(),
    })
}

#[tauri::command]
pub fn list_browser_web_evidence(
    project_root: String,
) -> Result<Vec<BrowserEvidenceHistoryRecord>, String> {
    let Some(directory) = existing_evidence_directory(Path::new(&project_root))? else {
        return Ok(Vec::new());
    };

    let mut documents = load_documents(&directory);
    documents.sort_by(|left, right| {
        right
            .1
            .last_captured_at
            .cmp(&left.1.last_captured_at)
            .then_with(|| right.1.revision.cmp(&left.1.revision))
    });

    Ok(documents
        .into_iter()
        .take(MAX_HISTORY_RECORDS)
        .map(|(path, document)| document_to_history_record(path, document))
        .collect())
}

#[tauri::command]
pub fn clear_browser_web_evidence(
    project_root: String,
) -> Result<BrowserEvidenceClearResult, String> {
    let Some(directory) = existing_evidence_directory(Path::new(&project_root))? else {
        return Ok(BrowserEvidenceClearResult {
            status: "empty".to_owned(),
            deleted_records: 0,
            preserved_action_records: true,
        });
    };

    let deleted_records = clear_history_documents(&directory)?;
    Ok(BrowserEvidenceClearResult {
        status: if deleted_records == 0 {
            "empty".to_owned()
        } else {
            "cleared".to_owned()
        },
        deleted_records,
        preserved_action_records: true,
    })
}

#[cfg(test)]
mod tests {
    use super::{
        comparison_mode, content_hash, document_to_history_record, normalize_url, validate_action,
        validate_evidence, BrowserActionRecord, BrowserEvidenceControl, BrowserEvidenceCounts,
        BrowserEvidenceHistoryRecord, BrowserWebEvidence, BrowserWebEvidenceDocument,
        StoredBrowserWebEvidence,
    };

    fn evidence() -> BrowserWebEvidence {
        BrowserWebEvidence {
            schema_version: "1.0.0".to_owned(),
            evidence_id: "browser-read-test".to_owned(),
            captured_at: "2026-08-02T00:00:00.000Z".to_owned(),
            title: "Example".to_owned(),
            url: "https://example.com/".to_owned(),
            counts: BrowserEvidenceCounts {
                buttons: 1,
                links: 1,
                inputs: 0,
                forms: 0,
            },
            has_password_field: false,
            controls: vec![BrowserEvidenceControl {
                kind: "link".to_owned(),
                label: "More".to_owned(),
                input_type: String::new(),
                disabled: false,
                href: "https://example.com/more".to_owned(),
            }],
            project_id: Some("project-1".to_owned()),
            project_name: "Example project".to_owned(),
            session_id: Some("session-1".to_owned()),
            session_name: "Read page".to_owned(),
            source: "webview2-devtools-read-only".to_owned(),
        }
    }

    #[test]
    fn accepts_read_only_web_evidence() {
        assert!(validate_evidence(&evidence()).is_ok());
    }

    #[test]
    fn accepts_only_approved_single_click_action_records() {
        let action = BrowserActionRecord {
            schema_version: "1.0.0".to_owned(),
            action_id: "browser-click-1".to_owned(),
            created_at: "2026-08-02T00:00:00.000Z".to_owned(),
            action_type: "click".to_owned(),
            status: "succeeded".to_owned(),
            risk: "interaction".to_owned(),
            page_title: "Example".to_owned(),
            page_url: "https://example.com/".to_owned(),
            control_index: 0,
            control_kind: "button".to_owned(),
            control_label: "Continue".to_owned(),
            approval_scope: "once".to_owned(),
            approved_at: "2026-08-02T00:00:00.000Z".to_owned(),
            executed_at: Some("2026-08-02T00:00:01.000Z".to_owned()),
            result_url: Some("https://example.com/".to_owned()),
            reason: None,
            project_id: Some("project-1".to_owned()),
            project_name: "Example project".to_owned(),
            session_id: Some("session-1".to_owned()),
            session_name: "Read page".to_owned(),
            source: "webview2-devtools-click".to_owned(),
        };
        assert!(validate_action(&action).is_ok());
    }

    #[test]
    fn rejects_non_web_urls_and_path_like_ids() {
        let mut invalid_url = evidence();
        invalid_url.url = "file:///secret.txt".to_owned();
        assert!(validate_evidence(&invalid_url).is_err());

        let mut invalid_id = evidence();
        invalid_id.evidence_id = "../escape".to_owned();
        assert!(validate_evidence(&invalid_id).is_err());
    }

    #[test]
    fn observation_fingerprint_ignores_capture_identity_and_time() {
        let first = evidence();
        let mut second = first.clone();
        second.evidence_id = "browser-read-another".to_owned();
        second.captured_at = "2026-08-02T01:00:00.000Z".to_owned();

        let normalized_url = normalize_url(&first.url);
        assert_eq!(
            content_hash(&first, &normalized_url),
            content_hash(&second, &normalize_url(&second.url))
        );
    }

    #[test]
    fn changed_page_structure_gets_a_new_fingerprint() {
        let first = evidence();
        let mut second = first.clone();
        second.counts.links = 2;

        let normalized_url = normalize_url(&first.url);
        assert_ne!(
            content_hash(&first, &normalized_url),
            content_hash(&second, &normalized_url)
        );
    }

    #[test]
    fn dynamic_pages_use_structure_comparison() {
        let mut dynamic = evidence();
        dynamic.counts.links = 49;

        assert_eq!(comparison_mode(&dynamic), "structure");
    }

    #[test]
    fn dynamic_page_hash_ignores_labels_and_link_targets() {
        let mut first = evidence();
        first.counts.links = 49;
        let mut second = first.clone();
        second.title = "A different rotating headline".to_owned();
        second.controls[0].label = "Another label".to_owned();
        second.controls[0].href = "https://example.com/rotated".to_owned();

        let normalized_url = normalize_url(&first.url);
        assert_eq!(
            content_hash(&first, &normalized_url),
            content_hash(&second, &normalized_url)
        );
    }

    #[test]
    fn static_page_hash_detects_label_changes() {
        let first = evidence();
        let mut second = first.clone();
        second.controls[0].label = "Changed label".to_owned();

        let normalized_url = normalize_url(&first.url);
        assert_ne!(
            content_hash(&first, &normalized_url),
            content_hash(&second, &normalized_url)
        );
    }

    #[test]
    fn legacy_raw_evidence_can_be_read_with_observation_defaults() {
        let bytes = serde_json::to_vec(&evidence()).expect("legacy evidence serializes");
        let stored: StoredBrowserWebEvidence =
            serde_json::from_slice(&bytes).expect("legacy evidence remains readable");

        assert!(stored.normalized_url.is_none());
        assert!(stored.content_hash.is_none());
        assert_eq!(stored.observation_count, None);
        assert_eq!(stored.evidence.evidence_id, "browser-read-test");
    }

    #[test]
    fn history_record_preserves_revision_metadata_and_relative_path() {
        let document = BrowserWebEvidenceDocument {
            first_captured_at: "2026-08-02T00:00:00.000Z".to_owned(),
            last_captured_at: "2026-08-02T01:00:00.000Z".to_owned(),
            observation_count: 3,
            revision: 2,
            normalized_url: "https://example.com".to_owned(),
            content_hash: "hash".to_owned(),
            comparison_mode: "snapshot".to_owned(),
            evidence: evidence(),
        };

        let record = document_to_history_record(
            std::path::PathBuf::from("1770000000000_browser-read-test.json"),
            document,
        );

        assert_eq!(
            record.relative_path,
            "outputs/web_evidence/1770000000000_browser-read-test.json"
        );
        assert_eq!(record.normalized_url, "https://example.com");
        assert_eq!(record.observation_count, 3);
        assert_eq!(record.revision, 2);
        assert_eq!(record.comparison_mode, "snapshot");
        let _: BrowserEvidenceHistoryRecord = record;
    }
}
