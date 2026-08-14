from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
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

from shared.run_identity import sanitize_run_name, unique_run_dir as identity_unique_run_dir

SCHEMA_VERSION = "0.1.0"

SCHEMA_PROFILE_ID = "user_input_analysis"

COMMON_REQUIRED_FIELDS = [
    "input_type",
    "core_topic",
    "goal",
    "missing_information",
    "next_best_question",
]

SCHEMA_FIELD_KEYS = [
    "input_type",
    "core_topic",
    "goal",
    "missing_information",
    "next_best_question",
    "user_state",
    "explicit_facts",
    "inferred_assumptions",
    "expansion_axes",
    "confidence_summary",
    "problem_definition_level",
    "domain_candidates",
    "agent_response_strategy",
]

SCHEMA_PROFILE = {
    "id": SCHEMA_PROFILE_ID,
    "name": "user_input_analysis",
    "description": "사용자 입력의 의도, 모호성, 다음 질문, 에이전트 응답 전략을 구조화하는 스키마입니다.",
    "intended_use": "모호한 입력을 구조화하고 에이전트가 다음 행동을 정할 때 사용합니다.",
    "expected_strength": "모호한 부분과 보강해야 할 부분을 드러내면서도 후속 작업 맥락이 풍부합니다.",
    "expected_risk": "AI가 과하게 추론할 수 있으므로 confidence, evidence, unresolved_fields 검증이 중요합니다.",
}

FIELD_DEFINITIONS: dict[str, dict[str, Any]] = {
    "input_type": {
        "type": "enum",
        "description": "사용자 입력의 형태를 분류합니다.",
        "examples": [
            "sentence_request",
            "paragraph_request",
            "keyword",
            "question",
            "mixed",
            "unknown",
        ],
        "inference_rule": "원문의 길이, 문장 구조, 질문 표현, 동사 유무를 보고 입력 형태를 하나 이상 판단합니다.",
    },
    "core_topic": {
        "type": "string",
        "description": "사용자가 다루고 싶어 하는 핵심 주제입니다.",
        "examples": [
            "파이썬 자동화 구현",
            "업무 자동화 설계",
            "버그 관리 체계화",
        ],
        "inference_rule": "원문에 직접 드러난 핵심 대상은 explicit에, 문맥상 요약한 주제는 inferred에 넣습니다.",
    },
    "goal": {
        "type": "string",
        "description": "사용자가 최종적으로 달성하고 싶은 목표입니다.",
        "examples": [
            "자동화 프로그램 구현 방법을 알고 싶다",
            "문제를 구조화해서 다음 행동을 정하고 싶다",
        ],
        "inference_rule": "사용자가 원하는 결과나 도달 상태를 원문 근거와 함께 추론합니다.",
    },
    "missing_information": {
        "type": "array[string]",
        "description": "좋은 답변이나 다음 진행을 위해 부족한 정보입니다.",
        "examples": [
            "자동화 대상",
            "사용 환경",
            "원하는 결과물",
            "입력 데이터",
        ],
        "inference_rule": "현재 원문만으로 확정할 수 없는 핵심 정보를 항목 단위로 작성합니다.",
    },
    "next_best_question": {
        "type": "string",
        "description": "에이전트가 사용자에게 가장 먼저 물어봐야 할 질문입니다.",
        "examples": [
            "자동화하고 싶은 작업은 무엇인가요?",
            "최종 결과물은 코드, 설계서, 체크리스트 중 무엇에 가까운가요?",
        ],
        "inference_rule": "다음 진행을 가장 크게 열어주는 질문 하나를 우선 선택합니다.",
    },
    "user_state": {
        "type": "string",
        "description": "사용자의 현재 상태, 막힌 지점, 진행 감각입니다.",
        "examples": [
            "어디서부터 시작해야 할지 모름",
            "대상은 있으나 구현 방법을 모름",
        ],
        "inference_rule": "감정 진단이 아니라 작업 진행 상태를 중심으로 추론합니다.",
    },
    "explicit_facts": {
        "type": "array[string]",
        "description": "원문에 직접 명시된 사실입니다.",
        "examples": [
            "파이썬을 사용하고 싶다",
            "자동화를 구현하고 싶다",
        ],
        "inference_rule": "원문에 직접 존재하는 정보만 넣고, 해석이 필요한 내용은 inferred_assumptions로 분리합니다.",
    },
    "inferred_assumptions": {
        "type": "array[string]",
        "description": "원문에서 추론한 가정입니다.",
        "examples": [
            "초기 설계 단계에 있다",
            "구현 대상을 아직 좁히지 못했다",
        ],
        "inference_rule": "명시 정보와 구분하고, 각 추론에는 confidence와 evidence를 반드시 포함합니다.",
    },
    "expansion_axes": {
        "type": "array[string]",
        "description": "문제를 더 구조화하거나 확장할 때 사용할 분석 축입니다.",
        "examples": [
            "automation_target",
            "runtime_environment",
            "input_data",
            "output_result",
            "execution_method",
        ],
        "inference_rule": "만다라트처럼 8칸에 고정하지 말고, 입력에 맞는 축을 필요한 만큼 제안합니다.",
    },
    "confidence_summary": {
        "type": "string",
        "description": "분석 결과에서 확실한 부분과 불확실한 부분의 요약입니다.",
        "examples": [
            "핵심 주제는 확실하지만 자동화 대상은 불명확함",
        ],
        "inference_rule": "어떤 필드는 신뢰도가 높고 어떤 필드는 낮은지 짧게 요약합니다.",
    },
    "problem_definition_level": {
        "type": "enum",
        "description": "사용자의 문제가 얼마나 구체적으로 정의되어 있는지의 수준입니다.",
        "examples": ["low", "medium", "high"],
        "inference_rule": "대상, 맥락, 제약, 결과물이 얼마나 명확한지 기준으로 판단합니다.",
    },
    "domain_candidates": {
        "type": "array[string]",
        "description": "입력이 속할 수 있는 도메인 후보입니다.",
        "examples": [
            "software_automation",
            "data_processing",
            "document_generation",
            "workflow_design",
        ],
        "inference_rule": "하나로 확정하기 어렵다면 후보를 여러 개 제안하고 confidence로 구분합니다.",
    },
    "agent_response_strategy": {
        "type": "string",
        "description": "이 분석 결과를 읽은 에이전트가 취하면 좋은 응답 전략입니다.",
        "examples": [
            "바로 해결책을 제시하지 말고 자동화 대상을 먼저 좁히는 질문을 한다",
            "사용자의 막힌 지점을 요약한 뒤 하나씩 질문한다",
        ],
        "inference_rule": "사용자에게 직접 답변하지 말고, 후속 에이전트가 어떤 방식으로 응답하면 좋은지 제안합니다.",
    },
}

VALUE_TEMPLATE = {
    "explicit": [],
    "inferred": [],
}

INFERRED_ITEM_SCHEMA = {
    "content": "추론한 값 또는 항목",
    "confidence": "0.0부터 1.0 사이의 숫자",
    "evidence": "원문에서 추론 근거가 된 표현",
}

UNRESOLVED_ITEM_SCHEMA = {
    "field_name": "채우지 못한 필드명",
    "reason": "왜 채울 수 없었는지에 대한 짧은 이유",
}

EXTENSION_ITEM_SCHEMA = {
    "field_name": "새로 제안하는 필드명",
    "suggested_type": "string, array[string], object 등 제안 타입",
    "reason": "왜 이 필드가 필요한지",
    "example_value": "해당 필드가 가질 수 있는 예시 값",
    "promotion_status": "candidate",
}

OUTPUT_RULES = [
    "Return valid JSON only.",
    "Do not answer the user's original request directly.",
    "Fill only analysis_schema.<field>.value, unresolved_fields.items, and schema_extension_suggestions.items.",
    "Do not modify schema_version, input, descriptions, examples, types, required flags, inference rules, or validation rules.",
    "Each value must separate explicit facts from inferred assumptions.",
    "Each inferred item must include content, confidence, and evidence.",
    "Confidence must be a number from 0.0 to 1.0.",
    "Do not force a value when the raw input does not provide enough evidence.",
    "If a value would be a weak guess, leave the value empty and record the field in unresolved_fields.items.",
    "If a required field cannot be filled, add it to unresolved_fields.items with a reason.",
    "Do not add new fields inside analysis_schema.",
    "Suggest new fields only in schema_extension_suggestions.items.",
]

CLARIFICATION_QUESTIONS = {
    "input_type": "입력 의도가 요청, 질문, 키워드, 문단 중 어디에 가까운지 조금 더 설명해 줄 수 있나요?",
    "core_topic": "가장 중심에 두고 싶은 주제는 무엇인가요?",
    "goal": "최종적으로 얻고 싶은 결과는 무엇인가요?",
    "missing_information": "좋은 답변을 위해 아직 부족하다고 느끼는 정보가 있나요?",
    "next_best_question": "이후 진행을 위해 가장 먼저 확인하고 싶은 부분은 무엇인가요?",
    "user_state": "현재 가장 막히는 지점은 무엇인가요?",
    "explicit_facts": "반드시 사실로 반영해야 하는 조건이나 정보가 있나요?",
    "inferred_assumptions": "추론해도 되는 범위와 피해야 할 가정이 있나요?",
    "expansion_axes": "이 주제를 어떤 관점으로 넓혀보고 싶나요?",
    "confidence_summary": "확실한 정보와 아직 불확실한 정보를 구분해 줄 수 있나요?",
    "problem_definition_level": "문제가 이미 구체적인가요, 아니면 아직 탐색 단계인가요?",
    "domain_candidates": "이 요청이 속한 분야나 업무 영역은 무엇인가요?",
    "agent_response_strategy": "에이전트가 바로 답변하길 원하나요, 아니면 질문으로 좁혀가길 원하나요?",
}

HUMAN_EVALUATION_TEMPLATE = {
    "scale": "1 to 5",
    "criteria": {
        "intent_accuracy": {
            "score": None,
            "description": "사용자의 의도를 얼마나 정확하게 잡았는가",
        },
        "next_question_quality": {
            "score": None,
            "description": "다음 질문이 실제 진행을 잘 열어주는가",
        },
        "agent_actionability": {
            "score": None,
            "description": "에이전트가 이 JSON을 보고 바로 답변이나 작업을 이어가기 쉬운가",
        },
        "schema_complexity": {
            "score": None,
            "description": "JSON이 충분히 읽기 쉽고 복잡도가 적절한가",
        },
        "inference_control": {
            "score": None,
            "description": "AI가 과하게 상상하지 않고 명시 정보와 추론을 잘 구분했는가",
        },
    },
    "notes": "",
}


def safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("unicode_escape").decode("ascii"))


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_value_contract() -> dict[str, Any]:
    return {
        "shape": deepcopy(VALUE_TEMPLATE),
        "inferred_item_schema": deepcopy(INFERRED_ITEM_SCHEMA),
    }


def build_field(field_name: str) -> dict[str, Any]:
    definition = deepcopy(FIELD_DEFINITIONS[field_name])
    return {
        "type": definition["type"],
        "required": field_name in COMMON_REQUIRED_FIELDS,
        "description": definition["description"],
        "examples": definition["examples"],
        "inference_rule": definition["inference_rule"],
        "value_contract": build_value_contract(),
        "value": deepcopy(VALUE_TEMPLATE),
    }


def build_request(text: str, *, created_at: str | None = None) -> dict[str, Any]:
    timestamp = created_at or datetime.now().isoformat(timespec="seconds")

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_role": "base_schema_request_builder",
        "contract_name": "ai_fillable_user_input_analysis",
        "created_at": timestamp,
        "profile": {
            "id": SCHEMA_PROFILE["id"],
            "name": SCHEMA_PROFILE["name"],
            "description": SCHEMA_PROFILE["description"],
        },
        "input": {
            "raw_text": text,
        },
        "ai_task": {
            "task_name": "fill_user_input_analysis_schema",
            "instruction": (
                "Read input.raw_text and fill the schema values for an agent. "
                "Do not answer the user's original request directly."
            ),
            "allowed_mutations": [
                "analysis_schema.<field>.value",
                "unresolved_fields.items",
                "schema_extension_suggestions.items",
            ],
        },
        "analysis_schema": {
            field_name: build_field(field_name)
            for field_name in SCHEMA_FIELD_KEYS
        },
        "unresolved_fields": {
            "description": "Required fields that AI could not fill must be recorded here with a reason.",
            "item_schema": deepcopy(UNRESOLVED_ITEM_SCHEMA),
            "items": [],
        },
        "schema_extension_suggestions": {
            "description": (
                "AI may suggest new fields here only. Do not add new fields directly "
                "to analysis_schema."
            ),
            "item_schema": deepcopy(EXTENSION_ITEM_SCHEMA),
            "items": [],
        },
        "output_rules": deepcopy(OUTPUT_RULES),
        "validation_contract": {
            "immutable_policy": "Everything is immutable except allowed_mutations.",
            "required_value_policy": (
                "Required field with value is pass. Required field without value is warn "
                "only when unresolved_fields.items includes a reason. Otherwise it is fail."
            ),
            "extra_field_policy": "Extra fields are forbidden except inside schema_extension_suggestions.items.",
            "direct_answer_policy": "Directly answering the user is forbidden.",
        },
        "clarification_policy": {
            "principle": "Do not force uncertain fields. Ask the user when the input is too ambiguous.",
            "validator_behavior": (
                "When required fields are unresolved with reasons, validation returns warn "
                "and includes clarification_required details."
            ),
        },
        "human_evaluation_template": deepcopy(HUMAN_EVALUATION_TEMPLATE),
        "comparison_notes": {
            "intended_use": SCHEMA_PROFILE["intended_use"],
            "expected_strength": SCHEMA_PROFILE["expected_strength"],
            "expected_risk": SCHEMA_PROFILE["expected_risk"],
        },
    }


def unique_run_dir(base_dir: Path, run_name: str | None) -> Path:
    return identity_unique_run_dir(base_dir, run_name)


def write_json(path: Path, data: Any) -> None:
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def build_run_report(text: str, requests: list[dict[str, Any]]) -> str:
    lines = [
        "# Schema Request Build Report",
        "",
        "## Input",
        "",
        "```text",
        text,
        "```",
        "",
        "## Generated Schema",
        "",
    ]

    for request in requests:
        profile = request["profile"]
        fields = list(request["analysis_schema"].keys())
        required_fields = [
            field_name
            for field_name, field_spec in request["analysis_schema"].items()
            if field_spec.get("required")
        ]
        lines.extend(
            [
                f"### {profile['name']}",
                "",
                profile["description"],
                "",
                "Fields:",
            ]
        )
        lines.extend(f"- {field_name}" for field_name in fields)
        lines.extend(
            [
                "",
                "Required fields:",
            ]
        )
        lines.extend(f"- {field_name}" for field_name in required_fields)
        lines.extend(
            [
                "",
                "Field details:",
                "",
            ]
        )
        for field_name, field_spec in request["analysis_schema"].items():
            examples = field_spec.get("examples", [])
            lines.extend(
                [
                    f"#### {field_name}",
                    "",
                    f"- type: {field_spec.get('type')}",
                    f"- required: {field_spec.get('required')}",
                    f"- description: {field_spec.get('description')}",
                    "- examples:",
                ]
            )
            lines.extend(f"  - {example}" for example in examples)
            lines.extend(
                [
                    f"- inference_rule: {field_spec.get('inference_rule')}",
                    "- value:",
                    "  - explicit: []",
                    "  - inferred: []",
                    "",
                ]
            )

    lines.extend(
        [
            "## Contract Rules",
            "",
        ]
    )
    lines.extend(f"- {rule}" for rule in OUTPUT_RULES)
    lines.extend(
        [
            "",
            "## Next Step",
            "",
            "Send one request JSON to an AI agent and ask it to fill only the allowed value areas.",
            "Then validate the filled JSON with the validate command.",
            "",
        ]
    )
    return "\n".join(lines)


def build_run(
    text: str,
    output_dir: Path,
    run_name: str | None,
) -> dict[str, Any]:
    run_dir = unique_run_dir(output_dir, run_name)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    outputs_dir = run_dir / "outputs"
    data_dir.mkdir()
    outputs_dir.mkdir()

    input_path = data_dir / "input.txt"
    input_path.write_text(text + "\n", encoding="utf-8")

    created_at = datetime.now().isoformat(timespec="seconds")
    data_files: list[str] = [str(input_path)]
    output_files: list[str] = []
    request_docs: list[dict[str, Any]] = []

    request = build_request(text, created_at=created_at)
    path = data_dir / "user_input_analysis_request.json"
    write_json(path, request)
    data_files.append(str(path))
    request_docs.append(request)

    report_path = outputs_dir / "run_report.md"
    report_path.write_text(build_run_report(text, request_docs), encoding="utf-8")
    output_files.append(str(report_path))

    manifest_path = outputs_dir / "run_manifest.json"
    output_files.append(str(manifest_path))

    summary = {
        "run_dir": str(run_dir),
        "data_dir": str(data_dir),
        "outputs_dir": str(outputs_dir),
        "profile": SCHEMA_PROFILE_ID,
        "data_files": data_files,
        "output_files": output_files,
        "manifest_file": str(manifest_path),
        "report_file": str(report_path),
        "report_summary": {
            request["profile"]["name"]: {
                "description": request["profile"]["description"],
                "fields": list(request["analysis_schema"].keys()),
                "required_fields": [
                    field_name
                    for field_name, field_spec in request["analysis_schema"].items()
                    if field_spec.get("required")
                ],
                "field_details": {
                    field_name: {
                        "type": field_spec.get("type"),
                        "required": field_spec.get("required"),
                        "description": field_spec.get("description"),
                        "examples": field_spec.get("examples"),
                        "inference_rule": field_spec.get("inference_rule"),
                        "value": field_spec.get("value"),
                    }
                    for field_name, field_spec in request["analysis_schema"].items()
                },
            }
            for request in request_docs
        },
    }
    write_json(manifest_path, summary)
    return summary


def is_allowed_mutation_path(path: tuple[Any, ...]) -> bool:
    if len(path) >= 3 and path[0] == "analysis_schema" and path[2] == "value":
        return True
    if len(path) >= 2 and path[:2] == ("unresolved_fields", "items"):
        return True
    if len(path) >= 2 and path[:2] == ("schema_extension_suggestions", "items"):
        return True
    return False


def path_to_string(path: tuple[Any, ...]) -> str:
    if not path:
        return "<root>"
    parts: list[str] = []
    for part in path:
        if isinstance(part, int):
            parts.append(f"[{part}]")
        else:
            if parts:
                parts.append(".")
            parts.append(str(part))
    return "".join(parts)


def violation(code: str, path: tuple[Any, ...], message: str, *, severity: str = "fail") -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "path": path_to_string(path),
        "message": message,
    }


def compare_immutable(
    request_data: Any,
    filled_data: Any,
    path: tuple[Any, ...],
    violations: list[dict[str, str]],
) -> None:
    if is_allowed_mutation_path(path):
        return

    if type(request_data) is not type(filled_data):
        violations.append(
            violation(
                "IMMUTABLE_TYPE_CHANGED",
                path,
                f"Expected {type(request_data).__name__}, got {type(filled_data).__name__}.",
            )
        )
        return

    if isinstance(request_data, dict):
        request_keys = set(request_data)
        filled_keys = set(filled_data)

        for key in sorted(request_keys - filled_keys):
            violations.append(
                violation("REQUIRED_FIELD_MISSING", path + (key,), "Required contract field is missing.")
            )
        for key in sorted(filled_keys - request_keys):
            violations.append(
                violation("UNAUTHORIZED_FIELD_ADDED", path + (key,), "Extra field is not allowed.")
            )
        for key in sorted(request_keys & filled_keys):
            compare_immutable(request_data[key], filled_data[key], path + (key,), violations)
        return

    if isinstance(request_data, list):
        if len(request_data) != len(filled_data):
            violations.append(
                violation("IMMUTABLE_LIST_CHANGED", path, "Immutable list length was changed.")
            )
            return
        for index, (request_item, filled_item) in enumerate(zip(request_data, filled_data)):
            compare_immutable(request_item, filled_item, path + (index,), violations)
        return

    if request_data != filled_data:
        violations.append(
            violation("IMMUTABLE_VALUE_MODIFIED", path, "AI modified an immutable contract value.")
        )


def has_value_content(value: dict[str, Any]) -> bool:
    explicit = value.get("explicit", [])
    inferred = value.get("inferred", [])
    if explicit:
        return True
    for item in inferred:
        if isinstance(item, dict) and str(item.get("content", "")).strip():
            return True
    return False


def is_question_like(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if "?" in normalized or "？" in normalized:
        return True

    korean_markers = (
        "무엇",
        "뭐",
        "어떤",
        "어떻게",
        "왜",
        "언제",
        "어디",
        "누가",
        "얼마",
        "인가요",
        "일까요",
        "나요",
        "까요",
        "습니까",
        "합니까",
    )
    english_markers = (
        "what",
        "which",
        "how",
        "why",
        "when",
        "where",
        "who",
        "do ",
        "does ",
        "can ",
        "could ",
        "should ",
    )
    return any(marker in normalized for marker in korean_markers + english_markers)


def validate_next_best_question_semantics(
    field_name: str,
    value: dict[str, Any],
    violations: list[dict[str, str]],
) -> None:
    if field_name != "next_best_question":
        return

    for index, item in enumerate(value.get("explicit", [])):
        if isinstance(item, str) and not is_question_like(item):
            violations.append(
                violation(
                    "NEXT_BEST_QUESTION_NOT_QUESTION",
                    ("analysis_schema", field_name, "value", "explicit", index),
                    "next_best_question explicit content must be phrased as a question.",
                )
            )

    for index, item in enumerate(value.get("inferred", [])):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str) and not is_question_like(content):
            violations.append(
                violation(
                    "NEXT_BEST_QUESTION_NOT_QUESTION",
                    ("analysis_schema", field_name, "value", "inferred", index, "content"),
                    "next_best_question inferred content must be phrased as a question.",
                )
            )


def unresolved_reason_map(filled_data: dict[str, Any], violations: list[dict[str, str]]) -> dict[str, str]:
    unresolved = filled_data.get("unresolved_fields", {})
    items = unresolved.get("items")
    path = ("unresolved_fields", "items")

    if not isinstance(items, list):
        violations.append(violation("UNRESOLVED_ITEMS_INVALID", path, "unresolved_fields.items must be a list."))
        return {}

    result: dict[str, str] = {}
    known_fields = set(filled_data.get("analysis_schema", {}))
    for index, item in enumerate(items):
        item_path = path + (index,)
        if not isinstance(item, dict):
            violations.append(violation("UNRESOLVED_ITEM_INVALID", item_path, "Each unresolved item must be an object."))
            continue

        extra_keys = set(item) - {"field_name", "field", "reason"}
        for key in sorted(extra_keys):
            violations.append(
                violation("UNAUTHORIZED_FIELD_ADDED", item_path + (key,), "Extra unresolved item key is not allowed.")
            )

        field_name = item.get("field_name", item.get("field"))
        reason = item.get("reason")
        if not isinstance(field_name, str) or not field_name.strip():
            violations.append(violation("UNRESOLVED_FIELD_NAME_MISSING", item_path, "field_name is required."))
            continue
        if field_name not in known_fields:
            violations.append(violation("UNRESOLVED_UNKNOWN_FIELD", item_path, "Unknown field was marked unresolved."))
            continue
        if not isinstance(reason, str) or not reason.strip():
            violations.append(violation("UNRESOLVED_REASON_MISSING", item_path, "reason is required."))
            continue
        result[field_name] = reason
    return result


def build_clarification_items(
    filled_data: dict[str, Any],
    unresolved: dict[str, str],
) -> list[dict[str, Any]]:
    analysis_schema = filled_data.get("analysis_schema", {})
    if not isinstance(analysis_schema, dict):
        return []

    items: list[dict[str, Any]] = []
    for field_name, reason in unresolved.items():
        field_spec = analysis_schema.get(field_name, {})
        items.append(
            {
                "field_name": field_name,
                "required": bool(field_spec.get("required")) if isinstance(field_spec, dict) else False,
                "reason": reason,
                "suggested_question": CLARIFICATION_QUESTIONS.get(
                    field_name,
                    f"{field_name} 값을 명확히 채우기 위해 어떤 정보를 확인해야 하나요?",
                ),
            }
        )
    return items


def validate_value_shape(
    field_name: str,
    field_spec: dict[str, Any],
    unresolved: dict[str, str],
    violations: list[dict[str, str]],
) -> None:
    path = ("analysis_schema", field_name, "value")
    value = field_spec.get("value")
    if not isinstance(value, dict):
        violations.append(violation("VALUE_SHAPE_INVALID", path, "value must be an object."))
        return

    expected_keys = {"explicit", "inferred"}
    actual_keys = set(value)
    for key in sorted(expected_keys - actual_keys):
        violations.append(violation("VALUE_KEY_MISSING", path + (key,), "value key is missing."))
    for key in sorted(actual_keys - expected_keys):
        violations.append(violation("UNAUTHORIZED_FIELD_ADDED", path + (key,), "Extra value key is not allowed."))

    explicit = value.get("explicit")
    inferred = value.get("inferred")
    if not isinstance(explicit, list):
        violations.append(violation("EXPLICIT_VALUE_INVALID", path + ("explicit",), "explicit must be a list."))
    if not isinstance(inferred, list):
        violations.append(violation("INFERRED_VALUE_INVALID", path + ("inferred",), "inferred must be a list."))
        inferred = []

    for index, item in enumerate(inferred):
        item_path = path + ("inferred", index)
        if not isinstance(item, dict):
            violations.append(violation("INFERRED_ITEM_INVALID", item_path, "Each inferred item must be an object."))
            continue

        expected_item_keys = {"content", "confidence", "evidence"}
        item_keys = set(item)
        for key in sorted(expected_item_keys - item_keys):
            violations.append(violation("INFERRED_ITEM_KEY_MISSING", item_path + (key,), "Required inferred key is missing."))
        for key in sorted(item_keys - expected_item_keys):
            violations.append(violation("UNAUTHORIZED_FIELD_ADDED", item_path + (key,), "Extra inferred item key is not allowed."))

        if not str(item.get("content", "")).strip():
            violations.append(violation("INFERRED_CONTENT_EMPTY", item_path + ("content",), "content must not be empty."))

        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            violations.append(violation("CONFIDENCE_INVALID", item_path + ("confidence",), "confidence must be a number."))
        elif confidence < 0 or confidence > 1:
            violations.append(violation("CONFIDENCE_OUT_OF_RANGE", item_path + ("confidence",), "confidence must be between 0 and 1."))

        if not str(item.get("evidence", "")).strip():
            violations.append(violation("EVIDENCE_MISSING", item_path + ("evidence",), "evidence is required for inferred values."))

    if field_spec.get("required") and not has_value_content(value):
        if field_name in unresolved:
            violations.append(
                violation(
                    "REQUIRED_VALUE_UNRESOLVED",
                    path,
                    "Required field was left empty with an unresolved reason.",
                    severity="warn",
                )
            )
        else:
            violations.append(
                violation("REQUIRED_VALUE_EMPTY", path, "Required field is empty and no unresolved reason was provided.")
            )
    elif field_name in unresolved and has_value_content(value):
        violations.append(
            violation(
                "UNRESOLVED_FIELD_HAS_VALUE",
                path,
                "Field has a value but is also listed as unresolved.",
                severity="warn",
            )
        )

    validate_next_best_question_semantics(field_name, value, violations)


def validate_extension_suggestions(filled_data: dict[str, Any], violations: list[dict[str, str]]) -> None:
    suggestions = filled_data.get("schema_extension_suggestions", {})
    items = suggestions.get("items")
    path = ("schema_extension_suggestions", "items")
    if not isinstance(items, list):
        violations.append(violation("EXTENSION_ITEMS_INVALID", path, "schema_extension_suggestions.items must be a list."))
        return

    allowed_keys = {"field_name", "suggested_type", "reason", "example_value", "promotion_status"}
    required_keys = {"field_name", "suggested_type", "reason", "promotion_status"}
    for index, item in enumerate(items):
        item_path = path + (index,)
        if not isinstance(item, dict):
            violations.append(violation("EXTENSION_ITEM_INVALID", item_path, "Each extension suggestion must be an object."))
            continue
        for key in sorted(required_keys - set(item)):
            violations.append(violation("EXTENSION_ITEM_KEY_MISSING", item_path + (key,), "Required extension key is missing."))
        for key in sorted(set(item) - allowed_keys):
            violations.append(violation("UNAUTHORIZED_FIELD_ADDED", item_path + (key,), "Extra extension key is not allowed."))
        for key in ("field_name", "suggested_type", "reason", "promotion_status"):
            if key in item and (not isinstance(item[key], str) or not item[key].strip()):
                violations.append(violation("EXTENSION_ITEM_VALUE_EMPTY", item_path + (key,), f"{key} must be a non-empty string."))


def validate_filled_request(request_data: dict[str, Any], filled_data: dict[str, Any]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    clarification_items: list[dict[str, Any]] = []

    compare_immutable(request_data, filled_data, tuple(), violations)

    if not isinstance(filled_data.get("analysis_schema"), dict):
        violations.append(violation("ANALYSIS_SCHEMA_INVALID", ("analysis_schema",), "analysis_schema must be an object."))
    else:
        unresolved = unresolved_reason_map(filled_data, violations)
        clarification_items = build_clarification_items(filled_data, unresolved)
        for field_name, field_spec in filled_data["analysis_schema"].items():
            if isinstance(field_spec, dict):
                validate_value_shape(field_name, field_spec, unresolved, violations)
            else:
                violations.append(
                    violation("FIELD_SPEC_INVALID", ("analysis_schema", field_name), "Field spec must be an object.")
                )

    validate_extension_suggestions(filled_data, violations)

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
        "clarification_required": bool(clarification_items),
        "clarification_items": clarification_items,
        "user_message": (
            "명확히 채울 수 없는 필드가 있습니다. clarification_items의 suggested_question을 사용자에게 확인하세요."
            if clarification_items
            else ""
        ),
    }


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def command_build(args: argparse.Namespace) -> int:
    if args.input_file and args.text:
        raise SystemExit("Use either --text or --input-file, not both.")
    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text.strip()
    else:
        raise SystemExit("Provide --text or --input-file.")

    if not text:
        raise SystemExit("Input text must not be empty.")

    summary = build_run(
        text=text,
        output_dir=Path(args.output),
        run_name=args.run_name,
    )
    safe_print(to_json(summary))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    try:
        request_data = load_json_file(Path(args.request))
        filled_data = load_json_file(Path(args.filled))
    except json.JSONDecodeError as exc:
        report = {
            "valid": False,
            "severity": "fail",
            "violations": [
                {
                    "code": "JSON_PARSE_ERROR",
                    "severity": "fail",
                    "path": "<file>",
                    "message": str(exc),
                }
            ],
            "summary": {
                "fail_count": 1,
                "warn_count": 0,
            },
        }
        safe_print(to_json(report))
        return 1

    if not isinstance(request_data, dict) or not isinstance(filled_data, dict):
        report = {
            "valid": False,
            "severity": "fail",
            "violations": [
                {
                    "code": "JSON_ROOT_INVALID",
                    "severity": "fail",
                    "path": "<root>",
                    "message": "Both request and filled files must contain JSON objects.",
                }
            ],
            "summary": {
                "fail_count": 1,
                "warn_count": 0,
            },
        }
    else:
        report = validate_filled_request(request_data, filled_data)

    safe_print(to_json(report))
    return 0 if report["valid"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an AI-fillable user input analysis request JSON file."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Create a user input analysis request JSON file.")
    build_parser.add_argument("--text", help="User input text to place into the schema request.")
    build_parser.add_argument("--input-file", help="Read user input text from a UTF-8 file.")
    build_parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs" / "runs"),
        help="Base directory where run folders will be created.",
    )
    build_parser.add_argument(
        "--run-name",
        help="Optional run folder name. Defaults to a timestamp.",
    )
    build_parser.set_defaults(func=command_build)

    validate_parser = subparsers.add_parser("validate", help="Validate an AI-filled JSON against a request JSON.")
    validate_parser.add_argument("--request", required=True, help="Original schema request JSON.")
    validate_parser.add_argument("--filled", required=True, help="AI-filled schema JSON.")
    validate_parser.set_defaults(func=command_validate)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
