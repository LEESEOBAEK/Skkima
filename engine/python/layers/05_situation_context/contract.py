from __future__ import annotations


SITUATION_CONTEXT_VERSION = "0.4.0"


DIMENSION_CATALOG: dict[str, str] = {
    "central_problem": "The core situation or problem stated in one concise phrase.",
    "domain_area": "The broad domain or area connected to the situation.",
    "situation_context": "The current context in which the problem appears.",
    "actor_context": "Who is acting or deciding, and what position they are in.",
    "actor_scope": (
        "Structured actor ownership classification. It separates whether the situation belongs to an "
        "individual, team, company, department, customer, public group, mixed group, or is still unknown."
    ),
    "problem_type": "The kind of problem to solve, such as ambiguity, risk, design, diagnosis, planning, or execution.",
    "task_object": "The concrete object being worked on, such as code, file, contract, plan, process, data, or decision.",
    "situation_phase": "The current phase, such as exploration, definition, design, execution, validation, or improvement.",
    "required_context": "Information that should exist before confident problem solving.",
    "missing_context": "Important information not yet supported by explicit evidence.",
    "context_links": "Relationships between domain, situation, actor, problem type, task object, and next focus.",
    "recommended_next_focus": "What the next agent should focus on before choosing or running frameworks.",
    "evidence_basis": "Evidence used to justify the context map. Separate explicit evidence from inference when possible.",
    "confidence": "A 0.0 to 1.0 estimate of how well supported the context map is.",
    "experimental_context_exploration_path": (
        "Experimental five-stage path that narrows a broad situation from start area to subcategory, "
        "context object, primary structure axes, and detail expansion."
    ),
}


VALID_SITUATION_PHASES = {
    "exploration",
    "definition",
    "design",
    "execution",
    "validation",
    "improvement",
    "operation",
    "review",
    "unresolved",
}


VALID_NEXT_ACTION_TYPES = {
    "ask_user",
    "run_context_map",
    "run_framework",
    "proceed",
    "risk_review",
}


VALID_PRIMARY_ACTORS = {
    "individual",
    "team",
    "company",
    "department",
    "customer",
    "public",
    "mixed",
    "unknown",
}


VALID_REQUEST_CONTEXTS = {
    "personal_task",
    "learning_goal",
    "internal_work",
    "client_work",
    "business_contract",
    "personal_contract",
    "product_or_service",
    "compliance_or_risk",
    "public_service",
    "unknown",
}


REQUIRED_CONTEXT_FIELDS = [
    "central_problem",
    "domain_area",
    "situation_context",
    "problem_type",
    "task_object",
    "situation_phase",
]


START_AREA_CANDIDATES = [
    "개인",
    "학습",
    "업무",
    "직무",
    "문서",
    "데이터",
    "개발",
    "자동화",
    "의사결정",
    "리스크",
]


SUBCATEGORY_SEEDS: dict[str, list[str]] = {
    "개발": ["요구사항", "버그", "구현", "테스트", "배포", "문서"],
    "자동화": ["반복작업", "입력", "처리", "출력", "스케줄", "오류처리"],
    "문서": ["계약서", "보고서", "회의록", "매뉴얼", "정책문서"],
    "데이터": ["수집", "정제", "분석", "리포트", "시각화"],
    "학습": ["목표", "수준", "커리큘럼", "일정", "피드백", "결과물"],
    "업무": ["프로세스", "일정", "고객", "요청", "승인", "결과물"],
    "리스크": ["법무", "보안", "개인정보", "금전", "의료", "평판"],
}


CONTEXT_OBJECT_EXAMPLES = [
    "입력 파일",
    "반복 작업",
    "출력 결과",
    "실행 환경",
    "오류 처리",
    "문서 조항",
    "현재 수준",
    "결정 기준",
]


PRIMARY_STRUCTURE_AXES = [
    "type",
    "object",
    "attribute",
    "relationship",
    "state",
    "event",
    "rule",
    "context",
]


DETAIL_POLICIES = {
    "B_minimum_detail_required": {
        "detail_policy": "minimum_one_detail_per_activated_axis",
        "minimum_items_per_axis": 1,
    },
    "A_full_detail_required": {
        "detail_policy": "full_detail_required_for_activated_axes",
        "minimum_items_per_axis": 2,
    },
}
