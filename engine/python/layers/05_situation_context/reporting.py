from __future__ import annotations

from pathlib import Path
from typing import Any


def build_request_report(request: dict[str, Any], request_path: Path) -> str:
    router_summary = request["source_router"]["router_summary"]
    return "\n".join(
        [
            "# Situation Context Request Report",
            "",
            f"- Domain context: `{router_summary.get('domain_context') or 'unknown'}`",
            f"- Problem object: `{router_summary.get('problem_object') or 'unknown'}`",
            f"- User intent: `{router_summary.get('user_intent') or 'unknown'}`",
            f"- Direction available: `{request['source_direction']['available']}`",
            f"- Request file: `{request_path}`",
            "",
        ]
    )


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
            "# Situation Context Validation Test Report",
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
