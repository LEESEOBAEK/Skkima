# Agent Guide

Status: Agent Contract v1.3
Project: `schema_request_builder_tool`
Purpose: 에이전트가 이 프로젝트를 안전하게 실행, 검증, 보고, 확장하기 위한 운영 계약서다.

## 1. Contract Scope

이 문서는 사람용 사용 설명서가 아니라 에이전트 운영 계약서다.

에이전트는 이 문서를 기준으로 다음을 지킨다.

- 실행 가능한 현재 기능과 미래 확장 후보를 구분한다.
- Python이 담당할 일과 Agent가 담당할 일을 섞지 않는다.
- 검증 실패 상태에서 다음 단계로 진행하지 않는다.
- 파일 생성만 보고하지 않고, 요청된 경우 결과 내용을 확인해 보고한다.
- 구조 변경 시 온톨로지와 테스트 기준을 함께 확인한다.

## 2. Operating Decisions

| Decision | Value |
|---|---|
| Document role | `Agent Operating Contract` |
| Language style | English IDs + Korean explanation |
| Scope | Current layers 01-07 + short future extension policy |
| Default execution | `engine/python/workflow/workflow_runner.py` |
| Default report mode | `A_brief_completion` |
| Uncertainty handling | Record missing evidence; do not invent values |
| Ontology use | Structure governance and operation warning |
| Test rule | Include major validation commands |

## 3. Role Split

Python responsibilities:

- 요청 JSON을 생성한다.
- 고정된 스키마와 검증 규칙을 제공한다.
- 에이전트가 채운 JSON을 검증한다.
- workflow run, status, report, test artifact를 저장한다.

Agent responsibilities:

- 생성된 request JSON을 읽고 허용된 값만 채운다.
- 명시 근거와 추론 근거를 분리한다.
- 근거가 부족하면 `unknown`, `unresolved`, `missing_context`, `unresolved_fields`로 남긴다.
- 검증 결과를 확인하고 실패 시 다음 단계로 진행하지 않는다.
- 사용자의 요청 깊이에 맞춰 결과를 보고한다.

Do not:

- Python 검증을 우회하지 않는다.
- 스키마 필드명이나 검증 규칙을 임의로 바꾸지 않는다.
- 구조화 레이어에서 사용자의 원래 문제를 직접 해결하려고 하지 않는다.

## 4. Current Capabilities

현재 실행 가능한 기능은 01-07 레이어다.

```text
01_input_structuring
02_router
03_route_validation
04_direction_lens
05_situation_context
06_human_readable_report
07_fulfillment
```

Current vs Future Guard:

- 현재 기능은 위 01-07 레이어와 `workflow_runner.py`에 구현된 명령만 의미한다.
- 아직 구현되지 않은 프레임워크 조립 기능은 실행 가능한 기능처럼 다루지 않는다.
- 미래 기능은 `Future Extension Policy`에 원칙으로만 기록한다.

## 4.1 Skill Invocation Interface

`schema-workflow` is a short invocation interface for agents.
It is not a replacement for this operating contract.

Supported short invocation examples:

```text
$schema-workflow
input:
"..."
```

If the host platform supports at-sign skill selection, the same intent may be invoked as:

```text
@schema-workflow
"..."
```

Resume an existing run from a new chat or CLI session with:

```text
@schema-workflow continue
run: <run_id or run_dir>
request: <follow-up request>
```

Operating rules:

- The skill must treat this `agents/agent.md` as the live operating contract.
- The executable workflow engine remains `engine/python/workflow/workflow_runner.py`.
- Before project Skill installation or launch preparation, verify the user-scoped Engine through the Dashboard Engine Readiness boundary.
- A missing Engine may be installed only from a manifest-verified package after explicit user approval. Project Skills remain project-local and are installed only after the Engine is ready.
- The portable bundle installer must preflight Python 3.10+, Node.js, and Corepack before changing the user installation.
- The skill may shorten the user prompt, but it must not shorten validation, evidence handling, stop conditions, artifact policy, or ontology checks.
- A dashboard launch stores the complete user request in its launch directory and passes its SHA-256, character count, and byte count to the CLI. Read and verify that immutable source file before work; a context capsule or prompt summary is navigation context, never the completion contract.
- If the request source is missing, changed, or cannot be verified, stop with a request-integrity error. Never continue from a visibly truncated prompt.
- If the skill instruction and this document conflict, this `agents/agent.md` wins.
- The skill must not bypass the multi-problem guard or choose a single problem without user confirmation when independent problem candidates are present.
- Invoke the skill when starting or recovering work. In the same conversation and project, continue with natural language while preserving the linked run id.
- Detailed skill references may exist under `<UserSkillRoot>\schema-workflow\references`, but those references are support material, not a higher-priority contract.

## 4.2 Workspace And Operation Contract

Workspace governance is a runtime safety boundary, not an agent preference.

- `ToolRoot` contains the engine. Refer to it externally; never copy it into a project workspace.
- Resolve `ProjectRoot` before creating a run: explicit `--project-root`, then the nearest ancestor `.schema-workflow.json`, then a safe empty SessionCwd. If an unconfigured non-empty parent needs a new project, derive a child slug in `project-name`, `run-name`, input order.
- Never initialize the user home or Desktop itself; create the named or derived child project below it. Never create a project inside ToolRoot, and never initialize a filesystem root.
- A configured project owns exactly one `CanonicalRunsRoot`: `<ProjectRoot>/outputs/workflows`.
- Generate one `OperationId` at the start of each skill request and pass it to every `init` retry for that request.
- Reusing an OperationId is allowed only when its immutable contract is identical: InputHash, operation kind, `relation_type`, `parent_run_id`, and `target_run_id`. Independent, continuation, and branch operations must never share an OperationId.
- A different OperationId creates an independent RunId even when `InputHash` is equal.
- `InputHash` records comparison identity; it never blocks or merges a different operation.
- A request without RunId or RunDir is a new run. Only an explicit existing RunId or RunDir may continue a run.
- A new experiment based on an existing run creates a new RunId with `parent_run_id`; it does not edit the parent.
- A continuation reservation owns its lifecycle. Governed writer commands update that continuation OperationId through `running`, `waiting_user`, `completed`, or `failed`; they must not leave it permanently running while only changing the initial-run operation.
- `delivery_policy` is part of the continuation operation contract. Idempotent reuse may backfill a missing legacy policy when it matches the requested contract, but it must reject a conflicting policy.
- Each run has at most one active continuation owner in `active_continuation_operation_id`. A different OperationId is rejected while the owner is `running` or `waiting_user`; terminal `completed`, `failed`, or `aborted` releases ownership. Never bulk-update ambiguous legacy continuations.
- Run writers must use the run-scoped lock. When it is busy, report owner/wait information and offer a branch run for independent work.
- The run manifest is source of truth. Workspace registry and inspect reports are rebuildable derived views.
- `ready_for_next_action` means analysis handoff only. Only `request_completed` or an approved continuation completion may be reported as completed.
- A continuation with new user wording must be recorded by `continue-run`; do not leave material follow-up context only in chat. Use `--supplemental-input` only for short text. For a preserved dashboard request or long text, use `--supplemental-input-file` with `--supplemental-input-sha256` so the full UTF-8 request becomes the fulfillment source of truth.
- `build-fulfillment` binds to the latest supplemental input when one exists. If that binding changes, any earlier filled contract, evidence, and validation become stale and must be rebuilt before completion.
- Governed init has one final manifest writer. Atomic replacements retry only transient `PermissionError`/Windows sharing violations within the bounded policy; they must preserve the old target and clean temporary files on failure.
- Keep `RunDir` records separate from project-owned `DeliverablePath` files. Record deliverable paths and hashes in the manifest.
- For a standalone final file, use `register-artifact --final`: preserve the editable source, place the finalized project copy under `<ProjectRoot>/deliverables`, and create a Run-bound `milestone_snapshot` without overwriting collisions.
- For project-native code or directory results, keep their native project structure and use `register-deliverable` plus a project reference; do not relocate the project into `deliverables`.
- Operation status writes are idempotent. Preserve each distinct failure in `error_history`, clear only the active `error` after recovery, and do not append duplicate status events.
- Record the engine Git commit, dirty state, and deterministic fingerprint of relevant Python files. A dirty execution must not be identified by the clean HEAD alone.
- Inventory and migration dry-run outputs must resolve outside `source_root`; reject all destinations before scanning or writing if any destination is inside the source tree.
- Store only externally stated input, decisions, evidence summaries, validation, state, and artifacts. Do not attempt to persist hidden model reasoning.

## 5. Default Workflow

일반 사용자 요청은 기본적으로 workflow runner를 사용한다.

```powershell
$projectRoot = "<ProjectRoot>"
$operationId = "<one OperationId for this skill request>"
python .\engine\python\workflow\workflow_runner.py workspace-init --project-root $projectRoot
python .\engine\python\workflow\workflow_runner.py init --project-root $projectRoot --operation-id $operationId --text "<user input>" --run-name "<run name>"
python .\engine\python\workflow\workflow_runner.py workspace-inspect --project-root $projectRoot
python .\engine\python\workflow\workflow_runner.py status --run-dir "$projectRoot\outputs\workflows\<run_id>"
python .\engine\python\workflow\workflow_runner.py next --run-dir "$projectRoot\outputs\workflows\<run_id>"
```

Traceable run rule:

- New runtime workflow folders must keep the creation timestamp, shortened slug, and collision-resistant operation suffix in the folder name.
- Relative project paths always resolve from ProjectRoot, never ToolRoot or SessionCwd.
- `--output` is a compatibility option and is rejected unless it resolves to the configured CanonicalRunsRoot.
- Standard run id format: `YYYY-MM-DD_HHMMSS__<run_name_slug>`.
- If no run name is provided, use `YYYY-MM-DD_HHMMSS`.
- `run_name_slug` is the shortened filesystem-safe name. Do not use a very long user-provided name directly as a folder name.
- The full user-provided name must be preserved in `workflow_manifest.json` as `trace.original_run_name`.
- Run-name sanitizing, shortening, timestamp formatting, and unique run directory creation must use `engine/python/shared/run_identity.py`.
- The same values must be visible in `workflow_manifest.json` as `run_id`, `created_at`, and `trace`.
- Do not rename workflow output folders in a way that removes year, date, hour, minute, or second.
- When reporting a workflow result, include the timestamped `run_id` or the full run directory path.

Agent fill sequence:

1. `01_input_structuring/data/user_input_analysis_filled.json`을 먼저 채운다.
2. `02_router/data/facet_router_filled.json`을 채우고 `validate-route`를 실행한다.
3. 검증이 통과하면 `build-direction`을 실행하고 `direction_lens_filled.json`을 채운 뒤 `validate-direction`을 실행한다.
4. 검증이 통과하면 `build-context`를 실행하고 `situation_context_filled.json`을 채운 뒤 `validate-context`를 실행한다.
5. 분석 검증이 통과하면 `build-report`를 실행한다. 이 보고서는 아직 요청 완료를 의미하지 않는다.
6. `build-fulfillment`를 실행하고 `07_fulfillment/data/contract_filled.json`에 원래 요청 결과, 관찰 가능한 합격 기준, `finalization_mode`를 기록한다. 각 합격 기준의 `source_requirement_ids`는 `contract_request.json`의 명시 요구사항을 빠짐없이 연결해야 하며, 에이전트가 원문 요구를 삭제하거나 축소하면 안 된다.
7. standalone 최종 파일은 `register-artifact --final`로 확정한다. 프로젝트 구조 자체가 결과라면 기존 `register-artifact`와 `register-deliverable`을 사용한다.
8. `07_fulfillment/data/evidence_filled.json`에 최종 산출물 ID, 등록된 프로젝트 경로, 기준별 근거를 기록한다.
9. `validate-fulfillment`를 실행한다.
   - `SOURCE_REQUIREMENT_COVERAGE_INCOMPLETE`는 원문 명시 요구사항이 완료 기준에서 누락됐다는 뜻이다. 산출물을 완료 처리하지 말고 계약과 근거를 보강한다.
10. 상태가 `request_completed`이고 최종 보고서의 이행 검증이 통과한 경우에만 완료로 보고한다.

Report command:

```powershell
python .\engine\python\workflow\workflow_runner.py build-report --run-dir .\outputs\workflows\<run_id>
```

Layer scripts may be used directly only for:

- 테스트
- 디버깅
- 특정 단계 재실행
- workflow runner가 아직 지원하지 않는 좁은 점검

## 6. Layer Contracts

| Layer | Purpose | Input | Output | Agent caution |
|---|---|---|---|---|
| `01_input_structuring` | 사용자 입력을 분석 요청 구조로 만든다. | raw text or input file | `user_input_analysis_request.json` | 값이 없으면 억지로 채우지 않는다. |
| `02_router` | 입력을 facet으로 분류하고 route를 선택한다. | input analysis, raw text, files | `facet_router_filled.json` | route는 하나만 선택하거나 `unresolved`로 둔다. |
| `03_route_validation` | route 판단이 규칙을 지키는지 검증한다. | router request + filled output | `route_decision_validation.json` | 실패하면 다음 단계로 가지 않는다. |
| `04_direction_lens` | 다음 분석 렌즈를 선택한다. | validated router output | `direction_lens_filled.json` | high-risk 상황에서는 risk lens 누락을 피한다. |
| `05_situation_context` | 상황 맥락 지도를 만든다. | router + direction output | `situation_context_filled.json` | actor scope가 불명확하면 `unknown`으로 두고 근거 부족을 남긴다. |
| `06_human_readable_report` | 검증된 JSON을 사람이 읽는 보고서로 변환한다. | workflow JSON files | `human_readable_report.md` | 새 추론을 만들지 않고 기존 결과를 요약한다. |
| `07_fulfillment` | 원래 요청한 결과와 실제 등록 산출물이 일치하는지 검증한다. | report, contract, artifacts | `validation.json` | 코드나 앱을 일률적으로 요구하지 말고 원래 요청 기준만 적용한다. |

## 7. Evidence Rule

명시 근거와 추론 근거를 분리한다.

- `explicit`: 사용자의 원문, 첨부 파일, 이전 산출물에 직접 있는 정보
- `inferred`: 직접 쓰이지 않았지만 합리적으로 추론한 정보
- `missing_context`: 판단에 필요한데 아직 없는 정보
- `unresolved`: 현재 근거로 확정할 수 없는 상태

추론값에는 가능한 한 다음을 함께 남긴다.

- `content`
- `confidence`
- `evidence`
- `reason`

근거가 없는 값을 높은 confidence로 채우지 않는다.

## 8. Uncertainty Rule

근거가 부족하면 값을 억지로 채우지 않는다.

Allowed uncertainty markers:

- `unknown`
- `unresolved`
- `missing_context`
- `unresolved_fields`
- `clarification_required`
- `missing_decision_basis`

진행 가능한 부분은 계속 진행할 수 있다.
단, 핵심 판단에 필요한 값이 없거나 검증이 실패하면 사용자에게 보강이 필요하다고 보고한다.

## 8.1 Multi-Problem Guard

입력 안에 독립적인 문제 상황이 2개 이상 있으면 하나의 문제로 억지로 합치지 않는다.

Agent responsibilities:

- 서로 독립적인 문제를 `problem_candidates`로 분리해서 사용자에게 보고한다.
- 문제들이 서로 관련되어 있으면 하나의 상위 문제와 하위 이슈로 구조화한다.
- 문제들이 서로 독립적이면 각 문제별 별도 workflow가 필요한지 판단한다.
- 우선순위가 명확하면 가장 먼저 처리할 문제를 기준으로 workflow를 진행한다.
- 우선순위가 불명확하면 어떤 문제를 먼저 볼지 사용자에게 묻는다.
- 고위험 문제가 포함되어 있으면 고위험 문제를 분리하고 `risk_review` 또는 안전 route를 우선 고려한다.

Do not:

- 서로 다른 문제를 하나의 `central_problem`으로 합치지 않는다.
- 독립적인 여러 문제에서 하나의 route만 억지로 고르지 않는다.
- 사용자가 한 번에 모두 해결해달라고 했더라도, 서로 독립적인 문제라면 분리 필요성을 먼저 보고한다.

Recommended report:

```text
입력 안에 독립적인 문제 후보가 2개 이상 있습니다.
1. <problem candidate A>
2. <problem candidate B>

권장 처리:
- 먼저 처리할 문제를 선택하거나
- 각 문제를 별도 workflow로 실행합니다.
```

## 9. Report Mode Trigger

기본 보고는 가볍게 한다.
단, 사용자의 요청 의도에 따라 보고 깊이를 바꾼다.

### A. `brief_completion`

Default mode.

사용자가 단순히 실행, 생성, 정리를 요청한 경우 사용한다.

Include:

- 성공/실패
- 주요 파일 위치
- 다음 단계 한 줄

### B. `summary_report`

사용자가 결과, 내용, 판단, 요약, 무엇이 나왔는지를 요청하면 사용한다.

Include:

- 판단 요약
- 확실한 근거
- 불확실한 부분
- 다음 행동
- 주요 보고서 파일 위치

### C. `detailed_report`

사용자가 상세, 필드별, 검증, 테스트 결과, 전체 내용을 요청하면 사용한다.

Include:

- 레이어별 상태
- 주요 JSON 필드 상태
- 근거와 누락
- 검증 결과
- 테스트 결과
- 보고서 파일 위치

Important:

- `06_human_readable_report`가 생성된 경우, 상세 내용은 그 보고서를 우선 확인한다.
- 사용자가 “결과를 보고해줘”라고 하면 파일 위치만 말하지 말고 B 이상으로 보고한다.

## 9.1 Korean Report Check Rule

한국어 보고서가 터미널 출력에서 깨져 보이면, 파일 데이터가 깨졌다고 바로 판단하지 않는다.

Verification rule:

- `Get-Content` 또는 터미널 출력만 보고 한국어 손상 여부를 판정하지 않는다.
- `human_readable_report.md`, `report_summary.json`, `input.txt`는 UTF-8 파일로 직접 읽어 확인한다.
- 깨짐이 의심되면 Python UTF-8 read 또는 Markdown 파일 직접 열기로 다시 확인한다.
- 파일 내부에 `\ufffd` replacement 문자가 없고 한글 범위 문자가 정상 존재하면 데이터는 정상으로 본다.
- 사용자에게 보고할 때는 터미널 표시 문제가 아니라 실제 파일 기준으로 판단한다.

Recommended check:

```powershell
python -c "from pathlib import Path; s=Path('<report path>').read_text(encoding='utf-8-sig'); print(repr(s[:200])); print('\\ufffd' in s)"
```

## 10. Stop Conditions

다음 상황에서는 다음 단계로 진행하지 않는다.

- validation result가 failed인 경우
- selected route가 필요한데 `route_status`가 `unresolved`인 경우
- high-risk 요청인데 안전 route 또는 risk lens가 없는 경우
- 필수 filled JSON이 placeholder 상태인 경우
- 핵심 판단 근거가 없는데 proceed route를 강제로 선택해야 하는 경우

Stop condition이 발생하면:

1. 어느 단계에서 멈췄는지 말한다.
2. 실패 이유를 요약한다.
3. 필요한 사용자 보강 정보를 말한다.
4. 다음 실행 명령이 있다면 명확히 제시한다.

## 11. Forbidden Actions

- Do not invent missing evidence.
- Do not modify schema fields unless explicitly asked.
- Do not proceed after validation failure.
- Do not report only “file generated” when the user asked for result content.
- Do not mix runtime outputs with test artifacts.
- Do not treat generated artifacts as source code.
- Do not force a route when route decision is unresolved.
- Do not present future extension ideas as implemented capabilities.
- Do not add new layers, artifact stores, or schema contracts without updating project governance documents.
- Do not confuse `artifacts_manifest.json` file registration with `continuation_state.json` next-work state.

## 12. Artifact Policy

Runtime outputs:

```text
outputs/workflows/
outputs/runs/
outputs/backups/
```

Test and development artifacts:

```text
tests/artifacts/test_runs/
tests/artifacts/workflows/
tests/artifacts/runs/
tests/artifacts/experiments/
tests/artifacts/usage_probes/
```

Rules:

- Current user-facing runtime outputs stay under `outputs/`.
- Test, demo, synthetic, and probe outputs stay under `tests/artifacts/`.
- Generated artifacts are not source code.
- `tests/artifacts/README.md` explains artifact storage.

Workflow artifact binding:

```text
outputs/workflows/<run_id>/
  artifacts_manifest.json
  assets/
    images/generated/
    images/references/
    prompts/
    documents/
    other/
```

Rules:

- A generated image, prompt, document, reference file, or external tool result is not an official workflow artifact until it is registered in `artifacts_manifest.json`.
- Editable and intermediate outputs stay in the project workspace by default.
- Register working outputs as `project_reference`; do not copy them into the workflow run by default.
- For a standalone requested final file, use `register-artifact --final`; this creates the managed project deliverable and milestone snapshot together.
- Use `register-artifact --snapshot` for approved checkpoints that are not standalone final-deliverable promotion.
- Chat-only, temporary, clipboard, or external tool files may be mentioned as context, but they must not be treated as durable workflow outputs until bound to the run.
- Use `workflow_runner.py register-artifact` when a file already exists outside the run folder and should become part of the workflow record.
- A `generated_output`, `requested_output`, or `final_output` created outside `ProjectRoot` must be copied into the Run `assets/`; never treat a Gemini/agent cache path as the only durable artifact location.
- Use `role=final_output` for every user-facing final file. This role promotes the file into `<ProjectRoot>/deliverables` and keeps the matching Run snapshot even when `--final` is omitted.
- Promote only representative user-facing deliverables as `final_output`. Supporting notes, evidence logs, source fragments, and intermediate files remain `generated_output` or `project_reference`; do not flatten an entire working folder into `<ProjectRoot>/deliverables`.
- Governed continuations require every active non-reference artifact to resolve to a registered DeliverablePath before `complete-continuation` can succeed. Use `continue-run --allow-internal-only` only when the user explicitly requested no durable file result.
- If launch validation fails after `continue-run` reserves ownership, release only that exact OperationId with `abort-continuation --approved --reason ...`; never edit manifest ownership fields directly.
- The human-readable report should show registered artifact status and should not imply that an unregistered generated file exists.

### 12.1 Continuation State

이어 작업이 필요할 때만 다음 sidecar를 생성한다.

```text
outputs/workflows/<run_id>/continuation_state.json
```

Operating rules:

- Use `workflow_runner.py init-continuation` after required source artifacts are registered.
- `active_artifacts` references `(source_run_id, artifact_id)`; it does not duplicate artifact paths or file metadata. Version 0.1 permits only the anchor workflow `run_id` as `source_run_id`.
- A candidate sheet is one bound artifact. Candidates inside the sheet are logical state entries, not separate artifacts unless separate files are actually produced and registered.
- Preserve a deterministic index rule. For `left_to_right` A~E, numeric candidate 3 resolves to C and label B resolves to B.
- Do not infer `selected_candidate` before the user selects one.
- Distinguish `revise_candidate`, `generate_standalone_image`, and `build_codex_pet` in `next_actions` and `decision_log`.
- `completed_at` and `elapsed_seconds` describe continuation completion, not workflow report completion.
- Never register `continuation_state.json` in `artifacts_manifest.json`.
- New continuation state uses schema v0.2; v0.1 remains readable and may be upgraded with `migrate-continuation`.
- Project work uses `workspace_context.mode=project_first`.
- Keep `working_root`, `official_run_dir`, and optional `deployment_target` distinct.
- A provided `working_root` must resolve to an existing directory. Do not continue with a missing placeholder path.
- Correct an invalid or stale project path with `set-continuation-workspace`; do not hand-edit `continuation_state.json`.
- Use lifecycle phases: `candidate_review`, `asset_generation`, `awaiting_user_review`, `approved`, `deployment_ready`, `deployment`, `deployed`, and `completed`.
- Completion is scope-based. The gate is one of `artifact_ready`, `approved`, or `deployed`.
- Do not bypass the completion gate with agent judgment.
- Use risk-based deployment approval: low-risk local creation may proceed, recoverable local replacement requires an explicit scoped request and backup, and high-risk or external deployment requires confirmation.
- Record result artifacts before approval. Approved or deployed work may complete only after its configured gate is achieved.
- Separate `agent_work_seconds`, `user_review_seconds`, `deployment_seconds`, and total elapsed time.
- When a valid continuation exists, its phase owns the top-level `workflow_status` and `workflow_next` projection.
- Human-readable reports must preserve that continuation projection instead of resetting the state to base workflow handoff.

## 13. Ontology Rule

구조 변경 전후에는 `docs/project_ontology.md`를 확인한다.

Update the ontology when adding or changing:

- layer
- script ownership
- schema contract
- validation contract
- artifact store
- test case set
- test runner
- report layer
- operational document

50/50 Operating Balance:

- Current-state map: 50%
- Change-governance rules: 50%

If the ontology drifts:

- `Balanced`: 현재 구조와 변경 규칙이 모두 보인다.
- `Watch`: 한쪽이 눈에 띄게 강해진다.
- `Review Needed`: 주요 확장 전 균형 점검이 필요하다.

Do not expand the ontology with unrelated theory.
If a section is not current structure or change governance, move it to a design document.

## 14. Validation Trigger

구조, 코드, workflow, router, validation, context, report layer를 변경한 경우 주요 검증을 실행한다.

Recommended checks:

```powershell
python .\tests\runners\workflow_smoke_test_runner.py
python .\tests\runners\fulfillment_contract_test_runner.py
python .\tests\runners\workspace_governance_test_runner.py
python .\tests\runners\router_red_test_runner.py
python .\tests\runners\router_flow_test_runner.py
python .\layers\03_route_validation\route_decision_validator.py test
python .\layers\04_direction_lens\direction_lens_builder.py test
python .\layers\05_situation_context\situation_context_builder.py test
```

Light trigger map:

| Change area | Recommended check |
|---|---|
| workflow runner | workflow smoke |
| workspace bootstrap, identity, registry, lock, or migration | workspace governance + workflow smoke |
| router | router red + router flow |
| route validation | route decision test |
| direction lens | direction lens test |
| situation context | situation context test |
| human-readable report | workflow smoke |
| fulfillment contract or completion gate | fulfillment contract + workflow smoke |
| artifact path | workflow smoke + affected runners |
| continuation state | workflow smoke + continuation create/read round trip |
| ontology or docs only | read check + status check |

If tests fail:

- 실패한 테스트를 먼저 보고한다.
- 실패 상태에서 다음 확장으로 넘어가지 않는다.
- 수정 후 같은 검증을 다시 실행한다.

## 15. Backup Rule

중요 문서를 재작성하기 전에는 `outputs/backups/`에 백업한다.

Backup filename format:

```text
agent_YYYY-MM-DD_HHMMSS.md
```

Backups are recovery artifacts.
They are not source code and should not be tracked by Git.

## 16. Future Extension Policy

다음 확장 후보는 현재 실행 기능이 아니다.

Future candidates:

- framework assembly layer
- framework recommendation and validation
- framework-specific test matrix
- machine-readable project ontology
- agent-readable project index

Rules:

- 새 기능은 기존 레이어에 억지로 넣지 않는다.
- 새 역할이 생기면 별도 layer 또는 별도 document로 분리한다.
- 새 layer를 만들면 `docs/project_ontology.md`, tests, workflow docs를 함께 갱신한다.
- 프레임워크 조립 기능이 구현되기 전에는 프레임워크 관련 내용은 원칙 수준으로만 유지한다.

## 17. Final Agent Checklist

Before reporting completion:

- Did I use the workflow runner for normal requests?
- Did I fill only allowed JSON fields?
- Did I separate explicit evidence from inferred evidence?
- Did I preserve missing or uncertain information instead of inventing it?
- Did I separate multiple independent problem situations instead of merging them?
- Did validation pass before moving forward?
- Did the original requested output pass `validate-fulfillment`, with present registered evidence?
- Is the final state `request_completed` rather than only `ready_for_next_action`?
- Did I store artifacts in the correct folder?
- Did editable work remain in the project workspace and were only milestones snapshotted?
- For a standalone final file, did `--final` create a registered `deliverables` copy and matching milestone snapshot?
- If work continues, did I link continuation state only to registered artifact IDs?
- Did I keep continuation state separate from the artifact manifest?
- Did I enforce the configured completion gate and deployment risk policy?
- Did I report working, official snapshot, and deployment locations separately?
- Did I choose the right report mode A/B/C?
- If structure changed, did I check ontology impact?
- If code or workflow changed, did I run relevant validation?

If any answer is no, do not report final success yet.
