from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
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

from shared.run_identity import unique_run_dir as identity_unique_run_dir

ROUTER_VERSION = "0.1.0"

BASE_ROUTER = "B_route_ready"
CONDITIONAL_ROUTER = "C_conditional_extended"

BASE_FACETS = [
    "domain_context",
    "problem_object",
    "user_intent",
    "definition_level",
    "risk_level",
    "needed_output",
]

CONDITIONAL_C_FACETS = [
    "source_type",
    "urgency_level",
]

ROUTE_CATALOG = [
    "ask_user",
    "run_framework_check",
    "use_situation_map",
    "use_ipo",
    "risk_review",
    "proceed_to_solution",
]

HARD_GATE_RULES = [
    "If risk_level is high, do not select a direct proceed route.",
    "If problem_object is unresolved, select ask_user or unresolved.",
    "If user_intent is unresolved, select ask_user or unresolved.",
    "If needed_output is unresolved, do not select proceed_to_solution.",
    "If evidence is inferred-only and the route is high-risk, mark route_status as unresolved.",
]


@dataclass(frozen=True)
class TriggerRule:
    trigger_id: str
    activated_facets: tuple[str, ...]
    description: str
    patterns: tuple[str, ...]


LOG_SIGNAL_PATTERNS = (
    r"(?:서버|콘솔|실행|오류|에러)\s*로그",
    r"(?<![가-힣A-Za-z0-9_])로그(?!인|라인)",
    r"\bserver log\b",
    r"\bconsole log\b",
    r"\bexecution log\b",
    r"\blog\b",
    r"\b[45]\d{2}\s+error\b",
    r"\bERROR\b",
    r"\bWARN\b",
)


TRIGGER_RULES = [
    TriggerRule(
        trigger_id="attachment_present",
        activated_facets=("source_type",),
        description="An attachment, file path, file name, extension, or upload context is present.",
        patterns=(
            r"[\w가-힣 ._\-\(\)]+\.(xlsx|xls|csv|pdf|docx|doc|txt|json|py|js|ts|tsx|jsx|log|md|pptx)(?=$|[^A-Za-z0-9_])",
            r"[a-zA-Z]:\\[^\s]+",
            r"첨부(?:한|된)?",
            r"업로드",
            r"파일(?!럿)(?=$|[\s,.;:!?]|을|를|이|가|은|는|로|에서|명|경로|첨부)",
            r"attachment",
            r"\bfile\b",
        ),
    ),
    TriggerRule(
        trigger_id="code_or_error_present",
        activated_facets=("source_type",),
        description="Code, traceback, stack trace, exception name, or code-oriented error context is present.",
        patterns=(
            r"```",
            r"\btraceback\b",
            r"\bstack trace\b",
            r"\b(ValueError|TypeError|KeyError|IndexError|RuntimeError|Exception|SyntaxError|ImportError)\b",
            r"\b(line \d+)\b",
            r"\.(py|js|ts|tsx|jsx)\b",
            r"오류 메시지",
            r"예외(?:가|는)?\s*(?:발생|메시지|오류|처리|trace|stack)",
            r"코드",
        ),
    ),
    TriggerRule(
        trigger_id="log_present",
        activated_facets=("source_type",),
        description="Server log, console log, execution log, or repeated runtime event evidence is present.",
        patterns=LOG_SIGNAL_PATTERNS,
    ),
    TriggerRule(
        trigger_id="urgency_signal_present",
        activated_facets=("urgency_level",),
        description="Deadline, incident, blocking, deployment, or immediate action wording is present.",
        patterns=(
            r"지금 바로",
            r"오늘 안에",
            r"배포 전",
            r"장애",
            r"막힘",
            r"막혔",
            r"막혀",
            r"급하",
            r"급하게",
            r"긴급",
            r"\burgent\b",
            r"\bdeadline\b",
            r"\bblocked\b",
            r"\bincident\b",
        ),
    ),
    TriggerRule(
        trigger_id="high_risk_domain_present",
        activated_facets=("risk_level",),
        description="High-cost domain evidence such as legal, contract, payment, privacy, security, medical, or finance is present.",
        patterns=(
            r"법률",
            r"법무",
            r"계약(?!직)",
            r"계약서",
            r"결제",
            r"개인정보",
            r"보안",
            r"의료",
            r"재무",
            r"\blegal\b",
            r"\bcontract\b",
            r"\bpayment\b",
            r"\bprivacy\b",
            r"\bsecurity\b",
            r"\bmedical\b",
            r"\bfinance\b",
        ),
    ),
]

DOCUMENT_TERMS = [
    "계약서",
    "보고서",
    "제안서",
    "기획서",
    "약관",
    "정책",
    "문서",
    "pdf",
    "docx",
    "document",
    "report",
    "proposal",
    "policy",
    "terms",
]

REVIEW_TERMS = [
    "검토",
    "리뷰",
    "분석",
    "판단",
    "문제",
    "위험",
    "확인",
    "review",
    "analyze",
    "check",
    "risk",
]

MIXED_SOURCE_PATTERNS = (
    r"파일(?:과|이랑|하고|랑).*(코드|로그|요구사항|문서)",
    r"(코드|로그|문서).*(파일(?:과|이랑|하고|랑))",
    r"(로그|코드).*(설정|환경|요구사항)",
    r"\.(log|py|js|ts|json|csv|xlsx|pdf).*(설정|환경|요구사항|config|setting|requirement)",
    r"(file|log|code|document).*(requirement|config|setting)",
)

NEGATED_ATTACHMENT_PATTERNS = (
    r"첨부(?:는|가)?\s*(없|안|하지 않|하지않)",
    r"첨부\s*없음",
    r"첨부파일\s*없음",
    r"예시\s*파일명",
    r"example\s+file\s+name",
)
HIGH_RISK_TERMS = (
    "법률",
    "법무",
    "계약",
    "계약서",
    "결제",
    "개인정보",
    "보안",
    "의료",
    "재무",
    "legal",
    "contract",
    "payment",
    "privacy",
    "security",
    "medical",
    "finance",
)
HIGH_RISK_EXCLUSION_PATTERN = (
    r"(?P<context>[^.!?\n]{0,80})"
    r"(?:제외하고|제외한다|제외함|제외할|제외해|다루지\s*않고|다루지\s*않는다|대상이\s*아니다)"
)



def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name)


def read_text_argument(text: str | None, input_file: str | None) -> str:
    if text and input_file:
        raise SystemExit("Use either --text or --input-file, not both.")
    if input_file:
        value = Path(input_file).read_text(encoding="utf-8").strip()
    elif text:
        value = text.strip()
    else:
        raise SystemExit("Provide --text or --input-file.")
    if not value:
        raise SystemExit("Input text must not be empty.")
    return value


def extract_raw_text(input_analysis: dict[str, Any] | None) -> str | None:
    if not input_analysis:
        return None
    candidates = [
        ("input", "raw_text"),
        ("source", "raw_text"),
        ("source", "source_raw_text"),
    ]
    for first, second in candidates:
        value = input_analysis.get(first, {}).get(second)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = input_analysis.get("raw_text")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def collect_text(raw_text: str, source_files: list[str]) -> str:
    file_text = "\n".join(source_files)
    return f"{raw_text}\n{file_text}".strip()


def matched_patterns(text: str, patterns: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = match.group(0).strip()
            if value and value not in matches:
                matches.append(value)
    return matches


def has_any_term(text: str, terms: list[str]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def document_review_matches(text: str) -> list[str]:
    if not has_any_term(text, DOCUMENT_TERMS) or not has_any_term(text, REVIEW_TERMS):
        return []
    matches = []
    for term in DOCUMENT_TERMS + REVIEW_TERMS:
        if term.casefold() in text.casefold():
            matches.append(term)
    return matches


def mixed_source_matches(text: str) -> list[str]:
    return matched_patterns(text, MIXED_SOURCE_PATTERNS)


def has_negated_attachment_context(text: str) -> bool:
    return bool(matched_patterns(text, NEGATED_ATTACHMENT_PATTERNS))


def explicitly_excluded_high_risk_terms(text: str) -> set[str]:
    excluded: set[str] = set()
    for match in re.finditer(HIGH_RISK_EXCLUSION_PATTERN, text, flags=re.IGNORECASE):
        context = match.group("context").casefold()
        for term in HIGH_RISK_TERMS:
            if term.casefold() in context:
                excluded.add(term.casefold())
    return excluded


def infer_source_type_hint(text: str, source_files: list[str]) -> str | None:
    combined = collect_text(text, source_files).casefold()
    extension_map = {
        "xlsx": "spreadsheet_file",
        "xls": "spreadsheet_file",
        "csv": "data_file",
        "pdf": "document_file",
        "docx": "document_file",
        "doc": "document_file",
        "py": "code_file",
        "js": "code_file",
        "ts": "code_file",
        "tsx": "code_file",
        "jsx": "code_file",
        "log": "log_file",
        "json": "data_file",
        "md": "markdown_file",
        "pptx": "presentation_file",
    }
    for extension, hint in extension_map.items():
        if re.search(rf"\.{extension}(?=$|[^A-Za-z0-9_])", combined):
            return hint
    if "pdf" in combined or "docx" in combined:
        return "document_file"
    if any(term in combined for term in ["계약서", "약관", "정책 문서"]):
        return "document_text"
    if "traceback" in combined or "stack trace" in combined:
        return "error_text"
    if any(re.search(pattern, combined, re.IGNORECASE) for pattern in LOG_SIGNAL_PATTERNS):
        return "log_text"
    return None


def scan_triggers(raw_text: str, source_files: list[str] | None = None) -> dict[str, Any]:
    source_files = source_files or []
    text = collect_text(raw_text, source_files)
    trigger_results: list[dict[str, Any]] = []
    attachment_negated = has_negated_attachment_context(text) and not source_files
    excluded_high_risk = explicitly_excluded_high_risk_terms(text)

    for rule in TRIGGER_RULES:
        if rule.trigger_id == "attachment_present" and attachment_negated:
            continue
        evidence = matched_patterns(text, rule.patterns)
        if rule.trigger_id == "high_risk_domain_present" and excluded_high_risk:
            evidence = [
                item
                for item in evidence
                if item.casefold() not in excluded_high_risk
            ]
        if evidence:
            trigger_results.append(
                {
                    "trigger_id": rule.trigger_id,
                    "activated_facets": list(rule.activated_facets),
                    "basis": "explicit",
                    "matched_evidence": evidence,
                    "description": rule.description,
                }
            )

    doc_evidence = document_review_matches(text)
    if doc_evidence:
        trigger_results.append(
            {
                "trigger_id": "document_review_request",
                "activated_facets": ["source_type"],
                "basis": "explicit",
                "matched_evidence": doc_evidence,
                "description": "A document-like object is being reviewed, checked, analyzed, or risk-scanned.",
            }
        )

    mixed_evidence = mixed_source_matches(text)
    if mixed_evidence:
        trigger_results.append(
            {
                "trigger_id": "mixed_source_present",
                "activated_facets": ["source_type"],
                "basis": "explicit",
                "matched_evidence": mixed_evidence,
                "description": "Multiple input source types appear together.",
            }
        )

    triggered_by = [item["trigger_id"] for item in trigger_results]
    activated_facets = sorted(
        {
            facet
            for item in trigger_results
            for facet in item["activated_facets"]
        }
    )
    inactive_facets = [
        facet for facet in CONDITIONAL_C_FACETS if facet not in activated_facets
    ]

    enabled = bool(trigger_results)
    reason = (
        "Evidence-backed C triggers were found. Activate only the listed facets."
        if enabled
        else "No explicit C trigger evidence was found. Keep C facets inactive."
    )

    return {
        "base_router": BASE_ROUTER,
        "conditional_router": CONDITIONAL_ROUTER,
        "enabled": enabled,
        "triggered_by": triggered_by,
        "activated_facets": activated_facets,
        "inactive_facets": inactive_facets,
        "reason": reason,
        "source_type_hint": infer_source_type_hint(raw_text, source_files),
        "trigger_results": trigger_results,
    }


def empty_facet_slot(facet_name: str) -> dict[str, Any]:
    return {
        "value": "",
        "confidence": 0.0,
        "basis": "explicit | inferred | mixed | unknown",
        "evidence": "",
        "reason": "",
        "status": "agent_fill_required",
        "facet_name": facet_name,
    }


def build_router_request(
    raw_text: str,
    input_analysis_path: str | None,
    input_analysis: dict[str, Any] | None,
    source_files: list[str],
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or datetime.now().isoformat(timespec="seconds")
    c_activation = scan_triggers(raw_text, source_files)
    active_facets = list(BASE_FACETS)
    for facet in c_activation["activated_facets"]:
        if facet not in active_facets:
            active_facets.append(facet)

    return {
        "router_version": ROUTER_VERSION,
        "engine_role": "facet_router_trigger_request_builder",
        "contract_name": "ai_fillable_facet_router_request",
        "created_at": timestamp,
        "source": {
            "raw_text": raw_text,
            "source_files": source_files,
            "input_analysis_file": input_analysis_path,
            "input_analysis_included": input_analysis is not None,
        },
        "router_policy": {
            "default_router": BASE_ROUTER,
            "conditional_expansion": CONDITIONAL_ROUTER,
            "activation_method": "trigger_based",
            "forbidden_behavior": "Do not fill source_type or urgency_level without trigger evidence.",
        },
        "c_activation": c_activation,
        "ai_task": {
            "task_name": "fill_facet_router_decision",
            "instruction": (
                "Use source.raw_text, the input analysis if provided, and c_activation. "
                "Fill facet_classification and route_decision for the next agent step. "
                "Do not answer the original user request directly."
            ),
            "allowed_mutations": [
                "facet_classification.<facet>",
                "route_decision",
                "missing_decision_basis",
                "reference_lenses",
            ],
        },
        "facet_classification": {
            facet_name: empty_facet_slot(facet_name)
            for facet_name in active_facets
        },
        "inactive_facets": c_activation["inactive_facets"],
        "route_catalog": ROUTE_CATALOG,
        "route_decision": {
            "route_status": "selected | unresolved",
            "selected_route": "",
            "route_confidence": 0.0,
            "reason": "",
            "evidence": "",
        },
        "missing_decision_basis": [],
        "reference_lenses": [],
        "hard_gate_policy": HARD_GATE_RULES,
        "output_rules": [
            "Return valid JSON only when filling this request.",
            "Select exactly one route, or mark route_status as unresolved.",
            "Do not output route alternatives by default.",
            "Do not generate user-facing clarification questions in this stage.",
            "Use C facets only when c_activation enables them.",
            "Do not bypass hard gates because a trigger was found.",
        ],
    }


def build_report(request: dict[str, Any]) -> str:
    activation = request["c_activation"]
    trigger_rows = [
        "| Trigger | Activated Facets | Evidence |",
        "|---|---|---|",
    ]
    if activation["trigger_results"]:
        for item in activation["trigger_results"]:
            trigger_rows.append(
                "| {trigger} | {facets} | {evidence} |".format(
                    trigger=item["trigger_id"],
                    facets=", ".join(item["activated_facets"]),
                    evidence=", ".join(item["matched_evidence"]),
                )
            )
    else:
        trigger_rows.append("| none | none | No explicit C trigger evidence |")

    active_facets = ", ".join(request["facet_classification"].keys())
    inactive_facets = ", ".join(request["inactive_facets"]) or "none"

    return "\n".join(
        [
            "# Facet Router Trigger Report",
            "",
            "## Summary",
            "",
            f"- Default router: `{request['router_policy']['default_router']}`",
            f"- Conditional expansion: `{request['router_policy']['conditional_expansion']}`",
            f"- C enabled: `{activation['enabled']}`",
            f"- Triggered by: `{', '.join(activation['triggered_by']) or 'none'}`",
            f"- Activated facets: `{', '.join(activation['activated_facets']) or 'none'}`",
            f"- Inactive C facets: `{inactive_facets}`",
            f"- Source type hint: `{activation['source_type_hint'] or 'none'}`",
            "",
            "## Trigger Evidence",
            "",
            *trigger_rows,
            "",
            "## Active Facet Slots",
            "",
            f"`{active_facets}`",
            "",
            "## Next Agent Step",
            "",
            "Fill `facet_classification`, choose one `route_decision`, and record missing decision basis without generating user-facing questions.",
            "",
        ]
    )


def build_run(
    raw_text: str,
    output_dir: Path,
    run_name: str | None,
    input_analysis_path: str | None,
    input_analysis: dict[str, Any] | None,
    source_files: list[str],
) -> dict[str, Any]:
    run_dir = unique_run_dir(output_dir, run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    data_dir.mkdir()
    outputs_dir.mkdir()

    input_path = data_dir / "router_input.txt"
    input_path.write_text(raw_text + "\n", encoding="utf-8")

    request = build_router_request(
        raw_text=raw_text,
        input_analysis_path=input_analysis_path,
        input_analysis=input_analysis,
        source_files=source_files,
    )

    request_path = data_dir / "facet_router_request.json"
    write_json(request_path, request)

    report_path = outputs_dir / "facet_router_report.md"
    report_path.write_text(build_report(request), encoding="utf-8")

    manifest_path = outputs_dir / "router_manifest.json"
    summary = {
        "run_dir": str(run_dir),
        "data_dir": str(data_dir),
        "outputs_dir": str(outputs_dir),
        "router_version": ROUTER_VERSION,
        "request_file": str(request_path),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
        "c_activation": request["c_activation"],
    }
    write_json(manifest_path, summary)
    return summary


def command_scan(args: argparse.Namespace) -> int:
    input_analysis = load_json_file(Path(args.input_analysis)) if args.input_analysis else None
    text = read_text_argument(args.text, args.input_file) if (args.text or args.input_file) else extract_raw_text(input_analysis)
    if not text:
        raise SystemExit("Provide --text, --input-file, or --input-analysis containing input.raw_text.")

    report = scan_triggers(text, args.source_file or [])
    safe_print(to_json(report))
    return 0


def command_build(args: argparse.Namespace) -> int:
    input_analysis = load_json_file(Path(args.input_analysis)) if args.input_analysis else None
    if args.text or args.input_file:
        text = read_text_argument(args.text, args.input_file)
    else:
        text = extract_raw_text(input_analysis)
        if not text:
            raise SystemExit("Provide --text, --input-file, or --input-analysis containing input.raw_text.")

    summary = build_run(
        raw_text=text,
        output_dir=Path(args.output),
        run_name=args.run_name,
        input_analysis_path=args.input_analysis,
        input_analysis=input_analysis,
        source_files=args.source_file or [],
    )
    safe_print(to_json(summary))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a trigger-based Facet Router request without modifying schema_request_builder.py."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan raw input for C activation triggers.")
    scan_parser.add_argument("--text", help="Raw user input text.")
    scan_parser.add_argument("--input-file", help="Read raw user input text from a UTF-8 file.")
    scan_parser.add_argument("--input-analysis", help="Optional user_input_analysis JSON file.")
    scan_parser.add_argument(
        "--source-file",
        action="append",
        help="Optional source or attachment path. Can be provided multiple times.",
    )
    scan_parser.set_defaults(func=command_scan)

    build_run_parser = subparsers.add_parser("build", help="Create a Facet Router request run folder.")
    build_run_parser.add_argument("--text", help="Raw user input text.")
    build_run_parser.add_argument("--input-file", help="Read raw user input text from a UTF-8 file.")
    build_run_parser.add_argument("--input-analysis", help="Optional user_input_analysis JSON file.")
    build_run_parser.add_argument(
        "--source-file",
        action="append",
        help="Optional source or attachment path. Can be provided multiple times.",
    )
    build_run_parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs" / "runs"),
        help="Base directory where router run folders will be created.",
    )
    build_run_parser.add_argument(
        "--run-name",
        help="Optional run folder name. Defaults to a timestamp.",
    )
    build_run_parser.set_defaults(func=command_build)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
