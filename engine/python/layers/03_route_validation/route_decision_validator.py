from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent


def find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "agents" / "agent.md").exists() and (candidate / "tests").exists():
            return candidate
    return start


PROJECT_ROOT = find_project_root(SCRIPT_DIR)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAYER_DIRS = [
    PROJECT_ROOT / "layers" / "01_input_structuring",
    PROJECT_ROOT / "layers" / "02_router",
    PROJECT_ROOT / "layers" / "03_route_validation",
    PROJECT_ROOT / "layers" / "04_direction_lens",
]
for layer_dir in LAYER_DIRS:
    if layer_dir.exists() and str(layer_dir) not in sys.path:
        sys.path.insert(0, str(layer_dir))

import facet_router

from shared.run_identity import unique_run_dir as identity_unique_run_dir

TEST_CASES_DIR = PROJECT_ROOT / "tests" / "cases"

PROCEED_ROUTES = {"proceed_to_solution"}
HIGH_RISK_ALLOWED_ROUTES = {"ask_user", "run_framework_check", "risk_review"}
UNRESOLVED_ALLOWED_ROUTES = {"", "unresolved"}
ALTERNATIVE_KEYS = {"alternative_routes", "alternatives", "candidate_routes"}
MIN_SELECTED_ROUTE_CONFIDENCE = 0.65

UNKNOWN_VALUES = {
    "",
    "unknown",
    "unresolved",
    "missing",
    "none",
    "n/a",
    "null",
    "미확정",
    "미상",
    "불명",
}


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name, default_suffix="route_decision_test")


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
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def normalize_value(value: Any) -> str:
    return as_text(value).casefold()


def is_unknown_value(value: Any) -> bool:
    return normalize_value(value) in UNKNOWN_VALUES


def get_facet(filled: dict[str, Any], facet_name: str) -> dict[str, Any]:
    value = filled.get("facet_classification", {}).get(facet_name, {})
    return value if isinstance(value, dict) else {"value": value}


def get_facet_value(filled: dict[str, Any], facet_name: str) -> str:
    facet = get_facet(filled, facet_name)
    value = facet.get("value", "")
    if isinstance(value, dict):
        explicit = value.get("explicit", [])
        inferred = value.get("inferred", [])
        if explicit:
            return as_text(explicit[0])
        if inferred:
            first = inferred[0]
            if isinstance(first, dict):
                return as_text(first.get("content", ""))
            return as_text(first)
        return ""
    return as_text(value)


def get_facet_basis(filled: dict[str, Any], facet_name: str) -> str:
    return normalize_value(get_facet(filled, facet_name).get("basis", "unknown"))


def is_facet_unresolved(filled: dict[str, Any], facet_name: str) -> bool:
    facet = get_facet(filled, facet_name)
    value = get_facet_value(filled, facet_name)
    status = normalize_value(facet.get("status", ""))
    if "unresolved" in status:
        return True
    return is_unknown_value(value)


def is_high_risk(filled: dict[str, Any]) -> bool:
    risk_value = normalize_value(get_facet_value(filled, "risk_level"))
    return risk_value in {"high", "고위험", "높음"}


def route_decision(filled: dict[str, Any]) -> dict[str, Any]:
    route = filled.get("route_decision", {})
    return route if isinstance(route, dict) else {}


def selected_route_name(filled: dict[str, Any]) -> str:
    return as_text(route_decision(filled).get("selected_route", ""))


def route_status(filled: dict[str, Any]) -> str:
    return normalize_value(route_decision(filled).get("route_status", ""))


def route_confidence(filled: dict[str, Any]) -> float | None:
    raw = route_decision(filled).get("route_confidence", None)
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def has_non_empty_text(value: Any) -> bool:
    return bool(as_text(value))


def validate_route_decision(request: dict[str, Any], filled: dict[str, Any]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    route = route_decision(filled)
    status = route_status(filled)
    selected_route = selected_route_name(filled)
    confidence = route_confidence(filled)
    route_catalog = set(request.get("route_catalog") or facet_router.ROUTE_CATALOG)

    for key in ALTERNATIVE_KEYS:
        if key in filled:
            violations.append(
                violation(
                    "ROUTE_ALTERNATIVES_FORBIDDEN",
                    "fail",
                    key,
                    "Route alternatives must not be output in this stage.",
                )
            )
        if key in route:
            violations.append(
                violation(
                    "ROUTE_ALTERNATIVES_FORBIDDEN",
                    "fail",
                    f"route_decision.{key}",
                    "Route alternatives must not be output in route_decision.",
                )
            )

    if status not in {"selected", "unresolved"}:
        violations.append(
            violation(
                "INVALID_ROUTE_STATUS",
                "fail",
                "route_decision.route_status",
                "route_status must be selected or unresolved.",
            )
        )

    if status == "selected":
        if not selected_route:
            violations.append(
                violation(
                    "SELECTED_ROUTE_EMPTY",
                    "fail",
                    "route_decision.selected_route",
                    "selected route status requires a selected_route.",
                )
            )
        elif selected_route not in route_catalog:
            violations.append(
                violation(
                    "UNKNOWN_ROUTE",
                    "fail",
                    "route_decision.selected_route",
                    f"selected_route is not in route_catalog: {selected_route}",
                )
            )
    if status == "unresolved" and selected_route not in UNRESOLVED_ALLOWED_ROUTES:
        violations.append(
            violation(
                "UNRESOLVED_ROUTE_MUST_NOT_SELECT_ROUTE",
                "fail",
                "route_decision.selected_route",
                "unresolved route_status must not select a concrete route.",
            )
        )

    if confidence is None or not 0.0 <= confidence <= 1.0:
        violations.append(
            violation(
                "ROUTE_CONFIDENCE_INVALID",
                "fail",
                "route_decision.route_confidence",
                "route_confidence must be a number from 0.0 to 1.0.",
            )
        )
    elif status == "selected" and confidence < MIN_SELECTED_ROUTE_CONFIDENCE:
        violations.append(
            violation(
                "ROUTE_CONFIDENCE_TOO_LOW",
                "fail",
                "route_decision.route_confidence",
                f"selected route confidence must be >= {MIN_SELECTED_ROUTE_CONFIDENCE}.",
            )
        )

    if not has_non_empty_text(route.get("reason", "")):
        violations.append(
            violation(
                "ROUTE_REASON_REQUIRED",
                "fail",
                "route_decision.reason",
                "route_decision.reason is required.",
            )
        )
    if not has_non_empty_text(route.get("evidence", "")):
        violations.append(
            violation(
                "ROUTE_EVIDENCE_REQUIRED",
                "fail",
                "route_decision.evidence",
                "route_decision.evidence is required.",
            )
        )

    missing_basis = filled.get("missing_decision_basis", [])
    if not isinstance(missing_basis, list):
        violations.append(
            violation(
                "MISSING_DECISION_BASIS_INVALID",
                "fail",
                "missing_decision_basis",
                "missing_decision_basis must be a list.",
            )
        )
        missing_basis = []

    if (status == "unresolved" or selected_route == "ask_user") and not missing_basis:
        violations.append(
            violation(
                "MISSING_DECISION_BASIS_REQUIRED",
                "fail",
                "missing_decision_basis",
                "ask_user or unresolved routes require missing_decision_basis.",
            )
        )

    if is_facet_unresolved(filled, "problem_object") and status == "selected" and selected_route != "ask_user":
        violations.append(
            violation(
                "PROBLEM_OBJECT_UNRESOLVED_ROUTE",
                "fail",
                "facet_classification.problem_object",
                "If problem_object is unresolved, selected route must be ask_user or unresolved.",
            )
        )

    if is_facet_unresolved(filled, "user_intent") and status == "selected" and selected_route != "ask_user":
        violations.append(
            violation(
                "USER_INTENT_UNRESOLVED_ROUTE",
                "fail",
                "facet_classification.user_intent",
                "If user_intent is unresolved, selected route must be ask_user or unresolved.",
            )
        )

    if is_facet_unresolved(filled, "needed_output") and selected_route in PROCEED_ROUTES:
        violations.append(
            violation(
                "NEEDED_OUTPUT_UNRESOLVED_PROCEED",
                "fail",
                "facet_classification.needed_output",
                "If needed_output is unresolved, proceed routes are not allowed.",
            )
        )

    if is_high_risk(filled):
        if selected_route in PROCEED_ROUTES:
            violations.append(
                violation(
                    "HIGH_RISK_DIRECT_PROCEED_FORBIDDEN",
                    "fail",
                    "route_decision.selected_route",
                    "High-risk inputs must not go directly to proceed_to_solution.",
                )
            )
        if status == "selected" and selected_route not in HIGH_RISK_ALLOWED_ROUTES:
            violations.append(
                violation(
                    "HIGH_RISK_ROUTE_NOT_SAFE",
                    "fail",
                    "route_decision.selected_route",
                    "High-risk inputs should route to ask_user, run_framework_check, or risk_review.",
                )
            )

    c_activation = request.get("c_activation", {})
    activated_facets = set(c_activation.get("activated_facets", []))
    inactive_facets = set(c_activation.get("inactive_facets", []))
    facets = filled.get("facet_classification", {})
    if isinstance(facets, dict):
        for facet_name in activated_facets:
            if facet_name not in facets or is_facet_unresolved(filled, facet_name):
                violations.append(
                    violation(
                        "ACTIVATED_C_FACET_NOT_FILLED",
                        "fail",
                        f"facet_classification.{facet_name}",
                        "Activated C facets must be filled by the agent.",
                    )
                )
        for facet_name in inactive_facets:
            if facet_name in facets and not is_facet_unresolved(filled, facet_name):
                violations.append(
                    violation(
                        "INACTIVE_C_FACET_FILLED",
                        "fail",
                        f"facet_classification.{facet_name}",
                        "Inactive C facets must not be filled.",
                    )
                )

    reference_lenses = filled.get("reference_lenses", [])
    if not isinstance(reference_lenses, list):
        violations.append(
            violation(
                "REFERENCE_LENSES_INVALID",
                "fail",
                "reference_lenses",
                "reference_lenses must be a list.",
            )
        )
        reference_lenses = []

    normalized_lenses = {normalize_value(item) for item in reference_lenses}
    if selected_route == "use_ipo" and "ipo" not in normalized_lenses:
        violations.append(
            violation(
                "IPO_ROUTE_WITHOUT_IPO_LENS",
                "warn",
                "reference_lenses",
                "use_ipo route should include ipo in reference_lenses.",
            )
        )
    if selected_route == "risk_review" and not (
        "risk_matrix" in normalized_lenses or "risk" in normalized_lenses
    ):
        violations.append(
            violation(
                "RISK_REVIEW_WITHOUT_RISK_LENS",
                "warn",
                "reference_lenses",
                "risk_review route should include risk_matrix or risk lens.",
            )
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


def facet(value: Any, *, basis: str = "explicit", confidence: float = 0.85) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "basis": basis,
        "evidence": "route decision test fixture",
        "reason": "route decision test fixture",
    }


def build_payload_from_case(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    c_activation = case.get(
        "c_activation",
        {
            "enabled": False,
            "activated_facets": [],
            "inactive_facets": ["source_type", "urgency_level"],
            "triggered_by": [],
        },
    )
    request = {
        "route_catalog": facet_router.ROUTE_CATALOG,
        "c_activation": c_activation,
    }
    facets = {
        name: facet(spec.get("value", ""), basis=spec.get("basis", "explicit"), confidence=spec.get("confidence", 0.85))
        if isinstance(spec, dict)
        else facet(spec)
        for name, spec in case.get("facets", {}).items()
    }
    route = dict(case.get("route_decision", {}))
    filled = {
        "facet_classification": facets,
        "route_decision": route,
        "missing_decision_basis": case.get("missing_decision_basis", []),
        "reference_lenses": case.get("reference_lenses", []),
    }
    filled.update(case.get("extra_filled", {}))
    if case.get("extra_route_fields"):
        filled["route_decision"].update(case["extra_route_fields"])
    return request, filled


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = load_json_file(path)
    cases = data.get("cases", data)
    if not isinstance(cases, list):
        raise SystemExit("Route decision test file must contain a JSON list or an object with cases.")
    return cases


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    request, filled = build_payload_from_case(case)
    report = validate_route_decision(request, filled)
    expected_valid = bool(case["expected_valid"])
    expected_codes = set(case.get("expected_codes", []))
    actual_codes = {item["code"] for item in report["violations"]}
    missing_expected_codes = sorted(expected_codes - actual_codes)
    valid_matches = report["valid"] == expected_valid
    passed = valid_matches and not missing_expected_codes
    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "expected_valid": expected_valid,
        "actual_valid": report["valid"],
        "expected_codes": sorted(expected_codes),
        "actual_codes": sorted(actual_codes),
        "missing_expected_codes": missing_expected_codes,
        "pass": passed,
        "validation_report": report,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["pass"])
    false_accept = sum(
        1 for item in results if not item["expected_valid"] and item["actual_valid"]
    )
    false_reject = sum(
        1 for item in results if item["expected_valid"] and not item["actual_valid"]
    )
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


def build_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
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
            "# Route Decision Validation Test Report",
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


def run_tests(cases_path: Path, output_dir: Path, run_name: str | None) -> dict[str, Any]:
    cases = load_cases(cases_path)
    results = [evaluate_case(case) for case in cases]
    summary = summarize(results)

    run_dir = unique_run_dir(output_dir, run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    data_dir.mkdir()
    outputs_dir.mkdir()

    write_json(data_dir / "route_decision_test_cases.json", {"cases": cases})
    write_json(data_dir / "route_decision_test_results.json", {"summary": summary, "results": results})
    (outputs_dir / "route_decision_test_report.md").write_text(
        build_report(summary, results),
        encoding="utf-8",
    )

    manifest = {
        "run_dir": str(run_dir),
        "cases_file": str(data_dir / "route_decision_test_cases.json"),
        "results_file": str(data_dir / "route_decision_test_results.json"),
        "report_file": str(outputs_dir / "route_decision_test_report.md"),
        "summary": summary,
    }
    write_json(outputs_dir / "route_decision_test_manifest.json", manifest)
    return manifest


def command_validate(args: argparse.Namespace) -> int:
    request = load_json_file(Path(args.request))
    filled = load_json_file(Path(args.filled))
    report = validate_route_decision(request, filled)
    print(to_json(report))
    return 0 if report["valid"] else 1


def command_test(args: argparse.Namespace) -> int:
    manifest = run_tests(Path(args.cases), Path(args.output), args.run_name)
    print(to_json(manifest))
    return 0 if manifest["summary"]["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate agent-filled Facet Router route_decision outputs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate one filled route decision JSON.")
    validate_parser.add_argument("--request", required=True, help="Facet router request JSON.")
    validate_parser.add_argument("--filled", required=True, help="Agent-filled route decision JSON.")
    validate_parser.set_defaults(func=command_validate)

    test_parser = subparsers.add_parser("test", help="Run route decision validation fixture tests.")
    test_parser.add_argument(
        "--cases",
        default=str(TEST_CASES_DIR / "route_decision_tests.json"),
        help="Route decision test cases JSON file.",
    )
    test_parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "tests" / "artifacts" / "test_runs"),
        help="Base directory where test run folders will be created.",
    )
    test_parser.add_argument("--run-name", help="Optional run folder name.")
    test_parser.set_defaults(func=command_test)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
