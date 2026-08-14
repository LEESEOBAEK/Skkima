from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import time
import unicodedata
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from shared.run_identity import build_run_id, shorten_run_name_slug


WORKSPACE_CONFIG_NAME = ".schema-workflow.json"
WORKSPACE_SCHEMA_VERSION = "1.0"
WORKSPACE_GOVERNANCE_VERSION = "0.2.1"
REGISTRY_VERSION = "1.0"
OPERATION_RECORD_VERSION = "1.2"
INPUT_NORMALIZATION_VERSION = "text-nfc-lines-v1"
INPUT_HASH_ALGORITHM = "sha256"
RUNS_ROOT_RELATIVE = Path("outputs") / "workflows"
CONTROL_DIR_NAME = ".control"
REGISTRY_NAME = "workspace_registry.json"
INSPECT_JSON_NAME = "workspace_inspect.json"
INSPECT_REPORT_NAME = "workspace_registry_report.md"
AUDIT_NAME = "audit.jsonl"
ATOMIC_REPLACE_TIMEOUT_SECONDS = 2.0
ATOMIC_REPLACE_MAX_ATTEMPTS = 20
ATOMIC_REPLACE_INITIAL_BACKOFF_SECONDS = 0.01
ATOMIC_REPLACE_MAX_BACKOFF_SECONDS = 0.2
ATOMIC_TEMP_CLEANUP_TIMEOUT_SECONDS = 1.0
ATOMIC_TEMP_CLEANUP_MAX_ATTEMPTS = 12

RELATION_TYPES = {
    "independent",
    "continuation",
    "retry",
    "comparison",
    "branch",
    "accidental_duplicate",
}
CANONICAL_STATUSES = {"candidate", "selected", "superseded"}
OPERATION_STATUSES = {
    "reserved",
    "run_created",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "aborted",
    "init_failed",
}
OPERATION_KINDS = {"new_run", "continuation"}
ENGINE_PYTHON_ROOTS = ("workflow", "shared", "layers")
TERMINAL_CONTINUATION_STATUSES = {"completed", "failed", "aborted", "init_failed"}


class WorkspaceGovernanceError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": str(self),
            "details": self.details,
        }


class WorkspaceLockError(WorkspaceGovernanceError):
    pass


@dataclass(frozen=True)
class Workspace:
    project_root: Path
    config_path: Path
    runs_root: Path
    control_root: Path
    config: dict[str, Any]

    @property
    def workspace_id(self) -> str:
        return str(self.config["workspace_id"])

    @property
    def locks_root(self) -> Path:
        return self.control_root / "locks"

    @property
    def operations_root(self) -> Path:
        return self.control_root / "operations"

    @property
    def registry_path(self) -> Path:
        return self.control_root / REGISTRY_NAME

    @property
    def audit_path(self) -> Path:
        return self.control_root / AUDIT_NAME


@dataclass(frozen=True)
class ResolvedGovernedWorkspace:
    """A read-only resolution of a Run's current ProjectRoot."""

    project_root: Path
    run_dir: Path
    resolution_source: str
    workspace_id: str
    warnings: tuple[str, ...] = ()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def is_within(parent: Path, child: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def relative_to_project(project_root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(project_root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise WorkspaceGovernanceError(
            "PATH_OUTSIDE_PROJECT",
            f"Path must stay inside ProjectRoot: {path}",
            project_root=str(project_root),
            path=str(path),
        ) from exc


ERROR_SURFACE_VERSION = "0.1.0"


def classify_error_surface(
    *,
    stage: str,
    exception: BaseException | None = None,
    validation_result: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Classify a failure without replacing the original error or validation evidence."""
    normalized_stage = str(stage or "unknown").strip() or "unknown"
    validation_failed = bool(
        isinstance(validation_result, Mapping) and validation_result.get("valid") is False
    )
    exception_type = type(exception).__name__ if exception is not None else ""
    exception_message = str(exception or "")
    encoding_failure = (
        isinstance(exception, UnicodeEncodeError)
        or "UnicodeEncodeError" in exception_type
        or (normalized_stage == "cli_output" and "encoding" in exception_message.casefold())
    )
    if encoding_failure:
        return {
            "version": ERROR_SURFACE_VERSION,
            "category": "presentation_failure",
            "stage": "cli_output",
            "code": "CLI_OUTPUT_ENCODING_FAILED",
            "data_validation_status": "not_implied",
            "retryable": True,
            "recovery": ["Retry after configuring CLI stdout/stderr as UTF-8."],
        }
    if validation_failed:
        code_by_stage = {
            "route_validation": "ROUTE_VALIDATION_FAILED",
            "direction_validation": "DIRECTION_VALIDATION_FAILED",
            "context_validation": "CONTEXT_VALIDATION_FAILED",
            "fulfillment_validation": "FULFILLMENT_VALIDATION_FAILED",
        }
        return {
            "version": ERROR_SURFACE_VERSION,
            "category": "data_validation_failure",
            "stage": normalized_stage,
            "code": code_by_stage.get(normalized_stage, "WORKFLOW_VALIDATION_FAILED"),
            "data_validation_status": "failed",
            "retryable": True,
            "recovery": ["Inspect the validation report and correct the reported input or contract."],
        }
    if isinstance(exception, WorkspaceGovernanceError):
        actions = exception.details.get("recommended_actions")
        recovery = (
            [str(action) for action in actions if str(action).strip()]
            if isinstance(actions, list)
            else ["Inspect the governance details before retrying."]
        )
        return {
            "version": ERROR_SURFACE_VERSION,
            "category": "authority_failure",
            "stage": normalized_stage,
            "code": exception.code,
            "data_validation_status": "unknown",
            "retryable": False,
            "recovery": recovery,
        }
    if exception is not None:
        return {
            "version": ERROR_SURFACE_VERSION,
            "category": "execution_failure",
            "stage": normalized_stage,
            "code": "EXECUTION_FAILURE",
            "data_validation_status": "unknown",
            "retryable": False,
            "recovery": ["Inspect the recorded exception before retrying."],
        }
    return {
        "version": ERROR_SURFACE_VERSION,
        "category": "unknown_failure",
        "stage": normalized_stage,
        "code": "UNKNOWN_FAILURE",
        "data_validation_status": "unknown",
        "retryable": False,
        "recovery": ["Inspect the raw operation and validation records."],
    }


def _canonical_project_root_from_run(run_dir: Path) -> Path:
    """Derive the current ProjectRoot from CanonicalRunsRoot without using manifest paths."""
    resolved_run = run_dir.resolve(strict=False)
    runs_root = resolved_run.parent
    project_root = runs_root.parent.parent
    if runs_root.name.casefold() != "workflows" or runs_root.parent.name.casefold() != "outputs":
        raise WorkspaceGovernanceError(
            "RUN_DIR_NOT_CANONICAL",
            "The selected Run is not under ProjectRoot/outputs/workflows.",
            run_dir=str(resolved_run),
            expected_parent="outputs/workflows",
        )
    if resolved_run.parent != runs_root or resolved_run.name in {"", ".", ".."}:
        raise WorkspaceGovernanceError(
            "RUN_DIR_NOT_CANONICAL",
            "The selected Run must be a direct child of CanonicalRunsRoot.",
            run_dir=str(resolved_run),
            canonical_runs_root=str(runs_root),
        )
    return project_root


def _normalized_relative_run_path(raw_value: object) -> str:
    raw = str(raw_value or "").strip()
    relative = Path(raw)
    if not raw or relative.is_absolute() or any(part in {"", ".."} for part in relative.parts):
        raise WorkspaceGovernanceError(
            "RUN_RELATIVE_IDENTITY_INVALID",
            "run_dir_relative must be a non-empty relative path without parent traversal.",
            run_dir_relative=raw,
        )
    return relative.as_posix()


def resolve_governed_project_root(
    *,
    current_run_dir: Path,
    manifest: Mapping[str, object],
) -> ResolvedGovernedWorkspace:
    """Resolve a moved Run using current relative identity, without rewriting files."""
    run_dir = current_run_dir.resolve(strict=False)
    run_id = str(manifest.get("run_id") or "")
    if not run_id or run_id != run_dir.name:
        raise WorkspaceGovernanceError(
            "RUN_IDENTITY_MISMATCH",
            "workflow_manifest.json RunId does not match the current Run folder.",
            run_dir=str(run_dir),
            manifest_run_id=run_id or None,
        )

    recorded_root_value = manifest.get("project_root_absolute")
    recorded_root = Path(str(recorded_root_value)).resolve(strict=False) if recorded_root_value else None
    relative_value = manifest.get("run_dir_relative")
    if relative_value:
        relative = _normalized_relative_run_path(relative_value)
        project_root = _canonical_project_root_from_run(run_dir)
        expected_run_dir = (project_root / Path(relative)).resolve(strict=False)
        if not is_within(project_root, expected_run_dir) or expected_run_dir != run_dir:
            raise WorkspaceGovernanceError(
                "RUN_RELATIVE_IDENTITY_MISMATCH",
                "run_dir_relative does not round-trip to the current Run folder.",
                project_root=str(project_root),
                run_dir=str(run_dir),
                run_dir_relative=relative,
                expected_run_dir=str(expected_run_dir),
            )
        workspace = load_workspace(project_root)
        manifest_workspace_id = str(manifest.get("workspace_id") or "")
        if not manifest_workspace_id:
            raise WorkspaceGovernanceError(
                "WORKSPACE_IDENTITY_MISSING",
                "The moved Run does not contain workspace_id for safe relocation resolution.",
                run_dir=str(run_dir),
            )
        if manifest_workspace_id != workspace.workspace_id:
            raise WorkspaceGovernanceError(
                "WORKSPACE_IDENTITY_MISMATCH",
                "The moved Run belongs to a different ProjectRoot workspace.",
                run_dir=str(run_dir),
                manifest_workspace_id=manifest_workspace_id,
                current_workspace_id=workspace.workspace_id,
            )
        warnings: tuple[str, ...] = ()
        source = "current_relative_identity"
        if recorded_root is not None and _path_key(recorded_root) == _path_key(project_root):
            source = "recorded_absolute"
        elif recorded_root is not None:
            if recorded_root.is_dir() and (recorded_root / WORKSPACE_CONFIG_NAME).is_file():
                try:
                    recorded_workspace = load_workspace(recorded_root)
                except WorkspaceGovernanceError:
                    recorded_workspace = None
                if recorded_workspace is not None and recorded_workspace.workspace_id == workspace.workspace_id:
                    raise WorkspaceGovernanceError(
                        "AMBIGUOUS_WORKSPACE_LOCATION",
                        "Both the recorded and current ProjectRoot resolve to the same workspace identity.",
                        recorded_project_root=str(recorded_root),
                        current_project_root=str(project_root),
                        workspace_id=workspace.workspace_id,
                    )
            warnings = ("RECORDED_PROJECT_ROOT_STALE",)
        return ResolvedGovernedWorkspace(
            project_root=project_root,
            run_dir=run_dir,
            resolution_source=source,
            workspace_id=workspace.workspace_id,
            warnings=warnings,
        )

    if recorded_root is not None:
        workspace = load_workspace(recorded_root)
        return ResolvedGovernedWorkspace(
            project_root=recorded_root,
            run_dir=run_dir,
            resolution_source="recorded_absolute",
            workspace_id=workspace.workspace_id,
            warnings=("RUN_RELATIVE_IDENTITY_MISSING",),
        )
    raise WorkspaceGovernanceError(
        "WORKSPACE_LOCATION_UNRESOLVED",
        "The Run has neither a usable relative identity nor a recorded ProjectRoot.",
        run_dir=str(run_dir),
        recommended_action="run a reviewed migration or rebind dry-run",
    )


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _is_retryable_windows_file_error(error: OSError) -> bool:
    return isinstance(error, PermissionError) or getattr(error, "winerror", None) in {32, 33}


def _file_error_details(error: OSError) -> dict[str, Any]:
    return {
        "type": type(error).__name__,
        "message": str(error),
        "errno": getattr(error, "errno", None),
        "winerror": getattr(error, "winerror", None),
    }


def _cleanup_atomic_temporary(temporary: Path) -> None:
    started = time.monotonic()
    backoff = ATOMIC_REPLACE_INITIAL_BACKOFF_SECONDS
    attempts = 0
    while True:
        attempts += 1
        try:
            temporary.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            elapsed = time.monotonic() - started
            if (
                not _is_retryable_windows_file_error(exc)
                or attempts >= ATOMIC_TEMP_CLEANUP_MAX_ATTEMPTS
                or elapsed >= ATOMIC_TEMP_CLEANUP_TIMEOUT_SECONDS
            ):
                raise
            remaining = ATOMIC_TEMP_CLEANUP_TIMEOUT_SECONDS - elapsed
            delay = min(backoff, ATOMIC_REPLACE_MAX_BACKOFF_SECONDS, max(0.0, remaining))
            if delay <= 0:
                raise
            time.sleep(delay)
            backoff = min(backoff * 2, ATOMIC_REPLACE_MAX_BACKOFF_SECONDS)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    replace_timeout_seconds: float = ATOMIC_REPLACE_TIMEOUT_SECONDS,
    replace_max_attempts: int = ATOMIC_REPLACE_MAX_ATTEMPTS,
    initial_backoff_seconds: float = ATOMIC_REPLACE_INITIAL_BACKOFF_SECONDS,
    max_backoff_seconds: float = ATOMIC_REPLACE_MAX_BACKOFF_SECONDS,
) -> None:
    if replace_timeout_seconds < 0:
        raise ValueError("replace_timeout_seconds must be non-negative.")
    if replace_max_attempts < 1:
        raise ValueError("replace_max_attempts must be at least 1.")
    if initial_backoff_seconds < 0 or max_backoff_seconds < 0:
        raise ValueError("Atomic replace backoff values must be non-negative.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary filename short so an otherwise safe Windows target
    # does not cross MAX_PATH only during the atomic replacement step.
    temporary = path.with_name(f".tmp_{uuid.uuid4().hex[:8]}")
    primary_error: BaseException | None = None
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        started = time.monotonic()
        backoff = initial_backoff_seconds
        attempts = 0
        while True:
            attempts += 1
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                elapsed = time.monotonic() - started
                if not _is_retryable_windows_file_error(exc):
                    raise
                exhausted = attempts >= replace_max_attempts or elapsed >= replace_timeout_seconds
                if exhausted:
                    raise WorkspaceGovernanceError(
                        "ATOMIC_REPLACE_RETRY_EXHAUSTED",
                        f"Atomic replace could not complete for {path} after {attempts} attempts.",
                        target_path=str(path.resolve(strict=False)),
                        temporary_path=str(temporary.resolve(strict=False)),
                        replace_attempts=attempts,
                        retry_count=max(0, attempts - 1),
                        max_wait_seconds=replace_timeout_seconds,
                        last_error=_file_error_details(exc),
                    ) from exc
                remaining = replace_timeout_seconds - elapsed
                delay = min(backoff, max_backoff_seconds, max(0.0, remaining))
                if delay <= 0:
                    raise WorkspaceGovernanceError(
                        "ATOMIC_REPLACE_RETRY_EXHAUSTED",
                        f"Atomic replace timed out for {path} after {attempts} attempts.",
                        target_path=str(path.resolve(strict=False)),
                        temporary_path=str(temporary.resolve(strict=False)),
                        replace_attempts=attempts,
                        retry_count=max(0, attempts - 1),
                        max_wait_seconds=replace_timeout_seconds,
                        last_error=_file_error_details(exc),
                    ) from exc
                time.sleep(delay)
                backoff = min(backoff * 2, max_backoff_seconds)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if temporary.exists():
            try:
                _cleanup_atomic_temporary(temporary)
            except OSError as cleanup_error:
                cleanup_details = {
                    "temporary_path": str(temporary.resolve(strict=False)),
                    "cleanup_error": _file_error_details(cleanup_error),
                }
                if isinstance(primary_error, WorkspaceGovernanceError):
                    primary_error.details["temporary_cleanup_failure"] = cleanup_details
                elif primary_error is not None:
                    primary_error.add_note(f"Atomic temporary cleanup also failed: {cleanup_details}")
                else:
                    raise WorkspaceGovernanceError(
                        "ATOMIC_TEMP_CLEANUP_FAILED",
                        f"Atomic write completed without replacement but temporary cleanup failed: {temporary}",
                        **cleanup_details,
                    ) from cleanup_error


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, to_json(data) + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _append_audit(path: Path | None, event: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": now_iso(), **event}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pid_alive(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ScopedFileLock:
    def __init__(
        self,
        path: Path,
        *,
        scope: str,
        timeout_seconds: float = 10.0,
        stale_after_seconds: float = 120.0,
        audit_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.scope = scope
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.stale_after_seconds = max(0.0, float(stale_after_seconds))
        self.audit_path = audit_path
        self.metadata = dict(metadata or {})
        self.lock_id = uuid.uuid4().hex
        self.acquired = False

    def _payload(self) -> dict[str, Any]:
        timestamp = now_iso()
        return {
            "lock_version": "1.0",
            "lock_id": self.lock_id,
            "scope": self.scope,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": timestamp,
            "heartbeat_at": timestamp,
            **self.metadata,
        }

    def _read_owner(self) -> dict[str, Any]:
        try:
            data = load_json(self.path)
        except (OSError, ValueError, json.JSONDecodeError):
            return {"unreadable": True}
        return data if isinstance(data, dict) else {"unreadable": True}

    def _is_stale(self, owner: dict[str, Any]) -> bool:
        heartbeat = _parse_datetime(owner.get("heartbeat_at") or owner.get("created_at"))
        if heartbeat is None:
            try:
                age = max(0.0, time.time() - self.path.stat().st_mtime)
            except OSError:
                return False
        else:
            age = max(0.0, (datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)).total_seconds())
        return age >= self.stale_after_seconds and not _pid_alive(owner.get("pid"))

    def _recover_stale(self, owner: dict[str, Any]) -> bool:
        recovered = self.path.with_name(
            f"{self.path.name}.stale.{datetime.now().strftime('%Y%m%dT%H%M%S')}.{uuid.uuid4().hex[:8]}"
        )
        try:
            os.replace(self.path, recovered)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        _append_audit(
            self.audit_path,
            {
                "event": "stale_lock_recovered",
                "scope": self.scope,
                "lock_path": str(self.path),
                "recovered_path": str(recovered),
                "previous_owner": owner,
            },
        )
        return True

    def acquire(self) -> "ScopedFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        permission_retry_count = 0
        while True:
            payload = self._payload()
            encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                owner = self._read_owner()
                if self._is_stale(owner) and self._recover_stale(owner):
                    continue
                if time.monotonic() >= deadline:
                    raise WorkspaceLockError(
                        "LOCK_BUSY",
                        f"The {self.scope} lock is held by another writer.",
                        scope=self.scope,
                        lock_path=str(self.path),
                        owner=owner,
                        recommended_actions=[
                            "wait and retry the same operation",
                            "create a branch run when independent work is intended",
                        ],
                    )
                time.sleep(0.05)
                continue
            except PermissionError as exc:
                permission_retry_count += 1
                lock_path_exists = self.path.exists()
                owner = self._read_owner() if lock_path_exists else {"unreadable": True}
                if lock_path_exists and self._is_stale(owner) and self._recover_stale(owner):
                    continue
                if time.monotonic() >= deadline:
                    if lock_path_exists:
                        raise WorkspaceLockError(
                            "LOCK_BUSY",
                            f"The {self.scope} lock is held by another writer and Windows denied a competing create.",
                            scope=self.scope,
                            lock_path=str(self.path),
                            owner=owner,
                            permission_retry_count=permission_retry_count,
                            recommended_actions=[
                                "wait and retry the same operation",
                                "create a branch run when independent work is intended",
                            ],
                        ) from exc
                    raise WorkspaceGovernanceError(
                        "LOCK_CREATE_PERMISSION_DENIED",
                        f"The {self.scope} lock could not be created within its timeout.",
                        scope=self.scope,
                        lock_path=str(self.path),
                        permission_retry_count=permission_retry_count,
                        original_error=_file_error_details(exc),
                    ) from exc
                time.sleep(0.01)
                continue
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.acquired = True
            _append_audit(
                self.audit_path,
                {
                    "event": "lock_acquired",
                    "scope": self.scope,
                    "lock_path": str(self.path),
                    "lock_id": self.lock_id,
                    "pid": os.getpid(),
                },
            )
            return self

    def heartbeat(self) -> None:
        if not self.acquired:
            raise RuntimeError("Cannot heartbeat a lock that is not acquired.")
        owner = self._read_owner()
        if owner.get("lock_id") != self.lock_id:
            raise WorkspaceLockError(
                "LOCK_OWNERSHIP_LOST",
                f"Lock ownership changed while {self.scope} was running.",
                lock_path=str(self.path),
                expected_lock_id=self.lock_id,
                owner=owner,
            )
        owner["heartbeat_at"] = now_iso()
        atomic_write_json(self.path, owner)

    def release(self) -> None:
        if not self.acquired:
            return
        owner = self._read_owner()
        if owner.get("lock_id") == self.lock_id:
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    self.path.unlink()
                    break
                except FileNotFoundError:
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
        self.acquired = False
        _append_audit(
            self.audit_path,
            {
                "event": "lock_released",
                "scope": self.scope,
                "lock_path": str(self.path),
                "lock_id": self.lock_id,
                "pid": os.getpid(),
            },
        )

    def __enter__(self) -> "ScopedFileLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


def _protected_roots(tool_root: Path | None) -> set[str]:
    home = Path.home().resolve(strict=False)
    protected = {
        _path_key(home),
        _path_key(home / "Desktop"),
    }
    if tool_root is not None:
        protected.add(_path_key(tool_root))
    return protected


def derive_project_slug(
    *,
    project_name: str | None = None,
    run_name: str | None = None,
    input_text: str | None = None,
) -> str:
    """Choose a path-safe child project name using the D02 priority order."""
    for value in (project_name, run_name, input_text):
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            slug = shorten_run_name_slug(value, max_length=48)
        except ValueError:
            continue
        if slug.casefold() in {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{index}" for index in range(1, 10)),
            *(f"lpt{index}" for index in range(1, 10)),
        }:
            slug = f"project_{slug}"
        return slug
    raise WorkspaceGovernanceError(
        "PROJECT_NAME_REQUIRED",
        "A child ProjectRoot needs --project-name, --run-name, or workflow input to derive a safe slug.",
        next_action="provide --project-name or run init with input/run-name from the intended parent folder",
    )


def _assert_safe_project_root(project_root: Path, tool_root: Path | None) -> None:
    if project_root.parent == project_root:
        raise WorkspaceGovernanceError(
            "PROTECTED_PROJECT_ROOT",
            "A filesystem root cannot be initialized as ProjectRoot.",
            project_root=str(project_root),
        )
    if _path_key(project_root) in _protected_roots(tool_root):
        raise WorkspaceGovernanceError(
            "PROTECTED_PROJECT_ROOT",
            "Desktop, the user home, and ToolRoot cannot be initialized automatically.",
            project_root=str(project_root),
        )


def find_workspace_marker(start: Path) -> Path | None:
    start = start.resolve(strict=False)
    if start.is_file():
        start = start.parent
    for candidate in (start, *start.parents):
        if (candidate / WORKSPACE_CONFIG_NAME).is_file():
            return candidate
    return None


def _validate_config(project_root: Path, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise WorkspaceGovernanceError(
            "INVALID_WORKSPACE_CONFIG",
            "Workspace config must be a JSON object.",
            config_path=str(project_root / WORKSPACE_CONFIG_NAME),
        )
    required = {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "project_root": ".",
        "engine_policy": "external_reference_only",
    }
    for key, expected in required.items():
        if data.get(key) != expected:
            raise WorkspaceGovernanceError(
                "INVALID_WORKSPACE_CONFIG",
                f"Workspace config requires {key}={expected!r}.",
                config_path=str(project_root / WORKSPACE_CONFIG_NAME),
                actual=data.get(key),
            )
    runs_root = str(data.get("runs_root") or "").replace("\\", "/").strip("/")
    if runs_root != RUNS_ROOT_RELATIVE.as_posix():
        raise WorkspaceGovernanceError(
            "INVALID_RUNS_ROOT",
            "runs_root must be outputs/workflows.",
            configured=data.get("runs_root"),
        )
    workspace_id = data.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise WorkspaceGovernanceError(
            "INVALID_WORKSPACE_CONFIG",
            "workspace_id must be a non-empty string.",
            config_path=str(project_root / WORKSPACE_CONFIG_NAME),
        )
    return dict(data)


def load_workspace(project_root: Path) -> Workspace:
    project_root = project_root.resolve(strict=False)
    config_path = project_root / WORKSPACE_CONFIG_NAME
    if not config_path.is_file():
        raise WorkspaceGovernanceError(
            "WORKSPACE_NOT_INITIALIZED",
            "ProjectRoot does not contain .schema-workflow.json.",
            project_root=str(project_root),
        )
    try:
        config = _validate_config(project_root, load_json(config_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise WorkspaceGovernanceError(
            "INVALID_WORKSPACE_CONFIG",
            f"Workspace config cannot be read: {exc}",
            config_path=str(config_path),
        ) from exc
    runs_root = (project_root / RUNS_ROOT_RELATIVE).resolve(strict=False)
    if not is_within(project_root, runs_root):
        raise WorkspaceGovernanceError(
            "RUNS_ROOT_ESCAPE",
            "CanonicalRunsRoot must stay inside ProjectRoot.",
            project_root=str(project_root),
            runs_root=str(runs_root),
        )
    return Workspace(
        project_root=project_root,
        config_path=config_path,
        runs_root=runs_root,
        control_root=runs_root / CONTROL_DIR_NAME,
        config=config,
    )


def resolve_workspace(
    *,
    project_root: str | Path | None = None,
    session_cwd: str | Path | None = None,
) -> Workspace:
    if project_root is not None:
        return load_workspace(Path(project_root))
    marker_root = find_workspace_marker(Path(session_cwd) if session_cwd is not None else Path.cwd())
    if marker_root is None:
        raise WorkspaceGovernanceError(
            "WORKSPACE_NOT_FOUND",
            "No .schema-workflow.json was found from SessionCwd or its ancestors.",
            session_cwd=str(Path(session_cwd) if session_cwd is not None else Path.cwd()),
            next_action="run workspace-init in a safe project folder or pass --project-root",
        )
    return load_workspace(marker_root)


def _looks_like_partial_bootstrap(project_root: Path) -> bool:
    allowed = {WORKSPACE_CONFIG_NAME, ".schema-workflow.bootstrap.lock", "outputs"}
    try:
        names = {item.name for item in project_root.iterdir()}
    except OSError:
        return False
    return names <= allowed


def bootstrap_workspace(
    *,
    project_root: str | Path | None = None,
    session_cwd: str | Path | None = None,
    tool_root: str | Path | None = None,
    project_name: str | None = None,
    run_name: str | None = None,
    input_text: str | None = None,
    timeout_seconds: float = 15.0,
) -> Workspace:
    explicit = project_root is not None
    candidate = Path(project_root) if explicit else Path(session_cwd) if session_cwd is not None else Path.cwd()
    candidate = candidate.expanduser().resolve(strict=False)
    tool_path = Path(tool_root).resolve(strict=False) if tool_root is not None else None
    project_root_selection = "explicit_path" if explicit else "session_cwd"

    marker_root = find_workspace_marker(candidate) if candidate.exists() else None
    if marker_root is not None:
        if explicit and _path_key(marker_root) != _path_key(candidate):
            raise WorkspaceGovernanceError(
                "PROJECT_ROOT_MISMATCH",
                "Explicit --project-root points below a different configured ProjectRoot.",
                requested=str(candidate),
                configured=str(marker_root),
            )
        return load_workspace(marker_root)

    derived_child = False
    if not explicit:
        if not candidate.exists():
            raise WorkspaceGovernanceError(
                "PROJECT_ROOT_NOT_FOUND",
                "SessionCwd must exist before automatic workspace initialization.",
                project_root=str(candidate),
            )
        if not candidate.is_dir():
            raise WorkspaceGovernanceError(
                "PROJECT_ROOT_NOT_DIRECTORY",
                "SessionCwd must be a directory.",
                project_root=str(candidate),
            )
        if candidate.parent == candidate:
            _assert_safe_project_root(candidate, tool_path)
        candidate_key = _path_key(candidate)
        home = Path.home().resolve(strict=False)
        protected_parent = candidate_key in {_path_key(home), _path_key(home / "Desktop")}
        nonempty_parent = any(candidate.iterdir()) and not _looks_like_partial_bootstrap(candidate)
        if protected_parent or nonempty_parent:
            if tool_path is not None and candidate_key == _path_key(tool_path):
                raise WorkspaceGovernanceError(
                    "PROTECTED_PROJECT_ROOT",
                    "ToolRoot cannot own an automatically generated project workspace.",
                    project_root=str(candidate),
                    next_action="pass --project-root for a project folder outside ToolRoot",
                )
            child_slug = derive_project_slug(
                project_name=project_name,
                run_name=run_name,
                input_text=input_text,
            )
            candidate = (candidate / child_slug).resolve(strict=False)
            derived_child = True
            project_root_selection = "derived_child_slug"
            if (candidate / WORKSPACE_CONFIG_NAME).is_file():
                return load_workspace(candidate)

    _assert_safe_project_root(candidate, tool_path)
    if not candidate.exists():
        if not explicit and not derived_child:
            raise WorkspaceGovernanceError(
                "PROJECT_ROOT_NOT_FOUND",
                "SessionCwd must exist before automatic workspace initialization.",
                project_root=str(candidate),
            )
        if not candidate.parent.is_dir():
            raise WorkspaceGovernanceError(
                "PROJECT_PARENT_NOT_FOUND",
                "The parent directory must exist before initializing a new ProjectRoot.",
                project_root=str(candidate),
            )
        try:
            candidate.mkdir()
        except FileExistsError:
            pass
    if not candidate.is_dir():
        raise WorkspaceGovernanceError(
            "PROJECT_ROOT_NOT_DIRECTORY",
            "ProjectRoot must be a directory.",
            project_root=str(candidate),
        )
    if not explicit and any(candidate.iterdir()) and not _looks_like_partial_bootstrap(candidate):
        raise WorkspaceGovernanceError(
            "NONEMPTY_PROJECT_REQUIRES_EXPLICIT_ROOT",
            "Automatic initialization is limited to a safe empty folder.",
            project_root=str(candidate),
            next_action="pass --project-root explicitly after verifying the intended project boundary",
        )

    bootstrap_lock = ScopedFileLock(
        candidate / ".schema-workflow.bootstrap.lock",
        scope="workspace_initialization",
        timeout_seconds=timeout_seconds,
        stale_after_seconds=120.0,
        audit_path=candidate / RUNS_ROOT_RELATIVE / CONTROL_DIR_NAME / AUDIT_NAME,
        metadata={"project_root": str(candidate)},
    )
    with bootstrap_lock:
        config_path = candidate / WORKSPACE_CONFIG_NAME
        if config_path.exists():
            return load_workspace(candidate)
        runs_root = candidate / RUNS_ROOT_RELATIVE
        control_root = runs_root / CONTROL_DIR_NAME
        for directory in (
            runs_root,
            control_root,
            control_root / "locks",
            control_root / "operations",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        config = {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "workspace_id": f"ws_{uuid.uuid4().hex}",
            "project_root": ".",
            "runs_root": RUNS_ROOT_RELATIVE.as_posix(),
            "engine_policy": "external_reference_only",
            "project_slug": candidate.name,
            "project_root_selection": project_root_selection,
            "initialized_at": now_iso(),
        }
        atomic_write_json(config_path, config)
        _append_audit(
            control_root / AUDIT_NAME,
            {
                "event": "workspace_initialized",
                "workspace_id": config["workspace_id"],
                "project_root": str(candidate),
                "runs_root": str(runs_root),
            },
        )
    return load_workspace(candidate)


def validate_canonical_output(workspace: Workspace, output: str | Path | None) -> Path:
    if output is None:
        return workspace.runs_root
    candidate = Path(output)
    if not candidate.is_absolute():
        candidate = workspace.project_root / candidate
    candidate = candidate.resolve(strict=False)
    if _path_key(candidate) != _path_key(workspace.runs_root):
        raise WorkspaceGovernanceError(
            "UNOFFICIAL_OUTPUT_ROOT",
            "Normal workflow runs may only be created in ProjectRoot/outputs/workflows.",
            requested_output=str(candidate),
            canonical_runs_root=str(workspace.runs_root),
        )
    return workspace.runs_root


def normalize_input(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Workflow input must be a string.")
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip(" \t") for line in normalized.split("\n"))
    return normalized.strip("\n")


def input_hash_record(text: str) -> dict[str, str]:
    normalized = normalize_input(text)
    return {
        "algorithm": INPUT_HASH_ALGORITHM,
        "normalization_version": INPUT_NORMALIZATION_VERSION,
        "value": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def duplicate_group_id(input_hash: str) -> str:
    return f"dup_{input_hash[:24]}"


def new_operation_id() -> str:
    return f"op_{datetime.now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:12]}"


def _safe_key(value: str) -> str:
    # The full identity remains in the JSON payload. A 64-bit filename key
    # keeps control paths usable in deep Windows project trees.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def operation_path(workspace: Workspace, operation_id: str) -> Path:
    return workspace.operations_root / f"operation_{_safe_key(operation_id)}.json"


def operation_lock(workspace: Workspace, operation_id: str, *, timeout_seconds: float = 30.0) -> ScopedFileLock:
    return ScopedFileLock(
        workspace.locks_root / f"operation_{_safe_key(operation_id)}.lock",
        scope="operation",
        timeout_seconds=timeout_seconds,
        stale_after_seconds=120.0,
        audit_path=workspace.audit_path,
        metadata={"operation_id": operation_id},
    )


def registry_lock(workspace: Workspace, *, timeout_seconds: float = 30.0) -> ScopedFileLock:
    return ScopedFileLock(
        workspace.locks_root / "registry.lock",
        scope="registry",
        timeout_seconds=timeout_seconds,
        stale_after_seconds=120.0,
        audit_path=workspace.audit_path,
        metadata={"workspace_id": workspace.workspace_id},
    )


def deliverables_lock(workspace: Workspace, *, timeout_seconds: float = 30.0) -> ScopedFileLock:
    return ScopedFileLock(
        workspace.locks_root / "deliverables.lock",
        scope="deliverables",
        timeout_seconds=timeout_seconds,
        stale_after_seconds=120.0,
        audit_path=workspace.audit_path,
        metadata={"workspace_id": workspace.workspace_id},
    )


def run_writer_lock(
    workspace: Workspace,
    run_id: str,
    *,
    timeout_seconds: float = 0.0,
    stale_after_seconds: float = 120.0,
) -> ScopedFileLock:
    return ScopedFileLock(
        workspace.locks_root / f"run_{_safe_key(run_id)}.lock",
        scope="run_writer",
        timeout_seconds=timeout_seconds,
        stale_after_seconds=stale_after_seconds,
        audit_path=workspace.audit_path,
        metadata={"run_id": run_id},
    )


def _read_git_commit(tool_root: Path) -> str | None:
    git_dir = tool_root / ".git"
    if not git_dir.is_dir():
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{40}", head):
        return head.lower()
    if not head.startswith("ref: "):
        return None
    reference = head[5:].strip()
    ref_path = git_dir / Path(reference)
    try:
        value = ref_path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value.lower()
    packed = git_dir / "packed-refs"
    try:
        lines = packed.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1] == reference and re.fullmatch(r"[0-9a-fA-F]{40}", parts[0]):
            return parts[0].lower()
    return None


def _engine_python_fingerprint(tool_root: Path, entrypoint: Path) -> dict[str, Any]:
    root = tool_root.resolve(strict=False)
    candidates: set[Path] = set()
    for relative_root in ENGINE_PYTHON_ROOTS:
        python_root = root / relative_root
        if python_root.is_dir():
            candidates.update(
                path.resolve(strict=False)
                for path in python_root.rglob("*.py")
                if "__pycache__" not in path.parts
            )
    if entrypoint.is_file():
        candidates.add(entrypoint.resolve(strict=False))
    files: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: str(item).casefold()):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = str(path)
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        aggregate.update(f"{relative}\t{size}\t{content_hash}\n".encode("utf-8"))
        files.append({"path": relative, "sha256": content_hash, "size": size})
    return {
        "algorithm": "sha256",
        "value": aggregate.hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def _read_git_status(tool_root: Path) -> list[str] | None:
    if not (tool_root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={tool_root.resolve(strict=False)}",
                "-C",
                str(tool_root.resolve(strict=False)),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return [line for line in completed.stdout.splitlines() if line.strip()]


def engine_identity(tool_root: Path, *, version: str, entrypoint: Path) -> dict[str, Any]:
    tool_root = tool_root.resolve(strict=False)
    commit = _read_git_commit(tool_root)
    python_fingerprint = _engine_python_fingerprint(tool_root, entrypoint)
    git_status = _read_git_status(tool_root)
    dirty = None if git_status is None else bool(git_status)
    if commit:
        if dirty is True:
            identity = f"{commit}+dirty:{python_fingerprint['value'][:12]}"
        elif dirty is False:
            identity = commit
        else:
            identity = f"{commit}+dirty-unknown:{python_fingerprint['value'][:12]}"
    else:
        identity = f"sha256:{python_fingerprint['value']}"
    return {
        "tool_root_absolute": str(tool_root),
        "engine_version": version,
        "engine_commit_or_fingerprint": identity,
        "engine_git_commit": commit,
        "engine_git_dirty": dirty,
        "engine_git_status": git_status,
        "engine_python_fingerprint": python_fingerprint,
        "engine_policy": "external_reference_only",
    }


def _build_reserved_run_id(operation_id: str, run_name: str | None, created_at: datetime) -> str:
    stem = build_run_id(run_name, created_at, include_timestamp_for_named=True)
    return f"{stem}__{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()[:8]}"


def _load_operation_if_present(workspace: Workspace, operation_id: str) -> dict[str, Any] | None:
    path = operation_path(workspace, operation_id)
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise WorkspaceGovernanceError(
            "OPERATION_RECORD_INVALID",
            f"Operation reservation cannot be read: {exc}",
            operation_id=operation_id,
            operation_path=str(path),
        ) from exc
    if not isinstance(data, dict) or data.get("operation_id") != operation_id:
        raise WorkspaceGovernanceError(
            "OPERATION_RECORD_INVALID",
            "Operation reservation identity does not match the requested OperationId.",
            operation_id=operation_id,
            operation_path=str(path),
        )
    return data


def reserve_operation(
    workspace: Workspace,
    *,
    operation_id: str,
    input_hash: dict[str, str],
    run_name: str | None,
    session_reference: str | None,
    relation_type: str,
    parent_run_id: str | None,
    target_run_id: str | None = None,
    operation_kind: str = "new_run",
    delivery_policy: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if relation_type not in RELATION_TYPES:
        raise WorkspaceGovernanceError(
            "INVALID_RELATION_TYPE",
            f"relation_type must be one of {sorted(RELATION_TYPES)}.",
            relation_type=relation_type,
        )
    if operation_kind not in OPERATION_KINDS:
        raise WorkspaceGovernanceError(
            "INVALID_OPERATION_KIND",
            f"operation_kind must be one of {sorted(OPERATION_KINDS)}.",
            operation_kind=operation_kind,
        )
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise WorkspaceGovernanceError("INVALID_OPERATION_ID", "OperationId must be a non-empty string.")
    operation_id = operation_id.strip()
    with registry_lock(workspace):
        existing = _load_operation_if_present(workspace, operation_id)
        if existing is not None:
            existing_relation = str(existing.get("relation_type") or "independent")
            existing_kind = existing.get("operation_kind")
            if not existing_kind:
                existing_kind = "continuation" if existing_relation == "continuation" else "new_run"
            if "target_run_id" in existing:
                existing_target = existing.get("target_run_id")
            else:
                existing_target = existing.get("run_id") if existing_kind == "continuation" else None
            existing_contract = {
                "input_hash": (existing.get("input_hash") or {}).get("value"),
                "operation_kind": existing_kind,
                "relation_type": existing_relation,
                "parent_run_id": existing.get("parent_run_id"),
                "target_run_id": existing_target,
            }
            requested_contract = {
                "input_hash": input_hash.get("value"),
                "operation_kind": operation_kind,
                "relation_type": relation_type,
                "parent_run_id": parent_run_id,
                "target_run_id": target_run_id,
            }
            mismatches = {
                key: {"existing": existing_contract[key], "requested": requested_contract[key]}
                for key in requested_contract
                if existing_contract[key] != requested_contract[key]
            }
            if mismatches:
                raise WorkspaceGovernanceError(
                    "OPERATION_CONTRACT_MISMATCH",
                    "OperationId is immutable and cannot be reused with a different operation contract.",
                    operation_id=operation_id,
                    mismatches=mismatches,
                    existing_contract=existing_contract,
                    requested_contract=requested_contract,
                )
            if operation_kind == "continuation" and delivery_policy is not None:
                existing_delivery_policy = existing.get("delivery_policy")
                if existing_delivery_policy is None:
                    existing["operation_record_version"] = OPERATION_RECORD_VERSION
                    existing["delivery_policy"] = delivery_policy
                    existing["updated_at"] = now_iso()
                    atomic_write_json(operation_path(workspace, operation_id), existing)
                    _append_audit(
                        workspace.audit_path,
                        {
                            "event": "operation_contract_backfilled",
                            "operation_id": operation_id,
                            "run_id": existing.get("run_id"),
                            "field": "delivery_policy",
                            "value": delivery_policy,
                        },
                    )
                    _rebuild_registry_unlocked(workspace)
                elif existing_delivery_policy != delivery_policy:
                    raise WorkspaceGovernanceError(
                        "OPERATION_CONTRACT_MISMATCH",
                        "OperationId is immutable and cannot change continuation delivery_policy.",
                        operation_id=operation_id,
                        existing_delivery_policy=existing_delivery_policy,
                        requested_delivery_policy=delivery_policy,
                    )
            return existing, True

        created_at_dt = datetime.now()
        run_id = target_run_id or _build_reserved_run_id(operation_id, run_name, created_at_dt)
        candidate = workspace.runs_root / run_id
        reserved_run_ids = {
            str(item.get("run_id"))
            for item in _read_operations(workspace)
            if item.get("run_id")
        }
        collision = 2
        while target_run_id is None and (candidate.exists() or run_id in reserved_run_ids):
            run_id = f"{_build_reserved_run_id(operation_id, run_name, created_at_dt)}_{collision:02d}"
            candidate = workspace.runs_root / run_id
            collision += 1
        timestamp = created_at_dt.astimezone().isoformat(timespec="seconds")
        record = {
            "operation_record_version": OPERATION_RECORD_VERSION,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "workspace_id": workspace.workspace_id,
            "run_id": run_id,
            "target_run_id": target_run_id,
            "status": "reserved",
            "created_at": timestamp,
            "updated_at": timestamp,
            "input_hash": input_hash,
            "session_reference": session_reference,
            "relation_type": relation_type,
            "parent_run_id": parent_run_id,
            "error": None,
            "error_history": [],
            "history": [{"timestamp": timestamp, "status": "reserved"}],
        }
        if delivery_policy is not None:
            record["delivery_policy"] = delivery_policy
        atomic_write_json(operation_path(workspace, operation_id), record)
        _append_audit(
            workspace.audit_path,
            {
                "event": "operation_reserved",
                "operation_id": operation_id,
                "run_id": run_id,
                "operation_kind": operation_kind,
                "relation_type": relation_type,
                "parent_run_id": parent_run_id,
                "target_run_id": target_run_id,
            },
        )
        _rebuild_registry_unlocked(workspace)
        return record, False


def update_operation(
    workspace: Workspace,
    operation_id: str,
    status: str,
    *,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in OPERATION_STATUSES:
        raise ValueError(f"Unsupported operation status: {status}")
    with registry_lock(workspace):
        record = _load_operation_if_present(workspace, operation_id)
        if record is None:
            raise WorkspaceGovernanceError(
                "OPERATION_NOT_FOUND",
                "Operation reservation does not exist.",
                operation_id=operation_id,
            )
        previous_status = str(record.get("status") or "")
        previous_error = record.get("error")
        if previous_status == status and previous_error == error:
            return record

        timestamp = now_iso()
        event = {"timestamp": timestamp, "status": status, **({"error": error} if error else {})}
        record["operation_record_version"] = OPERATION_RECORD_VERSION
        record["status"] = status
        record["updated_at"] = timestamp
        record["error"] = error
        record.setdefault("history", []).append(event)
        if error:
            error_history = record.setdefault("error_history", [])
            if not error_history or error_history[-1].get("error") != error:
                error_history.append(event)
        atomic_write_json(operation_path(workspace, operation_id), record)
        _append_audit(
            workspace.audit_path,
            {
                "event": "operation_status_changed",
                "operation_id": operation_id,
                "run_id": record.get("run_id"),
                "status": status,
                "error": error,
            },
        )
        _rebuild_registry_unlocked(workspace)
        return record


def resolve_existing_run(
    workspace: Workspace,
    *,
    run_id: str | None = None,
    run_dir: str | Path | None = None,
) -> Path:
    if bool(run_id) == bool(run_dir):
        raise WorkspaceGovernanceError(
            "RUN_SELECTOR_REQUIRED",
            "Specify exactly one of RunId or RunDir.",
            run_id=run_id,
            run_dir=str(run_dir) if run_dir is not None else None,
        )
    if run_id is not None:
        if Path(run_id).name != run_id or run_id in {".", ".."}:
            raise WorkspaceGovernanceError("INVALID_RUN_ID", "RunId must be one folder name.", run_id=run_id)
        candidate = workspace.runs_root / run_id
    else:
        candidate = Path(run_dir)  # type: ignore[arg-type]
        if not candidate.is_absolute():
            candidate = workspace.project_root / candidate
    candidate = candidate.resolve(strict=False)
    if candidate.parent != workspace.runs_root.resolve(strict=False):
        raise WorkspaceGovernanceError(
            "CROSS_PROJECT_RUN_REJECTED",
            "RunDir must be a direct child of this ProjectRoot's CanonicalRunsRoot.",
            project_root=str(workspace.project_root),
            canonical_runs_root=str(workspace.runs_root),
            requested_run_dir=str(candidate),
        )
    manifest_path = candidate / "workflow_manifest.json"
    if not manifest_path.is_file():
        raise WorkspaceGovernanceError(
            "RUN_NOT_FOUND",
            "The selected run does not contain workflow_manifest.json.",
            run_dir=str(candidate),
        )
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("run_id") != candidate.name:
        raise WorkspaceGovernanceError(
            "RUN_IDENTITY_MISMATCH",
            "workflow_manifest.json RunId does not match its canonical folder.",
            run_dir=str(candidate),
            manifest_run_id=manifest.get("run_id") if isinstance(manifest, dict) else None,
        )
    declared_root = manifest.get("project_root_absolute")
    if declared_root and _path_key(Path(str(declared_root))) != _path_key(workspace.project_root):
        relative_value = manifest.get("run_dir_relative")
        manifest_workspace_id = str(manifest.get("workspace_id") or "")
        relative_matches = False
        if relative_value and manifest_workspace_id == workspace.workspace_id:
            try:
                relative = _normalized_relative_run_path(relative_value)
                expected_run_dir = (workspace.project_root / Path(relative)).resolve(strict=False)
                relative_matches = (
                    is_within(workspace.project_root, expected_run_dir)
                    and expected_run_dir == candidate
                )
            except WorkspaceGovernanceError:
                relative_matches = False
        if not relative_matches:
            raise WorkspaceGovernanceError(
                "CROSS_PROJECT_RUN_REJECTED",
                "The selected run belongs to a different ProjectRoot.",
                requested_project_root=str(workspace.project_root),
                manifest_project_root=str(declared_root),
                manifest_workspace_id=manifest_workspace_id or None,
            )
    return candidate


def enrich_manifest(
    manifest: dict[str, Any],
    *,
    workspace: Workspace,
    operation: dict[str, Any],
    engine: dict[str, Any],
    canonical_status: str = "candidate",
) -> dict[str, Any]:
    if canonical_status not in CANONICAL_STATUSES:
        raise ValueError(f"Unsupported canonical_status: {canonical_status}")
    run_id = str(manifest.get("run_id") or operation["run_id"])
    run_dir = workspace.runs_root / run_id
    manifest.update(
        {
            "governance_version": WORKSPACE_GOVERNANCE_VERSION,
            "workspace_id": workspace.workspace_id,
            "operation_id": operation["operation_id"],
            "session_reference": operation.get("session_reference"),
            "input_hash": operation["input_hash"],
            "duplicate_group_id": duplicate_group_id(operation["input_hash"]["value"]),
            "relation_type": operation.get("relation_type") or "independent",
            "parent_run_id": operation.get("parent_run_id"),
            "canonical_status": canonical_status,
            "status": "running",
            "updated_at": now_iso(),
            "project_root_absolute": str(workspace.project_root),
            "runs_root_absolute": str(workspace.runs_root),
            "run_dir_absolute": str(run_dir),
            "run_dir_relative": relative_to_project(workspace.project_root, run_dir),
            "deliverable_paths": list(manifest.get("deliverable_paths") or []),
            **engine,
        }
    )
    manifest["run_dir"] = str(run_dir)
    manifest.setdefault("revision_history", [])
    manifest.setdefault("continuation_operations", [])
    manifest.setdefault("active_continuation_operation_id", None)
    manifest.setdefault("continuation_ownership_status", "idle")
    return manifest


def initialize_operation_run(
    workspace: Workspace,
    *,
    text: str,
    operation_id: str | None,
    run_name: str | None,
    session_reference: str | None,
    relation_type: str,
    parent_run_id: str | None,
    engine: dict[str, Any],
    builder: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    operation_id = operation_id or new_operation_id()
    hash_record = input_hash_record(text)
    if parent_run_id is not None:
        resolve_existing_run(workspace, run_id=parent_run_id)
        if relation_type == "independent":
            relation_type = "branch"
    with operation_lock(workspace, operation_id):
        operation, reused = reserve_operation(
            workspace,
            operation_id=operation_id,
            input_hash=hash_record,
            run_name=run_name,
            session_reference=session_reference,
            relation_type=relation_type,
            parent_run_id=parent_run_id,
            operation_kind="new_run",
        )
        manifest_path = workspace.runs_root / operation["run_id"] / "workflow_manifest.json"
        if reused and manifest_path.is_file():
            manifest = load_json(manifest_path)
            if not isinstance(manifest, dict):
                raise WorkspaceGovernanceError(
                    "RUN_MANIFEST_INVALID",
                    "Existing idempotent RunId has an invalid manifest.",
                    operation_id=operation_id,
                    run_id=operation["run_id"],
                )
            return manifest, {"operation": operation, "idempotent_reuse": True}
        if reused and operation.get("status") == "init_failed":
            raise WorkspaceGovernanceError(
                "OPERATION_INIT_PREVIOUSLY_FAILED",
                "This OperationId is already recorded as init_failed; use a new OperationId after reviewing the error.",
                operation=operation,
            )
        try:
            manifest = builder(str(operation["run_id"]))
            manifest = enrich_manifest(
                manifest,
                workspace=workspace,
                operation=operation,
                engine=engine,
            )
            atomic_write_json(manifest_path, manifest)
            update_operation(workspace, operation_id, "run_created")
            operation = update_operation(workspace, operation_id, "running")
            rebuild_registry(workspace)
            return manifest, {"operation": operation, "idempotent_reuse": False}
        except Exception as exc:
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "next_action": "inspect the reserved operation and partial RunDir before retrying with a new OperationId",
            }
            update_operation(workspace, operation_id, "init_failed", error=error)
            raise


def _status_from_run(manifest: dict[str, Any], run_dir: Path) -> str:
    explicit = manifest.get("status")
    status_path = run_dir / "workflow_status.json"
    state = None
    if status_path.is_file():
        try:
            status_data = load_json(status_path)
            if isinstance(status_data, dict):
                state = status_data.get("workflow_state")
        except (OSError, ValueError, json.JSONDecodeError):
            state = None
    value = str(state or explicit or "running")
    lowered = value.casefold()
    if "completed" in lowered:
        return "completed"
    if value == "ready_for_next_action":
        return "running"
    if "waiting" in lowered or "required" in lowered:
        return "waiting_user" if "waiting" in lowered else "running"
    if "failed" in lowered or "invalid" in lowered:
        return "failed"
    if "aborted" in lowered or "interrupted" in lowered:
        return "aborted"
    return value


def _continuation_ownership(manifest: dict[str, Any]) -> dict[str, Any]:
    entries = {
        str(item["operation_id"]): item
        for item in manifest.get("continuation_operations", [])
        if isinstance(item, dict) and item.get("operation_id")
    }
    nonterminal = sorted(
        operation_id
        for operation_id, item in entries.items()
        if str(item.get("status") or "running") not in TERMINAL_CONTINUATION_STATUSES
    )
    pointer_value = manifest.get("active_continuation_operation_id")
    pointer = str(pointer_value) if pointer_value else None
    if pointer is not None and pointer not in entries:
        return {
            "status": "ambiguous",
            "operation_id": None,
            "operation_status": None,
            "candidates": sorted(set([pointer, *nonterminal])),
            "reason": "active pointer does not resolve to a continuation entry",
        }
    if pointer is not None and pointer in entries:
        pointer_status = str(entries[pointer].get("status") or "running")
        if pointer_status in TERMINAL_CONTINUATION_STATUSES:
            pointer = None
        elif len(nonterminal) == 1 and nonterminal[0] == pointer:
            return {
                "status": "active",
                "operation_id": pointer,
                "operation_status": pointer_status,
                "candidates": nonterminal,
                "reason": "explicit active pointer",
            }
        else:
            return {
                "status": "ambiguous",
                "operation_id": None,
                "operation_status": None,
                "candidates": nonterminal,
                "reason": "multiple non-terminal continuations conflict with the active pointer",
            }
    if not nonterminal:
        return {
            "status": "idle",
            "operation_id": None,
            "operation_status": None,
            "candidates": [],
            "reason": "no non-terminal continuation",
        }
    if len(nonterminal) == 1:
        operation_id = nonterminal[0]
        return {
            "status": "active",
            "operation_id": operation_id,
            "operation_status": str(entries[operation_id].get("status") or "running"),
            "candidates": nonterminal,
            "reason": "single legacy non-terminal continuation inferred as owner",
        }
    return {
        "status": "ambiguous",
        "operation_id": None,
        "operation_status": None,
        "candidates": nonterminal,
        "reason": "legacy manifest contains multiple non-terminal continuations",
    }


def _apply_continuation_ownership(manifest: dict[str, Any], ownership: dict[str, Any]) -> None:
    status = str(ownership["status"])
    manifest["continuation_ownership_status"] = status
    if status == "active":
        manifest["active_continuation_operation_id"] = ownership["operation_id"]
        manifest.pop("continuation_ownership_candidates", None)
        manifest.pop("continuation_ownership_issue", None)
    elif status == "idle":
        manifest["active_continuation_operation_id"] = None
        manifest.pop("continuation_ownership_candidates", None)
        manifest.pop("continuation_ownership_issue", None)
    else:
        manifest["active_continuation_operation_id"] = None
        manifest["continuation_ownership_candidates"] = list(ownership.get("candidates") or [])
        manifest["continuation_ownership_issue"] = ownership.get("reason")


def _sync_active_continuation_summary(
    manifest: dict[str, Any],
    *,
    operation_id: str,
    operation_status: str,
) -> bool:
    """Keep the persisted summary aligned when a continuation owns the run."""
    state_by_status = {
        "running": (
            "continuation_in_progress",
            "Inspect the active continuation state before proceeding.",
        ),
        "waiting_user": (
            "continuation_waiting_user",
            "Provide the required input for the active continuation.",
        ),
    }
    state = state_by_status.get(operation_status)
    if state is None:
        return False
    workflow_state, next_required_action = state
    summary = manifest.setdefault("summary", {})
    if not isinstance(summary, dict):
        summary = {}
        manifest["summary"] = summary
    summary["workflow_state"] = workflow_state
    summary["next_required_action"] = next_required_action
    continuation = summary.get("continuation")
    if not isinstance(continuation, dict):
        continuation = {}
        summary["continuation"] = continuation
    continuation["active_operation_id"] = operation_id
    continuation["operation_status"] = operation_status
    continuation["workflow_state"] = workflow_state
    return True


def _set_active_continuation_status(
    manifest: dict[str, Any],
    status: str,
    timestamp: str,
) -> tuple[str | None, dict[str, Any]]:
    ownership = _continuation_ownership(manifest)
    _apply_continuation_ownership(manifest, ownership)
    if ownership["status"] != "active":
        return None, ownership
    operation_id = str(ownership["operation_id"])
    for item in manifest.get("continuation_operations", []):
        if isinstance(item, dict) and str(item.get("operation_id")) == operation_id:
            item["status"] = status
            item["updated_at"] = timestamp
            break
    if status in TERMINAL_CONTINUATION_STATUSES:
        manifest["active_continuation_operation_id"] = None
        manifest["continuation_ownership_status"] = "idle"
    return operation_id, ownership



def _run_status_data(run_dir: Path) -> dict[str, Any]:
    status_path = run_dir / "workflow_status.json"
    if not status_path.is_file():
        return {}
    try:
        value = load_json(status_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolved_manifest_path(
    manifest: dict[str, Any],
    key: str,
    workspace: Workspace,
) -> Path | None:
    raw_value = (manifest.get("paths") or {}).get(key)
    if not raw_value:
        return None
    path = Path(str(raw_value))
    if not path.is_absolute():
        path = workspace.project_root / path
    return path.resolve(strict=False)


def _report_readiness(
    manifest: dict[str, Any],
    run_dir: Path,
    workspace: Workspace,
) -> dict[str, Any]:
    report_path = _resolved_manifest_path(manifest, "human_report", workspace)
    summary_path = _resolved_manifest_path(manifest, "report_summary", workspace)
    exists = bool(report_path and report_path.is_file())
    if not exists:
        return {"exists": False, "ready": False, "reason": "human report file is missing"}
    try:
        report_text = report_path.read_text(encoding="utf-8-sig")
    except OSError:
        return {"exists": True, "ready": False, "reason": "human report is unreadable"}
    if "pending until" in report_text.casefold():
        return {"exists": True, "ready": False, "reason": "human report is a pending stub"}
    if summary_path is None or not summary_path.is_file():
        return {"exists": True, "ready": False, "reason": "report summary is missing"}
    try:
        summary = load_json(summary_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"exists": True, "ready": False, "reason": "report summary is unreadable"}
    if not isinstance(summary, dict) or summary.get("workflow_placeholder") is True:
        return {"exists": True, "ready": False, "reason": "report summary is a placeholder"}
    return {"exists": True, "ready": True, "reason": "report and summary are materialized"}


def _manifest_entry(manifest: dict[str, Any], run_dir: Path, workspace: Workspace) -> dict[str, Any]:
    raw_text = str((manifest.get("source") or {}).get("raw_text") or "")
    hash_data = manifest.get("input_hash")
    if not isinstance(hash_data, dict) or not hash_data.get("value"):
        hash_data = input_hash_record(raw_text)
    report_path = (manifest.get("paths") or {}).get("human_report")
    if report_path:
        report_candidate = Path(str(report_path))
        if not report_candidate.is_absolute():
            report_candidate = workspace.project_root / report_candidate
        report_path = str(report_candidate.resolve(strict=False))
    report_readiness = _report_readiness(manifest, run_dir, workspace)
    status_data = _run_status_data(run_dir)
    workflow_state = str(status_data.get("workflow_state") or manifest.get("status") or "running")
    fulfillment_status = status_data.get("fulfillment_status")
    if not isinstance(fulfillment_status, dict):
        fulfillment_status = {}
    ownership = _continuation_ownership(manifest)
    return {
        "operation_id": manifest.get("operation_id"),
        "run_id": str(manifest.get("run_id") or run_dir.name),
        "input_hash": hash_data,
        "status": _status_from_run(manifest, run_dir),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at") or manifest.get("created_at"),
        "run_dir": str(run_dir),
        "run_dir_relative": relative_to_project(workspace.project_root, run_dir),
        "report_path": report_path,
        "report_exists": report_readiness["exists"],
        "report_ready": report_readiness["ready"],
        "report_readiness_reason": report_readiness["reason"],
        "workflow_state": workflow_state,
        "fulfillment_configured": "fulfillment_contract" in (manifest.get("paths") or {}),
        "fulfillment_status": fulfillment_status,
        "parent_run_id": manifest.get("parent_run_id"),
        "duplicate_group_id": manifest.get("duplicate_group_id") or duplicate_group_id(hash_data["value"]),
        "relation_type": manifest.get("relation_type") or "independent",
        "canonical_status": manifest.get("canonical_status") or "candidate",
        "governance_version": manifest.get("governance_version") or "legacy",
        "continuation_operations": list(manifest.get("continuation_operations") or []),
        "active_continuation_operation_id": ownership.get("operation_id"),
        "continuation_ownership_status": ownership["status"],
        "continuation_ownership_candidates": ownership.get("candidates") or [],
        "continuation_ownership_issue": ownership.get("reason") if ownership["status"] == "ambiguous" else None,
    }


def _read_operations(workspace: Workspace) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if not workspace.operations_root.exists():
        return operations
    for path in sorted(workspace.operations_root.glob("operation_*.json")):
        try:
            data = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            operations.append(
                {
                    "operation_id": None,
                    "status": "record_invalid",
                    "record_path": str(path),
                }
            )
            continue
        if isinstance(data, dict):
            operations.append({**data, "record_path": str(path)})
    return operations


def _rebuild_registry_unlocked(workspace: Workspace) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    missing_manifests: list[str] = []
    workspace.runs_root.mkdir(parents=True, exist_ok=True)
    for child in sorted(workspace.runs_root.iterdir(), key=lambda item: item.name.casefold()):
        if not child.is_dir() or child.name == CONTROL_DIR_NAME:
            continue
        manifest_path = child / "workflow_manifest.json"
        if not manifest_path.is_file():
            missing_manifests.append(str(child))
            continue
        try:
            manifest = load_json(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError):
            missing_manifests.append(str(child))
            continue
        if isinstance(manifest, dict):
            runs.append(_manifest_entry(manifest, child, workspace))
    operations = _read_operations(workspace)
    run_ids = {item["run_id"] for item in runs}
    operation_ids = {item.get("operation_id") for item in operations if item.get("operation_id")}
    missing_operation_runs = [
        {
            "operation_id": item.get("operation_id"),
            "run_id": item.get("run_id"),
            "status": item.get("status"),
            "error": item.get("error"),
        }
        for item in operations
        if item.get("run_id") not in run_ids
    ]
    unregistered_runs = [
        item["run_id"]
        for item in runs
        if item.get("operation_id") and item.get("operation_id") not in operation_ids
    ]
    groups: dict[str, list[str]] = {}
    for run in runs:
        groups.setdefault(str(run["duplicate_group_id"]), []).append(run["run_id"])
    duplicate_groups = [
        {"duplicate_group_id": key, "run_ids": value, "run_count": len(value)}
        for key, value in sorted(groups.items())
        if len(value) > 1
    ]
    registry = {
        "registry_version": REGISTRY_VERSION,
        "generated_at": now_iso(),
        "workspace_id": workspace.workspace_id,
        "project_root": str(workspace.project_root),
        "canonical_runs_root": str(workspace.runs_root),
        "manifest_is_source_of_truth": True,
        "expected_operation_count": len(operations),
        "actual_run_count": len(runs),
        "operations": operations,
        "runs": runs,
        "missing_operation_runs": missing_operation_runs,
        "unregistered_runs": unregistered_runs,
        "run_dirs_missing_manifest": missing_manifests,
        "duplicate_groups": duplicate_groups,
    }
    atomic_write_json(workspace.registry_path, registry)
    return registry


def rebuild_registry(workspace: Workspace) -> dict[str, Any]:
    with registry_lock(workspace):
        registry = _rebuild_registry_unlocked(workspace)
        _append_audit(
            workspace.audit_path,
            {
                "event": "registry_rebuilt",
                "operation_count": registry["expected_operation_count"],
                "run_count": registry["actual_run_count"],
            },
        )
        return registry


def _unofficial_runs(workspace: Workspace) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for manifest_path in workspace.project_root.rglob("workflow_manifest.json"):
        run_dir = manifest_path.parent.resolve(strict=False)
        if run_dir.parent == workspace.runs_root.resolve(strict=False):
            continue
        if is_within(workspace.control_root, run_dir):
            continue
        results.append(
            {
                "run_dir": str(run_dir),
                "manifest_path": str(manifest_path.resolve(strict=False)),
                "source_root": str(run_dir.parent),
            }
        )
    return sorted(results, key=lambda item: item["run_dir"].casefold())


def _engine_copy_candidates(workspace: Workspace, tool_root: Path | None) -> list[str]:
    candidates: set[str] = set()
    for path in workspace.project_root.rglob("workflow_runner.py"):
        if path.parent.name != "workflow":
            continue
        candidate = path.parent.parent.resolve(strict=False)
        if tool_root is not None and _path_key(candidate) == _path_key(tool_root):
            continue
        if (candidate / "layers").is_dir():
            candidates.add(str(candidate))
    named = workspace.project_root / "schema_workflow_engine"
    if named.exists():
        candidates.add(str(named.resolve(strict=False)))
    return sorted(candidates, key=str.casefold)


def _status_counts(registry: dict[str, Any]) -> dict[str, int]:
    counts = {
        "running": 0,
        "completed": 0,
        "failed": 0,
        "waiting_user": 0,
        "aborted": 0,
        "init_failed": 0,
        "other": 0,
    }
    for run in registry.get("runs", []):
        status = str(run.get("status") or "other")
        if status in counts:
            counts[status] += 1
        elif "failed" in status:
            counts["failed"] += 1
        elif "completed" in status:
            counts["completed"] += 1
        elif "waiting" in status:
            counts["waiting_user"] += 1
        else:
            counts["other"] += 1
    counts["init_failed"] = sum(
        1 for item in registry.get("operations", []) if item.get("status") == "init_failed"
    )
    return counts


def inspect_workspace(workspace: Workspace, *, tool_root: Path | None = None) -> dict[str, Any]:
    registry = rebuild_registry(workspace)
    unofficial = _unofficial_runs(workspace)
    engine_copies = _engine_copy_candidates(workspace, tool_root)
    missing_reports = [
        {
            "run_id": item["run_id"],
            "report_path": item.get("report_path"),
            "reason": item.get("report_readiness_reason"),
        }
        for item in registry["runs"]
        if not item.get("report_ready")
    ]
    incomplete_fulfillment = [
        {"run_id": item["run_id"], "workflow_state": item.get("workflow_state")}
        for item in registry["runs"]
        if item.get("fulfillment_configured") and item.get("status") != "completed"
    ]
    legacy_unverified = [
        {"run_id": item["run_id"], "workflow_state": item.get("workflow_state")}
        for item in registry["runs"]
        if not item.get("fulfillment_configured")
        and item.get("workflow_state") == "ready_for_next_action"
    ]
    parent_relations = [
        {
            "run_id": item["run_id"],
            "parent_run_id": item.get("parent_run_id"),
            "relation_type": item.get("relation_type"),
            "operation_id": item.get("operation_id"),
        }
        for item in registry["runs"]
        if item.get("parent_run_id")
    ]
    parent_relations.extend(
        {
            "run_id": item.get("run_id"),
            "parent_run_id": None,
            "relation_type": "continuation",
            "operation_id": item.get("operation_id"),
        }
        for item in registry["operations"]
        if item.get("relation_type") == "continuation"
    )
    continuation_ownership_issues = [
        {
            "run_id": item["run_id"],
            "candidates": item.get("continuation_ownership_candidates") or [],
            "reason": item.get("continuation_ownership_issue"),
        }
        for item in registry["runs"]
        if item.get("continuation_ownership_status") == "ambiguous"
    ]
    recovery_actions: list[str] = []
    if registry["missing_operation_runs"]:
        recovery_actions.append("inspect init_failed or interrupted operation reservations")
    if registry["run_dirs_missing_manifest"]:
        recovery_actions.append("inspect partial RunDirs; do not overwrite or delete them automatically")
    if unofficial:
        recovery_actions.append("run migration-dry-run before any copy or registration")
    if engine_copies:
        recovery_actions.append("treat engine copies as policy violations; do not delete them automatically")
    if continuation_ownership_issues:
        recovery_actions.append(
            "inspect ambiguous continuation owners and perform an explicit approved recovery; do not complete them in bulk"
        )
    if incomplete_fulfillment:
        recovery_actions.append("complete or explicitly block each configured fulfillment contract")
    if legacy_unverified:
        recovery_actions.append("treat legacy ready_for_next_action runs as analysis-ready, not proven complete")
    if not recovery_actions:
        recovery_actions.append("no recovery action required")
    return {
        "inspect_version": "1.0",
        "generated_at": now_iso(),
        "workspace_id": workspace.workspace_id,
        "project_root": str(workspace.project_root),
        "canonical_runs_root": str(workspace.runs_root),
        "expected_operation_count": registry["expected_operation_count"],
        "actual_run_count": registry["actual_run_count"],
        "status_counts": _status_counts(registry),
        "missing_operation_runs": registry["missing_operation_runs"],
        "unregistered_runs": registry["unregistered_runs"],
        "run_dirs_missing_manifest": registry["run_dirs_missing_manifest"],
        "runs_missing_report": missing_reports,
        "runs_incomplete_fulfillment": incomplete_fulfillment,
        "legacy_runs_without_fulfillment_contract": legacy_unverified,
        "unofficial_runs": unofficial,
        "duplicate_groups": registry["duplicate_groups"],
        "parent_relations": parent_relations,
        "continuation_ownership_issues": continuation_ownership_issues,
        "engine_copy_candidates": engine_copies,
        "recovery_actions": recovery_actions,
        "registry_path": str(workspace.registry_path),
    }


def inspection_markdown(report: dict[str, Any]) -> str:
    status = report["status_counts"]
    duplicate_rows = ["| Duplicate group | Runs |", "|---|---|"]
    for item in report["duplicate_groups"]:
        duplicate_rows.append(f"| {item['duplicate_group_id']} | {', '.join(item['run_ids'])} |")
    if len(duplicate_rows) == 2:
        duplicate_rows.append("| none | none |")
    relation_rows = ["| Run | Relation | Parent | Operation |", "|---|---|---|---|"]
    for item in report["parent_relations"]:
        relation_rows.append(
            f"| {item['run_id']} | {item.get('relation_type') or ''} | {item.get('parent_run_id') or ''} | {item.get('operation_id') or ''} |"
        )
    if len(relation_rows) == 2:
        relation_rows.append("| none | none | none | none |")
    ownership_rows = ["| Run | Candidates | Reason |", "|---|---|---|"]
    for item in report["continuation_ownership_issues"]:
        ownership_rows.append(
            f"| {item['run_id']} | {', '.join(item['candidates'])} | {item.get('reason') or ''} |"
        )
    if len(ownership_rows) == 2:
        ownership_rows.append("| none | none | none |")
    lines = [
        "# Workspace Inspect",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- ProjectRoot: `{report['project_root']}`",
        f"- CanonicalRunsRoot: `{report['canonical_runs_root']}`",
        f"- Expected operations: `{report['expected_operation_count']}`",
        f"- Actual runs: `{report['actual_run_count']}`",
        "",
        "## Status Counts",
        "",
        f"- Running: `{status['running']}`",
        f"- Completed: `{status['completed']}`",
        f"- Failed: `{status['failed']}`",
        f"- Waiting user: `{status['waiting_user']}`",
        f"- Aborted: `{status['aborted']}`",
        f"- Init failed: `{status['init_failed']}`",
        f"- Other: `{status['other']}`",
        "",
        "## Completeness",
        "",
        f"- Missing operation runs: `{len(report['missing_operation_runs'])}`",
        f"- Unregistered runs: `{len(report['unregistered_runs'])}`",
        f"- Run directories missing manifest: `{len(report['run_dirs_missing_manifest'])}`",
        f"- Runs with missing or pending report: `{len(report['runs_missing_report'])}`",
        f"- Runs with incomplete fulfillment: `{len(report['runs_incomplete_fulfillment'])}`",
        f"- Legacy completion without contract: `{len(report['legacy_runs_without_fulfillment_contract'])}`",
        f"- Unofficial runs: `{len(report['unofficial_runs'])}`",
        f"- Engine copy candidates: `{len(report['engine_copy_candidates'])}`",
        f"- Ambiguous continuation owners: `{len(report['continuation_ownership_issues'])}`",
        "",
        "## Duplicate Groups",
        "",
        *duplicate_rows,
        "",
        "## Parent And Continuation Relations",
        "",
        *relation_rows,
        "",
        "## Continuation Ownership Issues",
        "",
        *ownership_rows,
        "",
        "## Recovery Actions",
        "",
        *[f"- {item}" for item in report["recovery_actions"]],
        "",
    ]
    return "\n".join(lines)


def write_workspace_inspection(
    workspace: Workspace,
    *,
    tool_root: Path | None = None,
    json_output: Path | None = None,
    markdown_output: Path | None = None,
) -> dict[str, Any]:
    report = inspect_workspace(workspace, tool_root=tool_root)
    json_path = json_output or workspace.control_root / INSPECT_JSON_NAME
    markdown_path = markdown_output or workspace.control_root / INSPECT_REPORT_NAME
    atomic_write_json(json_path, report)
    atomic_write_text(markdown_path, inspection_markdown(report))
    return {**report, "json_report": str(json_path), "markdown_report": str(markdown_path)}


def _hash_path(path: Path) -> tuple[str, int, int]:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest(), 1, path.stat().st_size
    if not path.is_dir():
        raise FileNotFoundError(f"DeliverablePath does not exist: {path}")
    digest = hashlib.sha256()
    count = 0
    size = 0
    for file_path in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = file_path.relative_to(path).as_posix()
        file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        file_size = file_path.stat().st_size
        digest.update(f"{relative}\t{file_size}\t{file_hash}\n".encode("utf-8"))
        count += 1
        size += file_size
    return digest.hexdigest(), count, size


def build_deliverable_entry(
    workspace: Workspace,
    *,
    run_id: str,
    path: str | Path,
    role: str = "project_deliverable",
) -> tuple[Path, dict[str, Any]]:
    run_dir = resolve_existing_run(workspace, run_id=run_id)
    deliverable = Path(path)
    if not deliverable.is_absolute():
        deliverable = workspace.project_root / deliverable
    deliverable = deliverable.resolve(strict=False)
    if not is_within(workspace.project_root, deliverable):
        raise WorkspaceGovernanceError(
            "DELIVERABLE_OUTSIDE_PROJECT",
            "DeliverablePath must stay inside ProjectRoot.",
            path=str(deliverable),
            project_root=str(workspace.project_root),
        )
    if is_within(run_dir, deliverable):
        raise WorkspaceGovernanceError(
            "DELIVERABLE_ROLE_CONFLICT",
            "A project-owned DeliverablePath must be separate from the RunDir.",
            path=str(deliverable),
            run_dir=str(run_dir),
        )
    digest, file_count, total_bytes = _hash_path(deliverable)
    return run_dir, {
        "path_absolute": str(deliverable),
        "path_relative": relative_to_project(workspace.project_root, deliverable),
        "role": role,
        "sha256": digest,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "recorded_at": now_iso(),
    }


def upsert_deliverable_entry(manifest: dict[str, Any], entry: dict[str, Any]) -> None:
    items = [
        item
        for item in list(manifest.get("deliverable_paths") or [])
        if isinstance(item, dict) and item.get("path_relative") != entry["path_relative"]
    ]
    items.append(entry)
    manifest["deliverable_paths"] = items


def register_deliverable(
    workspace: Workspace,
    *,
    run_id: str,
    path: str | Path,
    role: str = "project_deliverable",
) -> dict[str, Any]:
    run_dir, entry = build_deliverable_entry(
        workspace,
        run_id=run_id,
        path=path,
        role=role,
    )
    with run_writer_lock(workspace, run_id, timeout_seconds=10.0):
        manifest_path = run_dir / "workflow_manifest.json"
        manifest = load_json(manifest_path)
        upsert_deliverable_entry(manifest, entry)
        manifest["updated_at"] = now_iso()
        atomic_write_json(manifest_path, manifest)
    rebuild_registry(workspace)
    return entry


def record_continuation_operation(
    workspace: Workspace,
    *,
    run_id: str | None = None,
    run_dir: str | Path | None = None,
    operation_id: str | None = None,
    session_reference: str | None = None,
    note: str | None = None,
    supplemental_input: str | None = None,
    supplemental_input_source: dict[str, Any] | None = None,
    delivery_policy: str = "required",
) -> dict[str, Any]:
    if delivery_policy not in {"required", "internal_only"}:
        raise WorkspaceGovernanceError(
            "CONTINUATION_DELIVERY_POLICY_INVALID",
            "Continuation delivery_policy must be required or internal_only.",
            delivery_policy=delivery_policy,
        )
    selected = resolve_existing_run(workspace, run_id=run_id, run_dir=run_dir)
    manifest_path = selected / "workflow_manifest.json"
    operation_id = operation_id or new_operation_id()
    manifest_reuse = False
    with operation_lock(workspace, operation_id):
        with run_writer_lock(workspace, selected.name, timeout_seconds=10.0):
            manifest = load_json(manifest_path)
            ownership = _continuation_ownership(manifest)
            if ownership["status"] == "ambiguous":
                _apply_continuation_ownership(manifest, ownership)
                manifest["updated_at"] = now_iso()
                atomic_write_json(manifest_path, manifest)
                raise WorkspaceGovernanceError(
                    "CONTINUATION_OWNERSHIP_AMBIGUOUS",
                    "The run has multiple or inconsistent active continuation owners.",
                    run_id=selected.name,
                    active_candidates=ownership["candidates"],
                    current_state="ambiguous",
                    reason=ownership["reason"],
                    recommended_actions=[
                        "inspect the run with workspace-inspect",
                        "perform an explicit approved ownership recovery",
                        "create a branch run for independent work",
                    ],
                )
            active_id = ownership.get("operation_id") if ownership["status"] == "active" else None
            if active_id is not None and str(active_id) != operation_id:
                raise WorkspaceGovernanceError(
                    "CONTINUATION_ALREADY_ACTIVE",
                    "A different continuation OperationId already owns this run.",
                    run_id=selected.name,
                    active_operation_id=active_id,
                    active_status=ownership.get("operation_status"),
                    requested_operation_id=operation_id,
                    recommended_actions=[
                        "wait for the active continuation to reach completed, failed, or aborted",
                        "inspect current ownership with workspace-inspect",
                        "create a branch run for independent work",
                    ],
                )
            raw_text = str((manifest.get("source") or {}).get("raw_text") or "")
            hash_record = manifest.get("input_hash")
            if not isinstance(hash_record, dict) or not hash_record.get("value"):
                hash_record = input_hash_record(raw_text)
            operation, reused = reserve_operation(
                workspace,
                operation_id=operation_id,
                input_hash=hash_record,
                run_name=None,
                session_reference=session_reference,
                relation_type="continuation",
                parent_run_id=None,
                target_run_id=selected.name,
                operation_kind="continuation",
                delivery_policy=delivery_policy,
            )
            entries = [
                item
                for item in manifest.get("continuation_operations", [])
                if isinstance(item, dict)
            ]
            existing_entry = next(
                (item for item in entries if str(item.get("operation_id")) == operation_id),
                None,
            )
            if active_id == operation_id:
                manifest_reuse = True
                delivery_policy_changed = False
                if existing_entry is not None and existing_entry.get("delivery_policy") != delivery_policy:
                    existing_entry["delivery_policy"] = delivery_policy
                    existing_entry["updated_at"] = now_iso()
                    delivery_policy_changed = True
                pointer_changed = (
                    manifest.get("active_continuation_operation_id") != operation_id
                    or manifest.get("continuation_ownership_status") != "active"
                )
                _apply_continuation_ownership(manifest, ownership)
                summary_changed = _sync_active_continuation_summary(
                    manifest,
                    operation_id=operation_id,
                    operation_status=str((existing_entry or {}).get("status") or "running"),
                )
                if pointer_changed or delivery_policy_changed or summary_changed:
                    manifest["updated_at"] = now_iso()
                    atomic_write_json(manifest_path, manifest)
            elif existing_entry is not None and str(existing_entry.get("status")) in TERMINAL_CONTINUATION_STATUSES:
                manifest_reuse = True
                _apply_continuation_ownership(manifest, ownership)
                if existing_entry.get("delivery_policy") != delivery_policy:
                    existing_entry["delivery_policy"] = delivery_policy
                    existing_entry["updated_at"] = now_iso()
                    manifest["updated_at"] = now_iso()
                    atomic_write_json(manifest_path, manifest)
            else:
                timestamp = now_iso()
                if existing_entry is None:
                    manifest.setdefault("continuation_operations", []).append(
                        {
                            "operation_id": operation_id,
                            "timestamp": timestamp,
                            "updated_at": timestamp,
                            "status": "running",
                            "session_reference": session_reference,
                            "note": note,
                            "delivery_policy": delivery_policy,
                        }
                    )
                else:
                    existing_entry["status"] = "running"
                    existing_entry["updated_at"] = timestamp
                    existing_entry["delivery_policy"] = delivery_policy
                manifest["active_continuation_operation_id"] = operation_id
                manifest["continuation_ownership_status"] = "active"
                manifest.pop("continuation_ownership_candidates", None)
                manifest.pop("continuation_ownership_issue", None)
                if manifest.get("status") == "completed":
                    manifest.setdefault("revision_history", []).append(
                        {
                            "timestamp": timestamp,
                            "operation_id": operation_id,
                            "event": "completed_run_continuation_started",
                            "note": note,
                        }
                    )
                manifest["status"] = "running"
                _sync_active_continuation_summary(
                    manifest,
                    operation_id=operation_id,
                    operation_status="running",
                )
                manifest["updated_at"] = timestamp
                atomic_write_json(manifest_path, manifest)
            supplemental_text = str(supplemental_input or "").strip()
            if supplemental_text:
                timestamp = now_iso()
                supplemental_hash = input_hash_record(supplemental_text)
                supplemental_records = [
                    item
                    for item in manifest.get("supplemental_inputs", [])
                    if isinstance(item, dict)
                ]
                already_recorded = any(
                    str(item.get("operation_id")) == operation_id
                    and (item.get("input_hash") or {}).get("value") == supplemental_hash["value"]
                    for item in supplemental_records
                )
                if not already_recorded:
                    supplemental_record = {
                        "supplemental_input_id": f"supplemental_{supplemental_hash['value'][:12]}",
                        "operation_id": operation_id,
                        "recorded_at": timestamp,
                        "text": supplemental_text,
                        "input_hash": supplemental_hash,
                        "note": note,
                    }
                    if supplemental_input_source:
                        supplemental_record["request_source"] = dict(supplemental_input_source)
                    manifest.setdefault("supplemental_inputs", []).append(supplemental_record)
                    manifest["updated_at"] = timestamp
                    atomic_write_json(manifest_path, manifest)
        if not reused or operation.get("status") == "reserved":
            operation = update_operation(workspace, operation_id, "running")
    rebuild_registry(workspace)
    return {
        "operation": operation,
        "run_id": selected.name,
        "run_dir": str(selected),
        "delivery_policy": delivery_policy,
        "idempotent_reuse": reused or manifest_reuse,
    }


def abort_continuation_operation(
    workspace: Workspace,
    *,
    run_id: str | None = None,
    run_dir: str | Path | None = None,
    operation_id: str,
    reason: str,
    approved: bool = False,
) -> dict[str, Any]:
    if not approved:
        raise WorkspaceGovernanceError(
            "CONTINUATION_ABORT_APPROVAL_REQUIRED",
            "Aborting a continuation operation requires explicit approval.",
            operation_id=operation_id,
        )
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise WorkspaceGovernanceError(
            "CONTINUATION_ABORT_REASON_REQUIRED",
            "A non-empty abort reason is required.",
            operation_id=operation_id,
        )
    selected = resolve_existing_run(workspace, run_id=run_id, run_dir=run_dir)
    manifest_path = selected / "workflow_manifest.json"
    timestamp = now_iso()
    idempotent = False
    with operation_lock(workspace, operation_id):
        with run_writer_lock(workspace, selected.name, timeout_seconds=10.0):
            manifest = load_json(manifest_path)
            entries = [
                item
                for item in manifest.get("continuation_operations", [])
                if isinstance(item, dict)
            ]
            target = next(
                (item for item in entries if str(item.get("operation_id") or "") == operation_id),
                None,
            )
            if target is None:
                raise WorkspaceGovernanceError(
                    "CONTINUATION_OPERATION_NOT_FOUND",
                    "The requested continuation OperationId is not registered on this run.",
                    run_id=selected.name,
                    requested_operation_id=operation_id,
                )
            ownership = _continuation_ownership(manifest)
            if ownership["status"] == "ambiguous":
                raise WorkspaceGovernanceError(
                    "CONTINUATION_OWNERSHIP_AMBIGUOUS",
                    "Ambiguous continuation ownership requires a separate recovery review.",
                    run_id=selected.name,
                    requested_operation_id=operation_id,
                    active_candidates=ownership.get("candidates", []),
                )
            active_id = ownership.get("operation_id") if ownership["status"] == "active" else None
            current_status = str(target.get("status") or "running")
            if current_status == "aborted":
                idempotent = True
            elif current_status in TERMINAL_CONTINUATION_STATUSES:
                raise WorkspaceGovernanceError(
                    "CONTINUATION_OPERATION_ALREADY_TERMINAL",
                    "The requested continuation operation is already terminal and cannot be aborted.",
                    run_id=selected.name,
                    requested_operation_id=operation_id,
                    current_status=current_status,
                )
            elif active_id != operation_id:
                raise WorkspaceGovernanceError(
                    "CONTINUATION_ABORT_OWNER_MISMATCH",
                    "Only the exact active continuation owner can be aborted.",
                    run_id=selected.name,
                    active_operation_id=active_id,
                    requested_operation_id=operation_id,
                )
            else:
                target["status"] = "aborted"
                target["updated_at"] = timestamp
                target["abort_reason"] = reason_text
                target["aborted_at"] = timestamp
                manifest["active_continuation_operation_id"] = None
                manifest["continuation_ownership_status"] = "idle"
                manifest.pop("continuation_ownership_candidates", None)
                manifest.pop("continuation_ownership_issue", None)
                restored_status = _status_from_run(manifest, selected)
                manifest["status"] = restored_status
                manifest["updated_at"] = timestamp
                manifest["last_command"] = "abort-continuation"
                manifest.setdefault("revision_history", []).append(
                    {
                        "timestamp": timestamp,
                        "operation_id": operation_id,
                        "event": "continuation_aborted",
                        "reason": reason_text,
                    }
                )
                atomic_write_json(manifest_path, manifest)
        if not idempotent:
            update_operation(
                workspace,
                operation_id,
                "aborted",
                error={
                    "timestamp": timestamp,
                    "type": "ContinuationAborted",
                    "message": reason_text,
                },
            )
    rebuild_registry(workspace)
    return {
        "status": "aborted",
        "run_id": selected.name,
        "operation_id": operation_id,
        "reason": reason_text,
        "idempotent_reuse": idempotent,
    }


def workspace_for_governed_run(run_dir: Path) -> Workspace | None:
    manifest_path = run_dir / "workflow_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if not manifest.get("project_root_absolute") and not manifest.get("run_dir_relative"):
        return None
    resolved = resolve_governed_project_root(current_run_dir=run_dir, manifest=manifest)
    workspace = load_workspace(resolved.project_root)
    resolve_existing_run(workspace, run_dir=run_dir)
    return workspace


@contextmanager
def governed_writer_context(
    run_dir: Path,
    *,
    timeout_seconds: float = 0.0,
    stale_after_seconds: float = 120.0,
) -> Iterator[Workspace | None]:
    workspace = workspace_for_governed_run(run_dir)
    context = (
        run_writer_lock(
            workspace,
            run_dir.name,
            timeout_seconds=timeout_seconds,
            stale_after_seconds=stale_after_seconds,
        )
        if workspace is not None
        else nullcontext()
    )
    with context:
        yield workspace


def refresh_governed_run(
    workspace: Workspace,
    run_dir: Path,
    *,
    command: str,
    workflow_state: str | None,
) -> None:
    manifest_path = run_dir / "workflow_manifest.json"
    manifest = load_json(manifest_path)
    timestamp = now_iso()
    manifest["updated_at"] = timestamp
    manifest["last_command"] = command
    status_path = run_dir / "workflow_status.json"
    if status_path.exists():
        try:
            status_snapshot = load_json(status_path)
        except (OSError, ValueError, json.JSONDecodeError):
            status_snapshot = {}
        if isinstance(status_snapshot, dict):
            status_summary = status_snapshot.get("summary")
            if isinstance(status_summary, dict):
                manifest["summary"] = status_summary
    if workflow_state:
        lowered = workflow_state.casefold()
        if "completed" in lowered:
            status = "completed"
        elif "waiting" in lowered:
            status = "waiting_user"
        elif "failed" in lowered or "invalid" in lowered:
            status = "failed"
        elif "aborted" in lowered or "interrupted" in lowered:
            status = "aborted"
        else:
            status = "running"
        manifest["status"] = status
    mapped = str(manifest.get("status") or "running")
    continuation_status = (
        mapped if mapped in {"running", "waiting_user", "completed", "failed", "aborted"} else "running"
    )
    continuation_id, ownership = _set_active_continuation_status(manifest, continuation_status, timestamp)
    if ownership["status"] == "ambiguous":
        manifest["status"] = "continuation_ownership_ambiguous"
    atomic_write_json(manifest_path, manifest)
    if continuation_id:
        update_operation(workspace, continuation_id, continuation_status)
    elif ownership["status"] == "idle":
        operation_id = manifest.get("operation_id")
        if operation_id:
            if mapped not in OPERATION_STATUSES:
                mapped = "running"
            update_operation(workspace, str(operation_id), mapped)
    rebuild_registry(workspace)


def record_governed_run_failure(
    workspace: Workspace,
    run_dir: Path,
    *,
    command: str,
    error: BaseException,
) -> None:
    with run_writer_lock(workspace, run_dir.name, timeout_seconds=10.0):
        manifest_path = run_dir / "workflow_manifest.json"
        manifest = load_json(manifest_path)
        timestamp = now_iso()
        stage_by_command = {
            "validate-route": "route_validation",
            "validate-direction": "direction_validation",
            "validate-context": "context_validation",
            "validate-fulfillment": "fulfillment_validation",
        }
        error_surface = classify_error_surface(
            stage=stage_by_command.get(command, "workflow_execution"),
            exception=error,
        )
        error_record = {
            "timestamp": timestamp,
            "command": command,
            "type": type(error).__name__,
            "message": str(error),
            "error_surface": error_surface,
        }
        manifest["updated_at"] = timestamp
        manifest["last_command"] = command
        manifest.setdefault("failure_history", []).append(error_record)
        manifest.setdefault("summary", {})["error_surface"] = error_surface
        continuation_id, ownership = _set_active_continuation_status(manifest, "failed", timestamp)
        manifest["status"] = "failed" if ownership["status"] != "ambiguous" else "continuation_ownership_ambiguous"
        atomic_write_json(manifest_path, manifest)
    if continuation_id:
        update_operation(workspace, continuation_id, "failed", error=error_record)
    elif ownership["status"] == "idle":
        operation_id = manifest.get("operation_id")
        if operation_id:
            update_operation(workspace, str(operation_id), "failed", error=error_record)
    rebuild_registry(workspace)
