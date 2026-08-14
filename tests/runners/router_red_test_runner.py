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

from shared.run_identity import unique_run_dir as identity_unique_run_dir


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = data.get("cases", data)
    if not isinstance(cases, list):
        raise SystemExit("Test case file must contain a JSON list or an object with a cases list.")
    return cases


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name, default_suffix="red_test")


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    text = case["text"]
    source_files = case.get("source_files", [])
    result = facet_router.scan_triggers(text, source_files)

    expected_enabled = bool(case["expected_enabled"])
    expected_triggers = set(case.get("expected_triggers", []))
    expected_facets = set(case.get("expected_activated_facets", []))
    forbidden_triggers = set(case.get("forbidden_triggers", []))
    forbidden_facets = set(case.get("forbidden_activated_facets", []))

    actual_triggers = set(result["triggered_by"])
    actual_facets = set(result["activated_facets"])

    issues: list[dict[str, Any]] = []
    if result["enabled"] != expected_enabled:
        issues.append(
            {
                "type": "enabled_mismatch",
                "expected": expected_enabled,
                "actual": result["enabled"],
            }
        )
    missing_triggers = sorted(expected_triggers - actual_triggers)
    extra_triggers = sorted(forbidden_triggers & actual_triggers)
    missing_facets = sorted(expected_facets - actual_facets)
    extra_facets = sorted(forbidden_facets & actual_facets)

    if missing_triggers:
        issues.append({"type": "missing_triggers", "items": missing_triggers})
    if extra_triggers:
        issues.append({"type": "forbidden_triggers_present", "items": extra_triggers})
    if missing_facets:
        issues.append({"type": "missing_activated_facets", "items": missing_facets})
    if extra_facets:
        issues.append({"type": "forbidden_activated_facets_present", "items": extra_facets})

    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "text": text,
        "expected": {
            "enabled": expected_enabled,
            "triggers": sorted(expected_triggers),
            "activated_facets": sorted(expected_facets),
            "forbidden_triggers": sorted(forbidden_triggers),
            "forbidden_activated_facets": sorted(forbidden_facets),
        },
        "actual": {
            "enabled": result["enabled"],
            "triggers": result["triggered_by"],
            "activated_facets": result["activated_facets"],
            "source_type_hint": result["source_type_hint"],
        },
        "pass": not issues,
        "issues": issues,
        "scan_result": result,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["pass"])
    failed = total - passed

    issue_counts: dict[str, int] = {}
    for item in results:
        for issue in item["issues"]:
            issue_counts[issue["type"]] = issue_counts.get(issue["type"], 0) + 1

    false_positive = 0
    false_negative = 0
    for item in results:
        expected = item["expected"]["enabled"]
        actual = item["actual"]["enabled"]
        if not expected and actual:
            false_positive += 1
        if expected and not actual:
            false_negative += 1

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "score_100": round((passed / total) * 100, 2) if total else 0.0,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "issue_counts": issue_counts,
    }


def build_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    rows = [
        "| Case | Category | Result | Expected | Actual | Issues |",
        "|---|---|---|---|---|---|",
    ]
    for item in results:
        expected = ", ".join(item["expected"]["triggers"]) or "off"
        actual = ", ".join(item["actual"]["triggers"]) or "off"
        issues = "; ".join(issue["type"] for issue in item["issues"]) or "none"
        rows.append(
            f"| {item['id']} | {item['category']} | {'PASS' if item['pass'] else 'FAIL'} | {expected} | {actual} | {issues} |"
        )

    return "\n".join(
        [
            "# Facet Router Red Test Report",
            "",
            "## Summary",
            "",
            f"- Total: {summary['total']}",
            f"- Passed: {summary['passed']}",
            f"- Failed: {summary['failed']}",
            f"- Score: {summary['score_100']} / 100",
            f"- False positive: {summary['false_positive']}",
            f"- False negative: {summary['false_negative']}",
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

    write_json(data_dir / "router_red_test_cases.json", {"cases": cases})
    write_json(data_dir / "router_red_test_results.json", {"summary": summary, "results": results})
    (outputs_dir / "router_red_test_report.md").write_text(
        build_report(summary, results),
        encoding="utf-8",
    )

    manifest = {
        "run_dir": str(run_dir),
        "cases_file": str(data_dir / "router_red_test_cases.json"),
        "results_file": str(data_dir / "router_red_test_results.json"),
        "report_file": str(outputs_dir / "router_red_test_report.md"),
        "summary": summary,
    }
    write_json(outputs_dir / "router_red_test_manifest.json", manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run red tests for facet_router C trigger activation.")
    parser.add_argument(
        "--cases",
        default=str(CASES_DIR / "router_red_tests.json"),
        help="JSON file containing red test cases.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "tests" / "artifacts" / "test_runs"),
        help="Base directory where red test run folders will be created.",
    )
    parser.add_argument("--run-name", help="Optional run folder name.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    manifest = run_tests(Path(args.cases), Path(args.output), args.run_name)
    print(to_json(manifest))
    return 0 if manifest["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
