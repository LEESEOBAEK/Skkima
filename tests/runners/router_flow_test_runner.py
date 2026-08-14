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
ENGINE_DIR = PROJECT_ROOT / "engine" / "python"
CASES_DIR = PROJECT_ROOT / "tests" / "cases"
LAYER_DIRS = [
    ENGINE_DIR / "layers" / "01_input_structuring",
    ENGINE_DIR / "layers" / "02_router",
    ENGINE_DIR / "layers" / "03_route_validation",
    ENGINE_DIR / "layers" / "04_direction_lens",
]
for layer_dir in LAYER_DIRS:
    if layer_dir.exists() and str(layer_dir) not in sys.path:
        sys.path.insert(0, str(layer_dir))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import facet_router
import route_decision_validator

from shared.run_identity import unique_run_dir as identity_unique_run_dir


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name, default_suffix="router_flow_test")


def facet_slot(value: Any, *, basis: str = "explicit", confidence: float = 0.85) -> dict[str, Any]:
    if isinstance(value, dict) and "value" in value:
        return {
            "value": value.get("value", ""),
            "confidence": value.get("confidence", confidence),
            "basis": value.get("basis", basis),
            "evidence": value.get("evidence", "flow test fixture"),
            "reason": value.get("reason", "flow test fixture"),
            "status": value.get("status", "agent_filled"),
            "facet_name": value.get("facet_name", ""),
        }
    return {
        "value": value,
        "confidence": confidence,
        "basis": basis,
        "evidence": "flow test fixture",
        "reason": "flow test fixture",
        "status": "agent_filled",
    }


def build_filled_from_case(case: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    facets = {
        facet_name: facet_slot(spec)
        for facet_name, spec in case.get("agent_fill", {}).get("facets", {}).items()
    }

    c_activation = request.get("c_activation", {})
    for facet_name in c_activation.get("activated_facets", []):
        if facet_name not in facets:
            facets[facet_name] = facet_slot(
                {
                    "value": "unresolved",
                    "basis": "unknown",
                    "confidence": 0.0,
                    "status": "unresolved",
                    "evidence": "activated C facet was not filled in the fixture",
                    "reason": "activated C facet was not filled in the fixture",
                }
            )

    return {
        "router_version": request.get("router_version"),
        "source": request.get("source", {}),
        "c_activation": request.get("c_activation", {}),
        "facet_classification": facets,
        "route_decision": case.get("agent_fill", {}).get("route_decision", {}),
        "missing_decision_basis": case.get("agent_fill", {}).get("missing_decision_basis", []),
        "reference_lenses": case.get("agent_fill", {}).get("reference_lenses", []),
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = load_json_file(path)
    cases = data.get("cases", data)
    if not isinstance(cases, list):
        raise SystemExit("Flow test file must contain a JSON list or an object with cases.")
    return cases


def evaluate_case(case: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    raw_text = case["raw_text"]
    source_files = case.get("source_files", [])
    request = facet_router.build_router_request(
        raw_text=raw_text,
        input_analysis_path=None,
        input_analysis=None,
        source_files=source_files,
    )
    filled = build_filled_from_case(case, request)
    validation_report = route_decision_validator.validate_route_decision(request, filled)

    case_dir.mkdir(parents=True, exist_ok=False)
    write_json(case_dir / "facet_router_request.json", request)
    write_json(case_dir / "facet_router_filled.json", filled)
    write_json(case_dir / "route_decision_validation.json", validation_report)

    expected = case.get("expected", {})
    issues: list[dict[str, Any]] = []

    if "c_enabled" in expected and request["c_activation"]["enabled"] != expected["c_enabled"]:
        issues.append(
            {
                "type": "c_enabled_mismatch",
                "expected": expected["c_enabled"],
                "actual": request["c_activation"]["enabled"],
            }
        )

    expected_triggers = set(expected.get("triggered_by", []))
    actual_triggers = set(request["c_activation"]["triggered_by"])
    missing_triggers = sorted(expected_triggers - actual_triggers)
    if missing_triggers:
        issues.append({"type": "missing_triggers", "items": missing_triggers})

    expected_facets = set(expected.get("activated_facets", []))
    actual_facets = set(request["c_activation"]["activated_facets"])
    missing_facets = sorted(expected_facets - actual_facets)
    if missing_facets:
        issues.append({"type": "missing_activated_facets", "items": missing_facets})

    if "selected_route" in expected:
        actual_route = filled.get("route_decision", {}).get("selected_route", "")
        if actual_route != expected["selected_route"]:
            issues.append(
                {
                    "type": "selected_route_mismatch",
                    "expected": expected["selected_route"],
                    "actual": actual_route,
                }
            )

    expected_valid = bool(expected.get("validation_valid", True))
    if validation_report["valid"] != expected_valid:
        issues.append(
            {
                "type": "validation_result_mismatch",
                "expected": expected_valid,
                "actual": validation_report["valid"],
            }
        )

    expected_codes = set(expected.get("validation_codes", []))
    actual_codes = {item["code"] for item in validation_report["violations"]}
    missing_codes = sorted(expected_codes - actual_codes)
    if missing_codes:
        issues.append({"type": "missing_validation_codes", "items": missing_codes})

    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "pass": not issues,
        "issues": issues,
        "request_file": str(case_dir / "facet_router_request.json"),
        "filled_file": str(case_dir / "facet_router_filled.json"),
        "validation_file": str(case_dir / "route_decision_validation.json"),
        "c_activation": request["c_activation"],
        "selected_route": filled.get("route_decision", {}).get("selected_route", ""),
        "validation_valid": validation_report["valid"],
        "validation_codes": sorted(actual_codes),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["pass"])
    valid_flows = sum(1 for item in results if item["validation_valid"])
    invalid_flows = total - valid_flows
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
        "validation_valid_flows": valid_flows,
        "validation_invalid_flows": invalid_flows,
        "issue_counts": issue_counts,
    }


def build_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    rows = [
        "| Case | Category | Result | C Enabled | Route | Validation | Codes |",
        "|---|---|---|---:|---|---|---|",
    ]
    for item in results:
        codes = ", ".join(item["validation_codes"]) or "none"
        rows.append(
            "| {case} | {category} | {result} | {c_enabled} | {route} | {validation} | {codes} |".format(
                case=item["id"],
                category=item["category"],
                result="PASS" if item["pass"] else "FAIL",
                c_enabled=item["c_activation"]["enabled"],
                route=item["selected_route"] or "unresolved",
                validation="valid" if item["validation_valid"] else "invalid",
                codes=codes,
            )
        )

    return "\n".join(
        [
            "# Router Flow Test Report",
            "",
            "## Summary",
            "",
            f"- Total: {summary['total']}",
            f"- Passed: {summary['passed']}",
            f"- Failed: {summary['failed']}",
            f"- Score: {summary['score_100']} / 100",
            f"- Validation valid flows: {summary['validation_valid_flows']}",
            f"- Validation invalid flows: {summary['validation_invalid_flows']}",
            "",
            "## Results",
            "",
            *rows,
            "",
        ]
    )


def run_flow_tests(cases_path: Path, output_dir: Path, run_name: str | None) -> dict[str, Any]:
    cases = load_cases(cases_path)
    run_dir = unique_run_dir(output_dir, run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    case_outputs_dir = data_dir / "case_outputs"
    data_dir.mkdir()
    outputs_dir.mkdir()
    case_outputs_dir.mkdir()

    results = [
        evaluate_case(case, case_outputs_dir / case["id"])
        for case in cases
    ]
    summary = summarize(results)

    write_json(data_dir / "router_flow_test_cases.json", {"cases": cases})
    write_json(data_dir / "router_flow_test_results.json", {"summary": summary, "results": results})
    (outputs_dir / "router_flow_test_report.md").write_text(
        build_report(summary, results),
        encoding="utf-8",
    )

    manifest = {
        "run_dir": str(run_dir),
        "cases_file": str(data_dir / "router_flow_test_cases.json"),
        "results_file": str(data_dir / "router_flow_test_results.json"),
        "report_file": str(outputs_dir / "router_flow_test_report.md"),
        "case_outputs_dir": str(case_outputs_dir),
        "summary": summary,
    }
    write_json(outputs_dir / "router_flow_test_manifest.json", manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run end-to-end router flow tests: request build, agent-style fill, route validation."
    )
    parser.add_argument(
        "--cases",
        default=str(CASES_DIR / "router_flow_tests.json"),
        help="Router flow test cases JSON file.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "tests" / "artifacts" / "test_runs"),
        help="Base directory where flow test run folders will be created.",
    )
    parser.add_argument("--run-name", help="Optional run folder name.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    manifest = run_flow_tests(Path(args.cases), Path(args.output), args.run_name)
    print(to_json(manifest))
    return 0 if manifest["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
