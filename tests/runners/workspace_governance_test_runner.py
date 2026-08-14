from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "agents" / "agent.md").exists() and (
            candidate / "engine" / "python" / "layers"
        ).exists():
            return candidate
    return start


PROJECT_ROOT = find_project_root(SCRIPT_DIR)
ENGINE_DIR = PROJECT_ROOT / "engine" / "python"
WORKFLOW_DIR = ENGINE_DIR / "workflow"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

import workflow_runner
from shared import workspace_governance as governance
from shared import workspace_migration
from shared.run_identity import unique_run_dir


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class GovernanceRegressionTests(unittest.TestCase):
    artifact_root: Path

    def project_path(self, name: str) -> Path:
        root = self.artifact_root / "workspaces" / name
        root.mkdir(parents=True, exist_ok=False)
        return root

    def bootstrap(self, name: str) -> governance.Workspace:
        root = self.project_path(name)
        return governance.bootstrap_workspace(project_root=root, tool_root=PROJECT_ROOT)

    def initialize(
        self,
        workspace: governance.Workspace,
        *,
        operation_id: str,
        text: str,
        run_name: str = "governance-test",
        parent_run_id: str | None = None,
        relation_type: str = "independent",
    ) -> dict[str, Any]:
        return workflow_runner.initialize_governed_workflow(
            text=text,
            project_root=str(workspace.project_root),
            output=None,
            operation_id=operation_id,
            run_name=run_name,
            source_files=[],
            session_reference=f"test:{operation_id}",
            relation_type=relation_type,
            parent_run_id=parent_run_id,
        )

    def manifest(self, workspace: governance.Workspace, run_id: str) -> dict[str, Any]:
        return load_json(workspace.runs_root / run_id / "workflow_manifest.json")

    def canonical_run_dirs(self, workspace: governance.Workspace) -> list[Path]:
        return sorted(
            [item for item in workspace.runs_root.iterdir() if item.is_dir() and item.name != governance.CONTROL_DIR_NAME]
        )

    def test_01_empty_bootstrap_and_initialization_lock(self) -> None:
        root = self.project_path("01_empty_260711_shape")

        def bootstrap_once(_: int) -> governance.Workspace:
            return governance.bootstrap_workspace(session_cwd=root, tool_root=PROJECT_ROOT)

        with ThreadPoolExecutor(max_workers=6) as pool:
            workspaces = list(pool.map(bootstrap_once, range(6)))
        self.assertEqual(1, len({item.workspace_id for item in workspaces}))
        workspace = workspaces[0]
        self.assertTrue(workspace.config_path.is_file())
        self.assertTrue(workspace.runs_root.is_dir())
        self.assertEqual([], self.canonical_run_dirs(workspace))
        with self.assertRaises(governance.WorkspaceGovernanceError):
            governance.bootstrap_workspace(project_root=PROJECT_ROOT, tool_root=PROJECT_ROOT)

    def test_02_child_session_discovers_same_project_root(self) -> None:
        workspace = self.bootstrap("02_ancestor_discovery")
        child = workspace.project_root / "assets" / "references"
        child.mkdir(parents=True)
        discovered = governance.resolve_workspace(session_cwd=child)
        self.assertEqual(workspace.project_root, discovered.project_root)
        result = workflow_runner.initialize_governed_workflow(
            text="child cwd input",
            project_root=None,
            output=None,
            operation_id="op-child-cwd",
            run_name="child-cwd",
            source_files=[],
            session_reference="test:child-cwd",
            relation_type="independent",
            parent_run_id=None,
            session_cwd=child,
        )
        self.assertEqual(workspace.runs_root, Path(result["run_dir"]).parent)

    def test_03_eight_parallel_operations_create_exactly_eight_runs(self) -> None:
        workspace = self.bootstrap("03_parallel_eight")

        def create(index: int) -> str:
            return self.initialize(
                workspace,
                operation_id=f"op-parallel-{index}",
                text=f"parallel input {index}",
                run_name=f"parallel-{index}",
            )["run_id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            run_ids = list(pool.map(create, range(8)))
        registry = governance.rebuild_registry(workspace)
        self.assertEqual(8, len(set(run_ids)))
        self.assertEqual(8, len(self.canonical_run_dirs(workspace)))
        self.assertEqual(8, registry["expected_operation_count"])
        self.assertEqual(8, registry["actual_run_count"])
        self.assertEqual([], registry["missing_operation_runs"])

    def test_04_same_input_different_operations_are_independent_duplicates(self) -> None:
        workspace = self.bootstrap("04_same_input")
        first = self.initialize(workspace, operation_id="op-same-a", text="same normalized input")
        second = self.initialize(workspace, operation_id="op-same-b", text="same normalized input")
        self.assertNotEqual(first["run_id"], second["run_id"])
        first_manifest = self.manifest(workspace, first["run_id"])
        second_manifest = self.manifest(workspace, second["run_id"])
        self.assertEqual(first_manifest["input_hash"], second_manifest["input_hash"])
        self.assertEqual(first_manifest["duplicate_group_id"], second_manifest["duplicate_group_id"])
        self.assertEqual("independent", first_manifest["relation_type"])
        self.assertEqual("independent", second_manifest["relation_type"])

    def test_05_same_operation_id_is_idempotent(self) -> None:
        workspace = self.bootstrap("05_idempotency")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: self.initialize(
                        workspace,
                        operation_id="op-idempotent",
                        text="idempotent input",
                    ),
                    range(4),
                )
            )
        self.assertEqual(1, len({item["run_id"] for item in results}))
        self.assertEqual(3, sum(bool(item["idempotent_reuse"]) for item in results))
        self.assertEqual(1, len(self.canonical_run_dirs(workspace)))

    def test_06_runless_new_request_never_modifies_existing_run(self) -> None:
        workspace = self.bootstrap("06_new_request")
        first = self.initialize(workspace, operation_id="op-new-a", text="first request")
        manifest_path = workspace.runs_root / first["run_id"] / "workflow_manifest.json"
        before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        second = self.initialize(workspace, operation_id="op-new-b", text="second request")
        after = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(before, after)

    def test_07_explicit_continuation_updates_exact_run(self) -> None:
        workspace = self.bootstrap("07_continuation")
        first = self.initialize(workspace, operation_id="op-cont-source", text="source one")
        second = self.initialize(workspace, operation_id="op-cont-other", text="source two")
        other_path = workspace.runs_root / second["run_id"] / "workflow_manifest.json"
        other_before = hashlib.sha256(other_path.read_bytes()).hexdigest()
        result = governance.record_continuation_operation(
            workspace,
            run_id=first["run_id"],
            operation_id="op-explicit-continuation",
            session_reference="test:continuation",
            note="continue the selected run only",
        )
        self.assertEqual(first["run_id"], result["run_id"])
        first_manifest = self.manifest(workspace, first["run_id"])
        self.assertIn(
            "op-explicit-continuation",
            {item["operation_id"] for item in first_manifest["continuation_operations"]},
        )
        continuation = next(
            item
            for item in first_manifest["continuation_operations"]
            if item["operation_id"] == "op-explicit-continuation"
        )
        self.assertEqual("required", continuation["delivery_policy"])
        self.assertEqual("required", result["delivery_policy"])
        self.assertEqual(other_before, hashlib.sha256(other_path.read_bytes()).hexdigest())
        inspection = governance.inspect_workspace(workspace, tool_root=PROJECT_ROOT)
        self.assertTrue(any(item["relation_type"] == "continuation" for item in inspection["parent_relations"]))

    def test_07b_terminal_legacy_continuation_backfills_delivery_policy(self) -> None:
        workspace = self.bootstrap("07b_continuation_backfill")
        created = self.initialize(workspace, operation_id="op-backfill-source", text="source")
        operation_id = "op-terminal-legacy"
        governance.record_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id=operation_id,
            session_reference="session-backfill",
        )

        manifest_path = workspace.runs_root / created["run_id"] / "workflow_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(item for item in manifest["continuation_operations"] if item["operation_id"] == operation_id)
        entry["status"] = "completed"
        entry.pop("delivery_policy", None)
        manifest["status"] = "completed"
        manifest["active_continuation_operation_id"] = None
        manifest["continuation_ownership_status"] = "idle"
        governance.atomic_write_json(manifest_path, manifest)

        operation_path = governance.operation_path(workspace, operation_id)
        operation = json.loads(operation_path.read_text(encoding="utf-8"))
        operation["status"] = "completed"
        operation.pop("delivery_policy", None)
        governance.atomic_write_json(operation_path, operation)

        repeated = governance.record_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id=operation_id,
            session_reference="session-backfill",
            delivery_policy="required",
        )

        self.assertTrue(repeated["idempotent_reuse"])
        repaired_manifest = self.manifest(workspace, created["run_id"])
        repaired_entry = next(item for item in repaired_manifest["continuation_operations"] if item["operation_id"] == operation_id)
        self.assertEqual("required", repaired_entry["delivery_policy"])
        repaired_operation = json.loads(operation_path.read_text(encoding="utf-8"))
        self.assertEqual("required", repaired_operation["delivery_policy"])

    def test_08_branch_creates_new_run_with_parent(self) -> None:
        workspace = self.bootstrap("08_branch")
        parent = self.initialize(workspace, operation_id="op-parent", text="parent input")
        parent_path = workspace.runs_root / parent["run_id"] / "workflow_manifest.json"
        before = hashlib.sha256(parent_path.read_bytes()).hexdigest()
        branch = self.initialize(
            workspace,
            operation_id="op-branch",
            text="branch experiment",
            parent_run_id=parent["run_id"],
        )
        self.assertNotEqual(parent["run_id"], branch["run_id"])
        self.assertEqual(parent["run_id"], branch["parent_run_id"])
        self.assertEqual("branch", branch["relation_type"])
        self.assertEqual(before, hashlib.sha256(parent_path.read_bytes()).hexdigest())

    def test_09_writer_lock_conflict_and_stale_recovery(self) -> None:
        workspace = self.bootstrap("09_locks")
        result = self.initialize(workspace, operation_id="op-lock", text="lock input")
        active = governance.run_writer_lock(workspace, result["run_id"], timeout_seconds=0.0)
        with active:
            contender = governance.run_writer_lock(workspace, result["run_id"], timeout_seconds=0.0)
            with self.assertRaises(governance.WorkspaceLockError) as captured:
                contender.acquire()
            self.assertIn("branch", " ".join(captured.exception.details["recommended_actions"]))
        stale = governance.run_writer_lock(
            workspace,
            result["run_id"],
            timeout_seconds=0.0,
            stale_after_seconds=0.0,
        )
        governance.atomic_write_json(
            stale.path,
            {
                "lock_id": "stale-owner",
                "scope": "run_writer",
                "pid": -1,
                "created_at": "2000-01-01T00:00:00+00:00",
                "heartbeat_at": "2000-01-01T00:00:00+00:00",
            },
        )
        with stale:
            self.assertTrue(stale.acquired)
        self.assertTrue(list(stale.path.parent.glob(f"{stale.path.name}.stale.*")))
        self.assertIn("stale_lock_recovered", workspace.audit_path.read_text(encoding="utf-8"))

    def test_10_registry_rebuild_matches_manifests_and_reads_legacy(self) -> None:
        workspace = self.bootstrap("10_registry")
        for index in range(3):
            self.initialize(workspace, operation_id=f"op-registry-{index}", text=f"registry input {index}")
        legacy_dir = workspace.runs_root / "legacy-v01-run"
        legacy_dir.mkdir()
        governance.atomic_write_json(
            legacy_dir / "workflow_manifest.json",
            {
                "workflow_version": "0.5.0",
                "run_id": legacy_dir.name,
                "created_at": "2026-01-01T00:00:00",
                "run_dir": str(legacy_dir),
                "source": {"raw_text": "legacy input", "source_files": []},
                "paths": {},
            },
        )
        governance.atomic_write_json(workspace.registry_path, {"corrupt": True})
        registry = governance.rebuild_registry(workspace)
        manifest_ids = {
            load_json(path)["run_id"] for path in workspace.runs_root.glob("*/workflow_manifest.json")
        }
        self.assertEqual(manifest_ids, {item["run_id"] for item in registry["runs"]})
        self.assertEqual(4, registry["actual_run_count"])
        self.assertEqual([], registry["run_dirs_missing_manifest"])
        inventory = workspace_migration.inventory_workspace(workspace.project_root)
        dry_run = workspace_migration.migration_dry_run(inventory, project_root=workspace.project_root)
        self.assertEqual(4, inventory["observed"]["workflow_manifest_count"])
        self.assertTrue(dry_run["safety"]["source_unchanged_during_dry_run"])
        self.assertFalse(dry_run["safety"]["copy_performed"])

    def test_11_unofficial_output_root_is_rejected(self) -> None:
        workspace = self.bootstrap("11_unofficial_root")
        unofficial = workspace.project_root / "schema_workflows"
        with self.assertRaises(governance.WorkspaceGovernanceError) as captured:
            governance.validate_canonical_output(workspace, unofficial)
        self.assertEqual("UNOFFICIAL_OUTPUT_ROOT", captured.exception.code)
        self.assertFalse(unofficial.exists())
        other = self.bootstrap("11_other_project")
        foreign = self.initialize(other, operation_id="op-foreign", text="foreign project run")
        with self.assertRaises(governance.WorkspaceGovernanceError) as foreign_error:
            governance.resolve_existing_run(workspace, run_dir=foreign["run_dir"])
        self.assertEqual("CROSS_PROJECT_RUN_REJECTED", foreign_error.exception.code)

    def test_12_engine_copy_zero_and_deliverable_hash_recorded(self) -> None:
        workspace = self.bootstrap("12_engine_deliverable")
        result = self.initialize(workspace, operation_id="op-engine", text="engine boundary")
        deliverable = workspace.project_root / "src" / "result.txt"
        deliverable.parent.mkdir()
        deliverable.write_text("project-owned result\n", encoding="utf-8")
        entry = governance.register_deliverable(
            workspace,
            run_id=result["run_id"],
            path=deliverable,
        )
        inspection = governance.inspect_workspace(workspace, tool_root=PROJECT_ROOT)
        self.assertEqual([], inspection["engine_copy_candidates"])
        self.assertFalse((workspace.project_root / "schema_workflow_engine").exists())
        self.assertEqual("src/result.txt", entry["path_relative"])
        self.assertEqual(hashlib.sha256(deliverable.read_bytes()).hexdigest(), entry["sha256"])

    def test_13_failure_and_init_failed_operations_are_visible(self) -> None:
        workspace = self.bootstrap("13_failures")
        engine = governance.engine_identity(
            PROJECT_ROOT,
            version=workflow_runner.WORKFLOW_VERSION,
            entrypoint=Path(workflow_runner.__file__),
        )

        def fail_builder(_: str) -> dict[str, Any]:
            raise RuntimeError("intentional initialization failure")

        with self.assertRaises(RuntimeError):
            governance.initialize_operation_run(
                workspace,
                text="init failure input",
                operation_id="op-init-failed",
                run_name="init-failed",
                session_reference="test:init-failed",
                relation_type="independent",
                parent_run_id=None,
                engine=engine,
                builder=fail_builder,
            )
        operation = load_json(governance.operation_path(workspace, "op-init-failed"))
        self.assertEqual("init_failed", operation["status"])
        normal = self.initialize(workspace, operation_id="op-runtime-failed", text="runtime failure input")
        normal_dir = workspace.runs_root / normal["run_id"]
        governance.record_governed_run_failure(
            workspace,
            normal_dir,
            command="test-command",
            error=RuntimeError("intentional runtime failure"),
        )
        inspection = governance.inspect_workspace(workspace, tool_root=PROJECT_ROOT)
        self.assertEqual(1, inspection["status_counts"]["init_failed"])
        self.assertEqual(1, inspection["status_counts"]["failed"])
        self.assertTrue(any(item["operation_id"] == "op-init-failed" for item in inspection["missing_operation_runs"]))

    def test_14_windows_path_length_regression(self) -> None:
        base = self.artifact_root / "p14"
        target_project_length = 108
        component_length = max(0, min(24, target_project_length - len(str(base)) - 1))
        root = base / ("p" * component_length) if component_length else base
        root.mkdir(parents=True)
        workspace = governance.bootstrap_workspace(project_root=root, tool_root=PROJECT_ROOT)
        result = self.initialize(
            workspace,
            operation_id="op-path-length",
            text="windows path length regression",
            run_name="very-long-run-name-" * 40,
        )
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "workflow_manifest.json").is_file())
        self.assertLessEqual(len(result["run_id"]), 64)
        self.assertLess(len(str(run_dir / "workflow_manifest.json")), 260)

    def test_15_source_internal_outputs_are_rejected_without_mutation(self) -> None:
        source = self.project_path("15_read_only_source")
        (source / "seed.txt").write_text("read-only inventory seed\n", encoding="utf-8")
        before = workspace_migration.tree_snapshot(source)
        inventory = workspace_migration.inventory_workspace(source)
        report = workspace_migration.migration_dry_run(inventory, project_root=source)
        inventory_output = source / "reports" / "inventory.json"
        migration_json = source / "reports" / "migration.json"
        migration_markdown = source / "reports" / "migration.md"

        with self.assertRaises(governance.WorkspaceGovernanceError) as inventory_error:
            workspace_migration.write_inventory(inventory_output, inventory)
        self.assertEqual("SOURCE_OUTPUT_PATH_FORBIDDEN", inventory_error.exception.code)
        with self.assertRaises(governance.WorkspaceGovernanceError) as migration_error:
            workspace_migration.write_migration_report(
                json_path=migration_json,
                markdown_path=migration_markdown,
                report=report,
            )
        self.assertEqual("SOURCE_OUTPUT_PATH_FORBIDDEN", migration_error.exception.code)
        after = workspace_migration.tree_snapshot(source)
        self.assertEqual(before, after)
        self.assertFalse(inventory_output.exists())
        self.assertFalse(migration_json.exists())
        self.assertFalse(migration_markdown.exists())

    def test_16_operation_contract_rejects_relation_parent_and_target_changes(self) -> None:
        workspace = self.bootstrap("16_operation_contract")
        original = self.initialize(
            workspace,
            operation_id="op-immutable",
            text="immutable operation input",
        )
        parent = self.initialize(workspace, operation_id="op-contract-parent", text="parent input")
        other = self.initialize(workspace, operation_id="op-contract-other", text="other input")

        with self.assertRaises(governance.WorkspaceGovernanceError) as relation_error:
            self.initialize(
                workspace,
                operation_id="op-immutable",
                text="immutable operation input",
                relation_type="comparison",
            )
        self.assertEqual("OPERATION_CONTRACT_MISMATCH", relation_error.exception.code)
        self.assertIn("relation_type", relation_error.exception.details["mismatches"])

        with self.assertRaises(governance.WorkspaceGovernanceError) as parent_error:
            self.initialize(
                workspace,
                operation_id="op-immutable",
                text="immutable operation input",
                parent_run_id=parent["run_id"],
            )
        self.assertEqual("OPERATION_CONTRACT_MISMATCH", parent_error.exception.code)
        self.assertIn("parent_run_id", parent_error.exception.details["mismatches"])

        with self.assertRaises(governance.WorkspaceGovernanceError) as kind_error:
            governance.record_continuation_operation(
                workspace,
                run_id=original["run_id"],
                operation_id="op-immutable",
            )
        self.assertEqual("OPERATION_CONTRACT_MISMATCH", kind_error.exception.code)
        self.assertIn("operation_kind", kind_error.exception.details["mismatches"])

        governance.record_continuation_operation(
            workspace,
            run_id=original["run_id"],
            operation_id="op-target-immutable",
        )
        with self.assertRaises(governance.WorkspaceGovernanceError) as target_error:
            governance.record_continuation_operation(
                workspace,
                run_id=other["run_id"],
                operation_id="op-target-immutable",
            )
        self.assertEqual("OPERATION_CONTRACT_MISMATCH", target_error.exception.code)
        self.assertIn("target_run_id", target_error.exception.details["mismatches"])

    def test_17_dirty_engine_identity_includes_commit_and_python_fingerprint(self) -> None:
        repository = self.project_path("17_dirty_engine")
        entrypoint = repository / "workflow" / "runner.py"
        shared_file = repository / "shared" / "governance.py"
        layer_file = repository / "layers" / "layer.py"
        for path, content in (
            (entrypoint, "VALUE = 'clean'\n"),
            (shared_file, "SHARED = True\n"),
            (layer_file, "LAYER = 1\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        def git(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                capture_output=True,
                text=True,
            )

        git("init", "-q")
        git("config", "user.email", "governance-test@example.invalid")
        git("config", "user.name", "Governance Test")
        git("add", ".")
        git("commit", "-q", "-m", "clean engine")
        clean = governance.engine_identity(repository, version="test", entrypoint=entrypoint)
        entrypoint.write_text("VALUE = 'dirty'\n", encoding="utf-8")
        dirty = governance.engine_identity(repository, version="test", entrypoint=entrypoint)

        self.assertFalse(clean["engine_git_dirty"])
        self.assertTrue(dirty["engine_git_dirty"])
        self.assertEqual(clean["engine_git_commit"], dirty["engine_git_commit"])
        self.assertRegex(str(dirty["engine_git_commit"]), r"^[0-9a-f]{40}$")
        self.assertIn("+dirty:", dirty["engine_commit_or_fingerprint"])
        self.assertNotEqual(clean["engine_commit_or_fingerprint"], dirty["engine_commit_or_fingerprint"])
        self.assertNotEqual(
            clean["engine_python_fingerprint"]["value"],
            dirty["engine_python_fingerprint"]["value"],
        )
        self.assertEqual(3, dirty["engine_python_fingerprint"]["file_count"])

    def test_18_generic_parent_derives_child_project_name(self) -> None:
        parent = self.project_path("18_generic_parent")
        (parent / "parent-marker.txt").write_text("generic parent\n", encoding="utf-8")
        run_name = "Lunar Notes Project"
        expected = parent / governance.derive_project_slug(run_name=run_name)
        workspace = governance.bootstrap_workspace(
            session_cwd=parent,
            tool_root=PROJECT_ROOT,
            run_name=run_name,
            input_text="organize lunar research notes",
        )
        self.assertEqual(expected.resolve(), workspace.project_root.resolve())
        self.assertFalse((parent / governance.WORKSPACE_CONFIG_NAME).exists())
        self.assertTrue((expected / governance.WORKSPACE_CONFIG_NAME).is_file())
        config = load_json(expected / governance.WORKSPACE_CONFIG_NAME)
        self.assertEqual("derived_child_slug", config["project_root_selection"])

        input_parent = self.project_path("18_input_fallback")
        (input_parent / "parent-marker.txt").write_text("generic parent\n", encoding="utf-8")
        input_text = "Build a comet observation journal"
        input_expected = input_parent / governance.derive_project_slug(input_text=input_text)
        fallback = governance.bootstrap_workspace(
            session_cwd=input_parent,
            tool_root=PROJECT_ROOT,
            input_text=input_text,
        )
        self.assertEqual(input_expected.resolve(), fallback.project_root.resolve())

    def test_19_continuation_operation_lifecycle_completes_and_fails(self) -> None:
        workspace = self.bootstrap("19_continuation_lifecycle")
        initial = self.initialize(workspace, operation_id="op-lifecycle-initial", text="lifecycle input")
        run_dir = workspace.runs_root / initial["run_id"]
        governance.record_continuation_operation(
            workspace,
            run_id=initial["run_id"],
            operation_id="op-lifecycle-complete",
        )
        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="test-wait",
            workflow_state="continuation_waiting_user",
        )
        waiting = load_json(governance.operation_path(workspace, "op-lifecycle-complete"))
        self.assertEqual("waiting_user", waiting["status"])
        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="test-complete",
            workflow_state="continuation_completed",
        )
        completed = load_json(governance.operation_path(workspace, "op-lifecycle-complete"))
        self.assertEqual("completed", completed["status"])

        governance.record_continuation_operation(
            workspace,
            run_id=initial["run_id"],
            operation_id="op-lifecycle-failed",
        )
        governance.record_governed_run_failure(
            workspace,
            run_dir,
            command="test-failure",
            error=RuntimeError("continuation failed intentionally"),
        )
        failed = load_json(governance.operation_path(workspace, "op-lifecycle-failed"))
        original = load_json(governance.operation_path(workspace, "op-lifecycle-initial"))
        self.assertEqual("failed", failed["status"])
        self.assertEqual("running", original["status"])
        self.assertEqual(
            {"op-lifecycle-complete": "completed", "op-lifecycle-failed": "failed"},
            {
                item["operation_id"]: item["status"]
                for item in self.manifest(workspace, initial["run_id"])["continuation_operations"]
            },
        )

    def test_20_atomic_replace_retries_transient_permission_errors(self) -> None:
        root = self.project_path("a20")
        target = root / "workflow_manifest.json"
        target.write_text("old target\n", encoding="utf-8")
        real_replace = governance.os.replace
        attempts: list[int] = []

        def flaky_replace(source: str | Path, destination: str | Path) -> None:
            attempts.append(len(attempts) + 1)
            if len(attempts) <= 3:
                raise PermissionError(13, "simulated Windows sharing violation", str(destination))
            real_replace(source, destination)

        with mock.patch.object(governance.os, "replace", side_effect=flaky_replace):
            governance.atomic_write_text(
                target,
                "new target\n",
                replace_timeout_seconds=1.0,
                replace_max_attempts=6,
                initial_backoff_seconds=0.001,
                max_backoff_seconds=0.002,
            )
        self.assertEqual([1, 2, 3, 4], attempts)
        self.assertEqual("new target\n", target.read_text(encoding="utf-8"))
        self.assertEqual([], list(root.glob(".tmp_*")))

    def test_21_atomic_replace_exhaustion_preserves_target_and_cleans_temp(self) -> None:
        root = self.project_path("a21")
        target = root / "workflow_manifest.json"
        target.write_text("preserve me\n", encoding="utf-8")
        sharing_error = PermissionError(13, "persistent Windows sharing violation", str(target))
        with mock.patch.object(governance.os, "replace", side_effect=sharing_error):
            with self.assertRaises(governance.WorkspaceGovernanceError) as captured:
                governance.atomic_write_text(
                    target,
                    "must not replace\n",
                    replace_timeout_seconds=1.0,
                    replace_max_attempts=3,
                    initial_backoff_seconds=0.001,
                    max_backoff_seconds=0.002,
                )
        self.assertEqual("ATOMIC_REPLACE_RETRY_EXHAUSTED", captured.exception.code)
        self.assertEqual(3, captured.exception.details["replace_attempts"])
        self.assertEqual(2, captured.exception.details["retry_count"])
        self.assertEqual(str(target.resolve()), captured.exception.details["target_path"])
        self.assertIsInstance(captured.exception.__cause__, PermissionError)
        self.assertEqual("preserve me\n", target.read_text(encoding="utf-8"))
        self.assertEqual([], list(root.glob(".tmp_*")))

        with mock.patch.object(governance.os, "replace", side_effect=FileNotFoundError("not retryable")):
            with self.assertRaises(FileNotFoundError):
                governance.atomic_write_text(target, "still not replaced\n")
        self.assertEqual("preserve me\n", target.read_text(encoding="utf-8"))
        self.assertEqual([], list(root.glob(".tmp_*")))

    def test_22_governed_init_has_one_final_manifest_replace(self) -> None:
        workspace = self.bootstrap("a22")
        real_replace = governance.os.replace
        manifest_replaces: list[str] = []

        def observe_replace(source: str | Path, destination: str | Path) -> None:
            destination_path = Path(destination)
            if destination_path.name == "workflow_manifest.json":
                manifest_replaces.append(str(destination_path))
            real_replace(source, destination)

        with mock.patch.object(governance.os, "replace", side_effect=observe_replace):
            result = self.initialize(workspace, operation_id="op-single-writer", text="single writer")
        self.assertTrue(Path(result["run_dir"], "workflow_manifest.json").is_file())
        self.assertEqual(1, len(manifest_replaces))

    def test_23_continuation_single_owner_waiting_and_completion(self) -> None:
        workspace = self.bootstrap("c23")
        initial = self.initialize(workspace, operation_id="op-owner-initial", text="owner lifecycle")
        run_id = initial["run_id"]
        run_dir = workspace.runs_root / run_id
        first = governance.record_continuation_operation(
            workspace,
            run_id=run_id,
            operation_id="op-owner-first",
        )
        same = governance.record_continuation_operation(
            workspace,
            run_id=run_id,
            operation_id="op-owner-first",
        )
        self.assertFalse(first["idempotent_reuse"])
        self.assertTrue(same["idempotent_reuse"])
        manifest = self.manifest(workspace, run_id)
        self.assertEqual("op-owner-first", manifest["active_continuation_operation_id"])
        self.assertEqual(1, len(manifest["continuation_operations"]))

        with self.assertRaises(governance.WorkspaceGovernanceError) as active_error:
            governance.record_continuation_operation(
                workspace,
                run_id=run_id,
                operation_id="op-owner-second",
            )
        self.assertEqual("CONTINUATION_ALREADY_ACTIVE", active_error.exception.code)
        self.assertEqual("op-owner-first", active_error.exception.details["active_operation_id"])
        self.assertFalse(governance.operation_path(workspace, "op-owner-second").exists())

        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="test-waiting-owner",
            workflow_state="continuation_waiting_user",
        )
        with self.assertRaises(governance.WorkspaceGovernanceError) as waiting_error:
            governance.record_continuation_operation(
                workspace,
                run_id=run_id,
                operation_id="op-owner-second",
            )
        self.assertEqual("waiting_user", waiting_error.exception.details["active_status"])
        self.assertEqual("op-owner-first", self.manifest(workspace, run_id)["active_continuation_operation_id"])

        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="test-complete-owner",
            workflow_state="continuation_completed",
        )
        completed_first = load_json(governance.operation_path(workspace, "op-owner-first"))
        self.assertEqual("completed", completed_first["status"])
        self.assertIsNone(self.manifest(workspace, run_id)["active_continuation_operation_id"])
        second = governance.record_continuation_operation(
            workspace,
            run_id=run_id,
            operation_id="op-owner-second",
        )
        self.assertFalse(second["idempotent_reuse"])
        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="test-complete-second",
            workflow_state="continuation_completed",
        )
        self.assertEqual("completed", load_json(governance.operation_path(workspace, "op-owner-second"))["status"])
        self.assertEqual("completed", load_json(governance.operation_path(workspace, "op-owner-first"))["status"])

    def test_24_continuation_failure_and_abort_release_owner(self) -> None:
        workspace = self.bootstrap("c24")
        initial = self.initialize(workspace, operation_id="op-release-initial", text="release owner")
        run_id = initial["run_id"]
        run_dir = workspace.runs_root / run_id
        governance.record_continuation_operation(workspace, run_id=run_id, operation_id="op-release-fail")
        governance.record_governed_run_failure(
            workspace,
            run_dir,
            command="test-owner-failure",
            error=RuntimeError("owner failed"),
        )
        self.assertEqual("failed", load_json(governance.operation_path(workspace, "op-release-fail"))["status"])
        self.assertIsNone(self.manifest(workspace, run_id)["active_continuation_operation_id"])

        governance.record_continuation_operation(workspace, run_id=run_id, operation_id="op-release-abort")
        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="test-owner-abort",
            workflow_state="continuation_aborted",
        )
        self.assertEqual("aborted", load_json(governance.operation_path(workspace, "op-release-abort"))["status"])
        self.assertIsNone(self.manifest(workspace, run_id)["active_continuation_operation_id"])
        next_result = governance.record_continuation_operation(
            workspace,
            run_id=run_id,
            operation_id="op-release-next",
        )
        self.assertFalse(next_result["idempotent_reuse"])
        self.assertEqual("op-release-next", self.manifest(workspace, run_id)["active_continuation_operation_id"])

    def test_25_legacy_ambiguous_owners_and_cross_run_parallelism(self) -> None:
        workspace = self.bootstrap("c25")
        legacy = self.initialize(workspace, operation_id="op-legacy-initial", text="legacy ambiguous")
        legacy_run_id = legacy["run_id"]
        legacy_run_dir = workspace.runs_root / legacy_run_id
        legacy_manifest_path = legacy_run_dir / "workflow_manifest.json"
        legacy_manifest = load_json(legacy_manifest_path)
        for operation_id in ("op-legacy-a", "op-legacy-b"):
            operation, _ = governance.reserve_operation(
                workspace,
                operation_id=operation_id,
                input_hash=legacy_manifest["input_hash"],
                run_name=None,
                session_reference="test:legacy",
                relation_type="continuation",
                parent_run_id=None,
                target_run_id=legacy_run_id,
                operation_kind="continuation",
            )
            governance.update_operation(workspace, operation_id, "running")
            legacy_manifest.setdefault("continuation_operations", []).append(
                {
                    "operation_id": operation_id,
                    "timestamp": governance.now_iso(),
                    "updated_at": governance.now_iso(),
                    "status": "running",
                }
            )
            self.assertEqual(legacy_run_id, operation["run_id"])
        legacy_manifest.pop("active_continuation_operation_id", None)
        legacy_manifest.pop("continuation_ownership_status", None)
        governance.atomic_write_json(legacy_manifest_path, legacy_manifest)

        governance.refresh_governed_run(
            workspace,
            legacy_run_dir,
            command="test-legacy-complete",
            workflow_state="continuation_completed",
        )
        ambiguous = self.manifest(workspace, legacy_run_id)
        self.assertEqual("ambiguous", ambiguous["continuation_ownership_status"])
        self.assertIsNone(ambiguous["active_continuation_operation_id"])
        self.assertEqual(
            {"op-legacy-a": "running", "op-legacy-b": "running"},
            {
                item["operation_id"]: item["status"]
                for item in ambiguous["continuation_operations"]
            },
        )
        self.assertEqual("running", load_json(governance.operation_path(workspace, "op-legacy-a"))["status"])
        self.assertEqual("running", load_json(governance.operation_path(workspace, "op-legacy-b"))["status"])
        inspection = governance.inspect_workspace(workspace, tool_root=PROJECT_ROOT)
        self.assertEqual(1, len(inspection["continuation_ownership_issues"]))
        with self.assertRaises(governance.WorkspaceGovernanceError) as ambiguous_error:
            governance.record_continuation_operation(
                workspace,
                run_id=legacy_run_id,
                operation_id="op-legacy-new",
            )
        self.assertEqual("CONTINUATION_OWNERSHIP_AMBIGUOUS", ambiguous_error.exception.code)
        self.assertFalse(governance.operation_path(workspace, "op-legacy-new").exists())

        first_run = self.initialize(workspace, operation_id="op-cross-initial-a", text="cross run a")
        second_run = self.initialize(workspace, operation_id="op-cross-initial-b", text="cross run b")

        def continue_one(arguments: tuple[str, str]) -> dict[str, Any]:
            run_id, operation_id = arguments
            return governance.record_continuation_operation(
                workspace,
                run_id=run_id,
                operation_id=operation_id,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    continue_one,
                    [
                        (first_run["run_id"], "op-cross-cont-a"),
                        (second_run["run_id"], "op-cross-cont-b"),
                    ],
                )
            )
        self.assertEqual(2, len(results))
        self.assertEqual("op-cross-cont-a", self.manifest(workspace, first_run["run_id"])["active_continuation_operation_id"])
        self.assertEqual("op-cross-cont-b", self.manifest(workspace, second_run["run_id"])["active_continuation_operation_id"])

    def test_26_eight_by_ten_parallel_stress_has_exact_counts_and_no_temps(self) -> None:
        workspace = self.bootstrap("s26")
        run_ids: list[str] = []
        for round_index in range(10):
            def create(operation_index: int) -> str:
                operation_id = f"op-stress-{round_index:02d}-{operation_index:02d}"
                return self.initialize(
                    workspace,
                    operation_id=operation_id,
                    text=f"stress input {round_index} {operation_index}",
                    run_name=f"s{round_index:02d}{operation_index:02d}",
                )["run_id"]

            with ThreadPoolExecutor(max_workers=8) as pool:
                round_run_ids = list(pool.map(create, range(8)))
            self.assertEqual(8, len(set(round_run_ids)))
            run_ids.extend(round_run_ids)

        registry = governance.rebuild_registry(workspace)
        manifest_paths = list(workspace.runs_root.glob("*/workflow_manifest.json"))
        self.assertEqual(80, len(run_ids))
        self.assertEqual(80, len(set(run_ids)))
        self.assertEqual(80, len(self.canonical_run_dirs(workspace)))
        self.assertEqual(80, len(manifest_paths))
        self.assertEqual(80, registry["expected_operation_count"])
        self.assertEqual(80, registry["actual_run_count"])
        self.assertEqual([], registry["missing_operation_runs"])
        self.assertEqual([], registry["run_dirs_missing_manifest"])
        self.assertEqual([], [path for path in workspace.project_root.rglob(".tmp_*") if path.is_file()])

    def test_27_simultaneous_same_run_continuations_choose_one_owner(self) -> None:
        workspace = self.bootstrap("c27")
        initial = self.initialize(workspace, operation_id="op-race-initial", text="same run race")
        run_id = initial["run_id"]
        barrier = threading.Barrier(2)

        def compete(operation_id: str) -> tuple[str, str]:
            barrier.wait(timeout=5.0)
            try:
                governance.record_continuation_operation(
                    workspace,
                    run_id=run_id,
                    operation_id=operation_id,
                )
                return "created", operation_id
            except governance.WorkspaceGovernanceError as exc:
                return exc.code, operation_id

        contenders = ("op-race-a", "op-race-b")
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(compete, contenders))
        self.assertEqual(1, sum(status == "created" for status, _ in outcomes))
        self.assertEqual(1, sum(status == "CONTINUATION_ALREADY_ACTIVE" for status, _ in outcomes))
        winner = next(operation_id for status, operation_id in outcomes if status == "created")
        loser = next(operation_id for status, operation_id in outcomes if status != "created")
        manifest = self.manifest(workspace, run_id)
        self.assertEqual(winner, manifest["active_continuation_operation_id"])
        self.assertEqual([winner], [item["operation_id"] for item in manifest["continuation_operations"]])
        self.assertTrue(governance.operation_path(workspace, winner).is_file())
        self.assertFalse(governance.operation_path(workspace, loser).exists())

    def test_28_registry_lock_transient_permission_error_retries_then_succeeds(self) -> None:
        workspace = self.bootstrap("l28")
        created = self.initialize(workspace, operation_id="op-lock-transient", text="transient lock")
        registry_path = workspace.registry_path
        registry_lock_path = workspace.locks_root / "registry.lock"
        expected_lock_path = registry_lock_path.resolve(strict=False)
        real_open = governance.os.open
        registry_open_attempts: list[int] = []
        delegated_open_paths: list[Path] = []

        def selective_open(
            path: str | bytes | Path,
            flags: int,
            mode: int = 0o777,
            **kwargs: Any,
        ) -> int:
            candidate = Path(path).resolve(strict=False)
            if candidate == expected_lock_path:
                registry_open_attempts.append(len(registry_open_attempts) + 1)
                if len(registry_open_attempts) <= 3:
                    raise PermissionError(13, "transient registry.lock denial", str(candidate))
            else:
                delegated_open_paths.append(candidate)
            return real_open(path, flags, mode, **kwargs)

        started = time.monotonic()
        with mock.patch.object(governance.os, "open", side_effect=selective_open):
            probe = workspace.control_root / "non_registry_open_probe.tmp"
            descriptor = governance.os.open(
                probe,
                governance.os.O_WRONLY | governance.os.O_CREAT | governance.os.O_EXCL,
                0o600,
            )
            governance.os.close(descriptor)
            probe.unlink()
            lock = governance.registry_lock(workspace, timeout_seconds=0.5)
            with lock:
                rebuilt = governance._rebuild_registry_unlocked(workspace)
        elapsed = time.monotonic() - started

        self.assertEqual([1, 2, 3, 4], registry_open_attempts)
        self.assertTrue(delegated_open_paths)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(1, rebuilt["actual_run_count"])
        self.assertEqual(1, rebuilt["expected_operation_count"])
        self.assertEqual(created["run_id"], rebuilt["runs"][0]["run_id"])
        self.assertTrue(registry_path.is_file())
        self.assertFalse(registry_lock_path.exists())
        self.assertEqual([], [path for path in workspace.project_root.rglob(".tmp_*") if path.is_file()])

    def test_29_registry_lock_permanent_permission_error_is_explicit_and_non_destructive(self) -> None:
        workspace = self.bootstrap("l29")
        created = self.initialize(workspace, operation_id="op-lock-permanent", text="permanent lock")
        registry_path = workspace.registry_path
        manifest_path = workspace.runs_root / created["run_id"] / "workflow_manifest.json"
        registry_before = registry_path.read_bytes()
        manifest_before = manifest_path.read_bytes()
        registry_lock_path = workspace.locks_root / "registry.lock"
        expected_lock_path = registry_lock_path.resolve(strict=False)
        real_open = governance.os.open
        registry_open_attempts: list[int] = []

        def selective_open(
            path: str | bytes | Path,
            flags: int,
            mode: int = 0o777,
            **kwargs: Any,
        ) -> int:
            candidate = Path(path).resolve(strict=False)
            if candidate == expected_lock_path:
                registry_open_attempts.append(len(registry_open_attempts) + 1)
                raise PermissionError(13, "permanent registry.lock denial", str(candidate))
            return real_open(path, flags, mode, **kwargs)

        lock = governance.registry_lock(workspace, timeout_seconds=0.04)
        started = time.monotonic()
        with mock.patch.object(governance.os, "open", side_effect=selective_open):
            with self.assertRaises(governance.WorkspaceGovernanceError) as captured:
                lock.acquire()
        elapsed = time.monotonic() - started

        self.assertEqual("LOCK_CREATE_PERMISSION_DENIED", captured.exception.code)
        self.assertIsInstance(captured.exception.__cause__, PermissionError)
        self.assertGreaterEqual(captured.exception.details["permission_retry_count"], 2)
        self.assertEqual(len(registry_open_attempts), captured.exception.details["permission_retry_count"])
        self.assertEqual("PermissionError", captured.exception.details["original_error"]["type"])
        self.assertEqual(13, captured.exception.details["original_error"]["errno"])
        self.assertIn("permanent registry.lock denial", captured.exception.details["original_error"]["message"])
        self.assertLess(elapsed, 0.5)
        self.assertFalse(lock.acquired)
        self.assertFalse(registry_lock_path.exists())
        self.assertEqual(registry_before, registry_path.read_bytes())
        self.assertEqual(manifest_before, manifest_path.read_bytes())
        self.assertEqual([], [path for path in workspace.project_root.rglob(".tmp_*") if path.is_file()])


    def test_30_supplemental_input_is_canonical_and_idempotent(self) -> None:
        workspace = self.bootstrap("l30")
        created = self.initialize(workspace, operation_id="op-supplemental-base", text="original request")
        operation_id = "op-supplemental-followup"
        first = governance.record_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id=operation_id,
            note="user follow-up",
            supplemental_input="Add three concrete examples to the requested report.",
            supplemental_input_source={
                "path": "C:/project/.schema-workflow/launch/requests/example/user-request.md",
                "sha256": "a" * 64,
                "byte_count": 52,
                "character_count": 52,
            },
        )
        second = governance.record_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id=operation_id,
            note="user follow-up",
            supplemental_input="Add three concrete examples to the requested report.",
        )
        manifest = governance.load_json(
            workspace.runs_root / created["run_id"] / "workflow_manifest.json"
        )
        records = manifest.get("supplemental_inputs", [])
        self.assertEqual(1, len(records))
        self.assertEqual(operation_id, records[0]["operation_id"])
        self.assertEqual(
            "Add three concrete examples to the requested report.",
            records[0]["text"],
        )
        self.assertTrue((records[0].get("input_hash") or {}).get("value"))
        self.assertEqual("a" * 64, records[0]["request_source"]["sha256"])
        self.assertFalse(first["idempotent_reuse"])
        self.assertTrue(second["idempotent_reuse"])


    def test_31_operation_history_is_idempotent_and_preserves_errors(self) -> None:
        workspace = self.bootstrap("m31")
        created = self.initialize(workspace, operation_id="op-history", text="history test")
        operation_path = governance.operation_path(workspace, "op-history")
        before = load_json(operation_path)
        before_history_count = len(before["history"])

        governance.update_operation(workspace, "op-history", "running")
        governance.update_operation(workspace, "op-history", "running")
        unchanged = load_json(operation_path)
        self.assertEqual(before_history_count, len(unchanged["history"]))

        error = {
            "timestamp": governance.now_iso(),
            "command": "validate-fulfillment",
            "type": "RuntimeError",
            "message": "transient validation failure",
        }
        governance.update_operation(workspace, "op-history", "failed", error=error)
        governance.update_operation(workspace, "op-history", "failed", error=error)
        failed = load_json(operation_path)
        self.assertEqual("1.2", failed["operation_record_version"])
        self.assertEqual(1, len(failed["error_history"]))
        self.assertEqual(error, failed["error_history"][0]["error"])
        self.assertEqual(1, sum(item["status"] == "failed" for item in failed["history"]))

        governance.update_operation(workspace, "op-history", "running")
        governance.update_operation(workspace, "op-history", "completed")
        governance.update_operation(workspace, "op-history", "completed")
        recovered = load_json(operation_path)
        self.assertIsNone(recovered["error"])
        self.assertEqual(1, len(recovered["error_history"]))
        self.assertEqual(1, sum(item["status"] == "completed" for item in recovered["history"]))
        self.assertEqual(created["run_id"], recovered["run_id"])

    def test_32_final_artifact_preserves_working_source_and_avoids_collision(self) -> None:
        workspace = self.bootstrap("m32")
        first = self.initialize(workspace, operation_id="op-final-first", text="first final")
        source = workspace.project_root / "portfolio.md"
        source.write_text("# First final\n", encoding="utf-8")

        def finalize(run_id: str, artifact_id: str) -> dict[str, Any]:
            args = argparse.Namespace(
                run_dir=str(workspace.runs_root / run_id),
                artifact_id=artifact_id,
                type="document",
                role="generated_output",
                path=str(source),
                source_step="governance_test",
                prompt_file=None,
                description="managed final",
                target_path=None,
                final=True,
                snapshot=False,
                no_copy=False,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                workflow_runner.command_register_artifact(args)
            return json.loads(output.getvalue())

        first_result = finalize(first["run_id"], "first_final")
        first_deliverable = Path(first_result["deliverable"]["path_absolute"])
        self.assertEqual(workspace.project_root / "deliverables" / "portfolio.md", first_deliverable)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), first_deliverable.read_bytes())
        self.assertEqual("milestone_snapshot", first_result["artifact"]["storage_mode"])
        self.assertEqual(str(source.resolve()), first_result["artifact"]["working_source"])

        source.write_text("# Second final\n", encoding="utf-8")
        second = self.initialize(workspace, operation_id="op-final-second", text="second final")
        second_result = finalize(second["run_id"], "second_final")
        second_deliverable = Path(second_result["deliverable"]["path_absolute"])
        self.assertNotEqual(first_deliverable, second_deliverable)
        self.assertEqual("# First final\n", first_deliverable.read_text(encoding="utf-8"))
        self.assertEqual("# Second final\n", second_deliverable.read_text(encoding="utf-8"))
        self.assertTrue(governance.is_within(workspace.project_root / "deliverables", second_deliverable))

    def test_33_refresh_persists_dynamic_next_action_summary(self) -> None:
        workspace = self.bootstrap("m33")
        created = self.initialize(workspace, operation_id="op-summary", text="summary sync")
        run_dir = workspace.runs_root / created["run_id"]
        status = workflow_runner.build_workflow_status(run_dir)
        workflow_runner.write_workflow_status_files(run_dir, status)
        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="status",
            workflow_state=status["workflow_state"],
        )
        manifest = self.manifest(workspace, created["run_id"])
        self.assertEqual(status["summary"], manifest["summary"])
        self.assertEqual(
            status["next_action"]["reason"],
            manifest["summary"]["next_required_action"],
        )

        completed_status = dict(status)
        completed_status["workflow_state"] = "request_completed"
        completed_status["current_stage"] = "completed"
        completed_status["next_action"] = {"type": "none", "reason": "fulfilled"}
        completed_status["summary"] = dict(status["summary"])
        completed_status["summary"]["workflow_state"] = "request_completed"
        completed_status["summary"]["next_required_action"] = "none"
        workflow_runner.write_workflow_status_files(run_dir, completed_status)
        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="validate-fulfillment",
            workflow_state="request_completed",
        )
        completed_manifest = self.manifest(workspace, created["run_id"])
        self.assertEqual("none", completed_manifest["summary"]["next_required_action"])
        self.assertEqual("completed", completed_manifest["status"])

    def test_34_final_output_role_promotes_without_explicit_final_flag(self) -> None:
        workspace = self.bootstrap("m35")
        created = self.initialize(workspace, operation_id="op-auto-final", text="auto final")
        source = workspace.project_root / "working" / "campaign.png"
        source.parent.mkdir()
        source.write_bytes(b"campaign-image")
        args = argparse.Namespace(
            run_dir=str(workspace.runs_root / created["run_id"]),
            artifact_id="campaign_image",
            type="image",
            role="final_output",
            path=str(source),
            source_step="governance_test",
            prompt_file=None,
            description="user-facing final image",
            target_path=None,
            final=False,
            snapshot=False,
            no_copy=False,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            workflow_runner.command_register_artifact(args)
        result = json.loads(output.getvalue())
        self.assertEqual("managed_deliverable", result["finalization_mode"])
        self.assertEqual(
            "final_output_promoted_to_project_deliverables",
            result["delivery_policy_applied"],
        )
        self.assertTrue(Path(result["deliverable"]["path_absolute"]).is_file())
        self.assertTrue((workspace.runs_root / created["run_id"] / result["artifact"]["path"]).is_file())

    def test_35_continuation_completion_requires_active_output_delivery(self) -> None:
        workspace = self.bootstrap("m36")
        created = self.initialize(workspace, operation_id="op-delivery-source", text="delivery gate")
        run_dir = workspace.runs_root / created["run_id"]
        governance.record_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id="op-delivery-continuation",
            session_reference="test:delivery",
        )
        source = workspace.project_root / "working" / "followup.md"
        source.parent.mkdir()
        source.write_text("# Follow-up\n", encoding="utf-8")

        def register(role: str) -> dict[str, Any]:
            args = argparse.Namespace(
                run_dir=str(run_dir),
                artifact_id="followup_result",
                type="document",
                role=role,
                path=str(source),
                source_step="governance_test",
                prompt_file=None,
                description="continuation result",
                target_path=None,
                final=False,
                snapshot=False,
                no_copy=False,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                workflow_runner.command_register_artifact(args)
            return json.loads(output.getvalue())

        register("generated_output")
        workflow_runner.initialize_continuation_for_workflow(
            run_dir,
            current_phase="asset_generation",
            active_artifact_ids=["followup_result"],
            next_action_types=["deliver_result"],
            completion_gate="artifact_ready",
        )
        with self.assertRaisesRegex(ValueError, "CONTINUATION_DELIVERABLE_REQUIRED"):
            workflow_runner.continuation_store.record_result(
                run_dir,
                artifact_ids=["followup_result"],
                action_type="deliver_result",
                note="must not complete",
            )

        register("final_output")
        workflow_runner.continuation_store.record_result(
            run_dir,
            artifact_ids=["followup_result"],
            action_type="deliver_result",
            note="delivery complete",
        )
        state = workflow_runner.continuation_store.load_continuation_state(run_dir)
        self.assertEqual("completed", state["current_phase"])


    def test_36_parallel_final_deliverable_collisions_are_lossless(self) -> None:
        workspace = self.bootstrap("m34")
        jobs: list[tuple[dict[str, Any], Path, Path]] = []
        for index in range(8):
            created = self.initialize(
                workspace,
                operation_id=f"op-final-parallel-{index}",
                text=f"parallel final {index}",
            )
            run_dir = workspace.runs_root / created["run_id"]
            source_dir = workspace.project_root / "working" / str(index)
            source_dir.mkdir(parents=True, exist_ok=True)
            source = source_dir / "portfolio.md"
            source.write_text(f"# Portfolio {index}\n", encoding="utf-8")
            jobs.append((self.manifest(workspace, created["run_id"]), run_dir, source))

        def finalize_target(job: tuple[dict[str, Any], Path, Path]) -> Path:
            manifest, run_dir, source = job
            with governance.deliverables_lock(workspace):
                _, target = workflow_runner._managed_final_deliverable(
                    manifest,
                    run_dir,
                    str(source),
                )
            return target

        with ThreadPoolExecutor(max_workers=8) as pool:
            targets = list(pool.map(finalize_target, jobs))
        self.assertEqual(8, len({str(path) for path in targets}))
        self.assertEqual(8, len(list((workspace.project_root / "deliverables").glob("portfolio*.md"))))
        self.assertEqual(
            {f"# Portfolio {index}\n" for index in range(8)},
            {path.read_text(encoding="utf-8") for path in targets},
        )
        self.assertFalse((workspace.locks_root / "deliverables.lock").exists())

    def test_37_abort_exact_continuation_owner_releases_stale_lock(self) -> None:
        workspace = self.bootstrap("m37")
        created = self.initialize(workspace, operation_id="op-abort-source", text="abort source")
        run_dir = workspace.runs_root / created["run_id"]
        status = workflow_runner.build_workflow_status(run_dir)
        status["workflow_state"] = "request_completed"
        status["current_stage"] = "completed"
        status["summary"] = dict(status["summary"])
        status["summary"]["workflow_state"] = "request_completed"
        workflow_runner.write_workflow_status_files(run_dir, status)
        governance.record_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id="op-stale-continuation",
            session_reference="session-stale",
        )

        before = self.manifest(workspace, created["run_id"])
        with self.assertRaises(governance.WorkspaceGovernanceError) as wrong_owner:
            governance.abort_continuation_operation(
                workspace,
                run_id=created["run_id"],
                operation_id="op-not-the-owner",
                reason="must not release another owner",
                approved=True,
            )
        self.assertEqual("CONTINUATION_OPERATION_NOT_FOUND", wrong_owner.exception.code)
        self.assertEqual(before, self.manifest(workspace, created["run_id"]))

        result = governance.abort_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id="op-stale-continuation",
            reason="launch relationship validation failed before agent execution",
            approved=True,
        )
        self.assertEqual("aborted", result["status"])
        manifest = self.manifest(workspace, created["run_id"])
        self.assertIsNone(manifest["active_continuation_operation_id"])
        self.assertEqual("idle", manifest["continuation_ownership_status"])
        self.assertEqual("completed", manifest["status"])
        operation = next(
            item for item in manifest["continuation_operations"]
            if item["operation_id"] == "op-stale-continuation"
        )
        self.assertEqual("aborted", operation["status"])
        self.assertEqual(
            "launch relationship validation failed before agent execution",
            operation["abort_reason"],
        )
        repeated = governance.abort_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id="op-stale-continuation",
            reason="launch relationship validation failed before agent execution",
            approved=True,
        )
        self.assertTrue(repeated["idempotent_reuse"])

    def test_38_continuation_start_refreshes_completed_summary_projection(self) -> None:
        workspace = self.bootstrap("m38_continuation_summary")
        created = self.initialize(workspace, operation_id="op-summary-source", text="summary source")
        run_dir = workspace.runs_root / created["run_id"]

        status = workflow_runner.build_workflow_status(run_dir)
        status["workflow_state"] = "request_completed"
        status["current_stage"] = "completed"
        status["next_action"] = {"type": "none", "reason": "fulfilled"}
        status["summary"] = dict(status["summary"])
        status["summary"]["workflow_state"] = "request_completed"
        status["summary"]["next_required_action"] = "none"
        workflow_runner.write_workflow_status_files(run_dir, status)
        governance.refresh_governed_run(
            workspace,
            run_dir,
            command="validate-fulfillment",
            workflow_state="request_completed",
        )

        governance.record_continuation_operation(
            workspace,
            run_id=created["run_id"],
            operation_id="op-summary-continuation",
            note="follow-up work starts",
        )

        manifest = self.manifest(workspace, created["run_id"])
        self.assertEqual("running", manifest["status"])
        self.assertEqual("continuation_in_progress", manifest["summary"]["workflow_state"])
        self.assertNotEqual("request_completed", manifest["summary"]["workflow_state"])
        self.assertNotEqual("none", manifest["summary"]["next_required_action"])
        self.assertEqual(
            "completed",
            load_json(governance.operation_path(workspace, "op-summary-source"))["status"],
        )

    def test_39_cli_output_reconfigures_windows_streams_to_utf8(self) -> None:
        stdout = mock.Mock()
        stderr = mock.Mock()
        with mock.patch.object(workflow_runner.sys, "stdout", stdout), mock.patch.object(
            workflow_runner.sys, "stderr", stderr
        ):
            workflow_runner.configure_cli_output()

        stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="backslashreplace")
        stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="backslashreplace")

    def test_40_relocated_run_resolves_from_relative_identity_without_rewriting_manifest(self) -> None:
        workspace = self.bootstrap("40_relocated_run")
        result = self.initialize(workspace, operation_id="op-relocated", text="relocated input")
        run_dir = workspace.runs_root / result["run_id"]
        manifest_path = run_dir / "workflow_manifest.json"
        before = manifest_path.read_bytes()
        moved_root = self.artifact_root / "workspaces" / "40_relocated_run_moved"
        workspace.project_root.rename(moved_root)
        moved_run = moved_root / "outputs" / "workflows" / result["run_id"]

        moved_manifest = load_json(moved_run / "workflow_manifest.json")
        resolved = governance.resolve_governed_project_root(
            current_run_dir=moved_run,
            manifest=moved_manifest,
        )
        self.assertEqual(moved_root.resolve(), resolved.project_root)
        self.assertEqual("current_relative_identity", resolved.resolution_source)
        self.assertIn("RECORDED_PROJECT_ROOT_STALE", resolved.warnings)
        self.assertEqual(moved_root.resolve(), governance.workspace_for_governed_run(moved_run).project_root)
        self.assertEqual(before, (moved_run / "workflow_manifest.json").read_bytes())

    def test_41_relocated_run_rejects_workspace_identity_mismatch(self) -> None:
        source = self.bootstrap("41_relocated_source")
        result = self.initialize(source, operation_id="op-relocated-foreign", text="foreign relocation")
        destination = self.bootstrap("41_relocated_destination")
        copied_run = destination.runs_root / result["run_id"]
        shutil.copytree(source.runs_root / result["run_id"], copied_run)
        with self.assertRaises(governance.WorkspaceGovernanceError) as captured:
            governance.workspace_for_governed_run(copied_run)
        self.assertEqual("WORKSPACE_IDENTITY_MISMATCH", captured.exception.code)

    def test_42_relocated_run_rejects_relative_path_traversal(self) -> None:
        workspace = self.bootstrap("42_relocated_traversal")
        result = self.initialize(workspace, operation_id="op-relocated-traversal", text="traversal input")
        run_dir = workspace.runs_root / result["run_id"]
        manifest_path = run_dir / "workflow_manifest.json"
        manifest = load_json(manifest_path)
        manifest["run_dir_relative"] = "../../outside"
        governance.atomic_write_json(manifest_path, manifest)
        with self.assertRaises(governance.WorkspaceGovernanceError) as captured:
            governance.workspace_for_governed_run(run_dir)
        self.assertEqual("RUN_RELATIVE_IDENTITY_INVALID", captured.exception.code)

    def test_43_error_surface_classifies_presentation_and_validation_failures(self) -> None:
        encoding_error = UnicodeEncodeError("cp949", "—", 0, 1, "character cannot be encoded")
        presentation = governance.classify_error_surface(
            stage="workflow_execution",
            exception=encoding_error,
        )
        self.assertEqual("presentation_failure", presentation["category"])
        self.assertEqual("cli_output", presentation["stage"])
        self.assertEqual("CLI_OUTPUT_ENCODING_FAILED", presentation["code"])
        self.assertEqual("not_implied", presentation["data_validation_status"])

        validation = governance.classify_error_surface(
            stage="route_validation",
            validation_result={"valid": False, "violations": [{"code": "ROUTE_REQUIRED"}]},
        )
        self.assertEqual("data_validation_failure", validation["category"])
        self.assertEqual("ROUTE_VALIDATION_FAILED", validation["code"])
        self.assertEqual("failed", validation["data_validation_status"])

    def test_44_failure_history_keeps_raw_error_and_additive_surface(self) -> None:
        workspace = self.bootstrap("44_error_surface_record")
        result = self.initialize(workspace, operation_id="op-error-surface", text="error surface input")
        run_dir = workspace.runs_root / result["run_id"]
        governance.record_governed_run_failure(
            workspace,
            run_dir,
            command="validate-route",
            error=RuntimeError("route payload is invalid"),
        )
        manifest = self.manifest(workspace, result["run_id"])
        error_record = manifest["failure_history"][-1]
        self.assertEqual("RuntimeError", error_record["type"])
        self.assertEqual("route payload is invalid", error_record["message"])
        self.assertEqual("execution_failure", error_record["error_surface"]["category"])
        self.assertEqual("route_validation", error_record["error_surface"]["stage"])
        self.assertEqual(error_record["error_surface"], manifest["summary"]["error_surface"])

    def test_45_cli_status_survives_cp949_parent_with_unicode_output(self) -> None:
        workspace = self.bootstrap("45_cp949_cli")
        result = self.initialize(
            workspace,
            operation_id="op-cp949-cli",
            text="unicode output input — em dash",
        )
        run_dir = workspace.runs_root / result["run_id"]
        manifest_path = run_dir / "workflow_manifest.json"
        manifest = self.manifest(workspace, result["run_id"])
        manifest.setdefault("summary", {})["failure_reason"] = "unicode — diagnostic"
        governance.atomic_write_json(manifest_path, manifest)
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "cp949"
        environment["PYTHONUTF8"] = "0"
        process = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "engine" / "python" / "workflow" / "workflow_runner.py"),
                "status",
                "--run-dir",
                str(run_dir),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout = process.stdout.decode("utf-8")
        stderr = process.stderr.decode("utf-8", errors="replace")
        self.assertEqual(0, process.returncode, stderr)
        self.assertIn("—", stdout)
        self.assertNotIn("UnicodeEncodeError", stderr)

    def test_46_relocated_clone_with_same_workspace_identity_fails_closed(self) -> None:
        workspace = self.bootstrap("46_clone_ambiguity")
        result = self.initialize(workspace, operation_id="op-clone-ambiguity", text="clone ambiguity input")
        clone_root = self.artifact_root / "workspaces" / "46_clone_ambiguity_copy"
        shutil.copytree(workspace.project_root, clone_root)
        clone_run = clone_root / "outputs" / "workflows" / result["run_id"]
        with self.assertRaises(governance.WorkspaceGovernanceError) as captured:
            governance.workspace_for_governed_run(clone_run)
        self.assertEqual("AMBIGUOUS_WORKSPACE_LOCATION", captured.exception.code)


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: list[dict[str, Any]] = []
        self.started: dict[str, float] = {}

    def startTest(self, test: unittest.TestCase) -> None:
        self.started[test.id()] = time.perf_counter()
        super().startTest(test)

    def _record(self, test: unittest.TestCase, status: str, detail: str | None = None) -> None:
        elapsed = time.perf_counter() - self.started.get(test.id(), time.perf_counter())
        self.records.append(
            {
                "id": test.id().split(".")[-1],
                "status": status,
                "elapsed_seconds": round(elapsed, 4),
                "detail": detail,
            }
        )

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:
        super().addFailure(test, err)
        self._record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test: unittest.TestCase, err: Any) -> None:
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))


def build_markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    rows = ["| Test | Status | Seconds |", "|---|---|---:|"]
    rows.extend(f"| {item['id']} | {item['status']} | {item['elapsed_seconds']} |" for item in records)
    return "\n".join(
        [
            "# Workspace Governance Regression Test",
            "",
            f"- Total: {summary['total']}",
            f"- Passed: {summary['passed']}",
            f"- Failed: {summary['failed']}",
            f"- Errors: {summary['errors']}",
            f"- Score: {summary['score_100']} / 100",
            "",
            *rows,
            "",
        ]
    )


def main() -> int:
    output_root = PROJECT_ROOT / "tests" / "artifacts" / "test_runs"
    run_dir = unique_run_dir(
        output_root,
        "workspace_governance",
        created_at=datetime.now(),
        include_timestamp_for_named=True,
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    workspace_root = (
        PROJECT_ROOT
        / "tests"
        / "artifacts"
        / f"wg_{datetime.now().strftime('%H%M%S_%f')}"
    )
    workspace_root.mkdir(parents=True, exist_ok=False)
    GovernanceRegressionTests.artifact_root = workspace_root
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GovernanceRegressionTests)
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordingResult)
    result: RecordingResult = runner.run(suite)  # type: ignore[assignment]
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors
    summary = {
        "total": total,
        "passed": passed,
        "failed": failures,
        "errors": errors,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "score_100": round(100 * passed / total, 2) if total else 0.0,
    }
    results_path = run_dir / "workspace_governance_test_results.json"
    report_path = run_dir / "workspace_governance_test_report.md"
    transcript_path = run_dir / "unittest_output.txt"
    governance.atomic_write_json(results_path, {"summary": summary, "results": result.records})
    governance.atomic_write_text(report_path, build_markdown(summary, result.records))
    governance.atomic_write_text(transcript_path, stream.getvalue())
    manifest = {
        "run_dir": str(run_dir),
        "workspace_artifacts_root": str(workspace_root),
        "results_file": str(results_path),
        "report_file": str(report_path),
        "transcript_file": str(transcript_path),
        "summary": summary,
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
