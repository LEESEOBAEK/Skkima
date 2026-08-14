from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from contract import (
    CONTEXT_OBJECT_EXAMPLES,
    DETAIL_POLICIES,
    DIMENSION_CATALOG,
    PRIMARY_STRUCTURE_AXES,
    REQUIRED_CONTEXT_FIELDS,
    SITUATION_CONTEXT_VERSION,
    START_AREA_CANDIDATES,
    SUBCATEGORY_SEEDS,
    VALID_NEXT_ACTION_TYPES,
    VALID_PRIMARY_ACTORS,
    VALID_REQUEST_CONTEXTS,
    VALID_SITUATION_PHASES,
)
from reporting import build_request_report, build_test_report
from runner_support import (
    as_text,
    build_test_payload as _build_test_payload,
    ensure_list,
    evaluate_case as _evaluate_case,
    find_project_root,
    load_json_file,
    load_cases,
    summarize,
    to_json,
    unique_run_dir,
    write_json,
)
from sources import build_source_direction, build_source_router, facet_value
from templates import empty_actor_scope, empty_context_map, empty_exploration_path
from validation import (
    list_texts,
    needs_missing_context,
    validate_actor_scope,
    validate_candidate_selection,
    validate_context_links,
    validate_exploration_path,
    validate_mandalart_view,
    validate_situation_context,
    violation,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = find_project_root(SCRIPT_DIR)
TEST_CASES_DIR = PROJECT_ROOT / "tests" / "cases"


def build_context_request(
    filled_router: dict[str, Any],
    direction_filled: dict[str, Any] | None = None,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or datetime.now().isoformat(timespec="seconds")
    return {
        "situation_context_version": SITUATION_CONTEXT_VERSION,
        "engine_role": "situation_context_request_builder",
        "contract_name": "ai_fillable_situation_context_map_request",
        "created_at": timestamp,
        "source_router": build_source_router(filled_router),
        "source_direction": build_source_direction(direction_filled),
        "dimension_catalog": DIMENSION_CATALOG,
        "valid_actor_scope_values": {
            "primary_actor": sorted(VALID_PRIMARY_ACTORS),
            "request_context": sorted(VALID_REQUEST_CONTEXTS),
            "unknown_policy": (
                "Use unknown when personal/company/team ownership is not explicitly supported. "
                "Unknown values require missing_context instead of forced guessing."
            ),
            "self_check_policy": (
                "classification_self_check must record the minimum evidence used, competing actor "
                "classifications, why the selected actor is preferred, and when it could be wrong."
            ),
        },
        "valid_situation_phases": sorted(VALID_SITUATION_PHASES),
        "ai_task": {
            "task_name": "fill_situation_context_map",
            "instruction": (
                "Use the validated router and direction outputs to map the situation context. "
                "Do not solve the original user problem. Build a context map that helps the next agent understand "
                "the domain, situation, problem type, missing context, and next focus."
            ),
            "allowed_mutations": [
                "situation_context_map",
                "next_action",
            ],
        },
        "situation_context_map": empty_context_map(),
        "next_action": {
            "action_type": "ask_user | run_context_map | run_framework | proceed | risk_review",
            "target": "",
            "reason": "",
        },
        "output_rules": [
            "Do not solve the user's original problem inside this file.",
            "Do not force a Mandalart structure. Mandalart is only an optional view.",
            "Use explicit evidence when available and mark missing context instead of inventing details.",
            "If the route or direction requires more input, missing_context must not be empty.",
            "context_links must describe actual relationships between context map nodes.",
            "confidence must be between 0.0 and 1.0.",
            "actor_scope must classify the ownership subject separately from the problem area.",
            "Use actor_scope.primary_actor=unknown when personal/company/team ownership is unclear.",
            "Unknown actor_scope values require missing_context instead of forced guessing.",
            "actor_scope.classification_self_check is required to reduce unsupported actor classification.",
            "For unknown or mixed actor_scope, competing_classifications and could_be_wrong_if must not be empty.",
            "Fill experimental_context_exploration_path during the early experiment period.",
            "Use B_minimum_detail_required by default.",
            "Use A_full_detail_required when high-risk, complex, planning-heavy, or framework-basis depth is needed.",
            "Stage selections must come from default candidates or agent-added candidates.",
            "Every activated axis must have enough stage_5 detail items for the selected detail policy.",
        ],
    }


def build_test_payload(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return _build_test_payload(case, build_context_request)


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    return _evaluate_case(case, build_context_request, validate_situation_context)


def build_run(
    filled_router: dict[str, Any],
    direction_filled: dict[str, Any] | None,
    output_dir: Path,
    run_name: str | None,
) -> dict[str, Any]:
    run_dir = unique_run_dir(output_dir, run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    data_dir.mkdir()
    outputs_dir.mkdir()

    request = build_context_request(filled_router, direction_filled)
    request_path = data_dir / "situation_context_request.json"
    write_json(request_path, request)

    report_path = outputs_dir / "situation_context_report.md"
    report_path.write_text(build_request_report(request, request_path), encoding="utf-8")

    manifest_path = outputs_dir / "situation_context_manifest.json"
    manifest = {
        "run_dir": str(run_dir),
        "request_file": str(request_path),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
    }
    write_json(manifest_path, manifest)
    return manifest


def command_build(args: argparse.Namespace) -> int:
    filled_router = load_json_file(Path(args.filled_router))
    direction_filled = load_json_file(Path(args.filled_direction)) if args.filled_direction else None
    manifest = build_run(filled_router, direction_filled, Path(args.output), args.run_name)
    print(to_json(manifest))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    request = load_json_file(Path(args.request))
    filled = load_json_file(Path(args.filled))
    report = validate_situation_context(request, filled)
    print(to_json(report))
    return 0 if report["valid"] else 1


def command_test(args: argparse.Namespace) -> int:
    cases = load_cases(Path(args.cases))
    results = [evaluate_case(case) for case in cases]
    summary = summarize(results)

    run_dir = unique_run_dir(Path(args.output), args.run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    data_dir.mkdir()
    outputs_dir.mkdir()

    write_json(data_dir / "situation_context_test_cases.json", {"cases": cases})
    write_json(data_dir / "situation_context_test_results.json", {"summary": summary, "results": results})
    report_path = outputs_dir / "situation_context_test_report.md"
    report_path.write_text(build_test_report(summary, results), encoding="utf-8")

    manifest = {
        "run_dir": str(run_dir),
        "cases_file": str(data_dir / "situation_context_test_cases.json"),
        "results_file": str(data_dir / "situation_context_test_results.json"),
        "report_file": str(report_path),
        "summary": summary,
    }
    write_json(outputs_dir / "situation_context_test_manifest.json", manifest)
    print(to_json(manifest))
    return 0 if summary["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate Situation Context Map requests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build a Situation Context Map request.")
    build_parser.add_argument("--filled-router", required=True, help="Agent-filled facet router JSON.")
    build_parser.add_argument("--filled-direction", help="Optional agent-filled direction lens JSON.")
    build_parser.add_argument("--output", default=str(PROJECT_ROOT / "outputs" / "runs"), help="Base output directory.")
    build_parser.add_argument("--run-name", help="Optional run folder name.")
    build_parser.set_defaults(func=command_build)

    validate_parser = subparsers.add_parser("validate", help="Validate a filled Situation Context Map JSON.")
    validate_parser.add_argument("--request", required=True, help="Situation context request JSON.")
    validate_parser.add_argument("--filled", required=True, help="Agent-filled situation context JSON.")
    validate_parser.set_defaults(func=command_validate)

    test_parser = subparsers.add_parser("test", help="Run Situation Context Map validation tests.")
    test_parser.add_argument(
        "--cases",
        default=str(TEST_CASES_DIR / "situation_context_tests.json"),
        help="Situation context test cases JSON.",
    )
    test_parser.add_argument("--output", default=str(PROJECT_ROOT / "tests" / "artifacts" / "test_runs"), help="Base output directory.")
    test_parser.add_argument("--run-name", help="Optional run folder name.")
    test_parser.set_defaults(func=command_test)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
