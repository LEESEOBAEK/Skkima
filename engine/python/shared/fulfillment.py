from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from shared import artifacts as artifact_store


LEGACY_FULFILLMENT_VERSION = "0.1.0"
PREVIOUS_FULFILLMENT_VERSION = "0.2.0"
FULFILLMENT_VERSION = "0.3.0"
SUPPORTED_FULFILLMENT_VERSIONS = {
    LEGACY_FULFILLMENT_VERSION,
    PREVIOUS_FULFILLMENT_VERSION,
    FULFILLMENT_VERSION,
}
MODERN_FULFILLMENT_VERSIONS = {PREVIOUS_FULFILLMENT_VERSION, FULFILLMENT_VERSION}
CONTRACT_STATUSES = {"ready", "waiting_user", "not_required", "blocked"}
FINALIZATION_MODES = {"managed_deliverable", "project_native", "chat"}
TEXT_ARTIFACT_SUFFIXES = {".md", ".txt", ".html", ".htm", ".csv", ".json", ".yaml", ".yml"}
MAX_CLAIM_SCAN_BYTES = 1024 * 1024

_UNCERTAINTY_POLICY_MARKERS = (
    "확인되지 않은",
    "근거 없는",
    "근거가 없는",
    "가설 또는 validation_needed",
    "추정값을 표시",
    "unsupported claim",
    "unverified claim",
)
_CLAIM_DISCLOSURE_MARKERS = (
    "validation_needed",
    "검증 필요",
    "확인 필요",
    "가설",
    "예상",
    "추정",
    "목표",
    "후보",
    "예시",
    "unverified",
    "unsupported",
    "estimate",
    "hypothesis",
    "target",
    "example",
)
_QUANTIFIED_VALUE = re.compile(
    r"(?:(?<!\d)\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?(?!\d)|"
    r"(?<!\d)\d+(?:\.\d+)?\s*(?:%|배|초|분|시간|일|주|개월|년|명|건|회|원|만원|억원)(?![A-Za-z0-9_]))",
    re.IGNORECASE,
)
_PERFORMANCE_CLAIM_CONTEXT = re.compile(
    r"줄|절감|단축|증가|향상|개선|감소|확보|달성|높아|높인|높임|늘어|늘린|늘어난|빠르|효율|생산성|"
    r"전환|방문|팔로워|구독|매출|수익|걸리|만에|끝내|"
    r"reduce|save|increase|improve|decrease|achieve|faster|efficien|productiv|revenue|conversion",
    re.IGNORECASE,
)

_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|(?:\d+|[A-Za-z])[.)])\s+(.+?)\s*$")
_OUTPUT_CONTEXT_MARKERS = (
    "산출물",
    "결과물",
    "최종 결과",
    "필수 이미지",
    "필수 산출물",
    "원하는 최종 산출물",
    "deliverable",
    "output",
)
_OUTPUT_ACTION_MARKERS = (
    "제작",
    "생성",
    "작성",
    "제공",
    "저장",
    "등록",
    "만들",
    "create",
    "generate",
    "produce",
    "save",
    "register",
)
_OUTPUT_OBJECT_MARKERS = (
    "로고",
    "팔레트",
    "화면",
    "보드",
    "문서",
    "보고서",
    "이미지",
    "파일",
    "템플릿",
    "프롬프트",
    "산출물",
    "결과물",
    "학습서",
    "학습 문서",
    "logo",
    "palette",
    "screen",
    "board",
    "document",
    "report",
    "image",
    "file",
    "template",
    "prompt",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def is_placeholder(data: Any) -> bool:
    return bool(isinstance(data, dict) and data.get("workflow_placeholder"))


def extract_source_requirements(raw_text: str) -> list[dict[str, str]]:
    """Preserve explicit output statements without asking the agent to redefine them."""
    extracted: list[tuple[str, str]] = []
    seen: set[str] = set()
    output_context = False
    captured_in_context = False

    logical_lines: list[str] = []
    for raw_line in str(raw_text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or _LIST_ITEM.match(stripped):
            logical_lines.append(stripped)
            continue
        logical_lines.extend(
            item.strip()
            for item in re.split(r"(?<=[.!?。！？])\s+", stripped)
            if item.strip()
        )

    for raw_line in logical_lines:
        line = raw_line.strip()
        folded = line.casefold()
        context_marker = any(marker in folded for marker in _OUTPUT_CONTEXT_MARKERS)
        if line.startswith("#"):
            output_context = context_marker
            captured_in_context = False
            continue
        context_intro = (
            context_marker
            and not _LIST_ITEM.match(line)
            and any(
                marker in folded
                for marker in ("다음 산출물", "아래 산출물", "following deliverables")
            )
        )
        if context_intro:
            output_context = True
            captured_in_context = False
            continue

        match = _LIST_ITEM.match(line)
        statement = (match.group(1) if match else line).strip()
        statement_folded = statement.casefold()
        direct_output = (
            any(marker in statement_folded for marker in _OUTPUT_ACTION_MARKERS)
            and any(marker in statement_folded for marker in _OUTPUT_OBJECT_MARKERS)
        )
        if match and output_context:
            rule = "output_section_item"
            captured_in_context = True
        elif direct_output:
            rule = "explicit_output_action"
        else:
            if output_context and captured_in_context and not match:
                output_context = False
            continue

        key = re.sub(r"\s+", " ", statement).casefold()
        if key and key not in seen:
            seen.add(key)
            extracted.append((statement, rule))

    return [
        {
            "id": f"SRC-{index:03d}",
            "text": statement,
            "source": "explicit",
            "extraction_rule": rule,
        }
        for index, (statement, rule) in enumerate(extracted, start=1)
    ]


def build_contract_request(
    *,
    raw_text: str,
    needed_output: str,
    context_next_action: dict[str, Any] | None,
    request_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_requirements = extract_source_requirements(raw_text)
    result = {
        "fulfillment_request_version": FULFILLMENT_VERSION,
        "purpose": (
            "Define the smallest durable result that fulfills the original request. "
            "Do not require code, an app, or deployment unless the request requires it."
        ),
        "source": {
            "original_request": raw_text,
            "router_needed_output": needed_output,
            "context_next_action": context_next_action or {},
        },
        "source_requirements": source_requirements,
        "contract_schema": {
            "fulfillment_contract_version": FULFILLMENT_VERSION,
            "request_binding": "Copy fulfillment_request.request_binding exactly when present.",
            "contract_status": "ready | waiting_user | not_required | blocked",
            "requested_output": {
                "description": "Concrete result promised to the user",
                "deliverable_type": "report | ideas | code | image | plan | other",
                "format": "markdown | json | code | image | directory | chat | other",
                "minimum_count": 1,
                "unit": "file | item | image | directory | response",
            },
            "acceptance_criteria": [
                {
                    "id": "AC-01",
                    "description": "Observable condition derived from the original request",
                    "source": "explicit | inferred | mixed",
                    "source_requirement_ids": ["SRC-001"],
                }
            ],
            "artifact_policy": {
                "finalization_mode": "managed_deliverable | project_native | chat",
                "minimum_registered_artifacts": 1,
                "require_project_deliverable": True,
                "require_milestone_snapshot": True,
            },
            "risk": {
                "level": "low | medium | high",
                "requires_user_approval": False,
            },
            "needs_user_input": {
                "required": False,
                "questions": [],
            },
            "not_required_reason": "Required only when contract_status is not_required",
            "agent_notes": "Optional concise rationale",
        },
        "evidence_schema": {
            "fulfillment_evidence_version": FULFILLMENT_VERSION,
            "request_binding": "Copy fulfillment_request.request_binding exactly when present.",
            "result_status": "fulfilled | not_required",
            "artifact_ids": ["registered_artifact_id"],
            "deliverable_paths": ["registered project-relative or absolute path"],
            "criteria_results": [
                {
                    "criterion_id": "AC-01",
                    "status": "pass | fail",
                    "evidence": "Observable file, section, count, or result",
                }
            ],
            "agent_summary": "What was actually produced",
        },
        "rules": [
            "Judge completion against the original requested output, not a universal implementation artifact.",
            "Use waiting_user when a missing decision would materially change the result.",
            "For a requested durable result, register at least one present artifact.",
            "Use managed_deliverable for standalone final files: keep the working source, copy the final file into ProjectRoot/deliverables, and create a milestone snapshot.",
            "Use project_native for code or directory structures that must remain in their project-native location.",
            "Do not mark a criterion pass without observable evidence.",
            "Every source_requirements id must be linked by at least one acceptance criterion.",
            "Do not delete, merge away, or weaken explicit source requirements when writing the contract.",
        ],
    }
    if request_binding:
        result["request_binding"] = dict(request_binding)
    return result


def _violation(
    code: str,
    path: str,
    message: str,
    *,
    severity: str = "fail",
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "path": path,
        "message": message,
    }


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _path_key(value: str | Path, *, base: Path | None = None) -> str:
    path = Path(value)
    if not path.is_absolute() and base is not None:
        path = base / path
    return str(path.resolve(strict=False)).casefold()


def _is_within(parent: Path, child: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _requires_uncertainty_marking(criteria: list[Any]) -> bool:
    descriptions = "\n".join(
        _text(item.get("description")).casefold()
        for item in criteria
        if isinstance(item, dict)
    )
    return any(marker.casefold() in descriptions for marker in _UNCERTAINTY_POLICY_MARKERS)


def _has_quantified_performance_claim(line: str) -> bool:
    for match in _QUANTIFIED_VALUE.finditer(line):
        nearby_text = line[max(0, match.start() - 40) : min(len(line), match.end() + 40)]
        if _PERFORMANCE_CLAIM_CONTEXT.search(nearby_text):
            return True
    return False


def _scan_unmarked_quantified_claims(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for artifact in artifacts:
        path_value = _text(artifact.get("absolute_path"))
        path = Path(path_value) if path_value else None
        if (
            path is None
            or path.suffix.casefold() not in TEXT_ARTIFACT_SUFFIXES
            or not path.is_file()
        ):
            continue
        try:
            if path.stat().st_size > MAX_CLAIM_SCAN_BYTES:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        inside_fence = False
        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if line.startswith("```"):
                inside_fence = not inside_fence
                continue
            if inside_fence or not line:
                continue
            folded = line.casefold()
            if any(marker.casefold() in folded for marker in _CLAIM_DISCLOSURE_MARKERS):
                continue
            if _has_quantified_performance_claim(line):
                findings.append(
                    {
                        "artifact_id": _text(artifact.get("id")) or "unknown",
                        "path": str(path),
                        "line": line_number,
                        "text": line[:240],
                    }
                )
    return findings


def validate_fulfillment(
    run_dir: Path,
    manifest: dict[str, Any],
    contract: Any,
    evidence: Any,
    request: Any | None = None,
) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    unmarked_quantified_claims: list[dict[str, Any]] = []
    if not isinstance(contract, dict) or is_placeholder(contract):
        violations.append(
            _violation(
                "FULFILLMENT_CONTRACT_REQUIRED",
                "fulfillment_contract",
                "The agent-filled fulfillment contract is missing or still a placeholder.",
            )
        )
        contract = {}

    contract_status = _text(contract.get("contract_status"))
    contract_version = _text(contract.get("fulfillment_contract_version"))
    if contract_version not in SUPPORTED_FULFILLMENT_VERSIONS:
        violations.append(
            _violation(
                "FULFILLMENT_CONTRACT_VERSION_INVALID",
                "fulfillment_contract_version",
                f"Expected one of {sorted(SUPPORTED_FULFILLMENT_VERSIONS)}.",
            )
        )
    if contract_status not in CONTRACT_STATUSES:
        violations.append(
            _violation(
                "FULFILLMENT_CONTRACT_STATUS_INVALID",
                "contract_status",
                f"contract_status must be one of {sorted(CONTRACT_STATUSES)}.",
            )
        )

    requested_output = contract.get("requested_output")
    if not isinstance(requested_output, dict):
        requested_output = {}
    criteria = contract.get("acceptance_criteria")
    if not isinstance(criteria, list):
        criteria = []
    artifact_policy = contract.get("artifact_policy")
    if not isinstance(artifact_policy, dict):
        artifact_policy = {}
    needs_user_input = contract.get("needs_user_input")
    if not isinstance(needs_user_input, dict):
        needs_user_input = {}

    if contract_status == "ready":
        if not _text(requested_output.get("description")):
            violations.append(
                _violation(
                    "REQUESTED_OUTPUT_DESCRIPTION_REQUIRED",
                    "requested_output.description",
                    "A concrete requested output description is required.",
                )
            )
        if not _text(requested_output.get("deliverable_type")) or not _text(requested_output.get("format")):
            violations.append(
                _violation(
                    "REQUESTED_OUTPUT_TYPE_REQUIRED",
                    "requested_output",
                    "deliverable_type and format are required for a ready contract.",
                )
            )
        minimum_count = requested_output.get("minimum_count")
        if not isinstance(minimum_count, int) or minimum_count < 1:
            violations.append(
                _violation(
                    "REQUESTED_OUTPUT_MINIMUM_INVALID",
                    "requested_output.minimum_count",
                    "minimum_count must be an integer of at least 1 for a ready contract.",
                )
            )
        if not criteria:
            violations.append(
                _violation(
                    "ACCEPTANCE_CRITERIA_REQUIRED",
                    "acceptance_criteria",
                    "At least one observable acceptance criterion is required.",
                )
            )

    source_requirements: list[dict[str, Any]] = []
    source_requirement_ids: list[str] = []
    source_coverage_enabled = (
        contract_version == FULFILLMENT_VERSION
        and isinstance(request, dict)
        and not is_placeholder(request)
    )
    if source_coverage_enabled:
        if isinstance(request, dict):
            raw_requirements = request.get("source_requirements")
            if isinstance(raw_requirements, list):
                source_requirements = [item for item in raw_requirements if isinstance(item, dict)]
        if not isinstance(request.get("source_requirements"), list):
            violations.append(
                _violation(
                    "SOURCE_REQUIREMENT_BASELINE_REQUIRED",
                    "fulfillment_request.source_requirements",
                    "Fulfillment 0.3 requires a source-derived requirement baseline list.",
                )
            )
        for index, item in enumerate(source_requirements):
            requirement_id = _text(item.get("id"))
            if not requirement_id or not _text(item.get("text")) or requirement_id in source_requirement_ids:
                violations.append(
                    _violation(
                        "SOURCE_REQUIREMENT_INVALID",
                        f"fulfillment_request.source_requirements[{index}]",
                        "Each source requirement needs a unique id and preserved source text.",
                    )
                )
            else:
                source_requirement_ids.append(requirement_id)

    criterion_ids: list[str] = []
    covered_source_requirement_ids: set[str] = set()
    for index, item in enumerate(criteria):
        if not isinstance(item, dict):
            violations.append(
                _violation(
                    "ACCEPTANCE_CRITERION_INVALID",
                    f"acceptance_criteria[{index}]",
                    "Each acceptance criterion must be an object.",
                )
            )
            continue
        criterion_id = _text(item.get("id"))
        if not criterion_id or not _text(item.get("description")):
            violations.append(
                _violation(
                    "ACCEPTANCE_CRITERION_INCOMPLETE",
                    f"acceptance_criteria[{index}]",
                    "Each criterion needs a stable id and observable description.",
                )
            )
        elif criterion_id in criterion_ids:
            violations.append(
                _violation(
                    "ACCEPTANCE_CRITERION_DUPLICATE",
                    f"acceptance_criteria[{index}].id",
                    f"Duplicate acceptance criterion id: {criterion_id}",
                )
            )
        else:
            criterion_ids.append(criterion_id)
        if source_coverage_enabled and source_requirement_ids:
            linked_ids = item.get("source_requirement_ids")
            criterion_source = _text(item.get("source")).casefold()
            if criterion_source == "inferred" and not linked_ids:
                continue
            if not isinstance(linked_ids, list) or not linked_ids:
                violations.append(
                    _violation(
                        "ACCEPTANCE_SOURCE_LINK_REQUIRED",
                        f"acceptance_criteria[{index}].source_requirement_ids",
                        "Each 0.3 acceptance criterion must link to at least one source requirement.",
                    )
                )
                continue
            for linked_id in {_text(value) for value in linked_ids if _text(value)}:
                if linked_id not in source_requirement_ids:
                    violations.append(
                        _violation(
                            "ACCEPTANCE_SOURCE_LINK_UNKNOWN",
                            f"acceptance_criteria[{index}].source_requirement_ids",
                            f"Unknown source requirement id: {linked_id}",
                        )
                    )
                else:
                    covered_source_requirement_ids.add(linked_id)

    missing_source_coverage = sorted(set(source_requirement_ids) - covered_source_requirement_ids)
    if source_coverage_enabled and missing_source_coverage:
        violations.append(
            _violation(
                "SOURCE_REQUIREMENT_COVERAGE_INCOMPLETE",
                "acceptance_criteria.source_requirement_ids",
                "Acceptance criteria do not cover source requirements: "
                + ", ".join(missing_source_coverage),
            )
        )

    request_binding = request.get("request_binding") if isinstance(request, dict) else None
    if isinstance(request_binding, dict):
        contract_binding = contract.get("request_binding")
        if contract_binding != request_binding:
            violations.append(
                _violation(
                    "FULFILLMENT_REQUEST_BINDING_MISMATCH",
                    "request_binding",
                    "The fulfillment contract must copy the latest request binding exactly.",
                )
            )

    minimum_artifacts = artifact_policy.get("minimum_registered_artifacts", 0)
    if not isinstance(minimum_artifacts, int) or minimum_artifacts < 0:
        violations.append(
            _violation(
                "ARTIFACT_MINIMUM_INVALID",
                "artifact_policy.minimum_registered_artifacts",
                "minimum_registered_artifacts must be a non-negative integer.",
            )
        )
        minimum_artifacts = 0

    output_format = _text(requested_output.get("format")).casefold()
    finalization_mode = _text(artifact_policy.get("finalization_mode")).casefold()
    if contract_status == "ready" and contract_version in MODERN_FULFILLMENT_VERSIONS:
        if finalization_mode not in FINALIZATION_MODES:
            violations.append(
                _violation(
                    "FINALIZATION_MODE_INVALID",
                    "artifact_policy.finalization_mode",
                    f"finalization_mode must be one of {sorted(FINALIZATION_MODES)}.",
                )
            )
        elif output_format == "chat" and finalization_mode != "chat":
            violations.append(
                _violation(
                    "CHAT_FINALIZATION_MODE_REQUIRED",
                    "artifact_policy.finalization_mode",
                    "A chat-only result must use finalization_mode=chat.",
                )
            )
        elif output_format != "chat" and finalization_mode == "chat":
            violations.append(
                _violation(
                    "DURABLE_FINALIZATION_MODE_REQUIRED",
                    "artifact_policy.finalization_mode",
                    "A non-chat result must use managed_deliverable or project_native.",
                )
            )
        if output_format != "chat" and artifact_policy.get("require_project_deliverable") is not True:
            violations.append(
                _violation(
                    "PROJECT_DELIVERABLE_POLICY_REQUIRED",
                    "artifact_policy.require_project_deliverable",
                    "A durable result must require a registered project deliverable.",
                )
            )
        if finalization_mode == "managed_deliverable" and artifact_policy.get("require_milestone_snapshot") is not True:
            violations.append(
                _violation(
                    "MILESTONE_SNAPSHOT_POLICY_REQUIRED",
                    "artifact_policy.require_milestone_snapshot",
                    "A managed final file must require a milestone snapshot.",
                )
            )

    if contract_status == "ready" and output_format != "chat" and minimum_artifacts < 1:
        violations.append(
            _violation(
                "ARTIFACT_POLICY_TOO_WEAK",
                "artifact_policy.minimum_registered_artifacts",
                "A non-chat requested output requires at least one registered artifact.",
            )
        )

    if contract_status in {"waiting_user", "blocked"} or needs_user_input.get("required") is True:
        violations.append(
            _violation(
                "FULFILLMENT_NOT_READY",
                "contract_status",
                "The contract still requires user input or is blocked and cannot be completed.",
            )
        )

    if contract_status == "not_required":
        if not _text(contract.get("not_required_reason")):
            violations.append(
                _violation(
                    "NOT_REQUIRED_REASON_MISSING",
                    "not_required_reason",
                    "Explain why the original request does not require a durable result.",
                )
            )
    elif contract_status == "ready":
        if not isinstance(evidence, dict) or is_placeholder(evidence):
            violations.append(
                _violation(
                    "FULFILLMENT_EVIDENCE_REQUIRED",
                    "fulfillment_evidence",
                    "Fulfillment evidence is missing or still a placeholder.",
                )
            )
            evidence = {}
        if isinstance(request_binding, dict) and evidence.get("request_binding") != request_binding:
            violations.append(
                _violation(
                    "FULFILLMENT_EVIDENCE_BINDING_MISMATCH",
                    "fulfillment_evidence.request_binding",
                    "Fulfillment evidence must refer to the same latest request binding.",
                )
            )
        expected_evidence_version = contract_version or FULFILLMENT_VERSION
        if evidence.get("fulfillment_evidence_version") != expected_evidence_version:
            violations.append(
                _violation(
                    "FULFILLMENT_EVIDENCE_VERSION_INVALID",
                    "fulfillment_evidence_version",
                    f"Expected fulfillment evidence version {expected_evidence_version}.",
                )
            )
        if evidence.get("result_status") != "fulfilled":
            violations.append(
                _violation(
                    "FULFILLMENT_RESULT_NOT_FULFILLED",
                    "result_status",
                    "result_status must be fulfilled for a ready contract.",
                )
            )

        artifact_ids = evidence.get("artifact_ids")
        if not isinstance(artifact_ids, list):
            artifact_ids = []
        artifact_ids = [_text(item) for item in artifact_ids if _text(item)]
        artifact_status = artifact_store.inspect_artifacts(run_dir)
        present_by_id = {
            _text(item.get("id")): item
            for item in artifact_status.get("artifacts", [])
            if isinstance(item, dict) and item.get("exists") is True
        }
        if len(set(artifact_ids)) < minimum_artifacts:
            violations.append(
                _violation(
                    "REGISTERED_ARTIFACT_MINIMUM_NOT_MET",
                    "artifact_ids",
                    f"At least {minimum_artifacts} registered artifact id(s) are required.",
                )
            )
        missing_artifact_ids = sorted(set(artifact_ids) - set(present_by_id))
        if missing_artifact_ids:
            violations.append(
                _violation(
                    "FULFILLMENT_ARTIFACT_MISSING",
                    "artifact_ids",
                    f"Evidence references unregistered or missing artifacts: {', '.join(missing_artifact_ids)}",
                )
            )
        referenced_artifacts = [present_by_id[item] for item in artifact_ids if item in present_by_id]
        if _requires_uncertainty_marking(criteria):
            unmarked_quantified_claims = _scan_unmarked_quantified_claims(referenced_artifacts)
            if unmarked_quantified_claims:
                samples = "; ".join(
                    f"{item['artifact_id']}:{item['line']}"
                    for item in unmarked_quantified_claims[:5]
                )
                violations.append(
                    _violation(
                        "UNMARKED_QUANTIFIED_CLAIM",
                        "artifact_ids",
                        "Quantified performance claims require a validation_needed, hypothesis, estimate, or target marker. "
                        f"Found {len(unmarked_quantified_claims)} unmarked claim(s): {samples}.",
                    )
                )
        unique_artifact_keys = {
            (
                "sha256",
                _text(item.get("content_sha256")),
            )
            if _text(item.get("content_sha256"))
            else ("artifact_id", _text(item.get("id")))
            for item in referenced_artifacts
        }
        if len(unique_artifact_keys) < minimum_artifacts:
            duplicate_groups: dict[str, list[str]] = {}
            for item in referenced_artifacts:
                content_sha256 = _text(item.get("content_sha256"))
                if content_sha256:
                    duplicate_groups.setdefault(content_sha256, []).append(
                        _text(item.get("id")) or "unknown"
                    )
            duplicates = [
                ids for ids in duplicate_groups.values() if len(ids) > 1
            ]
            duplicate_detail = "; ".join(", ".join(ids) for ids in duplicates)
            violations.append(
                _violation(
                    "REGISTERED_ARTIFACT_UNIQUE_MINIMUM_NOT_MET",
                    "artifact_ids",
                    f"At least {minimum_artifacts} content-distinct registered artifact(s) are required; "
                    f"found {len(unique_artifact_keys)}. Duplicate groups: {duplicate_detail or 'none'}.",
                )
            )
        project_root_value = manifest.get("project_root_absolute")
        project_root = Path(str(project_root_value)) if project_root_value else None
        if contract_version in MODERN_FULFILLMENT_VERSIONS and project_root is not None:
            unmanaged_external_outputs = [
                item
                for item in referenced_artifacts
                if artifact_store.requires_external_output_custody(
                    source=Path(str(item.get("absolute_path") or "")),
                    role=str(item.get("role") or ""),
                    project_root=project_root,
                )
            ]
            if unmanaged_external_outputs:
                violations.append(
                    _violation(
                        "EXTERNAL_GENERATED_ARTIFACT_NOT_MANAGED",
                        "artifact_ids",
                        "Generated outputs outside ProjectRoot must be copied into the RunDir before completion: "
                        + ", ".join(str(item.get("id") or "unknown") for item in unmanaged_external_outputs),
                    )
                )
        if (
            contract_version in MODERN_FULFILLMENT_VERSIONS
            and finalization_mode == "managed_deliverable"
            and not any(
                item.get("storage_mode") == "milestone_snapshot"
                and item.get("bound_to_run") is True
                for item in referenced_artifacts
            )
        ):
            violations.append(
                _violation(
                    "FINAL_MILESTONE_SNAPSHOT_REQUIRED",
                    "artifact_ids",
                    "A managed final file requires a present milestone snapshot bound to the RunDir.",
                )
            )

        evidence_paths = evidence.get("deliverable_paths")
        if not isinstance(evidence_paths, list):
            evidence_paths = []
        evidence_path_keys = {
            _path_key(item, base=project_root) for item in evidence_paths if _text(item)
        }
        registered_deliverables = [
            item
            for item in manifest.get("deliverable_paths", [])
            if isinstance(item, dict) and item.get("path_absolute")
        ]
        registered_path_keys = {
            _path_key(str(item["path_absolute"]))
            for item in registered_deliverables
            if Path(str(item["path_absolute"])).exists()
        }
        unregistered_paths = sorted(evidence_path_keys - registered_path_keys)
        if (
            contract_version in MODERN_FULFILLMENT_VERSIONS
            and finalization_mode == "managed_deliverable"
            and evidence_path_keys
        ):
            snapshot_source_keys = {
                _path_key(str(item.get("original_source")))
                for item in referenced_artifacts
                if item.get("storage_mode") == "milestone_snapshot" and item.get("original_source")
            }
            if not snapshot_source_keys.intersection(evidence_path_keys):
                violations.append(
                    _violation(
                        "FINAL_SNAPSHOT_DELIVERABLE_MISMATCH",
                        "artifact_ids",
                        "The managed milestone snapshot must originate from a registered evidence deliverable.",
                    )
                )
        if unregistered_paths:
            violations.append(
                _violation(
                    "FULFILLMENT_DELIVERABLE_UNREGISTERED",
                    "deliverable_paths",
                    "Evidence references project deliverables that are absent or not registered.",
                )
            )
        if contract_version in MODERN_FULFILLMENT_VERSIONS and finalization_mode == "managed_deliverable":
            if project_root is None:
                violations.append(
                    _violation(
                        "PROJECT_ROOT_REQUIRED_FOR_FINALIZATION",
                        "workflow_manifest.project_root_absolute",
                        "Managed finalization requires a governed ProjectRoot.",
                    )
                )
            else:
                deliverables_root = project_root / "deliverables"
                outside_managed_root = []
                for item in evidence_paths:
                    candidate = Path(str(item))
                    if not candidate.is_absolute():
                        candidate = project_root / candidate
                    if not _is_within(deliverables_root, candidate):
                        outside_managed_root.append(str(item))
                if outside_managed_root:
                    violations.append(
                        _violation(
                            "FINAL_DELIVERABLE_DIRECTORY_REQUIRED",
                            "deliverable_paths",
                            "Managed final files must be registered under ProjectRoot/deliverables.",
                        )
                    )
        if artifact_policy.get("require_project_deliverable") is True and not evidence_path_keys:
            violations.append(
                _violation(
                    "PROJECT_DELIVERABLE_REQUIRED",
                    "deliverable_paths",
                    "At least one registered project-owned deliverable is required by the contract.",
                )
            )

        results = evidence.get("criteria_results")
        if not isinstance(results, list):
            results = []
        results_by_id = {
            _text(item.get("criterion_id")): item
            for item in results
            if isinstance(item, dict) and _text(item.get("criterion_id"))
        }
        for criterion_id in criterion_ids:
            result = results_by_id.get(criterion_id)
            if not result or result.get("status") != "pass" or not _text(result.get("evidence")):
                violations.append(
                    _violation(
                        "ACCEPTANCE_CRITERION_NOT_PROVEN",
                        f"criteria_results.{criterion_id}",
                        f"Criterion {criterion_id} needs pass status and observable evidence.",
                    )
                )

    valid = not any(item["severity"] == "fail" for item in violations)
    return {
        "fulfillment_validation_version": FULFILLMENT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "valid": valid,
        "can_complete": valid and contract_status in {"ready", "not_required"},
        "severity": "pass" if valid else "fail",
        "contract_status": contract_status or "unknown",
        "summary": {
            "fail_count": sum(item["severity"] == "fail" for item in violations),
            "warn_count": sum(item["severity"] == "warn" for item in violations),
            "acceptance_criteria_count": len(criterion_ids),
            "minimum_registered_artifacts": minimum_artifacts,
            "unique_referenced_artifact_count": (
                len(unique_artifact_keys) if contract_status == "ready" else 0
            ),
            "finalization_mode": finalization_mode or "legacy_unspecified",
            "source_requirement_count": len(source_requirement_ids),
            "source_requirement_covered_count": len(covered_source_requirement_ids),
            "unmarked_quantified_claim_count": len(unmarked_quantified_claims),
        },
        "violations": violations,
    }
