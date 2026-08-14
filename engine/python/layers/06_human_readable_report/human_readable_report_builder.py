from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPORT_VERSION = "0.3.0"

SCRIPT_DIR = Path(__file__).resolve().parent


def find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "agents" / "agent.md").exists() and (
            candidate / "engine" / "python" / "workflow"
        ).exists():
            return candidate
    return start


PROJECT_ROOT = find_project_root(SCRIPT_DIR)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared import artifacts as artifact_store


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(data) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, dict):
            content = as_text(item.get("content") or item.get("reason") or item.get("evidence"))
        else:
            content = as_text(item)
        if content:
            items.append(content)
    return items


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def is_placeholder(data: Any) -> bool:
    return bool(isinstance(data, dict) and data.get("workflow_placeholder"))


def load_optional_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = load_json(path)
    except Exception:
        return {}
    if not isinstance(data, dict) or is_placeholder(data):
        return {}
    return data


def workflow_path(manifest: dict[str, Any], key: str) -> Path | None:
    raw_path = manifest.get("paths", {}).get(key)
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def validation_is_valid(report: dict[str, Any]) -> bool:
    return bool(report.get("valid") is True)


def validation_state(report: dict[str, Any]) -> str:
    if not report:
        return "missing"
    if validation_is_valid(report):
        return "valid"
    return as_text(report.get("severity")) or "invalid"


def validation_codes(report: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for item in report.get("violations", []):
        if isinstance(item, dict) and item.get("code"):
            codes.append(str(item["code"]))
    return codes


def facet_value(router_filled: dict[str, Any], name: str) -> str:
    facet = router_filled.get("facet_classification", {}).get(name, {})
    if isinstance(facet, dict):
        return as_text(facet.get("value"))
    return as_text(facet)


def extract_analysis_values(input_analysis: dict[str, Any], field_name: str) -> dict[str, list[str]]:
    field = input_analysis.get("analysis_schema", {}).get(field_name, {})
    value = field.get("value", {}) if isinstance(field, dict) else {}
    if not isinstance(value, dict):
        return {"explicit": [], "inferred": []}
    return {
        "explicit": text_items(value.get("explicit", [])),
        "inferred": text_items(value.get("inferred", [])),
    }


def missing_from_input_analysis(input_analysis: dict[str, Any]) -> list[str]:
    if not input_analysis:
        return ["input analysis filled JSON is not available"]
    items = input_analysis.get("unresolved_fields", {}).get("items", [])
    missing: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                field_name = as_text(item.get("field_name"))
                reason = as_text(item.get("reason"))
                if field_name and reason:
                    missing.append(f"{field_name}: {reason}")
                elif field_name:
                    missing.append(field_name)
    return missing


def build_quality_gate(
    *,
    workflow_state: str,
    route_validation: dict[str, Any],
    direction_validation: dict[str, Any],
    context_validation: dict[str, Any],
    fulfillment_configured: bool,
    fulfillment_validation: dict[str, Any],
    missing_context: list[str],
) -> dict[str, Any]:
    validations_ready = (
        validation_is_valid(route_validation)
        and validation_is_valid(direction_validation)
        and validation_is_valid(context_validation)
    )
    if not validations_ready:
        return {
            "decision": "진행 보류",
            "can_handoff": False,
            "reason": "필수 검증이 아직 통과되지 않았거나 검증 결과가 없습니다.",
        }
    if workflow_state == "continuation_waiting_user":
        return {
            "decision": "사용자 검토 대기",
            "can_handoff": True,
            "request_completed": False,
            "reason": "후속 산출물은 준비되었지만 사용자의 선택 또는 승인이 남아 있습니다.",
        }
    if workflow_state in {
        "continuation_in_progress",
        "continuation_approved",
        "continuation_deployment_ready",
        "continuation_deployed",
    }:
        return {
            "decision": "후속 작업 진행 중",
            "can_handoff": True,
            "request_completed": False,
            "reason": "후속 작업이 진행 중이며 완료 조건이 아직 확정되지 않았습니다.",
        }
    if workflow_state == "continuation_completed":
        return {
            "decision": "후속 요청 완료",
            "can_handoff": True,
            "request_completed": True,
            "reason": "후속 작업의 완료 조건이 충족되었고 상태 기록이 확정되었습니다.",
        }
    if fulfillment_configured:
        if validation_is_valid(fulfillment_validation) and workflow_state == "request_completed":
            return {
                "decision": "요청 완료",
                "can_handoff": True,
                "request_completed": True,
                "reason": "원래 요청한 결과물과 등록된 근거가 이행 계약을 통과했습니다.",
            }
        return {
            "decision": "산출물 검증 대기",
            "can_handoff": True,
            "request_completed": False,
            "reason": "입력 분석은 통과했지만 원래 요청한 결과물의 생성 또는 이행 검증이 남아 있습니다.",
        }
    if missing_context:
        return {
            "decision": "조건부 진행 가능",
            "can_handoff": True,
            "reason": "검증은 통과했지만 보강하면 판단 품질이 올라가는 정보가 남아 있습니다.",
        }
    if workflow_state == "ready_for_next_action":
        return {
            "decision": "분석 완료(레거시)",
            "can_handoff": True,
            "request_completed": False,
            "reason": "분석 단계는 통과했지만 이 실행에는 산출물 이행 계약이 없어 완료를 증명하지 않습니다.",
        }
    return {
        "decision": "조건부 진행 가능",
        "can_handoff": True,
        "reason": "필수 검증은 통과했지만 워크플로우 상태 확인이 필요합니다.",
    }


def build_report_summary(run_dir: Path, status: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = load_json(run_dir / "workflow_manifest.json")
    status_data = status or load_optional_json(run_dir / "workflow_status.json")
    workflow_state = as_text(status_data.get("workflow_state")) or "unknown"
    artifact_status = artifact_store.inspect_artifacts(run_dir)
    continuation_status = status_data.get("continuation_status")
    if not isinstance(continuation_status, dict):
        continuation_status = {}
    continuation_state = continuation_status.get("state")
    if not isinstance(continuation_state, dict):
        continuation_state = {}
    continuation_projection = status_data.get("continuation_projection")
    if not isinstance(continuation_projection, dict):
        continuation_projection = {}

    input_analysis = load_optional_json(workflow_path(manifest, "input_filled"))
    router_filled = load_optional_json(workflow_path(manifest, "router_filled"))
    route_validation = load_optional_json(workflow_path(manifest, "route_validation"))
    direction_filled = load_optional_json(workflow_path(manifest, "direction_filled"))
    direction_validation = load_optional_json(run_dir / "04_direction_lens" / "data" / "direction_lens_validation.json")
    context_filled = load_optional_json(workflow_path(manifest, "context_filled"))
    context_validation = load_optional_json(workflow_path(manifest, "context_validation"))
    fulfillment_configured = "fulfillment_contract" in manifest.get("paths", {})
    fulfillment_contract = load_optional_json(workflow_path(manifest, "fulfillment_contract"))
    fulfillment_evidence = load_optional_json(workflow_path(manifest, "fulfillment_evidence"))
    fulfillment_validation = load_optional_json(workflow_path(manifest, "fulfillment_validation"))

    supplemental_inputs = [
        item
        for item in manifest.get("supplemental_inputs", [])
        if isinstance(item, dict) and as_text(item.get("text"))
    ]
    project_deliverables = []
    for item in manifest.get("deliverable_paths", []):
        if not isinstance(item, dict):
            continue
        deliverable = dict(item)
        absolute_path = as_text(deliverable.get("path_absolute"))
        deliverable["exists"] = bool(absolute_path and Path(absolute_path).exists())
        project_deliverables.append(deliverable)


    source = manifest.get("source", {})
    raw_text = as_text(source.get("raw_text"))
    source_files = text_items(source.get("source_files", []))

    context_map = context_filled.get("situation_context_map", {})
    if not isinstance(context_map, dict):
        context_map = {}
    actor_scope = context_map.get("actor_scope", {})
    if not isinstance(actor_scope, dict):
        actor_scope = {}
    self_check = actor_scope.get("classification_self_check", {})
    if not isinstance(self_check, dict):
        self_check = {}

    route_decision = router_filled.get("route_decision", {})
    if not isinstance(route_decision, dict):
        route_decision = {}
    direction_decision = direction_filled.get("direction_decision", {})
    if not isinstance(direction_decision, dict):
        direction_decision = {}
    domain_next_action = context_filled.get("next_action") or direction_filled.get("next_action") or {}
    if not isinstance(domain_next_action, dict):
        domain_next_action = {}
    workflow_next_action = status_data.get("next_action")
    if not isinstance(workflow_next_action, dict):
        workflow_next_action = {}
    if status_data.get("current_stage") == "continuation":
        next_action = {
            "action_type": as_text(workflow_next_action.get("type")),
            "target": as_text(workflow_next_action.get("selected_action")),
            "reason": as_text(workflow_next_action.get("reason")),
        }
    else:
        next_action = domain_next_action

    explicit_facts = dedupe(
        extract_analysis_values(input_analysis, "explicit_facts")["explicit"]
        + extract_analysis_values(input_analysis, "core_topic")["explicit"]
        + [raw_text if raw_text else ""]
    )
    inferred_facts = dedupe(
        extract_analysis_values(input_analysis, "inferred_assumptions")["inferred"]
        + extract_analysis_values(input_analysis, "core_topic")["inferred"]
        + text_items(context_map.get("evidence_basis", []))
    )

    missing_context = dedupe(
        missing_from_input_analysis(input_analysis)
        + text_items(router_filled.get("missing_decision_basis", []))
        + text_items(direction_filled.get("missing_basis", []))
        + text_items(context_map.get("missing_context", []))
        + text_items(actor_scope.get("missing_context", []))
    )

    could_be_wrong_if = text_items(self_check.get("could_be_wrong_if", []))
    competing_classifications = text_items(self_check.get("competing_classifications", []))
    minimum_evidence_used = text_items(self_check.get("minimum_evidence_used", []))

    classification = {
        "primary_actor": as_text(actor_scope.get("primary_actor")) or "unknown",
        "request_context": as_text(actor_scope.get("request_context")) or "unknown",
        "decision_owner": as_text(actor_scope.get("decision_owner")) or "unknown",
        "affected_parties": text_items(actor_scope.get("affected_parties", [])),
        "confidence": actor_scope.get("confidence"),
        "evidence": as_text(actor_scope.get("evidence")),
        "minimum_evidence_used": minimum_evidence_used,
        "competing_classifications": competing_classifications,
        "why_not_other_actor_scope": as_text(self_check.get("why_not_other_actor_scope")),
        "could_be_wrong_if": could_be_wrong_if,
    }

    exploration_path = context_map.get("experimental_context_exploration_path", {})
    if not isinstance(exploration_path, dict):
        exploration_path = {}
    stage_1 = exploration_path.get("stage_1_start_area", {})
    stage_2 = exploration_path.get("stage_2_subcategory", {})
    stage_3 = exploration_path.get("stage_3_context_object", {})
    stage_4 = exploration_path.get("stage_4_primary_structure", {})
    stage_5 = exploration_path.get("stage_5_detail_expansion", {})
    if not isinstance(stage_1, dict):
        stage_1 = {}
    if not isinstance(stage_2, dict):
        stage_2 = {}
    if not isinstance(stage_3, dict):
        stage_3 = {}
    if not isinstance(stage_4, dict):
        stage_4 = {}
    if not isinstance(stage_5, dict):
        stage_5 = {}

    route = {
        "selected_route": as_text(route_decision.get("selected_route")),
        "route_status": as_text(route_decision.get("route_status")),
        "route_confidence": route_decision.get("route_confidence"),
        "reason": as_text(route_decision.get("reason")),
        "evidence": as_text(route_decision.get("evidence")),
        "facets": {
            "domain_context": facet_value(router_filled, "domain_context"),
            "problem_object": facet_value(router_filled, "problem_object"),
            "user_intent": facet_value(router_filled, "user_intent"),
            "definition_level": facet_value(router_filled, "definition_level"),
            "risk_level": facet_value(router_filled, "risk_level"),
            "needed_output": facet_value(router_filled, "needed_output"),
            "source_type": facet_value(router_filled, "source_type"),
            "urgency_level": facet_value(router_filled, "urgency_level"),
        },
    }

    validations = {
        "route_validation": {
            "state": validation_state(route_validation),
            "codes": validation_codes(route_validation),
        },
        "direction_validation": {
            "state": validation_state(direction_validation),
            "codes": validation_codes(direction_validation),
        },
        "context_validation": {
            "state": validation_state(context_validation),
            "codes": validation_codes(context_validation),
        },
        "fulfillment_validation": {
            "state": validation_state(fulfillment_validation) if fulfillment_configured else "not_configured",
            "codes": validation_codes(fulfillment_validation),
        },
    }

    quality_gate = build_quality_gate(
        workflow_state=workflow_state,
        route_validation=route_validation,
        direction_validation=direction_validation,
        context_validation=context_validation,
        fulfillment_configured=fulfillment_configured,
        fulfillment_validation=fulfillment_validation,
        missing_context=missing_context,
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "workflow_state": workflow_state,
        "quality_gate": quality_gate,
        "source": {
            "raw_text": raw_text,
            "source_files": source_files,
            "supplemental_inputs": supplemental_inputs,
        },
        "classification": classification,
        "route": route,
        "direction": {
            "selected_lenses": text_items(direction_filled.get("selected_lenses", [])),
            "direction_status": as_text(direction_decision.get("direction_status")),
            "problem_direction": as_text(direction_decision.get("problem_direction")),
            "why_this_direction": as_text(direction_decision.get("why_this_direction")),
            "evidence": as_text(direction_decision.get("evidence")),
        },
        "context_map": {
            "central_problem": as_text(context_map.get("central_problem")),
            "domain_area": as_text(context_map.get("domain_area")),
            "situation_context": as_text(context_map.get("situation_context")),
            "actor_context": as_text(context_map.get("actor_context")),
            "problem_type": as_text(context_map.get("problem_type")),
            "task_object": as_text(context_map.get("task_object")),
            "situation_phase": as_text(context_map.get("situation_phase")),
            "required_context": text_items(context_map.get("required_context", [])),
            "missing_context": missing_context,
            "recommended_next_focus": text_items(context_map.get("recommended_next_focus", [])),
            "confidence": context_map.get("confidence"),
        },
        "exploration_path": {
            "enabled": exploration_path.get("enabled"),
            "mode": as_text(exploration_path.get("mode")),
            "stage_path": [
                as_text(stage_1.get("selected")),
                as_text(stage_2.get("selected")),
                as_text(stage_3.get("selected")),
            ],
            "activated_axes": text_items(stage_4.get("activated_axes", [])),
            "usage_direction": as_text(stage_5.get("usage_direction")),
        },
        "evidence": {
            "explicit": explicit_facts,
            "inferred": inferred_facts,
            "minimum_evidence_used": minimum_evidence_used,
            "route_evidence": as_text(route_decision.get("evidence")),
            "direction_evidence": as_text(direction_decision.get("evidence")),
        },
        "uncertainty": {
            "missing_context": missing_context,
            "competing_classifications": competing_classifications,
            "could_be_wrong_if": could_be_wrong_if,
        },
        "next_action": {
            "action_type": as_text(next_action.get("action_type")),
            "target": as_text(next_action.get("target")),
            "reason": as_text(next_action.get("reason")),
        },
        "continuation": {
            "exists": continuation_status.get("exists"),
            "valid": continuation_status.get("valid"),
            "current_phase": as_text(continuation_state.get("current_phase")),
            "completion_gate": as_text(
                (continuation_state.get("completion_policy") or {}).get("gate")
                if isinstance(continuation_state.get("completion_policy"), dict)
                else None
            ),
            "workspace": continuation_state.get("workspace_context", {}),
            "timing": continuation_projection.get("timing", continuation_state.get("timing", {})),
            "selected_candidate": (
                (continuation_state.get("candidate_set") or {}).get("selected_candidate")
                if isinstance(continuation_state.get("candidate_set"), dict)
                else None
            ),
            "state_file": continuation_status.get("state_file"),
        },
        "fulfillment": {
            "configured": fulfillment_configured,
            "contract_status": as_text(fulfillment_contract.get("contract_status")),
            "requested_output": fulfillment_contract.get("requested_output", {}),
            "acceptance_criteria": fulfillment_contract.get("acceptance_criteria", []),
            "evidence": fulfillment_evidence,
            "validation": fulfillment_validation,
        },
        "project_deliverables": project_deliverables,
        "artifacts": artifact_status,
        "validations": validations,
    }


def bullet_list(items: list[str], *, empty: str = "정보 부족") -> list[str]:
    if not items:
        return [f"- {empty}"]
    return [f"- {item}" for item in items]


def kv(label: str, value: Any, *, empty: str = "정보 부족") -> str:
    text = as_text(value)
    return f"- {label}: `{text or empty}`"


def artifact_lines(artifacts: dict[str, Any]) -> list[str]:
    items = artifacts.get("artifacts", [])
    if not isinstance(items, list) or not items:
        return ["- 등록된 산출물 없음"]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        artifact_id = as_text(item.get("id")) or "unknown"
        artifact_type = as_text(item.get("type")) or "unknown"
        role = as_text(item.get("role")) or "unknown"
        status = as_text(item.get("effective_status") or item.get("status")) or "unknown"
        path = as_text(item.get("path")) or "정보 부족"
        storage_mode = as_text(item.get("storage_mode")) or (
            "milestone_snapshot" if item.get("bound_to_run") else "project_reference"
        )
        deployment_status = as_text(item.get("deployment_status")) or "not_requested"
        lines.append(
            f"- `{artifact_id}` ({artifact_type}/{role}/{status}/{storage_mode}, deployment={deployment_status}): `{path}`"
        )
        if item.get("duplicate_content") is True:
            lines.append(f"  - 동일 콘텐츠: `{as_text(item.get('duplicate_of'))}`와 같은 파일 내용")
        working_source = as_text(item.get("working_source"))
        official_artifact = as_text(item.get("official_artifact"))
        deployment_target = as_text(item.get("deployment_target"))
        if working_source:
            lines.append(f"  - working source: `{working_source}`")
        if official_artifact:
            lines.append(f"  - official snapshot: `{official_artifact}`")
        if deployment_target:
            lines.append(f"  - deployment target: `{deployment_target}`")
    return lines or ["- 등록된 산출물 없음"]



def project_deliverable_lines(items: Any) -> list[str]:
    if not isinstance(items, list) or not items:
        return ["- 등록된 프로젝트 산출물 없음"]
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = as_text(item.get("path_relative") or item.get("path_absolute")) or "정보 부족"
        role = as_text(item.get("role")) or "project_deliverable"
        exists = item.get("exists") is True
        lines.append(f"- `{path}` ({role}, exists={str(exists).lower()})")
    return lines or ["- 등록된 프로젝트 산출물 없음"]


def build_markdown_report(summary: dict[str, Any]) -> str:
    gate = summary["quality_gate"]
    classification = summary["classification"]
    route = summary["route"]
    direction = summary["direction"]
    context_map = summary["context_map"]
    exploration = summary["exploration_path"]
    evidence = summary["evidence"]
    uncertainty = summary["uncertainty"]
    next_action = summary["next_action"]
    continuation = summary.get("continuation", {})
    artifacts = summary.get("artifacts", {})
    fulfillment = summary.get("fulfillment", {})
    requested_output = fulfillment.get("requested_output", {})
    if not isinstance(requested_output, dict):
        requested_output = {}
    project_deliverables = summary.get("project_deliverables", [])
    validations = summary["validations"]

    stage_path = " > ".join([item for item in exploration.get("stage_path", []) if item]) or "정보 부족"

    lines = [
        "# 입력 판단 보고서",
        "",
        "## 1. 판정 요약",
        "",
        f"현재 보고서 판정은 **{gate['decision']}**입니다.",
        gate["reason"],
        "",
        kv("워크플로우 상태", summary.get("workflow_state")),
        kv("원문 입력", summary.get("source", {}).get("raw_text")),
        kv("우선 방향", route.get("selected_route") or next_action.get("action_type")),
        kv("다음 대상", next_action.get("target") or route.get("facets", {}).get("needed_output")),
        kv("요청 이행 상태", fulfillment.get("contract_status") or ("미적용" if not fulfillment.get("configured") else "대기")),
        "",
        "## 2. 구조화 결과",
        "",
        kv("주체 분류", classification.get("primary_actor")),
        kv("요청 맥락", classification.get("request_context")),
        kv("결정 주체", classification.get("decision_owner")),
        kv("분류 신뢰도", classification.get("confidence")),
        kv("문제 경로", stage_path),
        kv("중심 문제", context_map.get("central_problem")),
        kv("도메인 영역", context_map.get("domain_area")),
        kv("문제 유형", context_map.get("problem_type")),
        kv("작업 대상", context_map.get("task_object")),
        kv("상황 단계", context_map.get("situation_phase")),
        "",
        "## 3. 판단 근거",
        "",
        "명시된 정보:",
        *bullet_list(evidence.get("explicit", [])),
        "",
        "AI가 추론한 정보:",
        *bullet_list(evidence.get("inferred", [])),
        "",
        "최소 판단 근거:",
        *bullet_list(evidence.get("minimum_evidence_used", [])),
        "",
        "## 4. 왜 다른 분류가 아닌가",
        "",
        classification.get("why_not_other_actor_scope") or "정보 부족",
        "",
        "경쟁 가능 분류:",
        *bullet_list(classification.get("competing_classifications", [])),
        "",
        "## 5. 불확실한 부분",
        "",
        *bullet_list(uncertainty.get("missing_context", []), empty="현재 기록된 부족 정보 없음"),
        "",
        "## 6. 잘못될 수 있는 조건",
        "",
        *bullet_list(uncertainty.get("could_be_wrong_if", [])),
        "",
        "## 7. 상황 맥락 지도",
        "",
        kv("탐색 모드", exploration.get("mode")),
        kv("1~3단계 경로", stage_path),
        "",
        "활성 구조 축:",
        *bullet_list(exploration.get("activated_axes", [])),
        "",
        kv("사용 방향", exploration.get("usage_direction")),
        "",
        "추천 다음 초점:",
        *bullet_list(context_map.get("recommended_next_focus", [])),
        "",
        "## 8. 다음 행동",
        "",
        kv("action_type", next_action.get("action_type")),
        kv("target", next_action.get("target")),
        kv("reason", next_action.get("reason")),
        "",
        "## 9. 연속 작업 상태",
        "",
        kv("state file", continuation.get("state_file")),
        kv("current phase", continuation.get("current_phase")),
        kv("completion gate", continuation.get("completion_gate")),
        kv("working root", (continuation.get("workspace") or {}).get("working_root")),
        kv("official run", (continuation.get("workspace") or {}).get("official_run_dir")),
        kv("deployment target", (continuation.get("workspace") or {}).get("deployment_target")),
        kv("deployment status", (continuation.get("workspace") or {}).get("deployment_status")),
        kv("agent work seconds", (continuation.get("timing") or {}).get("agent_work_seconds")),
        kv("user review seconds", (continuation.get("timing") or {}).get("user_review_seconds")),
        kv("deployment seconds", (continuation.get("timing") or {}).get("deployment_seconds")),
        "",
        "## 10. 산출물 바인딩",
        "",
        kv("artifact manifest", artifacts.get("manifest_file")),
        kv("assets root", artifacts.get("assets_root")),
        kv("registered count", artifacts.get("total_count")),
        kv("present count", artifacts.get("present_count")),
        kv("unique file count", artifacts.get("unique_present_count")),
        kv("duplicate content count", artifacts.get("duplicate_content_count")),
        kv("missing count", artifacts.get("missing_count")),
        "",
        "등록 산출물:",
        *artifact_lines(artifacts),
        "",
        "## 요청 결과 이행",
        "",
        kv("계약 적용", fulfillment.get("configured")),
        kv("계약 상태", fulfillment.get("contract_status")),
        kv("요청 결과", requested_output.get("description")),
        kv("결과 형식", requested_output.get("format")),
        kv("최소 수량", requested_output.get("minimum_count")),
        kv("이행 검증", (fulfillment.get("validation") or {}).get("severity")),
        "",
        "후속 입력 기록:",
        *bullet_list(
            [as_text(item.get("text")) for item in summary.get("source", {}).get("supplemental_inputs", [])],
            empty="추가 입력 없음",
        ),
        "",
        "프로젝트 산출물:",
        *project_deliverable_lines(project_deliverables),
        "",
        "## 11. 에이전트 인계 요약",
        "",
        "```json",
        to_json(
            {
                "quality_gate": gate,
                "classification": {
                    "primary_actor": classification.get("primary_actor"),
                    "request_context": classification.get("request_context"),
                    "confidence": classification.get("confidence"),
                },
                "route": {
                    "selected_route": route.get("selected_route"),
                    "route_confidence": route.get("route_confidence"),
                    "risk_level": route.get("facets", {}).get("risk_level"),
                },
                "context_map": {
                    "central_problem": context_map.get("central_problem"),
                    "problem_type": context_map.get("problem_type"),
                    "stage_path": exploration.get("stage_path"),
                    "missing_context": context_map.get("missing_context"),
                    "recommended_next_focus": context_map.get("recommended_next_focus"),
                },
                "next_action": next_action,
                "continuation": continuation,
                "fulfillment": fulfillment,
                "project_deliverables": project_deliverables,
                "artifacts": {
                    "manifest_file": artifacts.get("manifest_file"),
                    "registered_count": artifacts.get("total_count"),
                    "present_count": artifacts.get("present_count"),
                    "unique_present_count": artifacts.get("unique_present_count"),
                    "duplicate_content_count": artifacts.get("duplicate_content_count"),
                    "missing_count": artifacts.get("missing_count"),
                },
                "validations": validations,
            }
        ),
        "```",
        "",
    ]
    return "\n".join(lines)


def build_report_files(run_dir: Path, status: dict[str, Any] | None = None) -> dict[str, Any]:
    report_dir = run_dir / "06_human_readable_report"
    data_dir = report_dir / "data"
    reports_dir = report_dir / "reports"
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    summary = build_report_summary(run_dir, status=status)
    summary_path = data_dir / "report_summary.json"
    report_path = reports_dir / "human_readable_report.md"
    write_json(summary_path, summary)
    report_path.write_text(build_markdown_report(summary), encoding="utf-8")
    return {
        "report_version": REPORT_VERSION,
        "run_dir": str(run_dir),
        "report_file": str(report_path),
        "summary_file": str(summary_path),
        "quality_gate": summary["quality_gate"],
    }


def command_build(args: argparse.Namespace) -> int:
    manifest = build_report_files(Path(args.run_dir))
    print(to_json(manifest))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a human-readable workflow quality gate report.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build report files for a workflow run.")
    build_parser.add_argument("--run-dir", required=True, help="Workflow run directory.")
    build_parser.set_defaults(func=command_build)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
