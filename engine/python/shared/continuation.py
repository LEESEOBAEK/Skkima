from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from shared import artifacts as artifact_store
from shared import continuation_lifecycle as lifecycle


CONTINUATION_STATE_VERSION = lifecycle.CURRENT_VERSION
CONTINUATION_STATE_NAME = "continuation_state.json"
REPAIRABLE_WORKSPACE_ERROR_CODES = {
    "INVALID_WORKING_ROOT",
    "WORKING_ROOT_NOT_FOUND",
    "WORKING_ROOT_NOT_DIRECTORY",
}


def now_iso(*, tzinfo: Any = None) -> str:
    return datetime.now(tzinfo).isoformat(timespec="seconds")


def continuation_state_path(run_dir: Path) -> Path:
    return Path(run_dir) / CONTINUATION_STATE_NAME


def _to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_to_json(state) + "\n", encoding="utf-8")


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _error(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _excel_label(index: int) -> str:
    label = ""
    value = index
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _deduplicate_strings(values: list[str], *, field_name: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must contain non-empty strings.")
        normalized = value.strip()
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _normalize_artifact_ids(
    active_artifact_ids: list[str],
    candidate_artifact_id: str | None,
) -> list[str]:
    if not isinstance(active_artifact_ids, list):
        raise TypeError("active_artifact_ids must be a list of artifact ids.")
    artifact_ids = _deduplicate_strings(active_artifact_ids, field_name="active_artifact_ids")
    if candidate_artifact_id is not None:
        if not isinstance(candidate_artifact_id, str) or not candidate_artifact_id.strip():
            raise ValueError("candidate_artifact_id must be a non-empty string when provided.")
        candidate_id = candidate_artifact_id.strip()
        if candidate_id.casefold() not in {item.casefold() for item in artifact_ids}:
            artifact_ids.append(candidate_id)
    return artifact_ids


def _state_artifact_error(run_dir: Path, artifact_status: dict[str, Any]) -> dict[str, str] | None:
    state_path = continuation_state_path(run_dir).resolve()
    for artifact in artifact_status.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        absolute_path = artifact.get("absolute_path")
        if not isinstance(absolute_path, str) or not absolute_path:
            continue
        try:
            registered_path = Path(absolute_path).resolve()
        except (OSError, RuntimeError):
            continue
        if registered_path == state_path:
            return _error(
                "CONTINUATION_REGISTERED_AS_ARTIFACT",
                "active_artifacts",
                "continuation_state.json is workflow state and must never be registered as an artifact.",
            )
    return None


def _validate_artifact_references(run_dir: Path, state: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    try:
        artifact_status = artifact_store.inspect_artifacts(run_dir)
    except Exception as exc:
        return [
            _error(
                "ARTIFACT_INSPECTION_FAILED",
                "active_artifacts",
                f"Artifact binding could not be inspected: {exc}",
            )
        ]

    state_artifact_error = _state_artifact_error(run_dir, artifact_status)
    if state_artifact_error:
        errors.append(state_artifact_error)

    if not artifact_status.get("manifest_exists"):
        errors.append(
            _error(
                "ARTIFACT_MANIFEST_MISSING",
                "active_artifacts",
                "artifacts_manifest.json is required before continuation state can reference artifacts.",
            )
        )

    registered = {
        str(item.get("id")): item
        for item in artifact_status.get("artifacts", [])
        if isinstance(item, dict) and item.get("id")
    }
    references: list[tuple[str, str]] = []
    active_artifacts = state.get("active_artifacts")
    if isinstance(active_artifacts, list):
        for index, item in enumerate(active_artifacts):
            if isinstance(item, dict) and isinstance(item.get("artifact_id"), str):
                references.append((f"active_artifacts[{index}].artifact_id", item["artifact_id"]))

    candidate_set = state.get("candidate_set")
    if isinstance(candidate_set, dict):
        source_artifact = candidate_set.get("source_artifact")
        if isinstance(source_artifact, dict) and isinstance(source_artifact.get("artifact_id"), str):
            references.append(("candidate_set.source_artifact.artifact_id", source_artifact["artifact_id"]))

    checked: set[str] = set()
    for path, artifact_id in references:
        key = artifact_id.casefold()
        if key in checked:
            continue
        checked.add(key)
        matches = [item for registered_id, item in registered.items() if registered_id.casefold() == key]
        if not matches:
            errors.append(
                _error(
                    "ARTIFACT_NOT_REGISTERED",
                    path,
                    f"Artifact id '{artifact_id}' is not registered in artifacts_manifest.json.",
                )
            )
            continue
        if len(matches) > 1:
            errors.append(
                _error(
                    "ARTIFACT_ID_AMBIGUOUS",
                    path,
                    f"Artifact id '{artifact_id}' matches multiple case-insensitive ids.",
                )
            )
            continue
        artifact = matches[0]
        if not artifact.get("exists"):
            errors.append(
                _error(
                    "ARTIFACT_FILE_MISSING",
                    path,
                    f"Artifact id '{artifact_id}' does not resolve to an existing file.",
                )
            )
    return errors


def _validate_workflow_anchor(run_dir: Path, state: dict[str, Any]) -> list[dict[str, str]]:
    manifest_path = Path(run_dir) / "workflow_manifest.json"
    if not manifest_path.exists():
        return [
            _error(
                "WORKFLOW_MANIFEST_MISSING",
                "continuation_id",
                "workflow_manifest.json is required to validate the continuation anchor run.",
            )
        ]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            _error(
                "WORKFLOW_MANIFEST_INVALID",
                "continuation_id",
                f"workflow_manifest.json could not be read: {exc}",
            )
        ]
    anchor_run_id = manifest.get("run_id") if isinstance(manifest, dict) else None
    if not isinstance(anchor_run_id, str) or not anchor_run_id:
        return [
            _error(
                "WORKFLOW_RUN_ID_MISSING",
                "continuation_id",
                "workflow_manifest.json must contain a non-empty run_id.",
            )
        ]
    if state.get("continuation_id") != anchor_run_id:
        return [
            _error(
                "CONTINUATION_ANCHOR_MISMATCH",
                "continuation_id",
                "continuation_id must equal workflow_manifest.run_id.",
            )
        ]
    return []


def delivery_status(run_dir: Path, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return whether active continuation outputs have a user-facing delivery path."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "workflow_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {
            "valid": True,
            "policy": "legacy_untracked",
            "operation_id": None,
            "required_artifact_ids": [],
            "undelivered_artifact_ids": [],
        }

    operation_id = str(manifest.get("active_continuation_operation_id") or "")
    operations = [
        item
        for item in manifest.get("continuation_operations", [])
        if isinstance(item, dict)
    ]
    operation = next(
        (item for item in operations if str(item.get("operation_id") or "") == operation_id),
        None,
    )
    policy = str((operation or {}).get("delivery_policy") or "legacy_untracked")
    if policy != "required":
        return {
            "valid": True,
            "policy": policy,
            "operation_id": operation_id or None,
            "required_artifact_ids": [],
            "undelivered_artifact_ids": [],
        }

    current_state = state if state is not None else load_continuation_state(run_dir)
    active_ids = [
        str(item.get("artifact_id") or "")
        for item in current_state.get("active_artifacts", [])
        if isinstance(item, dict) and item.get("artifact_id")
    ]
    artifact_status = artifact_store.inspect_artifacts(run_dir)
    artifacts_by_id = {
        str(item.get("id") or ""): item
        for item in artifact_status.get("artifacts", [])
        if isinstance(item, dict) and item.get("id")
    }
    project_root_value = manifest.get("project_root_absolute")
    project_root = Path(str(project_root_value)).resolve(strict=False) if project_root_value else None

    def path_key(value: Any) -> str:
        if not str(value or "").strip():
            return ""
        candidate = Path(str(value))
        if not candidate.is_absolute() and project_root is not None:
            candidate = project_root / candidate
        return str(candidate.resolve(strict=False)).casefold()

    deliverable_keys: set[str] = set()
    for item in manifest.get("deliverable_paths", []):
        if not isinstance(item, dict):
            continue
        for key in ("path_absolute", "path_relative"):
            normalized = path_key(item.get(key))
            if normalized:
                deliverable_keys.add(normalized)

    required_ids: list[str] = []
    undelivered_ids: list[str] = []
    for artifact_id in active_ids:
        artifact = artifacts_by_id.get(artifact_id)
        if artifact is None:
            required_ids.append(artifact_id)
            undelivered_ids.append(artifact_id)
            continue
        role = str(artifact.get("role") or "").strip().lower()
        if role in {"reference", "reference_input", "source_input"} or "reference" in role:
            continue
        required_ids.append(artifact_id)
        artifact_keys = {
            path_key(artifact.get("absolute_path")),
            path_key(artifact.get("original_source")),
            path_key(artifact.get("working_source")),
        }
        artifact_keys.discard("")
        if not artifact_keys.intersection(deliverable_keys):
            undelivered_ids.append(artifact_id)

    return {
        "valid": not undelivered_ids,
        "policy": policy,
        "operation_id": operation_id or None,
        "required_artifact_ids": required_ids,
        "undelivered_artifact_ids": undelivered_ids,
        "deliverable_count": len(deliverable_keys),
    }


def _validate_candidate_set(candidate_set: Any, source_run_ids: list[str]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if candidate_set is None:
        return errors
    if not isinstance(candidate_set, dict):
        return [_error("INVALID_TYPE", "candidate_set", "candidate_set must be an object or null.")]

    source_artifact = candidate_set.get("source_artifact")
    if not isinstance(source_artifact, dict):
        errors.append(
            _error(
                "INVALID_TYPE",
                "candidate_set.source_artifact",
                "candidate_set.source_artifact must be an artifact reference object.",
            )
        )
    else:
        source_run_id = source_artifact.get("source_run_id")
        artifact_id = source_artifact.get("artifact_id")
        if not isinstance(source_run_id, str) or source_run_id not in source_run_ids:
            errors.append(
                _error(
                    "INVALID_SOURCE_RUN",
                    "candidate_set.source_artifact.source_run_id",
                    "The candidate artifact source_run_id must appear in source_run_ids.",
                )
            )
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            errors.append(
                _error(
                    "INVALID_ARTIFACT_ID",
                    "candidate_set.source_artifact.artifact_id",
                    "The candidate artifact id must be a non-empty string.",
                )
            )

    count = candidate_set.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        errors.append(_error("INVALID_COUNT", "candidate_set.count", "Candidate count must be a positive integer."))
        count = 0

    index_rule = candidate_set.get("index_rule")
    labels: list[Any] = []
    if not isinstance(index_rule, dict):
        errors.append(_error("INVALID_TYPE", "candidate_set.index_rule", "index_rule must be an object."))
    else:
        if not isinstance(index_rule.get("order"), str) or not index_rule["order"].strip():
            errors.append(
                _error("INVALID_INDEX_ORDER", "candidate_set.index_rule.order", "Index order must be a non-empty string.")
            )
        if index_rule.get("numeric_base") != 1:
            errors.append(
                _error("INVALID_NUMERIC_BASE", "candidate_set.index_rule.numeric_base", "numeric_base must be 1.")
            )
        raw_labels = index_rule.get("labels")
        if not isinstance(raw_labels, list):
            errors.append(_error("INVALID_TYPE", "candidate_set.index_rule.labels", "labels must be a list."))
        else:
            labels = raw_labels
            if count and len(labels) != count:
                errors.append(
                    _error(
                        "LABEL_COUNT_MISMATCH",
                        "candidate_set.index_rule.labels",
                        "The label count must match candidate_set.count.",
                    )
                )

    candidates = candidate_set.get("candidates")
    canonical_candidates: dict[str, dict[str, Any]] = {}
    if not isinstance(candidates, list):
        errors.append(_error("INVALID_TYPE", "candidate_set.candidates", "candidates must be a list."))
        candidates = []
    elif count and len(candidates) != count:
        errors.append(
            _error(
                "CANDIDATE_COUNT_MISMATCH",
                "candidate_set.candidates",
                "The number of candidates must match candidate_set.count.",
            )
        )

    alias_owners: dict[str, str] = {}
    for index, candidate in enumerate(candidates, start=1):
        path = f"candidate_set.candidates[{index - 1}]"
        if not isinstance(candidate, dict):
            errors.append(_error("INVALID_TYPE", path, "Each candidate must be an object."))
            continue
        candidate_id = candidate.get("candidate_id")
        ordinal = candidate.get("ordinal")
        aliases = candidate.get("aliases")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            errors.append(_error("INVALID_CANDIDATE_ID", f"{path}.candidate_id", "candidate_id must be non-empty."))
            continue
        candidate_key = candidate_id.casefold()
        if candidate_key in canonical_candidates:
            errors.append(_error("DUPLICATE_CANDIDATE_ID", f"{path}.candidate_id", "candidate_id values must be unique."))
        canonical_candidates[candidate_key] = candidate
        if ordinal != index:
            errors.append(
                _error(
                    "INVALID_ORDINAL",
                    f"{path}.ordinal",
                    "Candidate ordinals must be contiguous and 1-based.",
                )
            )
        if index - 1 < len(labels) and labels[index - 1] != candidate_id:
            errors.append(
                _error(
                    "LABEL_CANDIDATE_MISMATCH",
                    f"{path}.candidate_id",
                    "Candidate ids must match index_rule.labels in order.",
                )
            )
        if not isinstance(aliases, list) or not all(isinstance(item, str) and item.strip() for item in aliases):
            errors.append(_error("INVALID_ALIASES", f"{path}.aliases", "aliases must be non-empty strings."))
            continue
        required_aliases = {str(index).casefold(), f"{index}번".casefold(), candidate_id.casefold()}
        actual_aliases = {item.strip().casefold() for item in aliases}
        if not required_aliases.issubset(actual_aliases):
            errors.append(
                _error(
                    "REQUIRED_ALIAS_MISSING",
                    f"{path}.aliases",
                    "Each candidate needs numeric, Korean numeric, and label aliases.",
                )
            )
        for alias in actual_aliases:
            owner = alias_owners.get(alias)
            if owner is not None and owner != candidate_key:
                errors.append(
                    _error(
                        "AMBIGUOUS_ALIAS",
                        f"{path}.aliases",
                        f"Alias '{alias}' resolves to more than one candidate.",
                    )
                )
            else:
                alias_owners[alias] = candidate_key

    selected = candidate_set.get("selected_candidate")
    if selected is not None:
        if not isinstance(selected, dict):
            errors.append(
                _error(
                    "INVALID_TYPE",
                    "candidate_set.selected_candidate",
                    "selected_candidate must be a canonical candidate object or null.",
                )
            )
        else:
            selected_id = selected.get("candidate_id")
            canonical = canonical_candidates.get(selected_id.casefold()) if isinstance(selected_id, str) else None
            if canonical is None or selected != canonical:
                errors.append(
                    _error(
                        "NON_CANONICAL_SELECTION",
                        "candidate_set.selected_candidate",
                        "selected_candidate must exactly match one object in candidates.",
                    )
                )
    return errors


def _validate_state(run_dir: Path, state: Any) -> list[dict[str, str]]:
    if not isinstance(state, dict):
        return [_error("INVALID_ROOT", "", "Continuation state must be a JSON object.")]

    errors: list[dict[str, str]] = []
    required_fields = [
        "continuation_state_version",
        "continuation_id",
        "created_at",
        "updated_at",
        "completed_at",
        "elapsed_seconds",
        "current_phase",
        "source_run_ids",
        "active_artifacts",
        "candidate_set",
        "next_actions",
        "decision_log",
    ]
    if state.get("continuation_state_version") == lifecycle.CURRENT_VERSION:
        required_fields.extend(["completion_policy", "workspace_context", "timing"])
    for field in required_fields:
        if field not in state:
            errors.append(_error("MISSING_FIELD", field, f"Required field '{field}' is missing."))

    if state.get("continuation_state_version") not in lifecycle.SUPPORTED_VERSIONS:
        errors.append(
            _error(
                "UNSUPPORTED_VERSION",
                "continuation_state_version",
                f"continuation_state_version must be one of {sorted(lifecycle.SUPPORTED_VERSIONS)}.",
            )
        )

    continuation_id = state.get("continuation_id")
    if not isinstance(continuation_id, str) or not continuation_id.strip():
        errors.append(_error("INVALID_CONTINUATION_ID", "continuation_id", "continuation_id must be non-empty."))

    created_at = _parse_timestamp(state.get("created_at"))
    updated_at = _parse_timestamp(state.get("updated_at"))
    completed_raw = state.get("completed_at")
    completed_at = _parse_timestamp(completed_raw) if completed_raw is not None else None
    if created_at is None:
        errors.append(_error("INVALID_TIMESTAMP", "created_at", "created_at must be an ISO-8601 timestamp."))
    if updated_at is None:
        errors.append(_error("INVALID_TIMESTAMP", "updated_at", "updated_at must be an ISO-8601 timestamp."))
    if completed_raw is not None and completed_at is None:
        errors.append(_error("INVALID_TIMESTAMP", "completed_at", "completed_at must be null or ISO-8601."))
    try:
        if created_at is not None and updated_at is not None and updated_at < created_at:
            errors.append(_error("TIMESTAMP_ORDER", "updated_at", "updated_at cannot precede created_at."))
        if created_at is not None and completed_at is not None and completed_at < created_at:
            errors.append(_error("TIMESTAMP_ORDER", "completed_at", "completed_at cannot precede created_at."))
    except TypeError:
        errors.append(_error("TIMESTAMP_TIMEZONE_MISMATCH", "updated_at", "Timestamps must use compatible timezones."))

    elapsed = state.get("elapsed_seconds")
    if elapsed is not None and (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0):
        errors.append(_error("INVALID_ELAPSED", "elapsed_seconds", "elapsed_seconds must be null or non-negative."))
    if completed_raw is None and elapsed is not None:
        errors.append(_error("INCOMPLETE_ELAPSED", "elapsed_seconds", "elapsed_seconds must be null until completion."))
    if completed_raw is not None and elapsed is None:
        errors.append(_error("MISSING_ELAPSED", "elapsed_seconds", "Completed state requires elapsed_seconds."))

    current_phase = state.get("current_phase")
    if not isinstance(current_phase, str) or not current_phase.strip():
        errors.append(_error("INVALID_PHASE", "current_phase", "current_phase must be a non-empty string."))
    if completed_raw is None and current_phase == "completed":
        errors.append(
            _error("INCOMPLETE_PHASE", "current_phase", "current_phase cannot be completed before completed_at is set.")
        )
    if completed_raw is not None and current_phase != "completed":
        errors.append(
            _error("COMPLETED_PHASE_REQUIRED", "current_phase", "Completed continuation state requires current_phase=completed.")
        )
    if (
        created_at is not None
        and completed_at is not None
        and not isinstance(elapsed, bool)
        and isinstance(elapsed, (int, float))
    ):
        try:
            expected_elapsed = max(0, int((completed_at - created_at).total_seconds()))
        except TypeError:
            expected_elapsed = None
        if expected_elapsed is not None and elapsed != expected_elapsed:
            errors.append(
                _error(
                    "ELAPSED_MISMATCH",
                    "elapsed_seconds",
                    "elapsed_seconds must equal completed_at minus created_at in whole seconds.",
                )
            )

    source_run_ids = state.get("source_run_ids")
    if not isinstance(source_run_ids, list) or not source_run_ids or not all(
        isinstance(item, str) and item.strip() for item in source_run_ids
    ):
        errors.append(_error("INVALID_SOURCE_RUNS", "source_run_ids", "source_run_ids must be non-empty strings."))
        valid_source_run_ids: list[str] = []
    else:
        valid_source_run_ids = source_run_ids
        if len({item.casefold() for item in source_run_ids}) != len(source_run_ids):
            errors.append(_error("DUPLICATE_SOURCE_RUN", "source_run_ids", "source_run_ids must be unique."))
        if isinstance(continuation_id, str) and continuation_id not in source_run_ids:
            errors.append(
                _error(
                    "CONTINUATION_SOURCE_MISSING",
                    "source_run_ids",
                    "continuation_id must appear in source_run_ids.",
                )
            )
        if isinstance(continuation_id, str) and source_run_ids != [continuation_id]:
            errors.append(
                _error(
                    "UNSUPPORTED_SOURCE_RUNS",
                    "source_run_ids",
                    "Continuation state currently supports only the anchor workflow run.",
                )
            )

    active_artifacts = state.get("active_artifacts")
    active_ids: list[str] = []
    if not isinstance(active_artifacts, list):
        errors.append(_error("INVALID_TYPE", "active_artifacts", "active_artifacts must be a list."))
    else:
        for index, item in enumerate(active_artifacts):
            path = f"active_artifacts[{index}]"
            if not isinstance(item, dict):
                errors.append(_error("INVALID_TYPE", path, "Each active artifact must be an object."))
                continue
            for field in ("source_run_id", "artifact_id", "role"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    errors.append(_error("INVALID_ACTIVE_ARTIFACT", f"{path}.{field}", f"{field} must be non-empty."))
            if isinstance(item.get("source_run_id"), str) and item["source_run_id"] != continuation_id:
                errors.append(
                    _error(
                        "INVALID_SOURCE_RUN",
                        f"{path}.source_run_id",
                        "Continuation active artifacts must belong to the anchor workflow run.",
                    )
                )
            if isinstance(item.get("artifact_id"), str):
                active_ids.append(item["artifact_id"].casefold())
        if len(set(active_ids)) != len(active_ids):
            errors.append(_error("DUPLICATE_ACTIVE_ARTIFACT", "active_artifacts", "Artifact ids must be unique."))

    candidate_set = state.get("candidate_set")
    errors.extend(_validate_candidate_set(candidate_set, valid_source_run_ids))
    if isinstance(candidate_set, dict):
        source_artifact = candidate_set.get("source_artifact")
        if isinstance(source_artifact, dict) and isinstance(source_artifact.get("artifact_id"), str):
            if source_artifact["artifact_id"].casefold() not in active_ids:
                errors.append(
                    _error(
                        "CANDIDATE_ARTIFACT_NOT_ACTIVE",
                        "candidate_set.source_artifact.artifact_id",
                        "The candidate sheet must also appear in active_artifacts.",
                    )
                )

    next_actions = state.get("next_actions")
    action_types: list[str] = []
    if not isinstance(next_actions, list):
        errors.append(_error("INVALID_TYPE", "next_actions", "next_actions must be a list."))
    else:
        active_count = 0
        allowed_statuses = (
            lifecycle.ACTION_STATUSES
            if state.get("continuation_state_version") == lifecycle.CURRENT_VERSION
            else {"available", "ready"}
        )
        for index, action in enumerate(next_actions):
            path = f"next_actions[{index}]"
            if not isinstance(action, dict):
                errors.append(_error("INVALID_TYPE", path, "Each next action must be an object."))
                continue
            action_type = action.get("type")
            status = action.get("status")
            if not isinstance(action_type, str) or not action_type.strip():
                errors.append(_error("INVALID_ACTION", f"{path}.type", "Action type must be non-empty."))
            else:
                action_types.append(action_type.casefold())
            if status not in allowed_statuses:
                errors.append(
                    _error(
                        "INVALID_ACTION_STATUS",
                        f"{path}.status",
                        f"Action status must be one of {sorted(allowed_statuses)}.",
                    )
                )
            if status in {"ready", "in_progress"}:
                active_count += 1
        if len(set(action_types)) != len(action_types):
            errors.append(_error("DUPLICATE_ACTION", "next_actions", "Action types must be unique."))
        if active_count > 1:
            errors.append(
                _error("MULTIPLE_ACTIVE_ACTIONS", "next_actions", "At most one action may be ready or in progress.")
            )
        if completed_raw is not None and next_actions:
            errors.append(
                _error(
                    "COMPLETED_ACTIONS_REMAIN",
                    "next_actions",
                    "Completed continuation state must not retain pending next actions.",
                )
            )

    decision_log = state.get("decision_log")
    if not isinstance(decision_log, list) or not all(isinstance(item, dict) for item in decision_log):
        errors.append(_error("INVALID_DECISION_LOG", "decision_log", "decision_log must be a list of objects."))

    errors.extend(_validate_workflow_anchor(run_dir, state))
    errors.extend(_validate_artifact_references(run_dir, state))
    if state.get("continuation_state_version") == lifecycle.CURRENT_VERSION:
        errors.extend(lifecycle.validate_extension(run_dir, state))
    return errors


def _raise_for_errors(errors: list[dict[str, str]]) -> None:
    if not errors:
        return
    details = "; ".join(f"{item['path'] or '<root>'}: {item['message']}" for item in errors)
    raise ValueError(f"Invalid continuation state: {details}")


def _build_candidate_set(
    run_id: str,
    candidate_artifact_id: str,
    candidate_count: int | None,
    index_order: str,
    candidate_labels: list[str] | None,
) -> dict[str, Any]:
    if not isinstance(index_order, str) or not index_order.strip():
        raise ValueError("index_order must be a non-empty string.")
    if candidate_labels is not None:
        if not isinstance(candidate_labels, list):
            raise TypeError("candidate_labels must be a list when provided.")
        labels = _deduplicate_strings(candidate_labels, field_name="candidate_labels")
        if len(labels) != len(candidate_labels):
            raise ValueError("candidate_labels must be unique, including case-insensitive duplicates.")
        lookup_keys = ["".join(label.split()).casefold() for label in labels]
        if len(set(lookup_keys)) != len(lookup_keys):
            raise ValueError("candidate_labels must remain unique after whitespace normalization.")
        if any(key.endswith("후보") for key in lookup_keys):
            raise ValueError("candidate_labels must not end with the reserved selector suffix '후보'.")
        if candidate_count is None:
            candidate_count = len(labels)
    else:
        if candidate_count is None:
            raise ValueError("candidate_count is required when candidate_labels is not provided.")
        labels = [_excel_label(index) for index in range(1, candidate_count + 1)]

    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count < 1:
        raise ValueError("candidate_count must be a positive integer.")
    if len(labels) != candidate_count:
        raise ValueError("candidate_labels length must match candidate_count.")

    candidates = []
    for ordinal, label in enumerate(labels, start=1):
        aliases = _deduplicate_strings(
            [str(ordinal), f"{ordinal}번", label],
            field_name=f"candidate {label} aliases",
        )
        candidates.append(
            {
                "candidate_id": label,
                "ordinal": ordinal,
                "aliases": aliases,
            }
        )
    return {
        "source_artifact": {
            "source_run_id": run_id,
            "artifact_id": candidate_artifact_id,
        },
        "count": candidate_count,
        "index_rule": {
            "order": index_order.strip(),
            "numeric_base": 1,
            "labels": labels,
        },
        "candidates": candidates,
        "selected_candidate": None,
    }


def initialize_continuation(
    run_dir: Path,
    run_id: str,
    current_phase: str,
    active_artifact_ids: list[str],
    candidate_artifact_id: str | None = None,
    candidate_count: int | None = None,
    index_order: str = "left_to_right",
    candidate_labels: list[str] | None = None,
    next_action_types: list[str] | None = None,
    decision_note: str | None = None,
    working_root: str | None = None,
    completion_gate: str = "approved",
    risk_level: str = "medium",
    deployment_target: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state_path = continuation_state_path(run_dir)
    if state_path.exists():
        raise FileExistsError(f"Continuation state already exists: {state_path}")
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"Workflow run directory does not exist: {run_dir}")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string.")
    if not isinstance(current_phase, str) or not current_phase.strip():
        raise ValueError("current_phase must be a non-empty string.")

    normalized_run_id = run_id.strip()
    artifact_ids = _normalize_artifact_ids(active_artifact_ids, candidate_artifact_id)
    candidate_id = candidate_artifact_id.strip() if isinstance(candidate_artifact_id, str) else None
    if candidate_id is None and any(value is not None for value in (candidate_count, candidate_labels)):
        raise ValueError("candidate_artifact_id is required when candidate metadata is provided.")

    action_types = _deduplicate_strings(next_action_types or [], field_name="next_action_types")
    if next_action_types is not None and len(action_types) != len(next_action_types):
        raise ValueError("next_action_types must be unique, including case-insensitive duplicates.")

    timestamp = now_iso()
    candidate_key = candidate_id.casefold() if candidate_id else None
    active_artifacts = [
        {
            "source_run_id": normalized_run_id,
            "artifact_id": artifact_id,
            "role": "candidate_sheet" if candidate_key and artifact_id.casefold() == candidate_key else "active_artifact",
        }
        for artifact_id in artifact_ids
    ]
    candidate_set = (
        _build_candidate_set(
            normalized_run_id,
            candidate_id,
            candidate_count,
            index_order,
            candidate_labels,
        )
        if candidate_id
        else None
    )
    decision_log: list[dict[str, Any]] = []
    if decision_note is not None:
        if not isinstance(decision_note, str) or not decision_note.strip():
            raise ValueError("decision_note must be a non-empty string when provided.")
        decision_log.append(
            {
                "timestamp": timestamp,
                "event": "initialized",
                "note": decision_note.strip(),
            }
        )

    state = {
        "continuation_state_version": CONTINUATION_STATE_VERSION,
        "continuation_id": normalized_run_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": None,
        "elapsed_seconds": None,
        "current_phase": current_phase.strip(),
        "source_run_ids": [normalized_run_id],
        "active_artifacts": active_artifacts,
        "candidate_set": candidate_set,
        "next_actions": [{"type": action_type, "status": "available"} for action_type in action_types],
        "decision_log": decision_log,
    }
    state.update(
        lifecycle.build_extension(
            run_dir,
            phase=current_phase.strip(),
            timestamp=timestamp,
            working_root=working_root,
            completion_gate=completion_gate,
            risk_level=risk_level,
            deployment_target=deployment_target,
        )
    )
    _raise_for_errors(_validate_state(run_dir, state))
    _write_state(state_path, state)
    return state


def load_continuation_state(run_dir: Path) -> dict[str, Any]:
    state_path = continuation_state_path(Path(run_dir))
    if not state_path.exists():
        raise FileNotFoundError(f"Continuation state not found: {state_path}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Continuation state is not valid JSON: {state_path}: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError(f"Continuation state must be a JSON object: {state_path}")
    return state


def inspect_continuation_state(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state_path = continuation_state_path(run_dir)
    if not state_path.exists():
        return {
            "state_file": str(state_path),
            "exists": False,
            "valid": False,
            "errors": [
                _error(
                    "CONTINUATION_STATE_MISSING",
                    "",
                    "continuation_state.json does not exist for this workflow run.",
                )
            ],
            "state": None,
        }
    try:
        state = load_continuation_state(run_dir)
    except ValueError as exc:
        return {
            "state_file": str(state_path),
            "exists": True,
            "valid": False,
            "errors": [_error("CONTINUATION_STATE_INVALID_JSON", "", str(exc))],
            "state": None,
        }

    errors = _validate_state(run_dir, state)
    return {
        "state_file": str(state_path),
        "exists": True,
        "valid": not errors,
        "errors": errors,
        "state": state,
    }


def resolve_candidate(candidate_set: dict[str, Any], selector: str | int) -> dict[str, Any]:
    if not isinstance(candidate_set, dict):
        raise ValueError("candidate_set is required to resolve a candidate.")
    if isinstance(selector, bool) or not isinstance(selector, (str, int)):
        raise TypeError("selector must be a string or integer.")
    raw_selector = str(selector).strip()
    if not raw_selector:
        raise ValueError("selector must not be empty.")

    normalized = "".join(raw_selector.split()).casefold()
    if normalized.endswith("후보"):
        normalized = normalized[: -len("후보")].strip()
    matches: list[dict[str, Any]] = []
    candidates = candidate_set.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("candidate_set.candidates must be a list.")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        aliases = candidate.get("aliases")
        if not isinstance(aliases, list):
            continue
        normalized_aliases = {"".join(alias.split()).casefold() for alias in aliases if isinstance(alias, str)}
        candidate_id = candidate.get("candidate_id")
        if isinstance(candidate_id, str):
            normalized_aliases.add(candidate_id.casefold())
        if normalized in normalized_aliases:
            matches.append(candidate)
    if not matches:
        raise ValueError(f"Unknown candidate selector: {raw_selector}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous candidate selector: {raw_selector}")
    return dict(matches[0])


def _valid_state_for_update(run_dir: Path) -> dict[str, Any]:
    inspection = inspect_continuation_state(run_dir)
    if not inspection["exists"]:
        raise FileNotFoundError(f"Continuation state not found: {inspection['state_file']}")
    _raise_for_errors(inspection["errors"])
    state = inspection["state"]
    if state.get("continuation_state_version") == lifecycle.LEGACY_VERSION:
        return migrate_continuation(run_dir)
    return state


def migrate_continuation(
    run_dir: Path,
    *,
    working_root: str | None = None,
    completion_gate: str | None = None,
    risk_level: str = "medium",
    deployment_target: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    inspection = inspect_continuation_state(run_dir)
    if not inspection["exists"]:
        raise FileNotFoundError(f"Continuation state not found: {inspection['state_file']}")
    _raise_for_errors(inspection["errors"])
    state = inspection["state"]
    if state.get("continuation_state_version") == lifecycle.CURRENT_VERSION:
        return state
    timestamp = now_iso()
    upgraded = lifecycle.upgrade_legacy_state(
        state,
        run_dir,
        timestamp=timestamp,
        working_root=working_root,
        completion_gate=completion_gate,
        risk_level=risk_level,
        deployment_target=deployment_target,
    )
    upgraded.setdefault("decision_log", []).append(
        {
            "timestamp": timestamp,
            "event": "state_migrated",
            "from_version": lifecycle.LEGACY_VERSION,
            "to_version": lifecycle.CURRENT_VERSION,
        }
    )
    upgraded["updated_at"] = timestamp
    _raise_for_errors(_validate_state(run_dir, upgraded))
    _write_state(continuation_state_path(run_dir), upgraded)
    return upgraded


def set_workspace_context(
    run_dir: Path,
    *,
    working_root: str,
    note: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    inspection = inspect_continuation_state(run_dir)
    if not inspection["exists"]:
        raise FileNotFoundError(f"Continuation state not found: {inspection['state_file']}")
    nonrepairable_errors = [
        item
        for item in inspection.get("errors", [])
        if item.get("code") not in REPAIRABLE_WORKSPACE_ERROR_CODES
    ]
    _raise_for_errors(nonrepairable_errors)
    state = inspection["state"]
    if state.get("continuation_state_version") == lifecycle.LEGACY_VERSION:
        return migrate_continuation(run_dir, working_root=working_root)
    normalized_root = lifecycle.normalize_working_root(working_root)
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ValueError("note must be a non-empty string when provided.")
    workspace = state.get("workspace_context")
    if not isinstance(workspace, dict):
        raise ValueError("workspace_context is required before the working root can be updated.")
    previous_root = workspace.get("working_root")
    workspace["working_root"] = normalized_root
    timestamp = now_iso()
    state["updated_at"] = timestamp
    event = {
        "timestamp": timestamp,
        "event": "workspace_context_updated",
        "previous_working_root": previous_root,
        "working_root": normalized_root,
    }
    if note is not None:
        event["note"] = note.strip()
    state.setdefault("decision_log", []).append(event)
    _raise_for_errors(_validate_state(run_dir, state))
    _write_state(continuation_state_path(run_dir), state)
    return state


def select_candidate(
    run_dir: Path,
    selector: str | int,
    action_type: str,
    note: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state = _valid_state_for_update(run_dir)
    if state.get("completed_at") is not None:
        raise ValueError("Cannot select a candidate after continuation completion.")
    if not isinstance(action_type, str) or not action_type.strip():
        raise ValueError("action_type must be a non-empty string.")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ValueError("note must be a non-empty string when provided.")

    candidate_set = state.get("candidate_set")
    candidate = resolve_candidate(candidate_set, selector)
    requested_action = action_type.strip()
    matching_actions = [
        action
        for action in state.get("next_actions", [])
        if isinstance(action, dict)
        and isinstance(action.get("type"), str)
        and action["type"].casefold() == requested_action.casefold()
    ]
    if not matching_actions:
        raise ValueError(f"Action '{requested_action}' is not available in next_actions.")
    canonical_action_type = matching_actions[0]["type"]

    for action in state["next_actions"]:
        if not isinstance(action, dict):
            continue
        action["status"] = "ready" if action is matching_actions[0] else "available"
    state["candidate_set"]["selected_candidate"] = candidate
    timestamp = now_iso()
    lifecycle.transition_phase(state, "asset_generation", timestamp=timestamp)
    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "event": "candidate_selected",
        "raw_reference": str(selector),
        "candidate_id": candidate["candidate_id"],
        "ordinal": candidate["ordinal"],
        "action_type": canonical_action_type,
    }
    if note is not None:
        entry["note"] = note.strip()
    state["decision_log"].append(entry)

    _raise_for_errors(_validate_state(run_dir, state))
    _write_state(continuation_state_path(run_dir), state)
    return state


def _registered_artifact_ids(run_dir: Path) -> dict[str, dict[str, Any]]:
    status = artifact_store.inspect_artifacts(run_dir)
    return {
        str(item.get("id") or "").casefold(): item
        for item in status.get("artifacts", [])
        if isinstance(item, dict) and item.get("id")
    }


def record_result(
    run_dir: Path,
    *,
    artifact_ids: list[str],
    action_type: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state = _valid_state_for_update(run_dir)
    if state.get("completed_at") is not None:
        raise ValueError("Cannot record a result after continuation completion.")
    ids = _deduplicate_strings(artifact_ids, field_name="artifact_ids")
    if not ids:
        raise ValueError("At least one registered artifact id is required.")
    registered = _registered_artifact_ids(run_dir)
    for artifact_id in ids:
        artifact = registered.get(artifact_id.casefold())
        if artifact is None:
            raise ValueError(f"Artifact '{artifact_id}' is not registered.")
        if not artifact.get("exists"):
            raise ValueError(f"Artifact '{artifact_id}' does not resolve to an existing file.")

    actions = state.get("next_actions", [])
    selected_action = action_type
    if selected_action is None:
        selected_action = next(
            (
                str(item.get("type"))
                for item in actions
                if isinstance(item, dict) and item.get("status") in {"ready", "in_progress"}
            ),
            None,
        )
    if not selected_action:
        raise ValueError("action_type is required when no continuation action is ready.")
    matched = False
    for action in actions:
        if not isinstance(action, dict):
            continue
        if str(action.get("type") or "").casefold() == selected_action.casefold():
            action["status"] = "completed"
            selected_action = str(action.get("type"))
            matched = True
        elif action.get("status") in {"ready", "in_progress"}:
            action["status"] = "available"
    if not matched:
        raise ValueError(f"Action '{selected_action}' is not available in next_actions.")

    existing = {
        str(item.get("artifact_id") or "").casefold()
        for item in state.get("active_artifacts", [])
        if isinstance(item, dict)
    }
    for artifact_id in ids:
        if artifact_id.casefold() not in existing:
            state["active_artifacts"].append(
                {
                    "source_run_id": state["continuation_id"],
                    "artifact_id": artifact_id,
                    "role": "result_artifact",
                }
            )
    timestamp = now_iso()
    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "event": "result_recorded",
        "action_type": selected_action,
        "artifact_ids": ids,
    }
    if note is not None:
        if not isinstance(note, str) or not note.strip():
            raise ValueError("note must be a non-empty string when provided.")
        entry["note"] = note.strip()
    state["decision_log"].append(entry)
    lifecycle.transition_phase(state, "awaiting_user_review", timestamp=timestamp)
    if (state.get("completion_policy") or {}).get("gate") == "artifact_ready":
        return _finalize_continuation(run_dir, state, timestamp=timestamp)
    _raise_for_errors(_validate_state(run_dir, state))
    _write_state(continuation_state_path(run_dir), state)
    return state


def approve_result(run_dir: Path, *, note: str | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state = _valid_state_for_update(run_dir)
    if state.get("completed_at") is not None:
        return state
    events = {
        str(item.get("event") or "")
        for item in state.get("decision_log", [])
        if isinstance(item, dict)
    }
    if "result_recorded" not in events:
        raise ValueError("A registered result is required before approval.")
    timestamp = now_iso()
    entry: dict[str, Any] = {"timestamp": timestamp, "event": "result_approved"}
    if note is not None:
        if not isinstance(note, str) or not note.strip():
            raise ValueError("note must be a non-empty string when provided.")
        entry["note"] = note.strip()
    state["decision_log"].append(entry)
    gate = (state.get("completion_policy") or {}).get("gate")
    if gate == "deployed":
        state["workspace_context"]["deployment_status"] = "pending"
        lifecycle.transition_phase(state, "deployment_ready", timestamp=timestamp)
        _raise_for_errors(_validate_state(run_dir, state))
        _write_state(continuation_state_path(run_dir), state)
        return state
    lifecycle.transition_phase(state, "approved", timestamp=timestamp)
    return _finalize_continuation(run_dir, state, timestamp=timestamp)


def start_deployment(
    run_dir: Path,
    *,
    target: str | None = None,
    confirmed: bool = False,
    note: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state = _valid_state_for_update(run_dir)
    policy = state.get("completion_policy") or {}
    if policy.get("gate") != "deployed":
        raise ValueError("Deployment is not part of this continuation completion scope.")
    if state.get("current_phase") != "deployment_ready":
        raise ValueError("Continuation must be deployment_ready before deployment starts.")
    if policy.get("risk_level") == "high" and not confirmed:
        raise ValueError("High-risk deployment requires explicit confirmation.")
    workspace = state["workspace_context"]
    deployment_target = target or workspace.get("deployment_target")
    if not isinstance(deployment_target, str) or not deployment_target.strip():
        raise ValueError("A deployment target is required.")
    workspace["deployment_target"] = deployment_target.strip()
    workspace["deployment_status"] = "in_progress"
    timestamp = now_iso()
    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "event": "deployment_started",
        "target": deployment_target.strip(),
        "confirmed": bool(confirmed),
    }
    if note is not None:
        if not isinstance(note, str) or not note.strip():
            raise ValueError("note must be a non-empty string when provided.")
        entry["note"] = note.strip()
    state["decision_log"].append(entry)
    lifecycle.transition_phase(state, "deployment", timestamp=timestamp)
    _raise_for_errors(_validate_state(run_dir, state))
    _write_state(continuation_state_path(run_dir), state)
    return state


def record_deployment(run_dir: Path, *, note: str | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state = _valid_state_for_update(run_dir)
    if state.get("current_phase") != "deployment":
        raise ValueError("Deployment must be started before it can be recorded as complete.")
    timestamp = now_iso()
    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "event": "deployed",
        "target": state["workspace_context"].get("deployment_target"),
    }
    if note is not None:
        if not isinstance(note, str) or not note.strip():
            raise ValueError("note must be a non-empty string when provided.")
        entry["note"] = note.strip()
    state["decision_log"].append(entry)
    state["workspace_context"]["deployment_status"] = "deployed"
    result_artifact_ids = [
        str(item.get("artifact_id"))
        for item in state.get("active_artifacts", [])
        if isinstance(item, dict) and item.get("role") == "result_artifact" and item.get("artifact_id")
    ]
    if result_artifact_ids:
        artifact_store.record_artifact_deployment(
            run_dir,
            artifact_ids=result_artifact_ids,
            target=str(state["workspace_context"].get("deployment_target") or ""),
            status="deployed",
        )
    lifecycle.transition_phase(state, "deployed", timestamp=timestamp)
    return _finalize_continuation(run_dir, state, timestamp=timestamp)


def _finalize_continuation(
    run_dir: Path,
    state: dict[str, Any],
    *,
    timestamp: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    delivery = delivery_status(run_dir, state)
    if delivery.get("valid") is not True:
        artifact_ids = ", ".join(delivery.get("undelivered_artifact_ids", []))
        raise ValueError(
            "CONTINUATION_DELIVERABLE_REQUIRED: active output artifacts are not linked to "
            f"ProjectRoot deliverables: {artifact_ids}. Re-register each user-facing file with "
            "role final_output (or final=True), then retry completion."
        )
    if not lifecycle.gate_achieved(state):
        gate = (state.get("completion_policy") or {}).get("gate")
        raise ValueError(f"Continuation completion gate '{gate}' has not been achieved.")
    created_at = _parse_timestamp(state.get("created_at"))
    if created_at is None:
        raise ValueError("created_at must be a valid ISO-8601 timestamp before completion.")
    completed_at = _parse_timestamp(timestamp) if timestamp else None
    if completed_at is None:
        completed_at = datetime.now(created_at.tzinfo).replace(microsecond=0)
    completed_at_text = completed_at.isoformat(timespec="seconds")
    elapsed_seconds = max(0, int((completed_at - created_at).total_seconds()))
    lifecycle.transition_phase(state, "completed", timestamp=completed_at_text)
    state["completed_at"] = completed_at_text
    state["elapsed_seconds"] = elapsed_seconds
    state["timing"]["total_elapsed_seconds"] = elapsed_seconds
    state["next_actions"] = []
    entry: dict[str, Any] = {
        "timestamp": completed_at_text,
        "event": "completed",
        "elapsed_seconds": elapsed_seconds,
        "timing": lifecycle.timing_snapshot(state, at=completed_at_text),
    }
    if note is not None:
        entry["note"] = note.strip()
    state["decision_log"].append(entry)
    _raise_for_errors(_validate_state(run_dir, state))
    _write_state(continuation_state_path(run_dir), state)
    return state


def complete_continuation(run_dir: Path, note: str | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    state = _valid_state_for_update(run_dir)
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ValueError("note must be a non-empty string when provided.")
    if state.get("completed_at") is not None:
        if note is not None:
            raise ValueError("Continuation is already complete; the new note was not recorded.")
        return state

    return _finalize_continuation(run_dir, state, note=note)
