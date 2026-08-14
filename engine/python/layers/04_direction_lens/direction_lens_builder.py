from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


DIRECTION_LENS_VERSION = "0.1.0"


SCRIPT_DIR = Path(__file__).resolve().parent


def find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "agents" / "agent.md").exists() and (candidate / "tests").exists():
            return candidate
    return start


PROJECT_ROOT = find_project_root(SCRIPT_DIR)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.run_identity import unique_run_dir as identity_unique_run_dir

TEST_CASES_DIR = PROJECT_ROOT / "tests" / "cases"

LENS_CATALOG: dict[str, dict[str, str]] = {
    "5w1h": {
        "purpose": "Clarify who, what, when, where, why, and how.",
        "best_for": "unclear user input or missing decision basis",
    },
    "mece": {
        "purpose": "Check whether the analysis buckets are mutually exclusive and collectively exhaustive.",
        "best_for": "coverage and missing-area checks",
    },
    "gap_analysis": {
        "purpose": "Compare current information with needed information.",
        "best_for": "clarification and missing basis",
    },
    "assumption_mapping": {
        "purpose": "Separate evidence from assumptions and identify risky guesses.",
        "best_for": "uncertain or inferred-heavy routing",
    },
    "mandalart": {
        "purpose": "Expand a situation into structured domains and subdomains.",
        "best_for": "situation map and domain exploration",
    },
    "ipo": {
        "purpose": "Map input, process, and output.",
        "best_for": "automation, data processing, and workflow design",
    },
    "risk_matrix": {
        "purpose": "Classify impact and likelihood of risk.",
        "best_for": "legal, privacy, security, payment, medical, or financial risk",
    },
    "issue_tree": {
        "purpose": "Break a problem into diagnostic branches.",
        "best_for": "debugging, error analysis, and root-cause exploration",
    },
}

ROUTE_LENS_POLICY: dict[str, dict[str, list[str]]] = {
    "ask_user": {
        "required_any": ["5w1h", "gap_analysis"],
        "recommended": ["5w1h", "gap_analysis"],
    },
    "run_framework_check": {
        "required_any": ["mece", "assumption_mapping", "issue_tree", "risk_matrix", "ipo"],
        "recommended": ["mece", "assumption_mapping"],
    },
    "use_situation_map": {
        "required_any": ["mandalart"],
        "recommended": ["mandalart", "mece"],
    },
    "use_ipo": {
        "required_any": ["ipo"],
        "recommended": ["ipo", "mece"],
    },
    "risk_review": {
        "required_any": ["risk_matrix"],
        "recommended": ["risk_matrix", "assumption_mapping", "mece"],
    },
    "proceed_to_solution": {
        "required_any": [],
        "recommended": ["ipo"],
    },
}

HIGH_RISK_SAFE_STATUSES = {
    "needs_risk_review",
    "needs_more_input",
    "ready_for_framework_check",
}

VALID_DIRECTION_STATUSES = {
    "ready_for_next_step",
    "needs_more_input",
    "needs_risk_review",
    "ready_for_framework_check",
    "unresolved",
}


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name, default_suffix="direction_lens")


def route_name(filled_router: dict[str, Any]) -> str:
    return str(filled_router.get("route_decision", {}).get("selected_route", "")).strip()


def route_status(filled_router: dict[str, Any]) -> str:
    return str(filled_router.get("route_decision", {}).get("route_status", "")).strip()


def risk_level(filled_router: dict[str, Any]) -> str:
    facet = filled_router.get("facet_classification", {}).get("risk_level", {})
    if isinstance(facet, dict):
        return str(facet.get("value", "")).strip().casefold()
    return str(facet).strip().casefold()


def is_high_risk(filled_router: dict[str, Any]) -> bool:
    return risk_level(filled_router) in {"high", "고위험", "높음"}


def recommended_lenses_for_route(selected_route: str, filled_router: dict[str, Any]) -> list[str]:
    base = list(ROUTE_LENS_POLICY.get(selected_route, {}).get("recommended", []))
    if is_high_risk(filled_router) and "risk_matrix" not in base:
        base.insert(0, "risk_matrix")
    source_type = filled_router.get("facet_classification", {}).get("source_type", {})
    source_value = source_type.get("value", "") if isinstance(source_type, dict) else source_type
    source_text = str(source_value).casefold()
    if ("error" in source_text or "log" in source_text) and "issue_tree" not in base:
        base.append("issue_tree")
    return base


def build_direction_request(filled_router: dict[str, Any], *, created_at: str | None = None) -> dict[str, Any]:
    selected_route = route_name(filled_router)
    timestamp = created_at or datetime.now().isoformat(timespec="seconds")
    recommended_lenses = recommended_lenses_for_route(selected_route, filled_router)
    policy = ROUTE_LENS_POLICY.get(selected_route, {"required_any": [], "recommended": []})

    return {
        "direction_lens_version": DIRECTION_LENS_VERSION,
        "engine_role": "direction_lens_request_builder",
        "contract_name": "ai_fillable_problem_direction_lens_request",
        "created_at": timestamp,
        "source_router": {
            "route_status": route_status(filled_router),
            "selected_route": selected_route,
            "route_decision": filled_router.get("route_decision", {}),
            "facet_classification": filled_router.get("facet_classification", {}),
            "missing_decision_basis": filled_router.get("missing_decision_basis", []),
            "reference_lenses": filled_router.get("reference_lenses", []),
        },
        "lens_catalog": LENS_CATALOG,
        "lens_policy": {
            "required_any": policy.get("required_any", []),
            "recommended": recommended_lenses,
            "high_risk_safe_statuses": sorted(HIGH_RISK_SAFE_STATUSES),
            "valid_direction_statuses": sorted(VALID_DIRECTION_STATUSES),
        },
        "ai_task": {
            "task_name": "fill_problem_direction_lens",
            "instruction": (
                "Use the validated router output to choose analysis lenses and define the next problem-solving direction. "
                "Do not solve the user's original problem in this stage."
            ),
            "allowed_mutations": [
                "selected_lenses",
                "direction_decision",
                "coverage_check",
                "missing_basis",
                "next_action",
            ],
        },
        "selected_lenses": [],
        "direction_decision": {
            "direction_status": "ready_for_next_step | needs_more_input | needs_risk_review | ready_for_framework_check | unresolved",
            "problem_direction": "",
            "why_this_direction": "",
            "evidence": "",
        },
        "coverage_check": {
            "mece_checked": False,
            "missing_areas": [],
            "assumptions_to_verify": [],
        },
        "missing_basis": [],
        "next_action": {
            "action_type": "ask_user | run_lens | use_situation_map | proceed",
            "reason": "",
        },
        "output_rules": [
            "Do not solve the original problem.",
            "Select only lenses that exist in lens_catalog.",
            "Follow lens_policy.required_any for the selected route.",
            "High-risk inputs must not use direction_status ready_for_next_step.",
            "If direction_status is needs_more_input or unresolved, missing_basis must not be empty.",
        ],
    }


def violation(code: str, severity: str, path: str, message: str) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "path": path,
        "message": message,
    }


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def validate_direction_lens(request: dict[str, Any], filled: dict[str, Any]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    catalog = set(request.get("lens_catalog", LENS_CATALOG).keys())
    policy = request.get("lens_policy", {})
    selected_route = request.get("source_router", {}).get("selected_route", "")
    selected_lenses = filled.get("selected_lenses", [])

    if not isinstance(selected_lenses, list):
        violations.append(
            violation("SELECTED_LENSES_INVALID", "fail", "selected_lenses", "selected_lenses must be a list.")
        )
        selected_lenses = []

    selected_set = {as_text(item) for item in selected_lenses if as_text(item)}
    unknown_lenses = sorted(item for item in selected_set if item not in catalog)
    if unknown_lenses:
        violations.append(
            violation(
                "UNKNOWN_LENS",
                "fail",
                "selected_lenses",
                f"Unknown lenses are not allowed: {', '.join(unknown_lenses)}",
            )
        )

    required_any = set(policy.get("required_any", []))
    if required_any and not selected_set.intersection(required_any):
        violations.append(
            violation(
                "REQUIRED_LENS_MISSING",
                "fail",
                "selected_lenses",
                f"Route {selected_route} requires one of: {', '.join(sorted(required_any))}",
            )
        )

    decision = filled.get("direction_decision", {})
    if not isinstance(decision, dict):
        decision = {}
        violations.append(
            violation("DIRECTION_DECISION_INVALID", "fail", "direction_decision", "direction_decision must be an object.")
        )

    direction_status = as_text(decision.get("direction_status", ""))
    if direction_status not in VALID_DIRECTION_STATUSES:
        violations.append(
            violation(
                "DIRECTION_STATUS_INVALID",
                "fail",
                "direction_decision.direction_status",
                "direction_status is not allowed.",
            )
        )

    for field_name in ["problem_direction", "why_this_direction", "evidence"]:
        if not as_text(decision.get(field_name, "")):
            violations.append(
                violation(
                    "DIRECTION_FIELD_REQUIRED",
                    "fail",
                    f"direction_decision.{field_name}",
                    f"{field_name} is required.",
                )
            )

    source_router = request.get("source_router", {})
    risk_facet = source_router.get("facet_classification", {}).get("risk_level", {})
    risk_value = risk_facet.get("value", "") if isinstance(risk_facet, dict) else risk_facet
    high_risk = str(risk_value).casefold() in {"high", "고위험", "높음"}
    if high_risk:
        if "risk_matrix" not in selected_set:
            violations.append(
                violation(
                    "HIGH_RISK_REQUIRES_RISK_LENS",
                    "fail",
                    "selected_lenses",
                    "High-risk inputs require risk_matrix.",
                )
            )
        if direction_status not in HIGH_RISK_SAFE_STATUSES:
            violations.append(
                violation(
                    "HIGH_RISK_UNSAFE_DIRECTION_STATUS",
                    "fail",
                    "direction_decision.direction_status",
                    "High-risk inputs must not be marked ready_for_next_step.",
                )
            )

    missing_basis = filled.get("missing_basis", [])
    if not isinstance(missing_basis, list):
        violations.append(
            violation("MISSING_BASIS_INVALID", "fail", "missing_basis", "missing_basis must be a list.")
        )
        missing_basis = []
    if direction_status in {"needs_more_input", "unresolved"} and not missing_basis:
        violations.append(
            violation(
                "MISSING_BASIS_REQUIRED",
                "fail",
                "missing_basis",
                "needs_more_input or unresolved requires missing_basis.",
            )
        )

    coverage_check = filled.get("coverage_check", {})
    if not isinstance(coverage_check, dict):
        violations.append(
            violation("COVERAGE_CHECK_INVALID", "fail", "coverage_check", "coverage_check must be an object.")
        )
    elif "mece" in selected_set and coverage_check.get("mece_checked") is not True:
        violations.append(
            violation(
                "MECE_LENS_WITHOUT_COVERAGE_CHECK",
                "warn",
                "coverage_check.mece_checked",
                "If mece is selected, mece_checked should be true.",
            )
        )

    next_action = filled.get("next_action", {})
    if not isinstance(next_action, dict):
        next_action = {}
        violations.append(
            violation("NEXT_ACTION_INVALID", "fail", "next_action", "next_action must be an object.")
        )
    if as_text(next_action.get("action_type", "")) not in {"ask_user", "run_lens", "use_situation_map", "proceed"}:
        violations.append(
            violation(
                "NEXT_ACTION_TYPE_INVALID",
                "fail",
                "next_action.action_type",
                "next_action.action_type is not allowed.",
            )
        )
    if not as_text(next_action.get("reason", "")):
        violations.append(
            violation("NEXT_ACTION_REASON_REQUIRED", "fail", "next_action.reason", "next_action.reason is required.")
        )

    fail_count = sum(1 for item in violations if item["severity"] == "fail")
    warn_count = sum(1 for item in violations if item["severity"] == "warn")
    severity = "fail" if fail_count else "warn" if warn_count else "pass"
    return {
        "valid": fail_count == 0,
        "severity": severity,
        "violations": violations,
        "summary": {
            "fail_count": fail_count,
            "warn_count": warn_count,
        },
    }


def build_run(filled_router: dict[str, Any], output_dir: Path, run_name: str | None) -> dict[str, Any]:
    run_dir = unique_run_dir(output_dir, run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    data_dir.mkdir()
    outputs_dir.mkdir()

    request = build_direction_request(filled_router)
    request_path = data_dir / "direction_lens_request.json"
    write_json(request_path, request)

    manifest_path = outputs_dir / "direction_lens_manifest.json"
    report_path = outputs_dir / "direction_lens_report.md"
    report_path.write_text(
        "\n".join(
            [
                "# Direction Lens Request Report",
                "",
                f"- Selected route: `{request['source_router']['selected_route']}`",
                f"- Required lens candidates: `{', '.join(request['lens_policy']['required_any']) or 'none'}`",
                f"- Recommended lenses: `{', '.join(request['lens_policy']['recommended']) or 'none'}`",
                f"- Request file: `{request_path}`",
                "",
            ]
        ),
        encoding="utf-8",
    )

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
    manifest = build_run(filled_router, Path(args.output), args.run_name)
    print(to_json(manifest))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    request = load_json_file(Path(args.request))
    filled = load_json_file(Path(args.filled))
    report = validate_direction_lens(request, filled)
    print(to_json(report))
    return 0 if report["valid"] else 1


def build_test_payload(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    router_filled = case["router_filled"]
    request = build_direction_request(router_filled, created_at="test")
    return request, case["direction_filled"]


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = load_json_file(path)
    cases = data.get("cases", data)
    if not isinstance(cases, list):
        raise SystemExit("Direction lens test file must contain a list or an object with cases.")
    return cases


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    request, filled = build_test_payload(case)
    report = validate_direction_lens(request, filled)
    expected_valid = bool(case["expected_valid"])
    expected_codes = set(case.get("expected_codes", []))
    actual_codes = {item["code"] for item in report["violations"]}
    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "expected_valid": expected_valid,
        "actual_valid": report["valid"],
        "expected_codes": sorted(expected_codes),
        "actual_codes": sorted(actual_codes),
        "missing_expected_codes": sorted(expected_codes - actual_codes),
        "pass": report["valid"] == expected_valid and not (expected_codes - actual_codes),
        "validation_report": report,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["pass"])
    false_accept = sum(1 for item in results if not item["expected_valid"] and item["actual_valid"])
    false_reject = sum(1 for item in results if item["expected_valid"] and not item["actual_valid"])
    code_counts: dict[str, int] = {}
    for item in results:
        for code in item["actual_codes"]:
            code_counts[code] = code_counts.get(code, 0) + 1
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "score_100": round((passed / total) * 100, 2) if total else 0.0,
        "false_accept": false_accept,
        "false_reject": false_reject,
        "violation_code_counts": code_counts,
    }


def build_test_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    rows = [
        "| Case | Category | Result | Expected | Actual | Codes |",
        "|---|---|---|---|---|---|",
    ]
    for item in results:
        rows.append(
            "| {case} | {category} | {result} | {expected} | {actual} | {codes} |".format(
                case=item["id"],
                category=item["category"],
                result="PASS" if item["pass"] else "FAIL",
                expected="valid" if item["expected_valid"] else "invalid",
                actual="valid" if item["actual_valid"] else "invalid",
                codes=", ".join(item["actual_codes"]) or "none",
            )
        )
    return "\n".join(
        [
            "# Direction Lens Validation Test Report",
            "",
            "## Summary",
            "",
            f"- Total: {summary['total']}",
            f"- Passed: {summary['passed']}",
            f"- Failed: {summary['failed']}",
            f"- Score: {summary['score_100']} / 100",
            f"- False accept: {summary['false_accept']}",
            f"- False reject: {summary['false_reject']}",
            "",
            "## Results",
            "",
            *rows,
            "",
        ]
    )


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

    write_json(data_dir / "direction_lens_test_cases.json", {"cases": cases})
    write_json(data_dir / "direction_lens_test_results.json", {"summary": summary, "results": results})
    report_path = outputs_dir / "direction_lens_test_report.md"
    report_path.write_text(build_test_report(summary, results), encoding="utf-8")

    manifest = {
        "run_dir": str(run_dir),
        "cases_file": str(data_dir / "direction_lens_test_cases.json"),
        "results_file": str(data_dir / "direction_lens_test_results.json"),
        "report_file": str(report_path),
        "summary": summary,
    }
    write_json(outputs_dir / "direction_lens_test_manifest.json", manifest)
    print(to_json(manifest))
    return 0 if summary["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate problem direction lens requests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build a direction lens request from filled router output.")
    build_parser.add_argument("--filled-router", required=True, help="Agent-filled facet router JSON.")
    build_parser.add_argument("--output", default=str(PROJECT_ROOT / "outputs" / "runs"), help="Base output directory.")
    build_parser.add_argument("--run-name", help="Optional run folder name.")
    build_parser.set_defaults(func=command_build)

    validate_parser = subparsers.add_parser("validate", help="Validate a filled direction lens JSON.")
    validate_parser.add_argument("--request", required=True, help="Direction lens request JSON.")
    validate_parser.add_argument("--filled", required=True, help="Agent-filled direction lens JSON.")
    validate_parser.set_defaults(func=command_validate)

    test_parser = subparsers.add_parser("test", help="Run direction lens validation tests.")
    test_parser.add_argument("--cases", default=str(TEST_CASES_DIR / "direction_lens_tests.json"), help="Direction lens test cases JSON.")
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
