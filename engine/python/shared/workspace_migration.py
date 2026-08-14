from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from shared import workspace_governance as governance


INVENTORY_VERSION = "1.0"
MIGRATION_PLAN_VERSION = "1.0"


def validate_output_paths_outside_source(
    source_root: Path,
    output_paths: list[Path | None] | tuple[Path | None, ...],
) -> None:
    """Reject report destinations that would mutate the inventoried source tree."""
    source = Path(source_root).expanduser().resolve(strict=True)
    forbidden: list[str] = []
    for raw_path in output_paths:
        if raw_path is None:
            continue
        output = Path(raw_path).expanduser().resolve(strict=False)
        if governance.is_within(source, output):
            forbidden.append(str(output))
    if forbidden:
        raise governance.WorkspaceGovernanceError(
            "SOURCE_OUTPUT_PATH_FORBIDDEN",
            "Inventory and migration dry-run outputs must be outside the source root.",
            source_root=str(source),
            forbidden_output_paths=forbidden,
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def tree_snapshot(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_hash = _file_sha256(path)
        digest.update(f"{relative}\t{size}\t{file_hash}\n".encode("utf-8"))
        file_count += 1
        total_bytes += size
    return {
        "root": str(root),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }


def _run_snapshot(run_dir: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted((item for item in run_dir.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(run_dir).as_posix()
        size = path.stat().st_size
        file_hash = _file_sha256(path)
        digest.update(f"{relative}\t{size}\t{file_hash}\n".encode("utf-8"))
        file_count += 1
        total_bytes += size
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "content_sha256": digest.hexdigest(),
    }


def _load_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, "manifest root is not a JSON object"
    return data, None


def _raw_text(manifest: dict[str, Any], run_dir: Path) -> tuple[str, str]:
    source = manifest.get("source")
    if isinstance(source, dict) and isinstance(source.get("raw_text"), str):
        return source["raw_text"], "manifest.source.raw_text"
    source_file = run_dir / "00_source" / "data" / "input.txt"
    if source_file.is_file():
        return source_file.read_text(encoding="utf-8-sig").rstrip("\r\n"), "00_source/data/input.txt"
    return "", "missing"


def _resolve_report(manifest: dict[str, Any], run_dir: Path, source_root: Path) -> tuple[str | None, bool]:
    raw = (manifest.get("paths") or {}).get("human_report")
    candidates: list[Path] = []
    if raw:
        configured = Path(str(raw))
        if configured.is_absolute():
            candidates.append(configured)
        else:
            candidates.extend((source_root / configured, run_dir / configured))
    candidates.append(run_dir / "06_human_readable_report" / "reports" / "human_readable_report.md")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve(strict=False)), True
    return (str(candidates[0].resolve(strict=False)) if candidates else None), False


def _status(manifest: dict[str, Any], run_dir: Path) -> str:
    status_path = run_dir / "workflow_status.json"
    if status_path.is_file():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and data.get("workflow_state"):
            return str(data["workflow_state"])
    if manifest.get("status"):
        return str(manifest["status"])
    layers = manifest.get("layers")
    if isinstance(layers, list) and any(
        isinstance(item, dict) and item.get("id") == "06_human_readable_report" and item.get("status") == "ready"
        for item in layers
    ):
        return "ready_for_next_action"
    return "unknown"


def inventory_workspace(source_root: str | Path) -> dict[str, Any]:
    source_root = Path(source_root).resolve(strict=True)
    if not source_root.is_dir():
        raise NotADirectoryError(f"Inventory source must be a directory: {source_root}")
    snapshot = tree_snapshot(source_root)
    manifests = sorted(source_root.rglob("workflow_manifest.json"), key=lambda item: item.as_posix())
    runs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for manifest_path in manifests:
        run_dir = manifest_path.parent.resolve(strict=False)
        manifest, error = _load_manifest(manifest_path)
        if manifest is None:
            errors.append({"manifest_path": str(manifest_path), "error": str(error)})
            continue
        text, text_source = _raw_text(manifest, run_dir)
        hash_record = governance.input_hash_record(text)
        report_path, report_exists = _resolve_report(manifest, run_dir, source_root)
        run_snapshot = _run_snapshot(run_dir)
        source_runs_root = run_dir.parent
        run_id = str(manifest.get("run_id") or run_dir.name)
        runs.append(
            {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "run_dir_relative": run_dir.relative_to(source_root).as_posix(),
                "source_runs_root": str(source_runs_root),
                "source_runs_root_relative": source_runs_root.relative_to(source_root).as_posix(),
                "manifest_path": str(manifest_path.resolve(strict=False)),
                "manifest_sha256": _file_sha256(manifest_path),
                "workflow_version": manifest.get("workflow_version"),
                "governance_version": manifest.get("governance_version") or "legacy",
                "created_at": manifest.get("created_at"),
                "status": _status(manifest, run_dir),
                "raw_input_source": text_source,
                "raw_input_hash": hash_record,
                "raw_input_length": len(text),
                "duplicate_group_id": governance.duplicate_group_id(hash_record["value"]),
                "report_path": report_path,
                "report_exists": report_exists,
                "parent_run_id": manifest.get("parent_run_id"),
                "relation_type": manifest.get("relation_type") or "independent",
                **run_snapshot,
            }
        )

    source_roots = sorted({item["source_runs_root"] for item in runs}, key=str.casefold)
    hash_groups: dict[str, list[str]] = {}
    for run in runs:
        value = run["raw_input_hash"]["value"]
        hash_groups.setdefault(value, []).append(run["run_id"])
    duplicate_groups = [
        {
            "input_hash": key,
            "duplicate_group_id": governance.duplicate_group_id(key),
            "run_ids": value,
            "run_count": len(value),
        }
        for key, value in sorted(hash_groups.items())
        if len(value) > 1
    ]
    reasoning_support_groups = [
        item
        for item in duplicate_groups
        if sum("reasoning-support" in run_id.casefold() for run_id in item["run_ids"]) >= 2
    ]
    engine_copy_candidates = sorted(
        {
            str(path.parent.parent.resolve(strict=False))
            for path in source_root.rglob("engine/python/workflow/workflow_runner.py")
            if (path.parent.parent / "layers").is_dir()
        }
        | ({str((source_root / "schema_workflow_engine").resolve(strict=False))} if (source_root / "schema_workflow_engine").exists() else set()),
        key=str.casefold,
    )
    return {
        "inventory_version": INVENTORY_VERSION,
        "generated_at": governance.now_iso(),
        "mode": "read_only",
        "source_root": str(source_root),
        "source_snapshot": snapshot,
        "observed": {
            "workflow_manifest_count": len(runs),
            "unreadable_manifest_count": len(errors),
            "unique_raw_input_hash_count": len(hash_groups),
            "source_runs_root_count": len(source_roots),
            "duplicate_input_group_count": len(duplicate_groups),
            "reasoning_support_independent_run_count": sum(
                item["run_count"] for item in reasoning_support_groups
            ),
            "engine_copy_candidate_count": len(engine_copy_candidates),
        },
        "source_runs_roots": source_roots,
        "duplicate_groups": duplicate_groups,
        "reasoning_support_duplicate_groups": reasoning_support_groups,
        "engine_copy_candidates": engine_copy_candidates,
        "manifest_errors": errors,
        "runs": runs,
    }


def _collision_run_id(run_id: str, content_hash: str, occupied: set[str]) -> str:
    stem = f"{run_id}__migrated_{content_hash[:8]}"
    candidate = stem
    counter = 2
    while candidate in occupied:
        candidate = f"{stem}_{counter:02d}"
        counter += 1
    return candidate


def migration_dry_run(
    inventory: dict[str, Any],
    *,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    source_root = Path(str(inventory["source_root"])).resolve(strict=True)
    project_root_path = Path(project_root).resolve(strict=False) if project_root is not None else source_root
    canonical_root = (project_root_path / governance.RUNS_ROOT_RELATIVE).resolve(strict=False)
    canonical_key = str(canonical_root).casefold()
    canonical_by_id: dict[str, dict[str, Any]] = {
        str(item["run_id"]): item
        for item in inventory.get("runs", [])
        if str(Path(str(item["source_runs_root"])).resolve(strict=False)).casefold() == canonical_key
    }
    planned_by_id = dict(canonical_by_id)
    occupied = set(canonical_by_id)
    plans: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    for run in inventory.get("runs", []):
        run_id = str(run["run_id"])
        source_runs_root = Path(str(run["source_runs_root"])).resolve(strict=False)
        target_run_id = run_id
        collision: dict[str, Any] | None = None
        if str(source_runs_root).casefold() == canonical_key:
            action = "register_in_place"
            reason = "run is already under CanonicalRunsRoot"
        elif run_id not in occupied:
            action = "copy"
            reason = "unofficial run root requires a verified copy into CanonicalRunsRoot"
            occupied.add(target_run_id)
            planned_by_id[target_run_id] = run
        else:
            existing = planned_by_id.get(run_id)
            if existing and existing.get("content_sha256") == run.get("content_sha256"):
                action = "alias_existing"
                reason = "RunId collision has identical content fingerprint"
            else:
                action = "copy_with_collision_suffix"
                target_run_id = _collision_run_id(run_id, str(run["content_sha256"]), occupied)
                occupied.add(target_run_id)
                reason = "RunId collision has different or unavailable content fingerprint"
            collision = {
                "original_run_id": run_id,
                "source_run_dir": run["run_dir"],
                "existing_target_run_dir": str(canonical_root / run_id),
                "planned_target_run_id": target_run_id,
                "resolution": action,
            }
            collisions.append(collision)
        plans.append(
            {
                "original_run_id": run_id,
                "planned_run_id": target_run_id,
                "action": action,
                "reason": reason,
                "source_run_dir": run["run_dir"],
                "source_runs_root": run["source_runs_root"],
                "planned_target_run_dir": str(canonical_root / target_run_id),
                "file_count": run["file_count"],
                "total_bytes": run["total_bytes"],
                "content_sha256": run["content_sha256"],
                "input_hash": run["raw_input_hash"],
                "duplicate_group_id": run["duplicate_group_id"],
                "provenance": {
                    "inventory_source_root": str(source_root),
                    "original_run_id": run_id,
                    "original_run_dir": run["run_dir"],
                    "manifest_sha256": run["manifest_sha256"],
                },
                "verification": {
                    "copy_performed": False,
                    "required_after_copy": [
                        "file_count",
                        "total_bytes",
                        "content_sha256",
                        "manifest provenance",
                    ],
                },
                "collision": collision,
            }
        )
    after_snapshot = tree_snapshot(source_root)
    before_snapshot = inventory["source_snapshot"]
    unchanged = before_snapshot == after_snapshot
    action_counts: dict[str, int] = {}
    for plan in plans:
        action_counts[plan["action"]] = action_counts.get(plan["action"], 0) + 1
    return {
        "migration_plan_version": MIGRATION_PLAN_VERSION,
        "generated_at": governance.now_iso(),
        "mode": "dry_run_only",
        "source_root": str(source_root),
        "project_root": str(project_root_path),
        "canonical_runs_root": str(canonical_root),
        "safety": {
            "copy_performed": False,
            "move_performed": False,
            "delete_performed": False,
            "overwrite_performed": False,
            "source_snapshot_before": before_snapshot,
            "source_snapshot_after": after_snapshot,
            "source_unchanged_during_dry_run": unchanged,
        },
        "summary": {
            "inventory_run_count": len(plans),
            "action_counts": action_counts,
            "collision_count": len(collisions),
            "duplicate_group_count": len(inventory.get("duplicate_groups", [])),
            "engine_copy_candidate_count": len(inventory.get("engine_copy_candidates", [])),
        },
        "collisions": collisions,
        "duplicate_groups": inventory.get("duplicate_groups", []),
        "engine_copy_candidates": inventory.get("engine_copy_candidates", []),
        "plans": plans,
    }


def migration_markdown(report: dict[str, Any]) -> str:
    rows = [
        "| Source RunId | Action | Planned RunId | Source Root |",
        "|---|---|---|---|",
    ]
    for item in report["plans"]:
        rows.append(
            f"| {item['original_run_id']} | {item['action']} | {item['planned_run_id']} | {item['source_runs_root']} |"
        )
    collision_rows = ["| Original RunId | Resolution | Planned RunId |", "|---|---|---|"]
    for item in report["collisions"]:
        collision_rows.append(
            f"| {item['original_run_id']} | {item['resolution']} | {item['planned_target_run_id']} |"
        )
    if len(collision_rows) == 2:
        collision_rows.append("| none | none | none |")
    safety = report["safety"]
    return "\n".join(
        [
            "# Workspace Migration Dry-Run Report",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Source root: `{report['source_root']}`",
            f"- Planned CanonicalRunsRoot: `{report['canonical_runs_root']}`",
            "- Mode: `dry_run_only`",
            f"- Source unchanged during dry-run: `{safety['source_unchanged_during_dry_run']}`",
            "- Copy performed: `False`",
            "- Move performed: `False`",
            "- Delete performed: `False`",
            "- Overwrite performed: `False`",
            "",
            "## Plan",
            "",
            *rows,
            "",
            "## Collisions",
            "",
            *collision_rows,
            "",
            "## Duplicate Groups",
            "",
            f"- Groups: `{report['summary']['duplicate_group_count']}`",
            "- All same-input runs are preserved; only relationships are planned.",
            "",
            "## Engine Copy Candidates",
            "",
            *([f"- `{item}`" for item in report["engine_copy_candidates"]] or ["- none"]),
            "",
            "## Later Approved Migration Verification",
            "",
            "1. Copy without overwrite.",
            "2. Verify file count, byte count, and content fingerprint.",
            "3. Record original RunId, path, and manifest hash as provenance.",
            "4. Register manifests and rebuild the registry.",
            "5. Keep source folders until a separate cleanup approval.",
            "",
        ]
    )


def write_inventory(path: Path, inventory: dict[str, Any]) -> None:
    validate_output_paths_outside_source(Path(inventory["source_root"]), [path])
    governance.atomic_write_json(path, inventory)


def write_migration_report(
    *,
    json_path: Path | None,
    markdown_path: Path,
    report: dict[str, Any],
) -> None:
    validate_output_paths_outside_source(
        Path(report["source_root"]),
        [json_path, markdown_path],
    )
    if json_path is not None:
        governance.atomic_write_json(json_path, report)
    governance.atomic_write_text(markdown_path, migration_markdown(report))
