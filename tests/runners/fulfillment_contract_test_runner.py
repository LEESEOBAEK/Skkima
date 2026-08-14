from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
ENGINE_DIR = PROJECT_ROOT / "engine" / "python"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from shared import artifacts as artifact_store
from shared import fulfillment


def base_contract(status: str = "ready") -> dict[str, Any]:
    return {
        "fulfillment_contract_version": fulfillment.FULFILLMENT_VERSION,
        "contract_status": status,
        "requested_output": {
            "description": "A requested idea report",
            "deliverable_type": "report",
            "format": "markdown",
            "minimum_count": 1,
            "unit": "file",
        },
        "acceptance_criteria": [
            {
                "id": "AC-01",
                "description": "The report contains at least three concrete ideas.",
                "source": "explicit",
                "source_requirement_ids": ["SRC-001"],
            }
        ],
        "artifact_policy": {
            "finalization_mode": "project_native",
            "minimum_registered_artifacts": 1,
            "require_project_deliverable": True,
            "require_milestone_snapshot": False,
        },
        "risk": {"level": "low", "requires_user_approval": False},
        "needs_user_input": {"required": False, "questions": []},
        "not_required_reason": "",
    }


def evidence(
    *,
    artifact_id: str = "idea_report",
    deliverable_paths: list[str] | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    return {
        "fulfillment_evidence_version": version or fulfillment.FULFILLMENT_VERSION,
        "result_status": "fulfilled",
        "artifact_ids": [artifact_id],
        "deliverable_paths": list(deliverable_paths or []),
        "criteria_results": [
            {
                "criterion_id": "AC-01",
                "status": "pass",
                "evidence": "idea_report.md contains three numbered ideas.",
            }
        ],
        "agent_summary": "Created the requested report.",
    }


def codes(report: dict[str, Any]) -> set[str]:
    return {str(item.get("code")) for item in report.get("violations", []) if isinstance(item, dict)}


def run_tests(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    run_dir = output / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir()
    artifact_store.ensure_artifact_store(run_dir)
    manifest: dict[str, Any] = {"project_root_absolute": str(output), "deliverable_paths": []}
    results: list[dict[str, Any]] = []
    source_request = fulfillment.build_contract_request(
        raw_text="다음 산출물을 제작해줘.\n- 아이디어 3개가 포함된 보고서",
        needed_output="아이디어 보고서",
        context_next_action={},
    )

    placeholder = {"workflow_placeholder": True}
    report = fulfillment.validate_fulfillment(run_dir, manifest, placeholder, placeholder)
    results.append({
        "id": "contract_required",
        "pass": not report["valid"] and "FULFILLMENT_CONTRACT_REQUIRED" in codes(report),
    })

    contract = base_contract()
    report = fulfillment.validate_fulfillment(run_dir, manifest, contract, placeholder)
    results.append({
        "id": "evidence_required",
        "pass": not report["valid"] and "FULFILLMENT_EVIDENCE_REQUIRED" in codes(report),
    })

    result_path = output / f"idea_report_{run_dir.name}.md"
    result_path.write_text("# Ideas\n\n1. One\n2. Two\n3. Three\n", encoding="utf-8")
    artifact_store.register_artifact(
        run_dir,
        artifact_id="idea_report",
        artifact_type="document",
        role="generated_output",
        path=str(result_path),
        source_step="fulfillment_test",
        copy_into_run=False,
    )
    manifest["deliverable_paths"] = [{"path_absolute": str(result_path)}]
    project_evidence = evidence(deliverable_paths=[str(result_path)])
    report = fulfillment.validate_fulfillment(
        run_dir, manifest, contract, project_evidence, request=source_request
    )
    results.append({"id": "registered_result_passes", "pass": report["valid"] is True})

    request_binding = {
        "scope": "continuation",
        "operation_id": "op-bound-followup",
        "supplemental_input_id": "supplemental_bound",
        "input_hash": "b" * 64,
    }
    bound_request = fulfillment.build_contract_request(
        raw_text="다음 산출물을 작성한다.\n- 아이디어 3개가 포함된 보고서",
        needed_output="아이디어 보고서",
        context_next_action={},
        request_binding=request_binding,
    )
    bound_contract = base_contract()
    bound_contract["request_binding"] = request_binding
    bound_evidence = evidence(deliverable_paths=[str(result_path)])
    bound_evidence["request_binding"] = request_binding
    bound_report = fulfillment.validate_fulfillment(
        run_dir, manifest, bound_contract, bound_evidence, request=bound_request
    )
    results.append({"id": "matching_request_binding_passes", "pass": bound_report["valid"] is True})

    stale_contract = base_contract()
    stale_report = fulfillment.validate_fulfillment(
        run_dir, manifest, stale_contract, bound_evidence, request=bound_request
    )
    results.append({
        "id": "stale_contract_binding_is_rejected",
        "pass": not stale_report["valid"] and "FULFILLMENT_REQUEST_BINDING_MISMATCH" in codes(stale_report),
    })

    stale_evidence = evidence(deliverable_paths=[str(result_path)])
    stale_evidence["request_binding"] = {**request_binding, "input_hash": "c" * 64}
    stale_evidence_report = fulfillment.validate_fulfillment(
        run_dir, manifest, bound_contract, stale_evidence, request=bound_request
    )
    results.append({
        "id": "stale_evidence_binding_is_rejected",
        "pass": not stale_evidence_report["valid"] and "FULFILLMENT_EVIDENCE_BINDING_MISMATCH" in codes(stale_evidence_report),
    })

    artifact_store.register_artifact(
        run_dir,
        artifact_id="idea_report_duplicate",
        artifact_type="document",
        role="generated_output",
        path=str(result_path),
        source_step="fulfillment_duplicate_test",
        copy_into_run=False,
    )
    duplicate_contract = base_contract()
    duplicate_contract["artifact_policy"]["minimum_registered_artifacts"] = 2
    duplicate_evidence = evidence(deliverable_paths=[str(result_path)])
    duplicate_evidence["artifact_ids"] = ["idea_report", "idea_report_duplicate"]
    duplicate_report = fulfillment.validate_fulfillment(
        run_dir,
        manifest,
        duplicate_contract,
        duplicate_evidence,
        request=source_request,
    )
    results.append(
        {
            "id": "duplicate_content_cannot_satisfy_artifact_minimum",
            "pass": (
                not duplicate_report["valid"]
                and "REGISTERED_ARTIFACT_UNIQUE_MINIMUM_NOT_MET"
                in codes(duplicate_report)
                and duplicate_report["summary"]["unique_referenced_artifact_count"] == 1
            ),
        }
    )

    branch_request = fulfillment.build_contract_request(
        raw_text=(
            "다음 산출물을 제작해줘.\n"
            "- 로고 시안\n"
            "- 컬러 팔레트\n"
            "- 앱 핵심 화면\n"
            "- 브랜드 보드\n"
            "- 비교 문서\n"
        ),
        needed_output="브랜드 분기 시안",
        context_next_action={},
    )
    reduced_contract = base_contract()
    reduced_report = fulfillment.validate_fulfillment(
        run_dir,
        manifest,
        reduced_contract,
        project_evidence,
        request=branch_request,
    )
    results.append(
        {
            "id": "agent_cannot_reduce_explicit_source_requirements",
            "pass": (
                not reduced_report["valid"]
                and "SOURCE_REQUIREMENT_COVERAGE_INCOMPLETE" in codes(reduced_report)
            ),
        }
    )

    covered_contract = base_contract()
    covered_contract["acceptance_criteria"][0]["source_requirement_ids"] = [
        item["id"] for item in branch_request["source_requirements"]
    ]
    covered_report = fulfillment.validate_fulfillment(
        run_dir,
        manifest,
        covered_contract,
        project_evidence,
        request=branch_request,
    )
    results.append(
        {
            "id": "complete_source_requirement_mapping_passes",
            "pass": covered_report["valid"] is True,
        }
    )

    flattened_request = fulfillment.build_contract_request(
        raw_text=(
            "기준 Run의 정의는 유지한다. "
            "로고, 컬러 팔레트, 앱 핵심 화면과 브랜드 보드를 제작한다. "
            "실제로 열어볼 수 있는 이미지와 비교 문서를 생성한다. "
            "최종 산출물을 deliverables에 전달하고 Run 스냅샷에도 등록한다."
        ),
        needed_output="이미지 파일들과 비교 문서",
        context_next_action={},
    )
    flattened_requirements = flattened_request["source_requirements"]
    results.append(
        {
            "id": "flattened_dashboard_text_preserves_output_requirements",
            "pass": (
                len(flattened_requirements) == 3
                and any("로고" in item["text"] for item in flattened_requirements)
                and any("비교 문서" in item["text"] for item in flattened_requirements)
                and any("deliverables" in item["text"] for item in flattened_requirements)
            ),
        }
    )

    external_root = output.parent / f"external_generator_{run_dir.name}"
    external_root.mkdir(exist_ok=True)
    external_path = external_root / "generated_board.png"
    external_path.write_bytes(b"external-generator-output")
    custody_registration = artifact_store.register_artifact(
        run_dir,
        artifact_id="external_generated_board",
        artifact_type="image",
        role="generated_output",
        path=str(external_path),
        source_step="external_generator_test",
        project_root=output,
    )
    custody_artifact = custody_registration["artifact"]
    custody_path = run_dir / str(custody_artifact["path"])
    results.append({
        "id": "external_generated_output_is_copied_into_run",
        "pass": (
            custody_artifact.get("custody_mode") == "external_output_copy"
            and custody_artifact.get("storage_mode") == "milestone_snapshot"
            and custody_artifact.get("original_source") == str(external_path)
            and custody_path.is_file()
            and custody_path.read_bytes() == external_path.read_bytes()
        ),
    })
    repeated_registration = artifact_store.register_artifact(
        run_dir,
        artifact_id="external_generated_board",
        artifact_type="image",
        role="generated_output",
        path=str(external_path),
        source_step="external_generator_test_retry",
        project_root=output,
    )
    results.append({
        "id": "external_generated_output_retry_is_idempotent",
        "pass": (
            repeated_registration["artifact"]["path"] == custody_artifact["path"]
            and len(list(custody_path.parent.glob(f"{custody_path.stem}*{custody_path.suffix}"))) == 1
        ),
    })

    artifact_store.register_artifact(
        run_dir,
        artifact_id="unsafe_external_reference",
        artifact_type="image",
        role="generated_output",
        path=str(external_path),
        source_step="legacy_external_reference_test",
        copy_into_run=False,
    )
    unsafe_evidence = evidence(
        artifact_id="unsafe_external_reference",
        deliverable_paths=[str(result_path)],
    )
    report = fulfillment.validate_fulfillment(run_dir, manifest, contract, unsafe_evidence)
    results.append({
        "id": "external_generated_reference_cannot_complete",
        "pass": (
            not report["valid"]
            and "EXTERNAL_GENERATED_ARTIFACT_NOT_MANAGED" in codes(report)
        ),
    })

    waiting = base_contract("waiting_user")
    waiting["needs_user_input"] = {"required": True, "questions": ["Which audience?"]}
    report = fulfillment.validate_fulfillment(run_dir, manifest, waiting, placeholder)
    results.append({
        "id": "waiting_user_cannot_complete",
        "pass": not report["valid"] and "FULFILLMENT_NOT_READY" in codes(report),
    })

    not_required = base_contract("not_required")
    not_required["not_required_reason"] = "The original request explicitly asks for an in-chat answer only."
    not_required["artifact_policy"]["minimum_registered_artifacts"] = 0
    report = fulfillment.validate_fulfillment(run_dir, manifest, not_required, placeholder)
    results.append({"id": "not_required_with_reason", "pass": report["valid"] is True})

    project_contract = base_contract()
    project_evidence = evidence(deliverable_paths=[str(output / "unregistered_project_result.md")])
    report = fulfillment.validate_fulfillment(run_dir, manifest, project_contract, project_evidence)
    results.append({
        "id": "unregistered_project_deliverable_fails",
        "pass": not report["valid"] and "FULFILLMENT_DELIVERABLE_UNREGISTERED" in codes(report),
    })

    weak_policy = base_contract()
    weak_policy["artifact_policy"]["minimum_registered_artifacts"] = 0
    report = fulfillment.validate_fulfillment(run_dir, manifest, weak_policy, evidence())
    results.append({
        "id": "non_chat_artifact_policy_cannot_be_zero",
        "pass": not report["valid"] and "ARTIFACT_POLICY_TOO_WEAK" in codes(report),
    })

    managed_contract = base_contract()
    managed_contract["artifact_policy"].update(
        {
            "finalization_mode": "managed_deliverable",
            "require_milestone_snapshot": True,
        }
    )
    root_registered_evidence = evidence(deliverable_paths=[str(result_path)])
    report = fulfillment.validate_fulfillment(
        run_dir, manifest, managed_contract, root_registered_evidence
    )
    managed_failure_codes = codes(report)
    results.append(
        {
            "id": "managed_final_requires_directory_and_snapshot",
            "pass": not report["valid"]
            and "FINAL_DELIVERABLE_DIRECTORY_REQUIRED" in managed_failure_codes
            and "FINAL_MILESTONE_SNAPSHOT_REQUIRED" in managed_failure_codes,
        }
    )

    deliverables_root = output / "deliverables"
    deliverables_root.mkdir(exist_ok=True)
    managed_path = deliverables_root / "managed_idea_report.md"
    managed_path.write_text(result_path.read_text(encoding="utf-8"), encoding="utf-8")
    artifact_store.register_artifact(
        run_dir,
        artifact_id="managed_report",
        artifact_type="document",
        role="generated_output",
        path=str(managed_path),
        source_step="fulfillment_test_final",
        copy_into_run=True,
        working_source=str(result_path),
    )
    manifest["deliverable_paths"].append({"path_absolute": str(managed_path)})
    managed_evidence = evidence(
        artifact_id="managed_report",
        deliverable_paths=[str(managed_path)],
    )
    report = fulfillment.validate_fulfillment(run_dir, manifest, managed_contract, managed_evidence)
    results.append({"id": "managed_final_snapshot_passes", "pass": report["valid"] is True})

    claim_contract = base_contract()
    claim_contract["acceptance_criteria"].append(
        {
            "id": "AC-02",
            "description": "확인되지 않은 성과 수치는 가설 또는 validation_needed로 표시한다.",
            "source": "inferred",
            "source_requirement_ids": [],
        }
    )
    unmarked_claim_path = output / "unmarked_claim.md"
    unmarked_claim_path.write_text(
        "# Result\n\n업무 처리 시간은 1/10로 줄어든다.\n",
        encoding="utf-8",
    )
    artifact_store.register_artifact(
        run_dir,
        artifact_id="unmarked_claim_report",
        artifact_type="document",
        role="generated_output",
        path=str(unmarked_claim_path),
        source_step="fulfillment_claim_test",
        copy_into_run=False,
    )
    manifest["deliverable_paths"].append({"path_absolute": str(unmarked_claim_path)})
    unmarked_claim_evidence = evidence(
        artifact_id="unmarked_claim_report",
        deliverable_paths=[str(unmarked_claim_path)],
    )
    unmarked_claim_evidence["criteria_results"].append(
        {"criterion_id": "AC-02", "status": "pass", "evidence": "성과 수치 표시를 확인했다."}
    )
    report = fulfillment.validate_fulfillment(
        run_dir, manifest, claim_contract, unmarked_claim_evidence
    )
    results.append(
        {
            "id": "unmarked_quantified_claim_is_rejected_when_contract_requires_disclosure",
            "pass": (
                not report["valid"]
                and "UNMARKED_QUANTIFIED_CLAIM" in codes(report)
                and report["summary"]["unmarked_quantified_claim_count"] == 1
            ),
        }
    )

    marked_claim_path = output / "marked_claim.md"
    marked_claim_path.write_text(
        "# Result\n\n[validation_needed] 업무 처리 시간은 1/10로 줄어들 것으로 예상한다.\n",
        encoding="utf-8",
    )
    artifact_store.register_artifact(
        run_dir,
        artifact_id="marked_claim_report",
        artifact_type="document",
        role="generated_output",
        path=str(marked_claim_path),
        source_step="fulfillment_claim_test",
        copy_into_run=False,
    )
    manifest["deliverable_paths"].append({"path_absolute": str(marked_claim_path)})
    marked_claim_evidence = evidence(
        artifact_id="marked_claim_report",
        deliverable_paths=[str(marked_claim_path)],
    )
    marked_claim_evidence["criteria_results"].append(
        {"criterion_id": "AC-02", "status": "pass", "evidence": "성과 수치에 검증 필요 표시가 있다."}
    )
    report = fulfillment.validate_fulfillment(
        run_dir, manifest, claim_contract, marked_claim_evidence
    )
    results.append(
        {
            "id": "marked_quantified_claim_passes",
            "pass": (
                report["valid"] is True
                and report["summary"]["unmarked_quantified_claim_count"] == 0
            ),
        }
    )

    mismatched_evidence = evidence(
        artifact_id="managed_report",
        deliverable_paths=[str(result_path)],
    )
    report = fulfillment.validate_fulfillment(
        run_dir, manifest, managed_contract, mismatched_evidence
    )
    results.append(
        {
            "id": "managed_snapshot_must_match_deliverable",
            "pass": not report["valid"]
            and "FINAL_SNAPSHOT_DELIVERABLE_MISMATCH" in codes(report),
        }
    )

    legacy_contract = base_contract()
    legacy_contract["fulfillment_contract_version"] = fulfillment.LEGACY_FULFILLMENT_VERSION
    legacy_contract["artifact_policy"] = {
        "minimum_registered_artifacts": 1,
        "require_project_deliverable": False,
    }
    legacy_evidence = evidence(version=fulfillment.LEGACY_FULFILLMENT_VERSION)
    report = fulfillment.validate_fulfillment(run_dir, manifest, legacy_contract, legacy_evidence)
    results.append({"id": "legacy_contract_remains_compatible", "pass": report["valid"] is True})

    passed = sum(1 for item in results if item["pass"])
    return {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "score_100": round(passed / len(results) * 100, 1),
        },
        "results": results,
        "run_dir": str(run_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Test requested-output fulfillment contracts.")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "tests" / "artifacts" / "fulfillment_contract"))
    args = parser.parse_args()
    report = run_tests(Path(args.output))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
