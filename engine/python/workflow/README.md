# Workflow Runner

Status: Operation Guide v1.3
Role: 실행 런북
Owner: `engine/python/workflow/workflow_runner.py`

## Purpose

이 문서는 `schema_request_builder_tool`의 workflow 실행 순서를 안내한다.

설계 의도와 운영 계약은 별도 문서를 따른다.

- Agent contract: `agents/agent.md`
- Project structure: `docs/project_ontology.md`
- Workspace governance PRD: `docs/workspace_parallel_run_governance_prd.md`
- Router design: `router_design_spec.md`

이 문서는 라우터 설계 철학, 프레임워크 설명, 과거 실험 기록을 다루지 않는다.

## Agent/Skill Invocation

For normal agent-led use, the user can invoke the workflow with the `schema-workflow` skill.

Recommended short prompt:

```text
$schema-workflow
input:
"<problem situation>"
```

If the host platform supports at-sign skill selection, this form is also acceptable:

```text
@schema-workflow
"<problem situation>"
```

The skill is only the short entry point.
The operating contract remains `agents/agent.md`, and the executable engine remains `engine/python/workflow/workflow_runner.py`.

Use the manual commands in this document when:

- the agent needs to debug a specific stage,
- a workflow run must be reproduced exactly,
- validation or report artifacts need to be inspected directly,
- the skill interface is unavailable in the current host platform.

## Layer Sequence

Workflow runner는 현재 01-07 레이어를 순서대로 묶는다.

```text
01_input_structuring
02_router
03_route_validation
04_direction_lens
05_situation_context
06_human_readable_report
07_fulfillment
```

## Workspace Bootstrap And Canonical Runs

The engine stays in `ToolRoot`; the project workspace only stores project configuration, runs, and deliverables. The CLI owns workspace selection and atomic bootstrap, while the caller supplies an explicit path/name or enough run input to derive a project slug. Initialize or inspect a project with an explicit root:

```powershell
$projectRoot = "C:\path\to\project"
python .\engine\python\workflow\workflow_runner.py workspace-init --project-root $projectRoot
python .\engine\python\workflow\workflow_runner.py workspace-inspect --project-root $projectRoot
python .\engine\python\workflow\workflow_runner.py registry-rebuild --project-root $projectRoot
```

From a generic parent such as Desktop, create a child project instead of initializing the parent itself:

```powershell
python .\engine\python\workflow\workflow_runner.py workspace-init --project-name "research-notes"
python .\engine\python\workflow\workflow_runner.py init --project-name "research-notes" --operation-id $operationId --text "<user input>"
```

`.schema-workflow.json` fixes the workspace identity and the only normal run location:

```text
<ProjectRoot>/.schema-workflow.json
<ProjectRoot>/outputs/workflows/<run_id>/
```

ProjectRoot resolution order is explicit `--project-root`, the nearest ancestor `.schema-workflow.json`, then a safe empty SessionCwd. For an unconfigured non-empty parent, the runner creates one safe child slug using `project-name`, `run-name`, then input. Desktop and the user home are never initialized themselves; their child project may be initialized. ToolRoot and filesystem roots never own an auto-generated project. Running from a configured project child still uses the same ancestor ProjectRoot.

Do not create normal runs under `schema_workflows`, an engine copy, or a caller-selected output root. `--output` remains parse-compatible but must resolve to the configured CanonicalRunsRoot.

Each governed manifest records ToolRoot, engine version, Git commit, dirty state, and a deterministic fingerprint of the relevant `engine/python/workflow/`, `engine/python/shared/`, and `engine/python/layers/` Python files. A dirty execution is identified as `<commit>+dirty:<fingerprint-prefix>`, never as the clean commit alone.

## Start Workflow

새 workflow run을 생성한다.

```powershell
$operationId = "op_<stable id for this one skill request>"
python .\engine\python\workflow\workflow_runner.py init --project-root $projectRoot --operation-id $operationId --text "<user input>" --run-name "<run name>" --session-reference "<optional CLI reference>"
```

The same OperationId returns the existing RunId only when InputHash, operation kind, `relation_type`, `parent_run_id`, and `target_run_id` all match. Reusing an independent OperationId for continuation/branch, or changing its relation, parent, or target, fails with `OPERATION_CONTRACT_MISMATCH`. A different OperationId creates an independent RunId even for identical input; the manifests share a deterministic `duplicate_group_id` for comparison.

파일 입력을 사용할 때는 `--input-file`을 사용한다.

```powershell
python .\engine\python\workflow\workflow_runner.py init --project-root $projectRoot --operation-id $operationId --input-file "<input file path>" --run-name "<run name>"
```

생성 위치:

```text
<ProjectRoot>/outputs/workflows/<run_id>/
```

## Traceable Run ID

Every new runtime workflow directory must include a creation timestamp.

Standard format:

```text
<ProjectRoot>/outputs/workflows/YYYY-MM-DD_HHMMSS__<run_name_slug>__<operation_suffix>/
```

If `--run-name` is omitted, the run id still includes the timestamp and operation suffix:

```text
<ProjectRoot>/outputs/workflows/YYYY-MM-DD_HHMMSS__<operation_suffix>/
```

The same trace values are also stored in `workflow_manifest.json`:

```text
run_id
created_at
trace.created_date
trace.created_time
trace.run_name
trace.run_name_slug
trace.original_run_name
```

`run_name_slug` is shortened for filesystem safety. Keep the full user-provided name in
`trace.original_run_name` instead of using a very long folder name.

Do not rename runtime workflow folders to remove the timestamp.
Use the timestamped `run_id` when reporting, comparing, archiving, or restoring workflow outputs.

## New Run, Continuation, And Branch

- No RunId/RunDir: create a new operation and run.
- Explicit existing RunId/RunDir: reserve a continuation operation for exactly that run.
- New experiment from an existing result: create a new run and record `parent_run_id`.

```powershell
python .\engine\python\workflow\workflow_runner.py continue-run --project-root $projectRoot --operation-id "<new continuation OperationId>" --run-id "<existing RunId>" --supplemental-input "<follow-up request>" --note "<follow-up>"
python .\engine\python\workflow\workflow_runner.py continue-run --project-root $projectRoot --operation-id "<new continuation OperationId>" --run-id "<existing RunId>" --supplemental-input-file "<full-request.md>" --supplemental-input-sha256 "<sha256>" --note "<follow-up>"
python .\engine\python\workflow\workflow_runner.py init --project-root $projectRoot --operation-id "<new branch OperationId>" --parent-run-id "<existing RunId>" --text "<branch input>" --run-name "<branch name>"
```

The runner rejects a RunDir outside the selected project's CanonicalRunsRoot. A single-writer lock protects changes to a governed run; lock conflicts include wait/retry and branch guidance, and stale recovery is audited.

`continue-run` records a continuation OperationId as `running`. Short follow-up text may use `--supplemental-input`. Long or dashboard-preserved requests use `--supplemental-input-file` plus its SHA-256; the runner verifies the bytes before recording source path, hash, byte count, and character count. The manifest keeps an idempotent supplemental-input ledger. `build-fulfillment` always selects the latest recorded request and copies its binding into the request contract. A changed binding invalidates stale contract, evidence, and validation files. Later governed writer commands project the continuation workflow state onto that same operation as `running`, `waiting_user`, or `completed`; exceptions record `failed`. The original run-creation operation is not used as a substitute for the active continuation operation.

`delivery_policy` is an immutable part of that continuation contract. When an older terminal operation has no policy, idempotent reuse may add the requested policy to the manifest, operation record, and derived registry. A conflicting existing policy is rejected instead of being silently replaced.

The manifest field `active_continuation_operation_id` is the single owner from continuation start through terminal state. The same OperationId/contract is idempotent. A different OperationId receives `CONTINUATION_ALREADY_ACTIVE` while the owner is `running` or `waiting_user`; `completed`, `failed`, and `aborted` clear the pointer. Legacy manifests with multiple non-terminal candidates become `continuation_ownership_status=ambiguous`, appear in `workspace-inspect`, and require explicit recovery instead of bulk completion.

If a launch fails after reserving continuation ownership, use `abort-continuation` with the exact RunId, active OperationId, an audit reason, and `--approved`. The command rejects unrelated owners and restores the Run status from its existing workflow state instead of editing manifest lock fields by hand.

Governed init suppresses the builder's preliminary manifest write and lets governance persist one enriched final manifest. Atomic replacement retries `PermissionError` and Windows sharing/lock violations (WinError 32/33) for at most 2.0 seconds and 20 attempts, using exponential backoff from 10ms capped at 200ms. Exhaustion raises `ATOMIC_REPLACE_RETRY_EXHAUSTED` with the target, original error, attempts, and retry count; the prior target is never deleted.

## Workspace Registry And Inspect

`workflow_manifest.json` is source of truth. Operation files and `workspace_registry.json` make reservations and failures visible, while `registry-rebuild` can recreate the derived run index.

`workspace-inspect` writes JSON and Markdown with operation/run counts, status counts, missing or pending reports, incomplete fulfillment, legacy unverified completion, unofficial roots, duplicate groups, parent/continuation relations, ambiguous continuation ownership, engine-copy candidates, and recovery actions.

```text
<CanonicalRunsRoot>/.control/
  locks/
  operations/
  workspace_registry.json
  workspace_inspect.json
  workspace_registry_report.md
  audit.jsonl
```

Operation records use idempotent status transitions. Repeated identical states are not appended. A recovered operation clears its current `error` but retains distinct failures in `error_history` for incident review.

## Deliverable Paths

Use `register-deliverable` for editable or project-native results outside RunDir. The path must stay inside ProjectRoot and separate from RunDir; the manifest records absolute/relative paths and a SHA-256 relationship.

For a standalone final file, prefer `register-artifact --final`. It preserves the working source, creates a collision-safe copy under `<ProjectRoot>/deliverables`, registers its hash in the run manifest, and creates a Run-bound milestone snapshot in one command.

```powershell
python .\engine\python\workflow\workflow_runner.py register-deliverable --project-root $projectRoot --run-id "<RunId>" --path "src\result.py"
python .\engine\python\workflow\workflow_runner.py register-artifact --run-dir "$projectRoot\outputs\workflows\<RunId>" --artifact-id final_report --type document --role generated_output --path "$projectRoot\working\report.md" --source-step final_generation --final
```

`--final` accepts a project-owned file only. It never overwrites a different file with the same name; parallel collisions receive a deterministic Run-based suffix. Project-native code or directory structures remain in their native location and continue to use `register-deliverable` plus project-reference registration.

Choose final files deliberately. A representative guide, report, or package may be promoted to `deliverables`; supporting notes, evidence logs, source fragments, and intermediate files stay in their working structure as `generated_output` or `project_reference`. Do not promote every file in a working directory merely to satisfy a completion gate.

## Read-Only Inventory And Migration Dry-Run

Inventory and dry-run never initialize or change the source workspace. Dry-run performs no copy, move, delete, or overwrite. Every requested inventory/JSON/Markdown output must resolve outside `source-root`; all destinations are rejected before inventory begins if any one is inside the source tree.

```powershell
python .\engine\python\workflow\workflow_runner.py inventory --source-root "<legacy workspace>" --json-output "<inventory.json>"
python .\engine\python\workflow\workflow_runner.py migration-dry-run --source-root "<legacy workspace>" --project-root "<planned ProjectRoot>" --inventory-output "<inventory.json>" --json-output "<plan.json>" --markdown-output "<migration_dry_run_report.md>"
```

## Inspect Workflow

현재 상태와 다음 행동을 확인한다.

```powershell
python .\engine\python\workflow\workflow_runner.py inspect --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py status --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py next --run-dir .\outputs\workflows\<run_id>
```

주요 상태 파일:

```text
workflow_manifest.json
workflow_status.json
workflow_next.json
agent_todo.md
```

## Agent Fill Points

에이전트는 workflow가 생성한 request JSON을 읽고 filled JSON을 채운다.

| Stage | Agent fills |
|---|---|
| `01_input_structuring` | `01_input_structuring/data/user_input_analysis_filled.json` |
| `02_router` | `02_router/data/facet_router_filled.json` |
| `04_direction_lens` | `04_direction_lens/data/direction_lens_filled.json` |
| `05_situation_context` | `05_situation_context/data/situation_context_filled.json` |
| `07_fulfillment` | `07_fulfillment/data/contract_filled.json`, `evidence_filled.json` |
채울 때의 기본 원칙:

- 허용된 filled JSON만 수정한다.
- 명시 근거와 추론 근거를 분리한다.
- 근거가 부족하면 `unknown`, `unresolved`, `missing_context`로 남긴다.
- placeholder 파일은 실제 filled JSON으로 교체한 뒤 검증한다.

## Execution Flow

일반 실행 흐름은 아래 순서를 따른다.

```powershell
python .\engine\python\workflow\workflow_runner.py validate-route --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py build-direction --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py validate-direction --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py build-context --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py validate-context --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py build-report --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py build-fulfillment --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py validate-fulfillment --run-dir .\outputs\workflows\<run_id>
```

권장 사용 방식:

1. `next`로 다음 행동을 확인한다.
2. 필요한 filled JSON을 채운다.
3. 해당 validation 명령을 실행한다.
4. 통과하면 다음 build 명령으로 넘어간다.
5. 분석이 끝나면 `build-report`를 실행한다.
6. `build-fulfillment` 후 contract에 `finalization_mode`를 기록하고, `contract_request.json.source_requirements`의 모든 ID를 acceptance criteria의 `source_requirement_ids`에 연결한다. standalone 파일은 `register-artifact --final`로 확정한 뒤 evidence JSON을 채운다.
7. `validate-fulfillment`가 통과해 `request_completed`가 된 경우에만 완료로 보고한다.

## Stop Conditions

다음 경우에는 다음 단계로 진행하지 않는다.

- validation이 실패한 경우
- filled JSON이 placeholder 상태인 경우
- `01_input_structuring`이 채워지지 않은 경우
- 요청 결과물이 없거나 이행 근거가 등록되지 않은 경우
- `validate-fulfillment`가 실패한 경우
- `SOURCE_REQUIREMENT_COVERAGE_INCOMPLETE` 또는 `ACCEPTANCE_SOURCE_LINK_REQUIRED`가 발생한 경우
- route가 필요한데 `route_status`가 `unresolved`인 경우
- high-risk 요청인데 안전한 route 또는 risk lens가 없는 경우
- 핵심 판단 근거가 부족한데 진행 route를 강제로 선택해야 하는 경우

멈춘 경우에는 `workflow_next.json`과 validation report를 확인한다.

## Output Layout

Workflow run은 아래 구조를 가진다.

```text
outputs/workflows/<run_id>/
  agent_todo.md
  workflow_manifest.json
  workflow_status.json
  workflow_next.json
  artifacts_manifest.json
  continuation_state.json  # optional; created only when continuation tracking starts
  assets/
    images/generated/
    images/references/
    prompts/
    documents/
    other/
  00_source/
  01_input_structuring/
  02_router/
  03_route_validation/
  04_direction_lens/
  05_situation_context/
  06_human_readable_report/
  07_fulfillment/
```

각 stage는 일반적으로 `data/`와 `outputs/`를 가진다.

```text
<stage>/
  data/
  outputs/
```

## Workflow Artifacts

Workflow reports and JSON files are not enough when the run produces external files such as images, prompts, documents, or reference assets.

Each workflow run includes an artifact binding area:

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

Rule:

- A generated file is not an official workflow artifact until it appears in `artifacts_manifest.json`.
- Editable work stays in the project workspace and is registered as `project_reference` by default.
- Use `--final` for standalone final files; it creates both the managed `deliverables` copy and the Run milestone snapshot.
- Use `--snapshot` for approved checkpoints that do not need managed final-deliverable promotion.
- Temporary chat, clipboard, app, or CLI files are unstable unless registered into the run.

Register an existing file:

```powershell
python .\engine\python\workflow\workflow_runner.py register-artifact --run-dir .\outputs\workflows\<run_id> --artifact-id first_character_image --type image --role generated_output --path "<external image path>" --source-step image_generation
python .\engine\python\workflow\workflow_runner.py register-artifact --run-dir .\outputs\workflows\<run_id> --artifact-id approved_character_image --type image --role approved_output --path "<approved image path>" --source-step user_approval --snapshot
python .\engine\python\workflow\workflow_runner.py register-artifact --run-dir .\outputs\workflows\<run_id> --artifact-id final_report --type document --role generated_output --path "<project working file>" --source-step final_generation --final
```

The first command records a project reference when the file is inside `ProjectRoot`. If a `generated_output` comes from an external tool directory such as Gemini's cache, it is copied into the Run `assets/` automatically and the original source path is retained as provenance. The second copies an approved checkpoint into `assets/`. The third promotes a standalone final file into `deliverables` and creates its matching milestone snapshot.

For user-facing final files, `role=final_output` is equivalent to requesting `--final`: it creates the project `deliverables` copy and the matching Run snapshot. A governed `continue-run` records `delivery_policy=required` by default, so `complete-continuation` is rejected while any active output artifact lacks a registered DeliverablePath. Use `--allow-internal-only` only for an explicitly internal continuation with no durable user file.

## Continuation State

이어 작업 상태는 새 workspace 저장소를 만들지 않고 다음 run-local sidecar에 둔다.

```text
outputs/workflows/<run_id>/continuation_state.json
```

이 방식은 기존 `run_id`, `--run-dir`, `workflow_manifest.json`, `artifacts_manifest.json` 경계를 재사용한다. `continuation_state.json`은 산출물 파일이 아니라 “다음에 무엇을 할지”를 기록하는 상태이며, `active_artifacts`에는 파일 경로를 복제하지 않고 등록된 `(source_run_id, artifact_id)`를 참조한다. Version 0.2는 프로젝트 우선 작업 위치, 요청 범위 완료 게이트, 위험도 기반 배포 승인, 단계별 시간을 기록한다. Version 0.1은 계속 읽을 수 있고 `migrate-continuation`으로 승격한다.

Lifecycle:

```text
candidate_review -> asset_generation -> awaiting_user_review -> approved
approved -> completed                         # approval-scoped request
approved -> deployment_ready -> deployment -> deployed -> completed
```

후보 시트 예시:

- 한 장의 후보 시트 이미지는 artifact 1개다.
- A~E는 state 안의 논리 후보 5개다.
- `index_rule.order`가 `left_to_right`이면 `3` 또는 `3번`은 C, `B` 또는 `B 후보`는 B로 해석한다.
- `next_actions`는 `revise_candidate`, `generate_standalone_image`, `build_codex_pet`처럼 후속 작업을 구분한다.

```powershell
python .\engine\python\workflow\workflow_runner.py init-continuation --run-dir .\outputs\workflows\<run_id> --current-phase candidate_review --working-root "<project path>" --completion-gate approved --active-artifact-id slime_candidate_sheet --candidate-artifact-id slime_candidate_sheet --candidate-count 5 --candidate-label A --candidate-label B --candidate-label C --candidate-label D --candidate-label E --next-action revise_candidate --next-action generate_standalone_image --next-action build_codex_pet
python .\engine\python\workflow\workflow_runner.py inspect-continuation --run-dir .\outputs\workflows\<run_id>
python .\engine\python\workflow\workflow_runner.py select-candidate --run-dir .\outputs\workflows\<run_id> --candidate "3번" --action revise_candidate
python .\engine\python\workflow\workflow_runner.py record-continuation-result --run-dir .\outputs\workflows\<run_id> --artifact-id approved_character_image --action revise_candidate
python .\engine\python\workflow\workflow_runner.py approve-continuation --run-dir .\outputs\workflows\<run_id>
```

기존 v0.1 상태를 처음 수정해야 할 때는 먼저 명시적으로 v0.2로 승격한다.

```powershell
python .\engine\python\workflow\workflow_runner.py migrate-continuation --run-dir .\outputs\workflows\<run_id> --working-root "<project path>" --completion-gate approved
```

`working_root`는 실제 존재하는 프로젝트 디렉터리여야 한다. 잘못된 경로가 기록됐으면 JSON을 직접 수정하지 않고 다음 명령으로 복구한다.

```powershell
python .\engine\python\workflow\workflow_runner.py set-continuation-workspace --run-dir .\outputs\workflows\<run_id> --working-root "<existing project path>" --note "Correct stale project workspace"
```

배포가 요청 범위에 포함되면 `--completion-gate deployed`로 초기화하고 승인 후 다음 명령을 사용한다.

```powershell
python .\engine\python\workflow\workflow_runner.py start-deployment --run-dir .\outputs\workflows\<run_id> --target "<deployment target>" --confirmed
python .\engine\python\workflow\workflow_runner.py record-deployment --run-dir .\outputs\workflows\<run_id>
```

`timing`은 agent work, user review, deployment 시간을 분리하며 `elapsed_seconds`는 전체 경과 시간이다. 유효한 continuation이 있으면 해당 lifecycle이 `workflow_status.json`, `workflow_next.json`, 사람용 보고서의 최상위 상태를 소유한다.

## Human Report

검증된 workflow 결과는 사람이 읽는 보고서로 생성할 수 있다.

```powershell
python .\engine\python\workflow\workflow_runner.py build-report --run-dir .\outputs\workflows\<run_id>
```

보고서 위치:

```text
outputs/workflows/<run_id>/06_human_readable_report/reports/human_readable_report.md
```

요약 JSON 위치:

```text
outputs/workflows/<run_id>/06_human_readable_report/data/report_summary.json
```

보고서는 새 추론을 만들지 않는다.
기존 workflow JSON과 validation 결과를 사람이 읽기 쉬운 형태로 정리한다.
보고서는 `artifacts_manifest.json`, 프로젝트 산출물 관계, 후속 입력 기록, fulfillment contract/evidence/validation을 함께 보여준다.
`build-report` 직후의 보고서는 분석 완료 보고서일 수 있으며, `validate-fulfillment` 통과 후 자동 재생성된 보고서만 요청 완료를 증명한다.

## Korean Report Check

한국어 보고서가 터미널에서 깨져 보이면 파일 자체가 깨졌다고 바로 판단하지 않는다.

확인 기준:

- Markdown 보고서와 JSON 요약 파일은 UTF-8 파일로 직접 확인한다.
- `Get-Content` 출력이 깨져 보여도 Python UTF-8 read 또는 Markdown viewer로 다시 확인한다.
- `\ufffd` replacement 문자가 없고 한글 문자가 정상 존재하면 산출물 데이터는 정상으로 본다.

권장 확인:

```powershell
python -c "from pathlib import Path; s=Path('<report path>').read_text(encoding='utf-8-sig'); print(repr(s[:200])); print('\\ufffd' in s)"
```

## Artifact Policy

활성 runtime 결과는 `outputs/` 아래에 둔다.

테스트, 데모, 실험, probe 산출물은 `tests/artifacts/` 아래에 둔다.

```text
outputs/workflows/          active workflow runs
outputs/backups/            manual backups
tests/artifacts/test_runs/  generated test outputs
tests/artifacts/workflows/  demo or synthetic workflow runs
```

생성된 산출물은 source code로 취급하지 않는다.
런타임에서 의미 있는 외부 산출물은 해당 run의 `artifacts_manifest.json`에 등록한다.

런타임 외부 파일은 해당 run의 `artifacts_manifest.json`에 등록한다. `continuation_state.json`은 operational state이므로 artifact로 등록하지 않는다.

## Validation Checks

코드나 구조를 변경한 경우 주요 검증은 `agents/agent.md`의 `Validation Trigger`를 따른다.

이 문서는 workflow 실행 런북이므로 테스트 정책을 중복해서 관리하지 않는다.

## Related Documents

| Document | Role |
|---|---|
| `agents/agent.md` | 에이전트 운영 계약 |
| `docs/project_ontology.md` | 프로젝트 구조와 변경 관리 |
| `docs/project_ontology_audit.md` | 온톨로지 운영 점검 기록 |
| `router_design_spec.md` | 라우터 설계 기록 |
| `tests/artifacts/README.md` | 테스트 산출물 보관 기준 |
