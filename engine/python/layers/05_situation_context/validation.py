from __future__ import annotations

from typing import Any

from contract import (
    DETAIL_POLICIES,
    PRIMARY_STRUCTURE_AXES,
    REQUIRED_CONTEXT_FIELDS,
    SUBCATEGORY_SEEDS,
    VALID_NEXT_ACTION_TYPES,
    VALID_PRIMARY_ACTORS,
    VALID_REQUEST_CONTEXTS,
    VALID_SITUATION_PHASES,
)
from runner_support import as_text


def violation(code: str, severity: str, path: str, message: str) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "path": path,
        "message": message,
    }


def needs_missing_context(request: dict[str, Any], filled: dict[str, Any]) -> bool:
    source_router = request.get("source_router", {})
    route_decision = source_router.get("route_decision", {})
    route = as_text(route_decision.get("selected_route", ""))
    route_status = as_text(route_decision.get("route_status", ""))

    source_direction = request.get("source_direction", {})
    direction_decision = source_direction.get("direction_decision", {})
    direction_status = as_text(direction_decision.get("direction_status", ""))

    next_action = filled.get("next_action", {})
    action_type = as_text(next_action.get("action_type", ""))

    return (
        route == "ask_user"
        or route_status == "unresolved"
        or direction_status in {"needs_more_input", "unresolved"}
        or action_type == "ask_user"
    )


def validate_context_links(context_map: dict[str, Any]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    links = context_map.get("context_links", [])
    if not isinstance(links, list) or not links:
        return [
            violation(
                "CONTEXT_LINKS_REQUIRED",
                "fail",
                "situation_context_map.context_links",
                "context_links must contain at least one relationship.",
            )
        ]

    for index, link in enumerate(links):
        path = f"situation_context_map.context_links[{index}]"
        if not isinstance(link, dict):
            violations.append(
                violation("CONTEXT_LINK_INVALID", "fail", path, "Each context link must be an object.")
            )
            continue
        missing = [field for field in ["from", "to", "relation"] if not as_text(link.get(field))]
        if missing:
            violations.append(
                violation(
                    "CONTEXT_LINK_INVALID",
                    "fail",
                    path,
                    f"Context link is missing: {', '.join(missing)}",
                )
            )
    return violations


def validate_mandalart_view(context_map: dict[str, Any]) -> list[dict[str, str]]:
    optional_views = context_map.get("optional_views", {})
    if not isinstance(optional_views, dict):
        return [
            violation(
                "OPTIONAL_VIEWS_INVALID",
                "fail",
                "situation_context_map.optional_views",
                "optional_views must be an object.",
            )
        ]

    view = optional_views.get("mandalart_view", {})
    if not isinstance(view, dict) or not view.get("enabled"):
        return []

    violations: list[dict[str, str]] = []
    if not as_text(view.get("center")):
        violations.append(
            violation(
                "MANDALART_VIEW_INVALID",
                "fail",
                "situation_context_map.optional_views.mandalart_view.center",
                "Enabled Mandalart view requires center.",
            )
        )

    branches = view.get("branches", [])
    if not isinstance(branches, list) or not branches:
        violations.append(
            violation(
                "MANDALART_VIEW_INVALID",
                "fail",
                "situation_context_map.optional_views.mandalart_view.branches",
                "Enabled Mandalart view requires at least one branch.",
            )
        )
    return violations


def list_texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [as_text(item) for item in value if as_text(item)]


def validate_candidate_selection(
    stage: dict[str, Any],
    *,
    path: str,
    selected: str,
    candidates: list[str],
) -> list[dict[str, str]]:
    if not selected:
        return [
            violation(
                "EXPLORATION_SELECTION_REQUIRED",
                "fail",
                f"{path}.selected",
                "Exploration stage selected value is required.",
            )
        ]
    if selected == "unresolved":
        return []
    if selected not in candidates:
        return [
            violation(
                "EXPLORATION_SELECTION_NOT_IN_CANDIDATES",
                "fail",
                f"{path}.selected",
                "Selected value must exist in default candidates, example candidates, or agent-added candidates.",
            )
        ]
    return []


def validate_exploration_path(context_map: dict[str, Any]) -> list[dict[str, str]]:
    path = context_map.get("experimental_context_exploration_path")
    if not isinstance(path, dict):
        return [
            violation(
                "EXPLORATION_PATH_REQUIRED",
                "fail",
                "situation_context_map.experimental_context_exploration_path",
                "experimental_context_exploration_path is required during the experiment period.",
            )
        ]

    if path.get("enabled") is not True:
        return [
            violation(
                "EXPLORATION_PATH_DISABLED",
                "fail",
                "situation_context_map.experimental_context_exploration_path.enabled",
                "experimental_context_exploration_path must be enabled during the experiment period.",
            )
        ]

    violations: list[dict[str, str]] = []
    mode = as_text(path.get("mode", ""))
    if mode not in DETAIL_POLICIES:
        violations.append(
            violation(
                "EXPLORATION_MODE_INVALID",
                "fail",
                "situation_context_map.experimental_context_exploration_path.mode",
                "mode must be B_minimum_detail_required or A_full_detail_required.",
            )
        )

    stage_1 = path.get("stage_1_start_area", {})
    stage_2 = path.get("stage_2_subcategory", {})
    stage_3 = path.get("stage_3_context_object", {})
    stage_4 = path.get("stage_4_primary_structure", {})
    stage_5 = path.get("stage_5_detail_expansion", {})
    stages = {
        "stage_1_start_area": stage_1,
        "stage_2_subcategory": stage_2,
        "stage_3_context_object": stage_3,
        "stage_4_primary_structure": stage_4,
        "stage_5_detail_expansion": stage_5,
    }
    for stage_name, stage_value in stages.items():
        if not isinstance(stage_value, dict):
            violations.append(
                violation(
                    "EXPLORATION_STAGE_INVALID",
                    "fail",
                    f"situation_context_map.experimental_context_exploration_path.{stage_name}",
                    "Exploration stage must be an object.",
                )
            )
    if violations:
        return violations

    stage_1_selected = as_text(stage_1.get("selected"))
    stage_1_candidates = list_texts(stage_1.get("default_candidates")) + list_texts(stage_1.get("other_candidates"))
    violations.extend(
        validate_candidate_selection(
            stage_1,
            path="situation_context_map.experimental_context_exploration_path.stage_1_start_area",
            selected=stage_1_selected,
            candidates=stage_1_candidates,
        )
    )

    stage_2_parent = as_text(stage_2.get("parent"))
    if stage_1_selected and stage_1_selected != "unresolved" and stage_2_parent != stage_1_selected:
        violations.append(
            violation(
                "EXPLORATION_PARENT_MISMATCH",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_2_subcategory.parent",
                "stage_2 parent must match stage_1 selected value.",
            )
        )

    default_by_start = stage_2.get("default_candidates_by_start_area", {})
    seeded_candidates = []
    if isinstance(default_by_start, dict):
        seeded_candidates = list_texts(default_by_start.get(stage_2_parent))
    if not seeded_candidates and stage_2_parent in SUBCATEGORY_SEEDS:
        seeded_candidates = SUBCATEGORY_SEEDS[stage_2_parent]
    stage_2_candidates = (
        list_texts(stage_2.get("default_candidates"))
        + list_texts(stage_2.get("agent_added_candidates"))
        + seeded_candidates
    )
    stage_2_selected = as_text(stage_2.get("selected"))
    violations.extend(
        validate_candidate_selection(
            stage_2,
            path="situation_context_map.experimental_context_exploration_path.stage_2_subcategory",
            selected=stage_2_selected,
            candidates=stage_2_candidates,
        )
    )

    parent_path = list_texts(stage_3.get("parent_path"))
    if (
        stage_1_selected
        and stage_2_selected
        and stage_1_selected != "unresolved"
        and stage_2_selected != "unresolved"
        and parent_path[:2] != [stage_1_selected, stage_2_selected]
    ):
        violations.append(
            violation(
                "EXPLORATION_PARENT_PATH_MISMATCH",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_3_context_object.parent_path",
                "stage_3 parent_path must start with stage_1 selected and stage_2 selected.",
            )
        )

    stage_3_candidates = list_texts(stage_3.get("example_candidates")) + list_texts(stage_3.get("agent_generated_candidates"))
    stage_3_selected = as_text(stage_3.get("selected"))
    violations.extend(
        validate_candidate_selection(
            stage_3,
            path="situation_context_map.experimental_context_exploration_path.stage_3_context_object",
            selected=stage_3_selected,
            candidates=stage_3_candidates,
        )
    )

    base_axes = list_texts(stage_4.get("base_axes"))
    activated_axes = list_texts(stage_4.get("activated_axes"))
    if set(PRIMARY_STRUCTURE_AXES) - set(base_axes):
        violations.append(
            violation(
                "EXPLORATION_BASE_AXES_INCOMPLETE",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_4_primary_structure.base_axes",
                "stage_4 base_axes must include the primary 8 structure axes.",
            )
        )
    if not activated_axes:
        violations.append(
            violation(
                "EXPLORATION_ACTIVATED_AXES_REQUIRED",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_4_primary_structure.activated_axes",
                "At least one primary structure axis must be activated.",
            )
        )
    unknown_axes = sorted(set(activated_axes) - set(base_axes))
    if unknown_axes:
        violations.append(
            violation(
                "EXPLORATION_UNKNOWN_AXIS",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_4_primary_structure.activated_axes",
                f"Activated axes must exist in base_axes: {', '.join(unknown_axes)}",
            )
        )

    detail_policy = as_text(stage_5.get("detail_policy"))
    mode_policy = DETAIL_POLICIES.get(mode, {})
    allowed_policies = {item["detail_policy"] for item in DETAIL_POLICIES.values()}
    if detail_policy not in allowed_policies:
        violations.append(
            violation(
                "EXPLORATION_DETAIL_POLICY_INVALID",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_5_detail_expansion.detail_policy",
                "detail_policy is not allowed.",
            )
        )
        minimum_items = 1
    else:
        minimum_items = 2 if detail_policy == "full_detail_required_for_activated_axes" else 1
    if mode_policy and detail_policy and detail_policy != mode_policy["detail_policy"]:
        violations.append(
            violation(
                "EXPLORATION_MODE_POLICY_MISMATCH",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_5_detail_expansion.detail_policy",
                "detail_policy must match the selected exploration mode.",
            )
        )

    axis_details = stage_5.get("axis_details", {})
    if not isinstance(axis_details, dict):
        violations.append(
            violation(
                "EXPLORATION_AXIS_DETAILS_INVALID",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_5_detail_expansion.axis_details",
                "axis_details must be an object.",
            )
        )
        axis_details = {}
    for axis in activated_axes:
        details = list_texts(axis_details.get(axis))
        if len(details) < minimum_items:
            violations.append(
                violation(
                    "EXPLORATION_AXIS_DETAILS_REQUIRED",
                    "fail",
                    f"situation_context_map.experimental_context_exploration_path.stage_5_detail_expansion.axis_details.{axis}",
                    f"Activated axis requires at least {minimum_items} detail item(s).",
                )
            )

    if not as_text(stage_5.get("usage_direction")):
        violations.append(
            violation(
                "EXPLORATION_USAGE_DIRECTION_REQUIRED",
                "fail",
                "situation_context_map.experimental_context_exploration_path.stage_5_detail_expansion.usage_direction",
                "usage_direction is required.",
            )
        )

    unresolved_selected = "unresolved" in {stage_1_selected, stage_2_selected, stage_3_selected}
    missing_context = context_map.get("missing_context", [])
    if unresolved_selected and not missing_context:
        violations.append(
            violation(
                "EXPLORATION_UNRESOLVED_REQUIRES_MISSING_CONTEXT",
                "fail",
                "situation_context_map.missing_context",
                "Unresolved exploration selections require missing_context.",
            )
        )

    return violations


def validate_actor_scope(context_map: dict[str, Any]) -> list[dict[str, str]]:
    actor_scope = context_map.get("actor_scope")
    if not isinstance(actor_scope, dict):
        return [
            violation(
                "ACTOR_SCOPE_REQUIRED",
                "fail",
                "situation_context_map.actor_scope",
                "actor_scope is required to classify whether the situation belongs to an individual, team, company, or another actor.",
            )
        ]

    violations: list[dict[str, str]] = []
    primary_actor = as_text(actor_scope.get("primary_actor"))
    request_context = as_text(actor_scope.get("request_context"))
    decision_owner = as_text(actor_scope.get("decision_owner"))
    evidence = as_text(actor_scope.get("evidence"))

    if not primary_actor:
        violations.append(
            violation(
                "ACTOR_SCOPE_PRIMARY_ACTOR_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.primary_actor",
                "primary_actor is required.",
            )
        )
    elif primary_actor not in VALID_PRIMARY_ACTORS:
        violations.append(
            violation(
                "ACTOR_SCOPE_PRIMARY_ACTOR_INVALID",
                "fail",
                "situation_context_map.actor_scope.primary_actor",
                "primary_actor must be one of the allowed actor ownership values.",
            )
        )

    if not request_context:
        violations.append(
            violation(
                "ACTOR_SCOPE_REQUEST_CONTEXT_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.request_context",
                "request_context is required.",
            )
        )
    elif request_context not in VALID_REQUEST_CONTEXTS:
        violations.append(
            violation(
                "ACTOR_SCOPE_REQUEST_CONTEXT_INVALID",
                "fail",
                "situation_context_map.actor_scope.request_context",
                "request_context must be one of the allowed request context values.",
            )
        )

    if not decision_owner:
        violations.append(
            violation(
                "ACTOR_SCOPE_DECISION_OWNER_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.decision_owner",
                "decision_owner is required. Use unknown when the owner is unclear.",
            )
        )

    affected_parties = actor_scope.get("affected_parties", [])
    if not isinstance(affected_parties, list) or not list_texts(affected_parties):
        violations.append(
            violation(
                "ACTOR_SCOPE_AFFECTED_PARTIES_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.affected_parties",
                "affected_parties must contain at least one party, such as user, team, company, customer, or public.",
            )
        )

    if not evidence:
        violations.append(
            violation(
                "ACTOR_SCOPE_EVIDENCE_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.evidence",
                "actor_scope evidence is required.",
            )
        )

    confidence = actor_scope.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = -1.0
    if confidence_value < 0.0 or confidence_value > 1.0:
        violations.append(
            violation(
                "ACTOR_SCOPE_CONFIDENCE_INVALID",
                "fail",
                "situation_context_map.actor_scope.confidence",
                "actor_scope confidence must be a number between 0.0 and 1.0.",
            )
        )

    scope_missing_context = actor_scope.get("missing_context", [])
    if not isinstance(scope_missing_context, list):
        violations.append(
            violation(
                "ACTOR_SCOPE_MISSING_CONTEXT_INVALID",
                "fail",
                "situation_context_map.actor_scope.missing_context",
                "actor_scope missing_context must be a list.",
            )
        )
        scope_missing_context = []

    has_unknown = (
        primary_actor == "unknown"
        or request_context == "unknown"
        or decision_owner.lower() == "unknown"
    )
    global_missing_context = context_map.get("missing_context", [])
    if has_unknown and not list_texts(scope_missing_context) and not list_texts(global_missing_context):
        violations.append(
            violation(
                "ACTOR_SCOPE_UNKNOWN_REQUIRES_MISSING_CONTEXT",
                "fail",
                "situation_context_map.actor_scope.missing_context",
                "Unknown actor ownership values require missing_context instead of forced guessing.",
            )
        )

    self_check = actor_scope.get("classification_self_check")
    if not isinstance(self_check, dict):
        violations.append(
            violation(
                "ACTOR_SCOPE_SELF_CHECK_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.classification_self_check",
                "classification_self_check is required to make actor classification auditable.",
            )
        )
        return violations

    minimum_evidence = self_check.get("minimum_evidence_used", [])
    competing = self_check.get("competing_classifications", [])
    could_be_wrong_if = self_check.get("could_be_wrong_if", [])
    why_not_other = as_text(self_check.get("why_not_other_actor_scope"))

    if not isinstance(minimum_evidence, list) or not list_texts(minimum_evidence):
        violations.append(
            violation(
                "ACTOR_SCOPE_SELF_CHECK_EVIDENCE_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.classification_self_check.minimum_evidence_used",
                "minimum_evidence_used must contain at least one evidence item.",
            )
        )
    if not isinstance(competing, list):
        violations.append(
            violation(
                "ACTOR_SCOPE_SELF_CHECK_COMPETING_INVALID",
                "fail",
                "situation_context_map.actor_scope.classification_self_check.competing_classifications",
                "competing_classifications must be a list.",
            )
        )
        competing = []
    if not why_not_other:
        violations.append(
            violation(
                "ACTOR_SCOPE_SELF_CHECK_REASON_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.classification_self_check.why_not_other_actor_scope",
                "why_not_other_actor_scope is required.",
            )
        )
    if not isinstance(could_be_wrong_if, list):
        violations.append(
            violation(
                "ACTOR_SCOPE_SELF_CHECK_WRONG_IF_INVALID",
                "fail",
                "situation_context_map.actor_scope.classification_self_check.could_be_wrong_if",
                "could_be_wrong_if must be a list.",
            )
        )
        could_be_wrong_if = []

    if primary_actor in {"unknown", "mixed"} and not list_texts(competing):
        violations.append(
            violation(
                "ACTOR_SCOPE_SELF_CHECK_COMPETING_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.classification_self_check.competing_classifications",
                "unknown or mixed actor classifications require competing_classifications.",
            )
        )
    if (primary_actor in {"unknown", "mixed"} or confidence_value < 0.7) and not list_texts(could_be_wrong_if):
        violations.append(
            violation(
                "ACTOR_SCOPE_SELF_CHECK_WRONG_IF_REQUIRED",
                "fail",
                "situation_context_map.actor_scope.classification_self_check.could_be_wrong_if",
                "unknown, mixed, or low-confidence actor classifications require could_be_wrong_if.",
            )
        )

    return violations


def validate_situation_context(request: dict[str, Any], filled: dict[str, Any]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    context_map = filled.get("situation_context_map", {})
    if not isinstance(context_map, dict):
        context_map = {}
        violations.append(
            violation(
                "CONTEXT_MAP_INVALID",
                "fail",
                "situation_context_map",
                "situation_context_map must be an object.",
            )
        )

    for field_name in REQUIRED_CONTEXT_FIELDS:
        if not as_text(context_map.get(field_name)):
            violations.append(
                violation(
                    "CONTEXT_FIELD_REQUIRED",
                    "fail",
                    f"situation_context_map.{field_name}",
                    f"{field_name} is required.",
                )
            )

    phase = as_text(context_map.get("situation_phase", ""))
    if phase and phase not in VALID_SITUATION_PHASES:
        violations.append(
            violation(
                "SITUATION_PHASE_INVALID",
                "fail",
                "situation_context_map.situation_phase",
                "situation_phase is not allowed.",
            )
        )

    for list_field in ["required_context", "recommended_next_focus", "evidence_basis"]:
        value = context_map.get(list_field, [])
        if not isinstance(value, list) or not value:
            violations.append(
                violation(
                    "CONTEXT_LIST_REQUIRED",
                    "fail",
                    f"situation_context_map.{list_field}",
                    f"{list_field} must contain at least one item.",
                )
            )

    missing_context = context_map.get("missing_context", [])
    if not isinstance(missing_context, list):
        violations.append(
            violation(
                "MISSING_CONTEXT_INVALID",
                "fail",
                "situation_context_map.missing_context",
                "missing_context must be a list.",
            )
        )
        missing_context = []
    if needs_missing_context(request, filled) and not missing_context:
        violations.append(
            violation(
                "MISSING_CONTEXT_REQUIRED",
                "fail",
                "situation_context_map.missing_context",
                "This route or direction requires missing_context.",
            )
        )

    confidence = context_map.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = -1.0
    if confidence_value < 0.0 or confidence_value > 1.0:
        violations.append(
            violation(
                "CONTEXT_CONFIDENCE_INVALID",
                "fail",
                "situation_context_map.confidence",
                "confidence must be a number between 0.0 and 1.0.",
            )
        )

    violations.extend(validate_context_links(context_map))
    violations.extend(validate_mandalart_view(context_map))
    violations.extend(validate_actor_scope(context_map))
    violations.extend(validate_exploration_path(context_map))

    next_action = filled.get("next_action", {})
    if not isinstance(next_action, dict):
        next_action = {}
        violations.append(
            violation("NEXT_ACTION_INVALID", "fail", "next_action", "next_action must be an object.")
        )
    if as_text(next_action.get("action_type", "")) not in VALID_NEXT_ACTION_TYPES:
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
