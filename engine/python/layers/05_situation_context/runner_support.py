from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable


def find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "agents" / "agent.md").exists() and (candidate / "tests").exists():
            return candidate
    return start


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = find_project_root(SCRIPT_DIR)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.run_identity import unique_run_dir as identity_unique_run_dir


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name, default_suffix="situation_context")


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = load_json_file(path)
    cases = data.get("cases", data)
    if not isinstance(cases, list):
        raise SystemExit("Situation context test file must contain a list or an object with cases.")
    return cases


def build_test_payload(
    case: dict[str, Any],
    build_context_request: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = build_context_request(
        case["router_filled"],
        case.get("direction_filled"),
        created_at="test",
    )
    return request, case["context_filled"]


def evaluate_case(
    case: dict[str, Any],
    build_context_request: Callable[..., dict[str, Any]],
    validate_situation_context: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    request, filled = build_test_payload(case, build_context_request)
    report = validate_situation_context(request, filled)
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
