import test from "node:test";
import assert from "node:assert/strict";

import {
  addOrActivateProject,
  archiveTaskSession,
  deleteArchivedTaskSessions,
  listArchivedTaskSessions,
  loadWorkspaceState,
  restoreTaskSession,
  selectSession,
  syncWorkflowProject,
} from "../src/sidebar-state.js";
import {
  executionIsTerminal,
  executionRecordKey,
  loadExecutionRecords,
  saveExecutionRecords,
  workflowRunCanLaunch,
} from "../src/cli-execution-state.js";
import {
  createWorkflowSurface,
  workflowStatusLabel,
  workflowValidationLabel,
} from "../src/workflow-read-model.js";

function workflowRun(overrides) {
  return {
    runId: overrides.runId,
    shortId: overrides.runId,
    displayTitle: overrides.displayTitle,
    createdAt: overrides.createdAt,
    updatedAt: overrides.updatedAt ?? overrides.createdAt,
    status: overrides.status,
    workflowState: overrides.workflowState ?? null,
    requestCompleted: overrides.requestCompleted ?? false,
    validationValid: overrides.validationValid ?? null,
    deliverables: overrides.deliverables ?? [],
  };
}

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

test("bundle 4 keeps mixed Run states, restart recovery, and archive decisions consistent", () => {
  const projectId = "project-bundle-4";
  const projectPath =
    "C:\\Users\\tester\\Desktop\\operations\\2026\\marketing\\weekly-report";
  const runs = [
    workflowRun({
      runId: "run-running",
      displayTitle: "주간 보고서 생성",
      createdAt: "2026-07-30T10:00:00+09:00",
      status: "running",
    }),
    workflowRun({
      runId: "run-review",
      displayTitle: "검토가 필요한 보고서",
      createdAt: "2026-07-30T09:00:00+09:00",
      status: "waiting_user",
      workflowState: "awaiting_user_review",
    }),
    workflowRun({
      runId: "run-failed",
      displayTitle: "입력 파일 누락",
      createdAt: "2026-07-30T08:00:00+09:00",
      status: "failed",
      validationValid: false,
    }),
    workflowRun({
      runId: "run-completed",
      displayTitle: "완료된 운영안",
      createdAt: "2026-07-30T07:00:00+09:00",
      status: "completed",
      requestCompleted: true,
      validationValid: true,
      deliverables: [{ path: "deliverables/final.md" }],
    }),
  ];
  const snapshot = {
    projectName: "운영 통합 시뮬레이션",
    contractFound: true,
    workspaceId: "workspace-bundle-4",
    schemaVersion: "1.0.0",
    runsRoot: "outputs/workflows",
    warnings: [],
    runs,
  };

  let state = addOrActivateProject(
    loadWorkspaceState(null),
    projectPath,
    100,
    projectId,
  );
  state = syncWorkflowProject(state, projectId, snapshot, 110);
  assert.equal(state.projects[0].sessions.length, 4);

  state = selectSession(state, projectId, "workflow:run-running", 120);
  const runningSession = state.projects[0].sessions.find(
    (session) => session.runId === "run-running",
  );
  const runningSurface = createWorkflowSurface(
    state.projects[0],
    runningSession,
    runs[0],
  );
  assert.equal(runningSurface.runId, "run-running");
  assert.equal(workflowStatusLabel(runs[0]), "진행 중");
  assert.equal(workflowStatusLabel(runs[1]), "검토 대기");
  assert.equal(workflowStatusLabel(runs[2]), "실패");
  assert.equal(workflowStatusLabel(runs[3]), "완료");
  assert.equal(workflowValidationLabel(runs[2]), "미통과");
  assert.equal(workflowValidationLabel(runs[3]), "통과");

  state = archiveTaskSession(state, projectId, "workflow:run-failed", 130);
  const restarted = loadWorkspaceState(JSON.stringify(state));
  state = syncWorkflowProject(restarted, projectId, snapshot, 140);
  assert.deepEqual(
    listArchivedTaskSessions(state).map(({ session }) => session.runId),
    ["run-failed"],
  );

  state = restoreTaskSession(state, projectId, "workflow:run-failed", 150);
  assert.equal(listArchivedTaskSessions(state).length, 0);

  state = archiveTaskSession(state, projectId, "workflow:run-completed", 160);
  state = deleteArchivedTaskSessions(state, [
    { projectId, sessionId: "workflow:run-completed" },
  ]);
  state = syncWorkflowProject(state, projectId, snapshot, 170);
  assert.equal(
    state.projects[0].sessions.some(
      (session) => session.runId === "run-completed",
    ),
    false,
  );
});

test("bundle 4 restores CLI tracking without relaunching terminal Run states", () => {
  const storage = memoryStorage();
  const projectPath = "C:\\Work\\bundle-4";
  const runningKey = executionRecordKey(projectPath, "run-running");
  const completedKey = executionRecordKey(projectPath, "run-completed");
  const records = {
    [runningKey]: {
      launchId: "launch-running",
      projectRoot: projectPath,
      runId: "run-running",
      operationId: "operation-running",
      platform: "codex",
      status: "running",
    },
    [completedKey]: {
      launchId: "launch-completed",
      projectRoot: projectPath,
      runId: "run-completed",
      operationId: "operation-completed",
      platform: "claude",
      status: "completed",
    },
  };

  saveExecutionRecords(storage, records);
  const restored = loadExecutionRecords(storage);
  assert.equal(restored[runningKey].platform, "codex");
  assert.equal(executionIsTerminal(restored[runningKey]), false);
  assert.equal(executionIsTerminal(restored[completedKey]), true);
  assert.equal(
    workflowRunCanLaunch({ status: "running", requestCompleted: false }),
    true,
  );
  assert.equal(
    workflowRunCanLaunch({ status: "completed", requestCompleted: true }),
    false,
  );
  assert.equal(
    workflowRunCanLaunch({ status: "failed", requestCompleted: false }),
    false,
  );
});
