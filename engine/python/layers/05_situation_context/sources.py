from __future__ import annotations

from typing import Any

from runner_support import as_text


def facet_value(filled_router: dict[str, Any], facet_name: str) -> str:
    facet = filled_router.get("facet_classification", {}).get(facet_name, {})
    if isinstance(facet, dict):
        return as_text(facet.get("value", ""))
    return as_text(facet)


def build_source_router(filled_router: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_decision": filled_router.get("route_decision", {}),
        "facet_classification": filled_router.get("facet_classification", {}),
        "missing_decision_basis": filled_router.get("missing_decision_basis", []),
        "reference_lenses": filled_router.get("reference_lenses", []),
        "router_summary": {
            "domain_context": facet_value(filled_router, "domain_context"),
            "problem_object": facet_value(filled_router, "problem_object"),
            "user_intent": facet_value(filled_router, "user_intent"),
            "definition_level": facet_value(filled_router, "definition_level"),
            "risk_level": facet_value(filled_router, "risk_level"),
            "needed_output": facet_value(filled_router, "needed_output"),
        },
    }


def build_source_direction(direction_filled: dict[str, Any] | None) -> dict[str, Any]:
    if not direction_filled:
        return {
            "available": False,
            "direction_decision": {},
            "selected_lenses": [],
            "missing_basis": [],
            "next_action": {},
        }
    return {
        "available": True,
        "direction_decision": direction_filled.get("direction_decision", {}),
        "selected_lenses": direction_filled.get("selected_lenses", []),
        "missing_basis": direction_filled.get("missing_basis", []),
        "next_action": direction_filled.get("next_action", {}),
    }
