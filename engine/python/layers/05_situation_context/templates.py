from __future__ import annotations

from typing import Any

from contract import (
    CONTEXT_OBJECT_EXAMPLES,
    PRIMARY_STRUCTURE_AXES,
    START_AREA_CANDIDATES,
    SUBCATEGORY_SEEDS,
)


def empty_context_map() -> dict[str, Any]:
    return {
        "central_problem": "",
        "domain_area": "",
        "situation_context": "",
        "actor_context": "",
        "actor_scope": empty_actor_scope(),
        "problem_type": "",
        "task_object": "",
        "situation_phase": "exploration | definition | design | execution | validation | improvement | operation | review | unresolved",
        "required_context": [],
        "missing_context": [],
        "context_links": [
            {
                "from": "",
                "to": "",
                "relation": "",
                "evidence": "",
            }
        ],
        "recommended_next_focus": [],
        "evidence_basis": [],
        "confidence": 0.0,
        "optional_views": {
            "mandalart_view": {
                "enabled": False,
                "center": "",
                "branches": [],
                "note": "Optional display view only. The engine is not limited to 8 branches.",
            }
        },
        "experimental_context_exploration_path": empty_exploration_path(),
    }


def empty_actor_scope() -> dict[str, Any]:
    return {
        "primary_actor": "individual | team | company | department | customer | public | mixed | unknown",
        "request_context": (
            "personal_task | learning_goal | internal_work | client_work | business_contract | "
            "personal_contract | product_or_service | compliance_or_risk | public_service | unknown"
        ),
        "decision_owner": "",
        "affected_parties": [],
        "evidence": "",
        "confidence": 0.0,
        "missing_context": [],
        "classification_self_check": {
            "minimum_evidence_used": [],
            "competing_classifications": [],
            "why_not_other_actor_scope": "",
            "could_be_wrong_if": [],
        },
    }


def empty_exploration_path() -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": "B_minimum_detail_required | A_full_detail_required",
        "activation_reason": (
            "During the early experiment period this path is generated for every request. "
            "Use B by default; use A when high-risk, complex, planning-heavy, or framework-basis depth is needed."
        ),
        "stage_1_start_area": {
            "purpose": "Choose the broad starting area.",
            "default_candidates": START_AREA_CANDIDATES,
            "other_candidates": [],
            "selected": "",
            "reason": "",
            "evidence": "",
        },
        "stage_2_subcategory": {
            "purpose": "Choose or extend a subcategory under the selected start area.",
            "parent": "",
            "default_candidates_by_start_area": SUBCATEGORY_SEEDS,
            "default_candidates": [],
            "agent_added_candidates": [],
            "selected": "",
            "reason": "",
            "evidence": "",
        },
        "stage_3_context_object": {
            "purpose": "Generate and select the concrete context object or situation candidate.",
            "parent_path": [],
            "example_candidates": CONTEXT_OBJECT_EXAMPLES,
            "agent_generated_candidates": [],
            "selected": "",
            "reason": "",
            "evidence": "",
        },
        "stage_4_primary_structure": {
            "purpose": "Activate only the useful primary structure axes.",
            "base_axes": PRIMARY_STRUCTURE_AXES,
            "activated_axes": [],
            "axis_notes": {axis: "" for axis in PRIMARY_STRUCTURE_AXES},
        },
        "stage_5_detail_expansion": {
            "purpose": "Expand details for activated axes and record the usage direction.",
            "detail_policy": "minimum_one_detail_per_activated_axis | full_detail_required_for_activated_axes",
            "axis_details": {axis: [] for axis in PRIMARY_STRUCTURE_AXES},
            "usage_direction": "",
        },
    }
