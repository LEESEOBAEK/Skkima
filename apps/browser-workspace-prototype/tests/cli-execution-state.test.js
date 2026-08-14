import test from "node:test";
import assert from "node:assert/strict";
import {
  CLI_APPROVAL_MODES,
  executionCanRestart,
  executionCanStop,
  executionIsTerminal,
  executionPresentation,
  executionRecordKey,
  executionStatusDefinition,
  loadExecutionRecords,
  mergeExecutionRecord,
  normalizeExecutionRecord,
  saveExecutionRecords,
  selectReusableExecutionRecord,
  workflowRunCanLaunch,
  workflowRunIsVerifiedComplete,
} from "../src/cli-execution-state.js";

test("normalizes native snake_case execution records", () => {
  const record = normalizeExecutionRecord({
    launch_id: "launch-1",
    project_root: "C:\\work",
    run_id: "run-1",
    operation_id: "op-1",
    process_id: 42,
    platform: "codex",
    approval_mode: "auto",
    status: "running",
  });
  assert.equal(record.launchId, "launch-1");
  assert.equal(record.operationId, "op-1");
  assert.equal(record.processId, 42);
  assert.equal(record.approvalMode, "auto");
});

test("keeps review as the backwards-compatible approval mode", () => {
  const record = normalizeExecutionRecord({
    launch_id: "launch-legacy",
    project_root: "C:\\work",
    run_id: "run-legacy",
    status: "completed",
  });
  assert.deepEqual(CLI_APPROVAL_MODES.map((mode) => mode.id), ["review", "auto"]);
  assert.equal(record.approvalMode, "review");
});

test("classifies terminal execution states", () => {
  assert.equal(executionIsTerminal({ status: "running" }), false);
  for (const status of ["completed", "failed", "interrupted", "aborted"]) {
    assert.equal(executionIsTerminal({ status }), true);
  }
  assert.equal(executionStatusDefinition("failed").tone, "danger");
});

test("an interrupted CLI can restart only while the prepared Run is still launchable", () => {
  const run = { status: "running", requestCompleted: false };
  assert.equal(executionCanRestart({ status: "interrupted" }, run), true);
  assert.equal(executionCanRestart({ status: "failed" }, run), true);
  assert.equal(executionCanRestart({ status: "aborted" }, run), true);
  assert.equal(executionCanRestart({ status: "completed" }, run), false);
  assert.equal(
    executionCanRestart(
      { status: "interrupted" },
      { status: "completed", requestCompleted: true },
    ),
    false,
  );
});

test("only a live CLI record with a process can be stopped", () => {
  assert.equal(executionCanStop({ status: "starting", processId: 1234 }), true);
  assert.equal(executionCanStop({ status: "running", processId: 1234 }), true);
  assert.equal(executionCanStop({ status: "running", processId: null }), false);
  assert.equal(executionCanStop({ status: "completed", processId: 1234 }), false);
  assert.equal(executionCanStop({ status: "aborted", processId: 1234 }), false);
});

test("presents an interrupted CLI as completed when the Workflow is verified", () => {
  const run = {
    status: "completed",
    requestCompleted: true,
    validationValid: true,
  };
  assert.equal(workflowRunIsVerifiedComplete(run), true);
  assert.deepEqual(executionPresentation({ status: "interrupted" }, run), {
    label: "작업 완료",
    tone: "success",
    description: "Workflow 완료 및 검증 통과 후 CLI 연결이 종료되었습니다.",
    reconciled: true,
  });
  assert.equal(executionCanRestart({ status: "interrupted" }, run), false);
});

test("does not hide an interrupted CLI when completion is unverified", () => {
  for (const run of [
    { status: "running", requestCompleted: false, validationValid: null },
    { status: "completed", requestCompleted: true, validationValid: false },
    { status: "completed", requestCompleted: false, validationValid: true },
  ]) {
    const presentation = executionPresentation({ status: "interrupted" }, run);
    assert.equal(presentation.label, "CLI 중단");
    assert.equal(presentation.tone, "danger");
    assert.equal(presentation.reconciled, false);
  }
});

test("only a prepared running Run can start a CLI process", () => {
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
  assert.equal(
    workflowRunCanLaunch({ status: "waiting_user", requestCompleted: false }),
    false,
  );
  assert.equal(
    workflowRunCanLaunch({ status: "running", requestCompleted: true }),
    true,
    "a reused continuation Run is launchable even when its stored summary is stale",
  );
});

test("reuses the anchor CLI settings and falls back to the latest project execution", () => {
  const records = {
    old: {
      projectRoot: "C:\\work",
      runId: "run-1",
      platform: "codex",
      approvalMode: "review",
      createdAt: "10",
    },
    latest: {
      projectRoot: "C:\\work",
      runId: "run-2",
      platform: "claude",
      approvalMode: "auto",
      createdAt: "20",
    },
    otherProject: {
      projectRoot: "C:\\other",
      runId: "run-3",
      platform: "antigravity",
      approvalMode: "auto",
      createdAt: "30",
    },
  };
  assert.equal(
    selectReusableExecutionRecord(records, "c:\\WORK", "run-1").platform,
    "codex",
  );
  assert.equal(
    selectReusableExecutionRecord(records, "C:\\work").platform,
    "claude",
  );
  assert.equal(
    selectReusableExecutionRecord(records, "C:\\other").platform,
    "antigravity",
  );
  assert.equal(selectReusableExecutionRecord(records, "C:\\missing"), null);
});

test("uses a stable project and Run identity key", () => {
  assert.equal(
    executionRecordKey("C:\\Work", "run-1"),
    executionRecordKey("c:\\work", "run-1"),
  );
  assert.equal(
    executionRecordKey("\\\\?\\C:\\Work\\", "run-1"),
    executionRecordKey("c:/work", "run-1"),
  );
});

test("persists only valid execution records", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  saveExecutionRecords(storage, {
    valid: normalizeExecutionRecord({
      launchId: "launch-1",
      projectRoot: "C:\\work",
      runId: "run-1",
      status: "completed",
    }),
  });
  assert.equal(
    loadExecutionRecords(storage)[executionRecordKey("C:\\work", "run-1")]
      .status,
    "completed",
  );
});

test("stored execution keys are rebuilt from normalized project paths", () => {
  const values = new Map([
    [
      "skkima.cli-execution.v1",
      JSON.stringify({
        legacy: {
          launchId: "launch-1",
          projectRoot: "\\\\?\\C:\\Work\\",
          runId: "run-1",
          status: "interrupted",
        },
      }),
    ],
  ]);
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };

  const records = loadExecutionRecords(storage);

  assert.equal(Object.keys(records).length, 1);
  assert.equal(
    records[executionRecordKey("c:/work", "run-1")]?.status,
    "interrupted",
  );
});

test("a recovered launch keeps the prepared Run identity", () => {
  const records = {
    [executionRecordKey("C:\\work", "run-한글")]: {
      launchId: "launch-1",
      projectRoot: "C:\\work",
      runId: "run-한글",
      platform: "codex",
      status: "starting",
    },
  };

  const recovered = mergeExecutionRecord(records, {
    launch_id: "launch-1",
    project_root: "\\\\?\\C:\\work\\",
    run_id: "run-???",
    platform: "codex",
    status: "interrupted",
  });

  assert.equal(recovered.runId, "run-한글");
  assert.equal(recovered.status, "interrupted");
  assert.equal(Object.keys(records).length, 1);
  assert.equal(
    records[executionRecordKey("c:/work", "run-한글")]?.status,
    "interrupted",
  );
});
