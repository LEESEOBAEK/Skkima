from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "agents" / "agent.md").exists() and (
            candidate / "engine" / "python" / "workflow"
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

from shared import artifacts as artifact_store
from shared import continuation as continuation_store
from shared.run_identity import unique_run_dir as identity_unique_run_dir


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = data.get("cases", data)
    if not isinstance(cases, list):
        raise SystemExit("Workflow smoke test file must contain a JSON list or an object with cases.")
    return cases


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name, default_suffix="workflow_smoke")


def facet_slot(value: Any, *, basis: str = "inferred", confidence: float = 0.84) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "basis": basis,
        "evidence": "workflow smoke fixture",
        "reason": "workflow smoke fixture",
        "status": "agent_filled",
    }


def fill_input_for_case(run_dir: Path) -> None:
    request_path = run_dir / "01_input_structuring" / "data" / "user_input_analysis_request.json"
    filled_path = run_dir / "01_input_structuring" / "data" / "user_input_analysis_filled.json"
    filled = workflow_runner.load_json(request_path)
    filled["fixture_status"] = "agent_filled"
    write_json(filled_path, filled)


def fill_router_for_case(run_dir: Path) -> None:
    request_path = run_dir / "02_router" / "data" / "facet_router_request.json"
    filled_path = run_dir / "02_router" / "data" / "facet_router_filled.json"
    request = workflow_runner.load_json(request_path)
    values = {
        "domain_context": "python_automation",
        "problem_object": "spreadsheet_monthly_sales_summary",
        "user_intent": "build_python_automation",
        "definition_level": "medium",
        "risk_level": "low",
        "needed_output": "python_script_or_implementation_plan",
        "source_type": "spreadsheet_file",
    }
    facets = {}
    for name in request["facet_classification"]:
        value = values.get(name, "unresolved")
        facets[name] = facet_slot(
            value,
            basis="explicit" if name == "source_type" else "inferred",
            confidence=0.88 if value != "unresolved" else 0.0,
        )
        facets[name]["facet_name"] = name
        if value == "unresolved":
            facets[name]["status"] = "unresolved"

    filled = {
        "router_version": request["router_version"],
        "source": request["source"],
        "c_activation": request["c_activation"],
        "facet_classification": facets,
        "route_decision": {
            "route_status": "selected",
            "selected_route": "proceed_to_solution",
            "route_confidence": 0.82,
            "reason": "The request is a low-risk automation task with a clear target.",
            "evidence": "The user asks for Python automation and monthly sales aggregation.",
        },
        "missing_decision_basis": [],
        "reference_lenses": ["ipo"],
    }
    write_json(filled_path, filled)


def fill_direction_for_case(run_dir: Path) -> None:
    filled_path = run_dir / "04_direction_lens" / "data" / "direction_lens_filled.json"
    filled = {
        "selected_lenses": ["ipo"],
        "direction_decision": {
            "direction_status": "ready_for_next_step",
            "problem_direction": "Map input spreadsheet, aggregation process, and output summary.",
            "why_this_direction": "The validated route is a low-risk automation task.",
            "evidence": "The router decision passed and reference_lenses includes ipo.",
        },
        "coverage_check": {
            "mece_checked": False,
            "missing_areas": [],
            "assumptions_to_verify": ["Exact spreadsheet columns are not yet known."],
        },
        "missing_basis": [],
        "next_action": {
            "action_type": "run_lens",
            "reason": "Run IPO before implementation.",
        },
    }
    write_json(filled_path, filled)


def fill_context_for_case(run_dir: Path) -> None:
    filled_path = run_dir / "05_situation_context" / "data" / "situation_context_filled.json"
    filled = {
        "situation_context_map": {
            "central_problem": "Spreadsheet sales automation needs implementation context.",
            "domain_area": "software_development_and_data_automation",
            "situation_context": "The user wants a Python automation for monthly sales aggregation.",
            "actor_context": "Agent is preparing the next implementation step from validated routing.",
            "actor_scope": {
                "primary_actor": "company",
                "request_context": "internal_work",
                "decision_owner": "user_or_team_owner",
                "affected_parties": ["user", "sales reporting stakeholders"],
                "evidence": "Monthly sales aggregation implies an internal business reporting automation task.",
                "confidence": 0.68,
                "missing_context": [],
                "classification_self_check": {
                    "minimum_evidence_used": [
                        "Monthly sales aggregation implies an internal business reporting automation task."
                    ],
                    "competing_classifications": [],
                    "why_not_other_actor_scope": (
                        "The workflow is framed as sales reporting automation, so company/internal_work is better supported "
                        "than a personal task classification."
                    ),
                    "could_be_wrong_if": [
                        "The report could be a personal spreadsheet exercise rather than an internal business task."
                    ],
                },
            },
            "problem_type": "automation_workflow_design",
            "task_object": "spreadsheet_monthly_sales_summary",
            "situation_phase": "design",
            "required_context": [
                "spreadsheet path",
                "sales date column",
                "sales amount column",
                "desired output format",
            ],
            "missing_context": [],
            "context_links": [
                {
                    "from": "spreadsheet_monthly_sales_summary",
                    "to": "ipo_analysis",
                    "relation": "requires",
                    "evidence": "The validated direction selected IPO for automation planning.",
                }
            ],
            "recommended_next_focus": [
                "confirm spreadsheet columns",
                "map input process output",
                "draft implementation plan",
            ],
            "evidence_basis": [
                "Router selected proceed_to_solution for a low-risk automation task.",
                "Direction lens selected IPO.",
            ],
            "confidence": 0.84,
            "optional_views": {
                "mandalart_view": {
                    "enabled": False,
                    "center": "",
                    "branches": [],
                }
            },
            "experimental_context_exploration_path": {
                "enabled": True,
                "mode": "B_minimum_detail_required",
                "activation_reason": "Workflow smoke keeps the experimental path enabled.",
                "stage_1_start_area": {
                    "default_candidates": ["개인", "학습", "업무", "직무", "문서", "데이터", "개발", "자동화", "의사결정", "리스크"],
                    "other_candidates": [],
                    "selected": "자동화",
                    "reason": "The request is a Python automation task.",
                    "evidence": "The router classified the task as automation.",
                },
                "stage_2_subcategory": {
                    "parent": "자동화",
                    "default_candidates_by_start_area": {
                        "자동화": ["반복작업", "입력", "처리", "출력", "스케줄", "오류처리"]
                    },
                    "default_candidates": ["반복작업", "입력", "처리", "출력", "스케줄", "오류처리"],
                    "agent_added_candidates": ["집계"],
                    "selected": "처리",
                    "reason": "Monthly sales aggregation is a processing task.",
                    "evidence": "The user asks to calculate monthly sales totals.",
                },
                "stage_3_context_object": {
                    "parent_path": ["자동화", "처리"],
                    "example_candidates": ["입력 파일", "반복 작업", "출력 결과", "실행 환경", "오류 처리"],
                    "agent_generated_candidates": ["spreadsheet file", "monthly aggregation", "summary output"],
                    "selected": "monthly aggregation",
                    "reason": "Aggregation is the core context object.",
                    "evidence": "The task asks for monthly sales totals.",
                },
                "stage_4_primary_structure": {
                    "base_axes": ["type", "object", "attribute", "relationship", "state", "event", "rule", "context"],
                    "activated_axes": ["object", "attribute", "relationship", "rule", "context"],
                    "axis_notes": {
                        "type": "",
                        "object": "Spreadsheet and aggregation output.",
                        "attribute": "Columns and output format.",
                        "relationship": "Spreadsheet rows map to monthly totals.",
                        "state": "",
                        "event": "",
                        "rule": "Aggregation rules must be clear.",
                        "context": "Implementation planning follows IPO.",
                    },
                },
                "stage_5_detail_expansion": {
                    "detail_policy": "minimum_one_detail_per_activated_axis",
                    "axis_details": {
                        "object": ["spreadsheet monthly sales summary"],
                        "attribute": ["date column and sales amount column"],
                        "relationship": ["rows -> monthly group -> total"],
                        "rule": ["group sales by month before summing"],
                        "context": ["ready for IPO-based implementation planning"],
                    },
                    "usage_direction": "Use the path as basis for IPO-based implementation planning.",
                },
            },
        },
        "next_action": {
            "action_type": "run_framework",
            "target": "ipo",
            "reason": "The context map is ready for IPO-based implementation planning.",
        },
    }
    write_json(filled_path, filled)


def warning_codes(status: dict[str, Any]) -> set[str]:
    return {item.get("code", "") for item in status.get("warnings", [])}


def exercise_continuation_fixture(
    fixture: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    state_file = run_dir / continuation_store.CONTINUATION_STATE_NAME
    try:
        source_file = run_dir.parents[1] / "slime_candidate_sheet.png"
        source_file.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )
        registration = artifact_store.register_artifact(
            run_dir,
            artifact_id=fixture["artifact_id"],
            artifact_type="image",
            role="candidate_sheet",
            path=str(source_file),
            source_step="workflow_smoke_fixture",
            description="One image containing five left-to-right slime candidates.",
            copy_into_run=False,
        )
        registered_id = registration["artifact"]["id"]
        inspection = workflow_runner.initialize_continuation_for_workflow(
            run_dir,
            current_phase=fixture["current_phase"],
            active_artifact_ids=[registered_id],
            candidate_artifact_id=registered_id,
            candidate_count=fixture["candidate_count"],
            index_order=fixture["index_order"],
            candidate_labels=fixture["candidate_labels"],
            next_action_types=fixture["next_actions"],
            decision_note="Codex pet slime candidate sheet is ready for selection.",
            working_root=str(source_file.parent),
            completion_gate=fixture.get("completion_gate", "approved"),
            risk_level=fixture.get("risk_level", "medium"),
            deployment_target=fixture.get("deployment_target"),
        )
        if not inspection.get("valid"):
            issues.append(
                {
                    "type": "continuation_state_invalid",
                    "expected": "valid continuation state",
                    "actual": inspection.get("errors", []),
                }
            )

        state = continuation_store.load_continuation_state(run_dir)
        legacy_state = dict(state)
        legacy_state["continuation_state_version"] = "0.1.0"
        legacy_state.pop("completion_policy", None)
        legacy_state.pop("workspace_context", None)
        legacy_state.pop("timing", None)
        workflow_runner.write_json(state_file, legacy_state)
        legacy_inspection = workflow_runner.inspect_continuation_for_workflow(run_dir)
        if legacy_inspection.get("valid") is not True:
            issues.append(
                {
                    "type": "legacy_continuation_read_failed",
                    "expected": "v0.1 remains readable",
                    "actual": legacy_inspection.get("errors", []),
                }
            )
        state = continuation_store.migrate_continuation(
            run_dir,
            working_root=str(source_file.parent),
            completion_gate=fixture.get("completion_gate", "approved"),
            risk_level=fixture.get("risk_level", "medium"),
            deployment_target=fixture.get("deployment_target"),
        )
        if state.get("continuation_state_version") != "0.2.0":
            issues.append(
                {
                    "type": "legacy_continuation_migration_failed",
                    "expected": "0.2.0",
                    "actual": state.get("continuation_state_version"),
                }
            )

        invalid_root = run_dir / "__missing_workspace__"
        state["workspace_context"]["working_root"] = str(invalid_root)
        workflow_runner.write_json(state_file, state)
        invalid_workspace_inspection = workflow_runner.inspect_continuation_for_workflow(run_dir)
        invalid_workspace_codes = {
            item.get("code")
            for item in invalid_workspace_inspection.get("errors", [])
            if isinstance(item, dict)
        }
        if "WORKING_ROOT_NOT_FOUND" not in invalid_workspace_codes:
            issues.append(
                {
                    "type": "missing_working_root_not_rejected",
                    "expected": "WORKING_ROOT_NOT_FOUND",
                    "actual": sorted(str(item) for item in invalid_workspace_codes),
                }
            )
        try:
            continuation_store.set_workspace_context(
                run_dir,
                working_root=str(invalid_root),
                note="This missing workspace must be rejected.",
            )
            issues.append(
                {
                    "type": "missing_working_root_update_accepted",
                    "expected": "FileNotFoundError",
                    "actual": "workspace update succeeded",
                }
            )
        except FileNotFoundError:
            pass
        state = continuation_store.set_workspace_context(
            run_dir,
            working_root=str(source_file.parent),
            note="Repair the project workspace after validation failure.",
        )
        repaired_workspace_inspection = workflow_runner.inspect_continuation_for_workflow(run_dir)
        workspace_events = [
            item
            for item in state.get("decision_log", [])
            if isinstance(item, dict) and item.get("event") == "workspace_context_updated"
        ]
        if (
            repaired_workspace_inspection.get("valid") is not True
            or state.get("workspace_context", {}).get("working_root") != str(source_file.parent.resolve())
            or not workspace_events
        ):
            issues.append(
                {
                    "type": "working_root_repair_failed",
                    "expected": {
                        "valid": True,
                        "working_root": str(source_file.parent.resolve()),
                        "event": "workspace_context_updated",
                    },
                    "actual": {
                        "valid": repaired_workspace_inspection.get("valid"),
                        "working_root": state.get("workspace_context", {}).get("working_root"),
                        "events": workspace_events,
                    },
                }
            )
        required_fields = {
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
            "completion_policy",
            "workspace_context",
            "timing",
        }
        missing_fields = sorted(required_fields - set(state))
        if missing_fields:
            issues.append(
                {
                    "type": "continuation_required_field_missing",
                    "expected": sorted(required_fields),
                    "actual": missing_fields,
                }
            )
        if state.get("continuation_id") != run_dir.name or state.get("source_run_ids") != [run_dir.name]:
            issues.append(
                {
                    "type": "continuation_run_identity_mismatch",
                    "expected": {
                        "continuation_id": run_dir.name,
                        "source_run_ids": [run_dir.name],
                    },
                    "actual": {
                        "continuation_id": state.get("continuation_id"),
                        "source_run_ids": state.get("source_run_ids"),
                    },
                }
            )
        candidate_set = state.get("candidate_set") or {}
        if candidate_set.get("count") != fixture["candidate_count"]:
            issues.append(
                {
                    "type": "candidate_count_mismatch",
                    "expected": fixture["candidate_count"],
                    "actual": candidate_set.get("count"),
                }
            )
        if candidate_set.get("index_rule", {}).get("order") != fixture["index_order"]:
            issues.append(
                {
                    "type": "candidate_order_mismatch",
                    "expected": fixture["index_order"],
                    "actual": candidate_set.get("index_rule", {}).get("order"),
                }
            )

        numeric_candidate = continuation_store.resolve_candidate(
            candidate_set,
            fixture["numeric_selector"],
        )
        if numeric_candidate.get("candidate_id") != fixture["expected_numeric_candidate"]:
            issues.append(
                {
                    "type": "candidate_numeric_reference_mismatch",
                    "expected": fixture["expected_numeric_candidate"],
                    "actual": numeric_candidate.get("candidate_id"),
                }
            )
        state = continuation_store.select_candidate(
            run_dir,
            selector=fixture["numeric_selector"],
            action_type="revise_candidate",
            note="User asked to revise candidate 3.",
        )
        if state["candidate_set"]["selected_candidate"]["candidate_id"] != fixture["expected_numeric_candidate"]:
            issues.append(
                {
                    "type": "candidate_numeric_selection_persistence_mismatch",
                    "expected": fixture["expected_numeric_candidate"],
                    "actual": state["candidate_set"]["selected_candidate"],
                }
            )

        label_candidate = continuation_store.resolve_candidate(
            state["candidate_set"],
            fixture["label_selector"],
        )
        if label_candidate.get("candidate_id") != fixture["expected_label_candidate"]:
            issues.append(
                {
                    "type": "candidate_label_reference_mismatch",
                    "expected": fixture["expected_label_candidate"],
                    "actual": label_candidate.get("candidate_id"),
                }
            )
        state = continuation_store.select_candidate(
            run_dir,
            selector=fixture["label_selector"],
            action_type="generate_standalone_image",
            note="User asked for candidate B as a standalone image.",
        )
        if state["candidate_set"]["selected_candidate"]["candidate_id"] != fixture["expected_label_candidate"]:
            issues.append(
                {
                    "type": "candidate_label_selection_persistence_mismatch",
                    "expected": fixture["expected_label_candidate"],
                    "actual": state["candidate_set"]["selected_candidate"],
                }
            )

        action_types = {item.get("type") for item in state.get("next_actions", []) if isinstance(item, dict)}
        for required_action in {"generate_standalone_image", "build_codex_pet"}:
            if required_action not in action_types:
                issues.append(
                    {
                        "type": "continuation_action_missing",
                        "expected": required_action,
                        "actual": sorted(str(item) for item in action_types),
                    }
                )
        actions_by_type = {
            item.get("type"): item
            for item in state.get("next_actions", [])
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        }
        if (
            state.get("current_phase") != "asset_generation"
            or actions_by_type.get("generate_standalone_image", {}).get("status") != "ready"
            or actions_by_type.get("build_codex_pet", {}).get("status") != "available"
        ):
            issues.append(
                {
                    "type": "continuation_action_routing_mismatch",
                    "expected": {
                        "current_phase": "asset_generation",
                        "generate_standalone_image": "ready",
                        "build_codex_pet": "available",
                    },
                    "actual": {
                        "current_phase": state.get("current_phase"),
                        "next_actions": state.get("next_actions"),
                    },
                }
            )
        selection_log = [
            item
            for item in state.get("decision_log", [])
            if isinstance(item, dict) and item.get("event") == "candidate_selected"
        ]
        expected_decisions = [
            (fixture["numeric_selector"], fixture["expected_numeric_candidate"], "revise_candidate"),
            (fixture["label_selector"], fixture["expected_label_candidate"], "generate_standalone_image"),
        ]
        actual_decisions = [
            (item.get("raw_reference"), item.get("candidate_id"), item.get("action_type"))
            for item in selection_log
        ]
        if actual_decisions != expected_decisions:
            issues.append(
                {
                    "type": "continuation_decision_log_mismatch",
                    "expected": expected_decisions,
                    "actual": actual_decisions,
                }
            )

        try:
            continuation_store.complete_continuation(
                run_dir,
                note="This must be rejected before the approved completion gate.",
            )
            issues.append(
                {
                    "type": "continuation_completion_gate_bypassed",
                    "expected": "completion rejected before result and approval",
                    "actual": "completed without gate evidence",
                }
            )
        except ValueError:
            pass

        result_source = run_dir.parents[1] / "slime_candidate_b.png"
        result_source.write_bytes(source_file.read_bytes())
        result_registration = artifact_store.register_artifact(
            run_dir,
            artifact_id="slime_candidate_b_approved",
            artifact_type="image",
            role="approved_output",
            path=str(result_source),
            source_step="workflow_smoke_fixture",
            description="Approved standalone candidate B.",
            copy_into_run=True,
        )
        state = continuation_store.record_result(
            run_dir,
            artifact_ids=[result_registration["artifact"]["id"]],
            action_type="generate_standalone_image",
            note="Standalone candidate B is ready for user review.",
        )
        if state.get("current_phase") != "awaiting_user_review":
            issues.append(
                {
                    "type": "continuation_review_phase_missing",
                    "expected": "awaiting_user_review",
                    "actual": state.get("current_phase"),
                }
            )

        review_status = workflow_runner.build_workflow_status(run_dir)
        if (
            review_status.get("workflow_state") != "continuation_waiting_user"
            or review_status.get("next_action", {}).get("type") != "review_continuation_result"
        ):
            issues.append(
                {
                    "type": "continuation_projection_not_prioritized",
                    "expected": {
                        "workflow_state": "continuation_waiting_user",
                        "next_action": "review_continuation_result",
                    },
                    "actual": {
                        "workflow_state": review_status.get("workflow_state"),
                        "next_action": review_status.get("next_action"),
                    },
                }
            )
        report_status_input = dict(review_status)
        report_validation_status = dict(report_status_input.get("validation_status", {}))
        report_validation_status["route_validation"] = {"valid": True}
        report_validation_status["direction_validation"] = {"valid": True}
        report_validation_status["context_validation"] = {"valid": True}
        report_status_input["validation_status"] = report_validation_status
        continuation_report_status = workflow_runner.build_human_report_status(report_status_input)
        continuation_report_manifest = (
            workflow_runner.human_readable_report_builder.build_report_files(
                run_dir,
                status=continuation_report_status,
            )
        )
        continuation_report = workflow_runner.load_json(
            Path(continuation_report_manifest["summary_file"])
        )
        report_continuation = continuation_report.get("continuation", {})
        report_gate = workflow_runner.human_readable_report_builder.build_quality_gate(
            workflow_state="continuation_waiting_user",
            route_validation={"valid": True},
            direction_validation={"valid": True},
            context_validation={"valid": True},
            fulfillment_configured=True,
            fulfillment_validation={"valid": True},
            missing_context=[],
        )
        if (
            continuation_report.get("workflow_state") != "continuation_waiting_user"
            or report_continuation.get("current_phase") != "awaiting_user_review"
            or report_continuation.get("completion_gate") != fixture.get("completion_gate", "approved")
            or not isinstance(report_continuation.get("timing"), dict)
            or report_gate.get("decision") != "사용자 검토 대기"
            or report_gate.get("request_completed") is not False
        ):
            issues.append(
                {
                    "type": "continuation_human_report_missing",
                    "expected": {
                        "workflow_state": "continuation_waiting_user",
                        "current_phase": "awaiting_user_review",
                        "completion_gate": fixture.get("completion_gate", "approved"),
                        "timing": "object",
                    },
                    "actual": {
                        "workflow_state": continuation_report.get("workflow_state"),
                        "continuation": report_continuation,
                        "quality_gate": report_gate,
                    },
                }
            )

        artifact_status = artifact_store.inspect_artifacts(run_dir)
        candidate_artifact = next(
            (item for item in artifact_status["artifacts"] if item.get("id") == registered_id),
            {},
        )
        result_artifact = next(
            (
                item
                for item in artifact_status["artifacts"]
                if item.get("id") == result_registration["artifact"]["id"]
            ),
            {},
        )
        duplicate_groups = artifact_status.get("duplicate_groups", [])
        if (
            artifact_status.get("duplicate_content_count", 0) < 1
            or not any(
                registered_id in group.get("artifact_ids", [])
                and result_registration["artifact"]["id"] in group.get("artifact_ids", [])
                for group in duplicate_groups
                if isinstance(group, dict)
            )
        ):
            issues.append(
                {
                    "type": "duplicate_artifact_content_not_reported",
                    "expected": [registered_id, result_registration["artifact"]["id"]],
                    "actual": duplicate_groups,
                }
            )
        if candidate_artifact.get("storage_mode") != "project_reference" or candidate_artifact.get("bound_to_run"):
            issues.append(
                {
                    "type": "project_first_artifact_policy_failed",
                    "expected": "project_reference outside run",
                    "actual": candidate_artifact,
                }
            )
        if result_artifact.get("storage_mode") != "milestone_snapshot" or not result_artifact.get("bound_to_run"):
            issues.append(
                {
                    "type": "milestone_snapshot_policy_failed",
                    "expected": "milestone_snapshot inside run",
                    "actual": result_artifact,
                }
            )
        state_resolved = state_file.resolve()
        if any(Path(item["absolute_path"]).resolve() == state_resolved for item in artifact_status["artifacts"]):
            issues.append(
                {
                    "type": "continuation_artifact_conflation",
                    "expected": "continuation state is not registered as an artifact",
                    "actual": str(state_file),
                }
            )

        completed = continuation_store.approve_result(
            run_dir,
            note="User approved the standalone candidate result.",
        )
        if fixture.get("completion_gate", "approved") == "deployed":
            if completed.get("current_phase") != "deployment_ready":
                issues.append(
                    {
                        "type": "continuation_deployment_gate_missing",
                        "expected": "deployment_ready",
                        "actual": completed.get("current_phase"),
                    }
                )
            if fixture.get("risk_level") == "high":
                try:
                    continuation_store.start_deployment(
                        run_dir,
                        target=fixture.get("deployment_target"),
                        confirmed=False,
                    )
                    issues.append(
                        {
                            "type": "high_risk_deployment_confirmation_bypassed",
                            "expected": "explicit confirmation required",
                            "actual": "deployment started without confirmation",
                        }
                    )
                except ValueError:
                    pass
            continuation_store.start_deployment(
                run_dir,
                target=fixture.get("deployment_target"),
                confirmed=fixture.get("risk_level") == "high",
                note="Deployment smoke probe started.",
            )
            completed = continuation_store.record_deployment(
                run_dir,
                note="Deployment smoke probe completed.",
            )
        if completed.get("completed_at") is None or completed.get("elapsed_seconds") is None:
            issues.append(
                {
                    "type": "continuation_completion_time_missing",
                    "expected": "completed_at and elapsed_seconds are recorded",
                    "actual": {
                        "completed_at": completed.get("completed_at"),
                        "elapsed_seconds": completed.get("elapsed_seconds"),
                    },
                }
            )
        else:
            expected_elapsed = max(
                0,
                int(
                    (
                        datetime.fromisoformat(completed["completed_at"])
                        - datetime.fromisoformat(completed["created_at"])
                    ).total_seconds()
                ),
            )
            if completed.get("elapsed_seconds") != expected_elapsed:
                issues.append(
                    {
                        "type": "continuation_elapsed_mismatch",
                        "expected": expected_elapsed,
                        "actual": completed.get("elapsed_seconds"),
                    }
                )
        if completed.get("current_phase") != "completed" or completed.get("next_actions") != []:
            issues.append(
                {
                    "type": "continuation_completion_state_mismatch",
                    "expected": {"current_phase": "completed", "next_actions": []},
                    "actual": {
                        "current_phase": completed.get("current_phase"),
                        "next_actions": completed.get("next_actions"),
                    },
                }
            )
        timing = completed.get("timing") or {}
        for timing_field in (
            "agent_work_seconds",
            "user_review_seconds",
            "deployment_seconds",
            "total_elapsed_seconds",
        ):
            if timing.get(timing_field) is None:
                issues.append(
                    {
                        "type": "continuation_segmented_timing_missing",
                        "expected": timing_field,
                        "actual": timing,
                    }
                )
        final_inspection = workflow_runner.inspect_continuation_for_workflow(run_dir)
        if not final_inspection.get("valid"):
            issues.append(
                {
                    "type": "continuation_round_trip_invalid",
                    "expected": "valid after create, select, reload, and complete",
                    "actual": final_inspection.get("errors", []),
                }
            )
        workflow_manifest = workflow_runner.load_json(run_dir / "workflow_manifest.json")
        if workflow_manifest.get("paths", {}).get("continuation_state") != str(state_file):
            issues.append(
                {
                    "type": "continuation_manifest_link_mismatch",
                    "expected": str(state_file),
                    "actual": workflow_manifest.get("paths", {}).get("continuation_state"),
                }
            )
        refreshed_status = workflow_runner.build_workflow_status(run_dir)
        workflow_runner.write_workflow_status_files(run_dir, refreshed_status)
        status_continuation = refreshed_status.get("continuation_status", {})
        if status_continuation.get("exists") is not True or status_continuation.get("valid") is not True:
            issues.append(
                {
                    "type": "continuation_workflow_status_mismatch",
                    "expected": {"exists": True, "valid": True},
                    "actual": {
                        "exists": status_continuation.get("exists"),
                        "valid": status_continuation.get("valid"),
                        "errors": status_continuation.get("errors", []),
                    },
                }
            )
        next_snapshot = workflow_runner.load_json(run_dir / "workflow_next.json")
        next_continuation = next_snapshot.get("continuation_status", {})
        if next_continuation.get("exists") is not True or next_continuation.get("valid") is not True:
            issues.append(
                {
                    "type": "continuation_workflow_next_mismatch",
                    "expected": {"exists": True, "valid": True},
                    "actual": {
                        "exists": next_continuation.get("exists"),
                        "valid": next_continuation.get("valid"),
                    },
                }
            )
        if next_snapshot.get("workflow_state") != "continuation_completed":
            issues.append(
                {
                    "type": "continuation_completion_projection_missing",
                    "expected": "continuation_completed",
                    "actual": next_snapshot.get("workflow_state"),
                }
            )
    except Exception as exc:
        issues.append(
            {
                "type": "continuation_fixture_exception",
                "expected": "continuation fixture completes",
                "actual": f"{type(exc).__name__}: {exc}",
            }
        )
    return {
        "issues": issues,
        "state_file": str(state_file),
    }


def evaluate_case(case: dict[str, Any], workflow_base_dir: Path) -> dict[str, Any]:
    run_name = case["id"]
    manifest = workflow_runner.build_workflow(
        text=case["text"],
        output_dir=workflow_base_dir,
        run_name=run_name,
        source_files=case.get("source_files", []),
    )
    run_dir = Path(manifest["run_dir"])
    status = workflow_runner.build_workflow_status(run_dir)
    workflow_runner.write_workflow_status_files(run_dir, status)

    issues: list[dict[str, Any]] = []
    artifacts_manifest = run_dir / "artifacts_manifest.json"
    if not artifacts_manifest.exists():
        issues.append(
            {
                "type": "artifact_manifest_missing",
                "expected": "artifacts_manifest.json exists",
                "actual": str(artifacts_manifest),
            }
        )
    for relative_asset_dir in [
        "assets/images/generated",
        "assets/images/references",
        "assets/prompts",
        "assets/documents",
        "assets/other",
    ]:
        asset_dir = run_dir / relative_asset_dir
        if not asset_dir.exists():
            issues.append(
                {
                    "type": "asset_dir_missing",
                    "expected": relative_asset_dir,
                    "actual": str(asset_dir),
                }
            )

    expected_state = case.get("expected_initial_state")
    if expected_state and status["workflow_state"] != expected_state:
        issues.append({"type": "initial_state_mismatch", "expected": expected_state, "actual": status["workflow_state"]})

    expected_next_type = case.get("expected_initial_next_type")
    actual_next_type = status.get("next_action", {}).get("type")
    if expected_next_type and actual_next_type != expected_next_type:
        issues.append({"type": "initial_next_type_mismatch", "expected": expected_next_type, "actual": actual_next_type})

    expected_c_enabled = case.get("expected_c_enabled")
    actual_c_enabled = manifest["summary"]["c_activation"]["enabled"]
    if expected_c_enabled is not None and actual_c_enabled != expected_c_enabled:
        issues.append({"type": "c_enabled_mismatch", "expected": expected_c_enabled, "actual": actual_c_enabled})

    missing_triggers = sorted(set(case.get("expected_triggers", [])) - set(manifest["summary"]["c_activation"]["triggered_by"]))
    if missing_triggers:
        issues.append({"type": "missing_triggers", "items": missing_triggers})

    missing_warnings = sorted(set(case.get("expected_warning_codes", [])) - warning_codes(status))
    if missing_warnings:
        issues.append({"type": "missing_warning_codes", "items": missing_warnings})

    continuation_checks: dict[str, Any] = {}
    if isinstance(case.get("continuation_fixture"), dict):
        continuation_checks = exercise_continuation_fixture(case["continuation_fixture"], run_dir)
        issues.extend(continuation_checks["issues"])

    lifecycle_states: list[str] = []
    if case.get("run_lifecycle"):
        fill_input_for_case(run_dir)
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])
        fill_router_for_case(run_dir)
        route_result = workflow_runner.route_decision_validator.validate_route_decision(
            workflow_runner.load_json(run_dir / "02_router" / "data" / "facet_router_request.json"),
            workflow_runner.load_json(run_dir / "02_router" / "data" / "facet_router_filled.json"),
        )
        workflow_runner.write_json(run_dir / "03_route_validation" / "data" / "route_decision_validation.json", route_result)
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])

        direction_request = workflow_runner.direction_lens_builder.build_direction_request(
            workflow_runner.load_json(run_dir / "02_router" / "data" / "facet_router_filled.json")
        )
        workflow_runner.write_json(run_dir / "04_direction_lens" / "data" / "direction_lens_request.json", direction_request)
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])

        fill_direction_for_case(run_dir)
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])

        direction_result = workflow_runner.direction_lens_builder.validate_direction_lens(
            workflow_runner.load_json(run_dir / "04_direction_lens" / "data" / "direction_lens_request.json"),
            workflow_runner.load_json(run_dir / "04_direction_lens" / "data" / "direction_lens_filled.json"),
        )
        workflow_runner.write_json(run_dir / "04_direction_lens" / "data" / "direction_lens_validation.json", direction_result)
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])

        context_request = workflow_runner.situation_context_builder.build_context_request(
            workflow_runner.load_json(run_dir / "02_router" / "data" / "facet_router_filled.json"),
            workflow_runner.load_json(run_dir / "04_direction_lens" / "data" / "direction_lens_filled.json"),
        )
        workflow_runner.write_json(run_dir / "05_situation_context" / "data" / "situation_context_request.json", context_request)
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])

        fill_context_for_case(run_dir)
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])

        context_result = workflow_runner.situation_context_builder.validate_situation_context(
            workflow_runner.load_json(run_dir / "05_situation_context" / "data" / "situation_context_request.json"),
            workflow_runner.load_json(run_dir / "05_situation_context" / "data" / "situation_context_filled.json"),
        )
        workflow_runner.write_json(run_dir / "05_situation_context" / "data" / "situation_context_validation.json", context_result)
        report_required_status = workflow_runner.build_workflow_status(run_dir)
        lifecycle_states.append(report_required_status["workflow_state"])

        report_manifest = workflow_runner.build_human_report_for_workflow(run_dir)
        if not Path(report_manifest["report_file"]).exists():
            issues.append({"type": "human_report_missing", "expected": "report file exists", "actual": report_manifest["report_file"]})
        if not Path(report_manifest["summary_file"]).exists():
            issues.append({"type": "human_report_summary_missing", "expected": "summary file exists", "actual": report_manifest["summary_file"]})
        else:
            report_summary = workflow_runner.load_json(Path(report_manifest["summary_file"]))
            if "artifacts" not in report_summary:
                issues.append(
                    {
                        "type": "human_report_artifacts_missing",
                        "expected": "report summary includes artifacts",
                        "actual": sorted(report_summary.keys()),
                    }
                )

        fulfillment_request = workflow_runner.fulfillment.build_contract_request(
            raw_text=case["text"],
            needed_output="python_script_or_implementation_plan",
            context_next_action={"action_type": "run_framework", "target": "ipo"},
        )
        workflow_runner.write_json(
            run_dir / "07_fulfillment" / "data" / "contract_request.json",
            fulfillment_request,
        )
        lifecycle_states.append(workflow_runner.build_workflow_status(run_dir)["workflow_state"])

        contract = {
            "fulfillment_contract_version": workflow_runner.fulfillment.FULFILLMENT_VERSION,
            "contract_status": "ready",
            "requested_output": {
                "description": "A durable implementation-plan report for the requested automation",
                "deliverable_type": "report",
                "format": "markdown",
                "minimum_count": 1,
                "unit": "file",
            },
            "acceptance_criteria": [
                {
                    "id": "AC-01",
                    "description": "The report contains an observable automation implementation plan.",
                    "source": "explicit",
                }
            ],
            "artifact_policy": {
                "finalization_mode": "managed_deliverable",
                "minimum_registered_artifacts": 1,
                "require_project_deliverable": True,
                "require_milestone_snapshot": True,
            },
            "risk": {"level": "low", "requires_user_approval": False},
            "needs_user_input": {"required": False, "questions": []},
            "not_required_reason": "",
            "agent_notes": "workflow smoke fixture",
        }
        contract_path = run_dir / "07_fulfillment" / "data" / "contract_filled.json"
        workflow_runner.write_json(contract_path, contract)

        project_root = workflow_base_dir.parent
        deliverables_root = project_root / "deliverables"
        deliverables_root.mkdir(parents=True, exist_ok=True)
        result_path = deliverables_root / "automation_plan.md"
        result_path.write_text("# Automation Plan\n\n1. Read input.\n2. Aggregate monthly sales.\n3. Write output.\n", encoding="utf-8")
        artifact_store.register_artifact(
            run_dir,
            artifact_id="automation_plan",
            artifact_type="document",
            role="generated_output",
            path=str(result_path),
            source_step="workflow_smoke_fixture",
            copy_into_run=True,
        )
        evidence = {
            "fulfillment_evidence_version": workflow_runner.fulfillment.FULFILLMENT_VERSION,
            "result_status": "fulfilled",
            "artifact_ids": ["automation_plan"],
            "deliverable_paths": [str(result_path)],
            "criteria_results": [
                {
                    "criterion_id": "AC-01",
                    "status": "pass",
                    "evidence": "automation_plan.md contains the three-step implementation plan.",
                }
            ],
            "agent_summary": "Created and registered the requested automation plan.",
        }
        evidence_path = run_dir / "07_fulfillment" / "data" / "evidence_filled.json"
        workflow_runner.write_json(evidence_path, evidence)
        workflow_manifest_path = run_dir / "workflow_manifest.json"
        workflow_manifest = workflow_runner.load_json(workflow_manifest_path)
        workflow_manifest["project_root_absolute"] = str(project_root)
        workflow_manifest["deliverable_paths"] = [{"path_absolute": str(result_path)}]
        workflow_runner.write_json(workflow_manifest_path, workflow_manifest)
        fulfillment_result = workflow_runner.fulfillment.validate_fulfillment(
            run_dir, workflow_manifest, contract, evidence
        )
        workflow_runner.write_json(
            run_dir / "07_fulfillment" / "data" / "validation.json",
            fulfillment_result,
        )
        if fulfillment_result.get("valid") is not True:
            issues.append({"type": "fulfillment_validation_failed", "actual": fulfillment_result})
        workflow_runner.build_human_report_for_workflow(run_dir)

        final_status = workflow_runner.build_workflow_status(run_dir)
        lifecycle_states.append(final_status["workflow_state"])
        workflow_runner.write_workflow_status_files(run_dir, final_status)
        if final_status["workflow_state"] != "request_completed":
            issues.append({"type": "lifecycle_final_state_mismatch", "expected": "request_completed", "actual": final_status["workflow_state"]})
        if final_status.get("summary", {}).get("next_required_action") != "none":
            issues.append(
                {
                    "type": "stale_summary_next_action",
                    "expected": "none",
                    "actual": final_status.get("summary", {}).get("next_required_action"),
                }
            )

        workflow_runner.initialize_continuation_for_workflow(
            run_dir,
            current_phase="awaiting_user_review",
            active_artifact_ids=["automation_plan"],
            next_action_types=["review_continuation_result"],
            decision_note="The follow-up report is ready for user review.",
            working_root=str(project_root),
            completion_gate="approved",
        )
        synchronized_status = workflow_runner.synchronize_workflow_outputs(run_dir)
        synchronized_summary = workflow_runner.load_json(
            run_dir / "06_human_readable_report" / "data" / "report_summary.json"
        )
        if (
            synchronized_status.get("workflow_state") != "continuation_waiting_user"
            or synchronized_summary.get("workflow_state") != "continuation_waiting_user"
            or synchronized_summary.get("quality_gate", {}).get("decision") != "사용자 검토 대기"
            or synchronized_summary.get("continuation", {}).get("current_phase") != "awaiting_user_review"
        ):
            issues.append(
                {
                    "type": "continuation_projection_sync_failed",
                    "expected": "status and human report both wait for user review",
                    "actual": {
                        "status": synchronized_status.get("workflow_state"),
                        "report": synchronized_summary.get("workflow_state"),
                        "quality_gate": synchronized_summary.get("quality_gate"),
                        "continuation": synchronized_summary.get("continuation"),
                    },
                }
            )

    return {
        "id": case["id"],
        "pass": not issues,
        "issues": issues,
        "run_dir": str(run_dir),
        "initial_state": status["workflow_state"],
        "initial_next_type": actual_next_type,
        "c_enabled": actual_c_enabled,
        "lifecycle_states": lifecycle_states,
        "continuation_state_file": continuation_checks.get("state_file"),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["pass"])
    issue_counts: dict[str, int] = {}
    for item in results:
        for issue in item["issues"]:
            issue_counts[issue["type"]] = issue_counts.get(issue["type"], 0) + 1
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "score_100": round((passed / total) * 100, 2) if total else 0.0,
        "issue_counts": issue_counts,
    }


def build_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    rows = [
        "| Case | Result | Initial State | Initial Next | C Enabled | Lifecycle | Issues |",
        "|---|---|---|---|---:|---|---|",
    ]
    for item in results:
        lifecycle = ", ".join(item["lifecycle_states"]) or "not_run"
        issues = ", ".join(issue["type"] for issue in item["issues"]) or "none"
        rows.append(
            f"| {item['id']} | {'PASS' if item['pass'] else 'FAIL'} | {item['initial_state']} | {item['initial_next_type']} | {item['c_enabled']} | {lifecycle} | {issues} |"
        )
    return "\n".join(
        [
            "# Workflow Smoke Test Report",
            "",
            "## Summary",
            "",
            f"- Total: {summary['total']}",
            f"- Passed: {summary['passed']}",
            f"- Failed: {summary['failed']}",
            f"- Score: {summary['score_100']} / 100",
            "",
            "## Results",
            "",
            *rows,
            "",
        ]
    )


def run_tests(cases_path: Path, output_dir: Path, run_name: str | None) -> dict[str, Any]:
    cases = load_cases(cases_path)
    run_dir = unique_run_dir(output_dir, run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    workflow_runs_dir = data_dir / "workflow_runs"
    data_dir.mkdir()
    outputs_dir.mkdir()
    workflow_runs_dir.mkdir()

    results = [evaluate_case(case, workflow_runs_dir) for case in cases]
    summary = summarize(results)

    write_json(data_dir / "workflow_smoke_test_cases.json", {"cases": cases})
    write_json(data_dir / "workflow_smoke_test_results.json", {"summary": summary, "results": results})
    report_path = outputs_dir / "workflow_smoke_test_report.md"
    report_path.write_text(build_report(summary, results), encoding="utf-8")

    manifest = {
        "run_dir": str(run_dir),
        "cases_file": str(data_dir / "workflow_smoke_test_cases.json"),
        "results_file": str(data_dir / "workflow_smoke_test_results.json"),
        "report_file": str(report_path),
        "workflow_runs_dir": str(workflow_runs_dir),
        "summary": summary,
    }
    write_json(outputs_dir / "workflow_smoke_test_manifest.json", manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run workflow status/next smoke tests.")
    parser.add_argument("--cases", default=str(PROJECT_ROOT / "tests" / "cases" / "workflow_smoke_tests.json"))
    parser.add_argument("--output", default=str(PROJECT_ROOT / "tests" / "artifacts" / "test_runs"))
    parser.add_argument("--run-name", help="Optional test run folder name.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = run_tests(Path(args.cases), Path(args.output), args.run_name)
    print(to_json(manifest))
    return 0 if manifest["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
