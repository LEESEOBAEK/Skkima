from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


LEGACY_VERSION = "0.1.0"
CURRENT_VERSION = "0.2.0"
SUPPORTED_VERSIONS = {LEGACY_VERSION, CURRENT_VERSION}

COMPLETION_GATES = {"artifact_ready", "approved", "deployed"}
RISK_LEVELS = {"low", "medium", "high"}
APPROVAL_POLICIES = {"risk_based"}
WORKSPACE_MODES = {"project_first"}
SNAPSHOT_POLICIES = {"milestone_only"}
ACTION_STATUSES = {"available", "ready", "in_progress", "completed", "blocked", "not_requested"}

PHASE_CATEGORIES = {
    "candidate_review": "user_review",
    "asset_generation": "agent_work",
    "awaiting_user_review": "user_review",
    "approved": "user_review",
    "deployment_ready": "user_review",
    "deployment": "deployment",
    "deployed": "deployment",
    "completed": "none",
}

TIMING_TOTAL_FIELDS = {
    "agent_work": "agent_work_seconds",
    "user_review": "user_review_seconds",
    "deployment": "deployment_seconds",
}


def now_iso(*, tzinfo: Any = None) -> str:
    return datetime.now(tzinfo).isoformat(timespec="seconds")


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def phase_category(phase: str) -> str:
    return PHASE_CATEGORIES.get(phase, "agent_work")


def normalize_working_root(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("working_root must be a non-empty string when provided.")
    try:
        path = Path(value.strip()).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"working_root could not be resolved: {value}") from exc
    if not path.exists():
        raise FileNotFoundError(f"working_root does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"working_root must be a directory: {path}")
    return str(path)


def _duration_seconds(start: Any, end: Any) -> int:
    start_at = parse_timestamp(start)
    end_at = parse_timestamp(end)
    if start_at is None or end_at is None:
        return 0
    try:
        return max(0, int((end_at - start_at).total_seconds()))
    except TypeError:
        return 0


def build_extension(
    run_dir: Path,
    *,
    phase: str,
    timestamp: str,
    working_root: str | None = None,
    completion_gate: str = "approved",
    risk_level: str = "medium",
    deployment_target: str | None = None,
) -> dict[str, Any]:
    gate = completion_gate.strip() if isinstance(completion_gate, str) else ""
    risk = risk_level.strip() if isinstance(risk_level, str) else ""
    if gate not in COMPLETION_GATES:
        raise ValueError(f"completion_gate must be one of {sorted(COMPLETION_GATES)}.")
    if risk not in RISK_LEVELS:
        raise ValueError(f"risk_level must be one of {sorted(RISK_LEVELS)}.")
    normalized_working_root = normalize_working_root(working_root) if working_root is not None else None
    if deployment_target is not None and (
        not isinstance(deployment_target, str) or not deployment_target.strip()
    ):
        raise ValueError("deployment_target must be a non-empty string when provided.")
    category = phase_category(phase)
    return {
        "completion_policy": {
            "gate": gate,
            "approval_policy": "risk_based",
            "risk_level": risk,
            "deployment_requested": gate == "deployed",
        },
        "workspace_context": {
            "mode": "project_first",
            "working_root": normalized_working_root,
            "official_run_dir": str(Path(run_dir)),
            "deployment_target": (
                deployment_target.strip() if isinstance(deployment_target, str) else None
            ),
            "deployment_status": "pending" if gate == "deployed" else "not_requested",
            "snapshot_policy": "milestone_only",
        },
        "timing": {
            "current_category": category,
            "phase_started_at": timestamp,
            "segments": [],
            "agent_work_seconds": 0,
            "user_review_seconds": 0,
            "deployment_seconds": 0,
            "total_elapsed_seconds": None,
        },
    }


def transition_phase(state: dict[str, Any], new_phase: str, *, timestamp: str | None = None) -> None:
    if new_phase not in PHASE_CATEGORIES:
        raise ValueError(f"Unsupported continuation phase: {new_phase}")
    timing = state.get("timing")
    if not isinstance(timing, dict):
        raise ValueError("Continuation timing data is required before phase transitions.")
    transition_at = timestamp or now_iso()
    old_phase = str(state.get("current_phase") or "")
    old_category = str(timing.get("current_category") or phase_category(old_phase))
    started_at = timing.get("phase_started_at")
    elapsed = _duration_seconds(started_at, transition_at)
    if old_category in TIMING_TOTAL_FIELDS:
        total_field = TIMING_TOTAL_FIELDS[old_category]
        timing[total_field] = int(timing.get(total_field) or 0) + elapsed
    segments = timing.get("segments")
    if not isinstance(segments, list):
        segments = []
        timing["segments"] = segments
    if old_phase and started_at:
        segments.append(
            {
                "phase": old_phase,
                "category": old_category,
                "started_at": started_at,
                "ended_at": transition_at,
                "elapsed_seconds": elapsed,
            }
        )
    state["current_phase"] = new_phase
    state["updated_at"] = transition_at
    timing["current_category"] = phase_category(new_phase)
    timing["phase_started_at"] = None if new_phase == "completed" else transition_at


def timing_snapshot(state: dict[str, Any], *, at: str | None = None) -> dict[str, Any]:
    timing = state.get("timing")
    if not isinstance(timing, dict):
        return {
            "agent_work_seconds": None,
            "user_review_seconds": None,
            "deployment_seconds": None,
            "total_elapsed_seconds": state.get("elapsed_seconds"),
            "measurement": "legacy_wall_clock_only",
        }
    result = deepcopy(timing)
    current_category = result.get("current_category")
    phase_started_at = result.get("phase_started_at")
    if phase_started_at and current_category in TIMING_TOTAL_FIELDS:
        snapshot_at = at or now_iso(tzinfo=parse_timestamp(phase_started_at).tzinfo if parse_timestamp(phase_started_at) else None)
        field = TIMING_TOTAL_FIELDS[current_category]
        result[field] = int(result.get(field) or 0) + _duration_seconds(phase_started_at, snapshot_at)
    result["measurement"] = "phase_segmented"
    return result


def _event_phase(event: str, gate: str) -> str | None:
    if event == "candidate_selected":
        return "asset_generation"
    if event == "result_recorded" or event.endswith("_built"):
        return "awaiting_user_review"
    if event in {"result_approved", "approved"}:
        return "deployment_ready" if gate == "deployed" else "approved"
    if event == "deployment_started":
        return "deployment"
    if event == "deployed":
        return "deployed"
    if event == "completed":
        return "completed"
    return None


def upgrade_legacy_state(
    state: dict[str, Any],
    run_dir: Path,
    *,
    timestamp: str | None = None,
    working_root: str | None = None,
    completion_gate: str | None = None,
    risk_level: str = "medium",
    deployment_target: str | None = None,
) -> dict[str, Any]:
    if state.get("continuation_state_version") == CURRENT_VERSION:
        return deepcopy(state)
    if state.get("continuation_state_version") != LEGACY_VERSION:
        raise ValueError("Only continuation state v0.1 can be upgraded to v0.2.")
    upgraded = deepcopy(state)
    completed = upgraded.get("completed_at") is not None
    gate = completion_gate or ("artifact_ready" if completed else "approved")
    created_at = str(upgraded.get("created_at") or timestamp or now_iso())
    upgraded["continuation_state_version"] = CURRENT_VERSION
    upgraded["current_phase"] = "candidate_review"
    upgraded.update(
        build_extension(
            run_dir,
            phase="candidate_review",
            timestamp=created_at,
            working_root=working_root,
            completion_gate=gate,
            risk_level=risk_level,
            deployment_target=deployment_target,
        )
    )

    actions = upgraded.get("next_actions")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict) and action.get("status") not in ACTION_STATUSES:
                action["status"] = "available"

    decision_log = upgraded.get("decision_log")
    if not isinstance(decision_log, list):
        decision_log = []
        upgraded["decision_log"] = decision_log
    for entry in decision_log:
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("event") or "")
        new_phase = _event_phase(event, gate)
        event_at = entry.get("timestamp")
        if new_phase and parse_timestamp(event_at):
            if new_phase == "awaiting_user_review" and isinstance(actions, list):
                action_type = entry.get("action_type")
                for action in actions:
                    if isinstance(action, dict) and action.get("type") == action_type:
                        action["status"] = "completed"
            transition_phase(upgraded, new_phase, timestamp=event_at)

    if completed and upgraded.get("current_phase") != "completed":
        transition_phase(upgraded, "completed", timestamp=str(upgraded.get("completed_at") or timestamp or now_iso()))
    if completed:
        upgraded["next_actions"] = []
        upgraded["timing"]["total_elapsed_seconds"] = upgraded.get("elapsed_seconds")
    upgraded["updated_at"] = str(upgraded.get("updated_at") or timestamp or now_iso())
    return upgraded


def validate_extension(run_dir: Path, state: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    def add(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "path": path, "message": message})

    policy = state.get("completion_policy")
    if not isinstance(policy, dict):
        add("INVALID_COMPLETION_POLICY", "completion_policy", "completion_policy must be an object.")
    else:
        if policy.get("gate") not in COMPLETION_GATES:
            add("INVALID_COMPLETION_GATE", "completion_policy.gate", "Unknown completion gate.")
        if policy.get("approval_policy") not in APPROVAL_POLICIES:
            add("INVALID_APPROVAL_POLICY", "completion_policy.approval_policy", "Unknown approval policy.")
        if policy.get("risk_level") not in RISK_LEVELS:
            add("INVALID_RISK_LEVEL", "completion_policy.risk_level", "Unknown risk level.")
        if not isinstance(policy.get("deployment_requested"), bool):
            add("INVALID_DEPLOYMENT_FLAG", "completion_policy.deployment_requested", "deployment_requested must be boolean.")

    workspace = state.get("workspace_context")
    if not isinstance(workspace, dict):
        add("INVALID_WORKSPACE_CONTEXT", "workspace_context", "workspace_context must be an object.")
    else:
        if workspace.get("mode") not in WORKSPACE_MODES:
            add("INVALID_WORKSPACE_MODE", "workspace_context.mode", "Unknown workspace mode.")
        if workspace.get("snapshot_policy") not in SNAPSHOT_POLICIES:
            add("INVALID_SNAPSHOT_POLICY", "workspace_context.snapshot_policy", "Unknown snapshot policy.")
        working_root = workspace.get("working_root")
        if working_root is not None:
            try:
                normalize_working_root(working_root)
            except FileNotFoundError as exc:
                add("WORKING_ROOT_NOT_FOUND", "workspace_context.working_root", str(exc))
            except NotADirectoryError as exc:
                add("WORKING_ROOT_NOT_DIRECTORY", "workspace_context.working_root", str(exc))
            except ValueError as exc:
                add("INVALID_WORKING_ROOT", "workspace_context.working_root", str(exc))
        official = workspace.get("official_run_dir")
        if not isinstance(official, str) or not official:
            add("INVALID_OFFICIAL_RUN", "workspace_context.official_run_dir", "official_run_dir must be non-empty.")
        else:
            try:
                if Path(official).resolve() != Path(run_dir).resolve():
                    add("OFFICIAL_RUN_MISMATCH", "workspace_context.official_run_dir", "official_run_dir must match the anchor run.")
            except (OSError, RuntimeError):
                add("INVALID_OFFICIAL_RUN", "workspace_context.official_run_dir", "official_run_dir could not be resolved.")
        if workspace.get("deployment_status") not in {"not_requested", "pending", "in_progress", "deployed", "failed"}:
            add("INVALID_DEPLOYMENT_STATUS", "workspace_context.deployment_status", "Unknown deployment status.")

    timing = state.get("timing")
    if not isinstance(timing, dict):
        add("INVALID_TIMING", "timing", "timing must be an object.")
    else:
        if timing.get("current_category") not in {"agent_work", "user_review", "deployment", "none"}:
            add("INVALID_TIMING_CATEGORY", "timing.current_category", "Unknown timing category.")
        for field in (*TIMING_TOTAL_FIELDS.values(),):
            value = timing.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                add("INVALID_TIMING_TOTAL", f"timing.{field}", f"{field} must be non-negative.")
        total = timing.get("total_elapsed_seconds")
        if total is not None and (isinstance(total, bool) or not isinstance(total, (int, float)) or total < 0):
            add("INVALID_TIMING_TOTAL", "timing.total_elapsed_seconds", "total_elapsed_seconds must be null or non-negative.")
        segments = timing.get("segments")
        if not isinstance(segments, list) or not all(isinstance(item, dict) for item in segments):
            add("INVALID_TIMING_SEGMENTS", "timing.segments", "segments must be a list of objects.")

    phase = state.get("current_phase")
    if phase not in PHASE_CATEGORIES:
        add("INVALID_LIFECYCLE_PHASE", "current_phase", "Unknown v0.2 lifecycle phase.")
    return errors


def gate_achieved(state: dict[str, Any]) -> bool:
    policy = state.get("completion_policy") or {}
    gate = policy.get("gate")
    events = {
        str(item.get("event") or "")
        for item in state.get("decision_log", [])
        if isinstance(item, dict)
    }
    if gate == "artifact_ready":
        return "result_recorded" in events
    if gate == "approved":
        return "result_approved" in events
    if gate == "deployed":
        return "deployed" in events
    return False


def workflow_projection(state: dict[str, Any], state_file: str) -> dict[str, Any]:
    phase = str(state.get("current_phase") or "")
    gate = str((state.get("completion_policy") or {}).get("gate") or "approved")
    ready_action = next(
        (
            item.get("type")
            for item in state.get("next_actions", [])
            if isinstance(item, dict) and item.get("status") in {"ready", "in_progress"}
        ),
        None,
    )
    mapping = {
        "candidate_review": ("continuation_waiting_user", "select_candidate", "Select a candidate before continuing."),
        "asset_generation": ("continuation_in_progress", "execute_continuation_action", "Produce the selected continuation result."),
        "awaiting_user_review": ("continuation_waiting_user", "review_continuation_result", "Review and approve or revise the generated result."),
        "approved": ("continuation_approved", "complete_continuation", "The approved result satisfies the requested scope."),
        "deployment_ready": ("continuation_deployment_ready", "start_deployment", "The approved result is ready for risk-governed deployment."),
        "deployment": ("continuation_in_progress", "record_deployment", "Finish deployment and record the target."),
        "deployed": ("continuation_deployed", "complete_continuation", "Deployment satisfies the requested scope."),
        "completed": ("continuation_completed", "none", "The continuation request is complete."),
    }
    workflow_state, action_type, reason = mapping.get(
        phase,
        ("continuation_in_progress", "inspect_continuation", "Inspect the continuation state before proceeding."),
    )
    return {
        "workflow_state": workflow_state,
        "current_stage": "continuation",
        "next_action": {
            "type": action_type,
            "stage": "continuation",
            "reason": reason,
            "source_file": state_file,
            "continuation_phase": phase,
            "completion_gate": gate,
            "selected_action": ready_action,
        },
        "timing": timing_snapshot(state),
    }
