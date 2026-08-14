from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ARTIFACT_MANIFEST_VERSION = "0.2.0"
ARTIFACT_MANIFEST_NAME = "artifacts_manifest.json"

ASSET_ROOTS = {
    "images_generated": "assets/images/generated",
    "images_references": "assets/images/references",
    "prompts": "assets/prompts",
    "documents": "assets/documents",
    "other": "assets/other",
}

MANAGED_OUTPUT_ROLES = {"generated_output", "requested_output", "final_output"}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def artifact_manifest_path(run_dir: Path) -> Path:
    return run_dir / ARTIFACT_MANIFEST_NAME


def assets_root(run_dir: Path) -> Path:
    return run_dir / "assets"


def ensure_asset_dirs(run_dir: Path) -> None:
    for relative in ASSET_ROOTS.values():
        (run_dir / relative).mkdir(parents=True, exist_ok=True)


def default_manifest(run_dir: Path, *, created_at: str | None = None) -> dict[str, Any]:
    timestamp = created_at or now_iso()
    return {
        "artifact_manifest_version": ARTIFACT_MANIFEST_VERSION,
        "created_at": timestamp,
        "updated_at": timestamp,
        "run_dir": str(run_dir),
        "asset_roots": ASSET_ROOTS,
        "policy": {
            "binding_rule": (
                "Project files remain in their working workspace by default and must be registered "
                "before reports can treat them as produced artifacts."
            ),
            "official_artifact_rule": (
                "A generated file is not an official workflow artifact until it appears "
                "in artifacts_manifest.json and resolves to an existing file when status is present."
            ),
            "storage_rule": (
                "Use project_reference for working outputs and milestone_snapshot only for approved "
                "or final checkpoints."
            ),
        },
        "artifacts": [],
    }


def ensure_artifact_store(run_dir: Path, *, created_at: str | None = None) -> tuple[Path, dict[str, Any]]:
    ensure_asset_dirs(run_dir)
    manifest_path = artifact_manifest_path(run_dir)
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict):
            manifest = default_manifest(run_dir, created_at=created_at)
    else:
        manifest = default_manifest(run_dir, created_at=created_at)
        write_json(manifest_path, manifest)
    return manifest_path, manifest


def load_artifact_manifest(run_dir: Path, *, create_if_missing: bool = False) -> tuple[Path, dict[str, Any], bool]:
    manifest_path = artifact_manifest_path(run_dir)
    if manifest_path.exists():
        data = load_json(manifest_path)
        if isinstance(data, dict):
            return manifest_path, data, True
    if create_if_missing:
        path, manifest = ensure_artifact_store(run_dir)
        return path, manifest, True
    return manifest_path, default_manifest(run_dir), False


def is_within(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def requires_external_output_custody(
    *,
    source: Path,
    role: str,
    project_root: Path | None,
) -> bool:
    if project_root is None or role.strip().lower() not in MANAGED_OUTPUT_ROLES:
        return False
    return not is_within(project_root, source)


def relative_artifact_path(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def resolve_artifact_path(run_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return run_dir / path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_artifacts(run_dir: Path) -> dict[str, Any]:
    manifest_path, manifest, manifest_exists = load_artifact_manifest(run_dir)
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []

    inspected: list[dict[str, Any]] = []
    present_count = 0
    missing_count = 0
    external_count = 0
    by_type: dict[str, int] = {}
    content_groups: dict[str, list[dict[str, Any]]] = {}

    for item in artifacts:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "")
        resolved = resolve_artifact_path(run_dir, raw_path) if raw_path else run_dir
        exists = bool(raw_path and resolved.exists())
        bound_to_run = bool(raw_path and is_within(run_dir, resolved))
        status = str(item.get("status") or "unknown")
        effective_status = status
        if status == "present" and not exists:
            effective_status = "missing"
        if exists:
            present_count += 1
        else:
            missing_count += 1
        if not bound_to_run:
            external_count += 1
        artifact_type = str(item.get("type") or "unknown")
        by_type[artifact_type] = by_type.get(artifact_type, 0) + 1
        storage_mode = str(
            item.get("storage_mode")
            or ("milestone_snapshot" if bound_to_run else "project_reference")
        )
        working_source = str(item.get("working_source") or item.get("original_source") or raw_path)
        official_artifact = item.get("official_artifact")
        if official_artifact is None and bound_to_run:
            official_artifact = raw_path
        content_sha256 = None
        if exists and resolved.is_file():
            try:
                content_sha256 = file_sha256(resolved)
            except OSError:
                content_sha256 = None
        inspected_item = {
                "id": str(item.get("id") or ""),
                "type": artifact_type,
                "role": str(item.get("role") or ""),
                "path": raw_path,
                "absolute_path": str(resolved),
                "exists": exists,
                "bound_to_run": bound_to_run,
                "status": status,
                "effective_status": effective_status,
                "source_step": str(item.get("source_step") or ""),
                "prompt_file": str(item.get("prompt_file") or ""),
                "description": str(item.get("description") or ""),
                "storage_mode": storage_mode,
                "original_source": str(item.get("original_source") or raw_path),
                "working_source": working_source,
                "official_artifact": official_artifact,
                "snapshot_policy": str(item.get("snapshot_policy") or "milestone_only"),
                "deployment_target": item.get("deployment_target"),
                "deployment_status": str(item.get("deployment_status") or "not_requested"),
                "content_sha256": content_sha256,
                "duplicate_content": False,
                "duplicate_of": None,
            }
        inspected.append(inspected_item)
        if content_sha256:
            content_groups.setdefault(content_sha256, []).append(inspected_item)

    duplicate_groups: list[dict[str, Any]] = []
    for content_sha256, group in content_groups.items():
        if len(group) < 2:
            continue
        primary_id = group[0]["id"]
        for duplicate in group[1:]:
            duplicate["duplicate_content"] = True
            duplicate["duplicate_of"] = primary_id
        duplicate_groups.append(
            {
                "content_sha256": content_sha256,
                "artifact_ids": [item["id"] for item in group],
                "paths": [item["path"] for item in group],
                "roles": [item["role"] for item in group],
            }
        )

    hashed_present_count = sum(len(group) for group in content_groups.values())
    unique_present_count = present_count - hashed_present_count + len(content_groups)

    return {
        "manifest_file": str(manifest_path),
        "manifest_exists": manifest_exists,
        "assets_root": str(assets_root(run_dir)),
        "total_count": len(inspected),
        "present_count": present_count,
        "unique_present_count": unique_present_count,
        "duplicate_content_count": sum(len(group) - 1 for group in content_groups.values()),
        "duplicate_groups": duplicate_groups,
        "missing_count": missing_count,
        "external_count": external_count,
        "by_type": by_type,
        "artifacts": inspected,
    }


def safe_artifact_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    normalized = normalized.strip("._-")
    return normalized or "artifact"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def default_target_dir(artifact_type: str, role: str) -> str:
    artifact_type_lower = artifact_type.lower()
    role_lower = role.lower()
    if artifact_type_lower == "image":
        if "reference" in role_lower or "input" in role_lower:
            return ASSET_ROOTS["images_references"]
        return ASSET_ROOTS["images_generated"]
    if artifact_type_lower in {"prompt", "template"}:
        return ASSET_ROOTS["prompts"]
    if artifact_type_lower in {"document", "markdown", "text", "json"}:
        return ASSET_ROOTS["documents"]
    return ASSET_ROOTS["other"]


def resolve_target_path(
    run_dir: Path,
    source_path: Path,
    *,
    artifact_type: str,
    role: str,
    target_path: str | None = None,
) -> Path:
    if target_path:
        target = Path(target_path)
        if not target.is_absolute():
            target = run_dir / target
        if not is_within(run_dir, target):
            raise ValueError("target_path must stay inside the workflow run directory.")
        return unique_path(target)
    target_dir = run_dir / default_target_dir(artifact_type, role)
    target_dir.mkdir(parents=True, exist_ok=True)
    return unique_path(target_dir / source_path.name)


def register_artifact(
    run_dir: Path,
    *,
    artifact_id: str,
    artifact_type: str,
    role: str,
    path: str,
    source_step: str = "external_artifact",
    prompt_file: str | None = None,
    description: str | None = None,
    target_path: str | None = None,
    copy_into_run: bool = False,
    working_source: str | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    manifest_path, manifest, _ = load_artifact_manifest(run_dir, create_if_missing=True)
    ensure_asset_dirs(run_dir)
    normalized_artifact_id = safe_artifact_id(artifact_id)
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    previous_entry = next(
        (
            item
            for item in artifacts
            if isinstance(item, dict) and item.get("id") == normalized_artifact_id
        ),
        None,
    )

    source = Path(path)
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    automatic_custody = requires_external_output_custody(
        source=source,
        role=role,
        project_root=project_root,
    )
    if copy_into_run or automatic_custody:
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Artifact source file does not exist: {source}")
        previous_target = None
        if previous_entry and previous_entry.get("path"):
            candidate = resolve_artifact_path(run_dir, str(previous_entry["path"]))
            if is_within(run_dir, candidate) and candidate.is_file():
                previous_target = candidate
        if previous_target is not None and file_sha256(previous_target) == file_sha256(source):
            target = previous_target
        else:
            target = resolve_target_path(
                run_dir,
                source,
                artifact_type=artifact_type,
                role=role,
                target_path=target_path,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        stored_path = relative_artifact_path(run_dir, target)
        original_source = str(source)
        status = "present"
        storage_mode = "milestone_snapshot"
        official_artifact: str | None = stored_path
        custody_mode = "external_output_copy" if automatic_custody else "requested_snapshot"
    else:
        target = source
        stored_path = relative_artifact_path(run_dir, target)
        original_source = str(source)
        status = "present" if target.exists() else "missing"
        storage_mode = "project_reference"
        official_artifact = None
        custody_mode = "project_reference"

    entry = {
        "id": normalized_artifact_id,
        "type": artifact_type,
        "role": role,
        "path": stored_path,
        "source_step": source_step,
        "prompt_file": prompt_file or "",
        "description": description or "",
        "status": status,
        "registered_at": now_iso(),
        "original_source": original_source,
        "working_source": working_source or original_source,
        "official_artifact": official_artifact,
        "storage_mode": storage_mode,
        "custody_mode": custody_mode,
        "snapshot_policy": "milestone_only",
        "deployment_target": None,
        "deployment_status": "not_requested",
    }

    artifacts = [item for item in artifacts if not (isinstance(item, dict) and item.get("id") == entry["id"])]
    artifacts.append(entry)
    manifest["artifacts"] = artifacts
    manifest["artifact_manifest_version"] = ARTIFACT_MANIFEST_VERSION
    manifest["policy"] = default_manifest(run_dir)["policy"]
    manifest["updated_at"] = now_iso()
    write_json(manifest_path, manifest)
    return {
        "manifest_file": str(manifest_path),
        "artifact": entry,
        "artifact_status": inspect_artifacts(run_dir),
    }


def record_artifact_deployment(
    run_dir: Path,
    *,
    artifact_ids: list[str],
    target: str,
    status: str = "deployed",
) -> dict[str, Any]:
    if status not in {"in_progress", "deployed", "failed"}:
        raise ValueError("Deployment status must be in_progress, deployed, or failed.")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Deployment target must be a non-empty string.")
    normalized_ids = {safe_artifact_id(value).casefold() for value in artifact_ids if isinstance(value, str)}
    if not normalized_ids:
        raise ValueError("At least one artifact id is required for deployment recording.")
    manifest_path, manifest, manifest_exists = load_artifact_manifest(run_dir)
    if not manifest_exists:
        raise FileNotFoundError(f"Artifact manifest does not exist: {manifest_path}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Artifact manifest artifacts must be a list.")
    matched: set[str] = set()
    timestamp = now_iso()
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("id") or "")
        if artifact_id.casefold() not in normalized_ids:
            continue
        matched.add(artifact_id.casefold())
        item["deployment_target"] = target.strip()
        item["deployment_status"] = status
        item["deployment_updated_at"] = timestamp
    missing = sorted(normalized_ids - matched)
    if missing:
        raise ValueError(f"Deployment artifacts are not registered: {missing}")
    manifest["artifact_manifest_version"] = ARTIFACT_MANIFEST_VERSION
    manifest["updated_at"] = timestamp
    write_json(manifest_path, manifest)
    return inspect_artifacts(run_dir)
