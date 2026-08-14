import test from "node:test";
import assert from "node:assert/strict";

import {
  buildWorkflowActivity,
  createWorkflowSurface,
  formatWorkflowBytes,
  workflowErrorSurfaceCode,
  workflowErrorSurfaceLabel,
  workflowEvidenceLabel,
  workflowNextActionLabel,
  workflowRelationLabel,
  workflowStatusLabel,
  workflowValidationLabel,
} from "../src/workflow-read-model.js";

const completedRun = {
  runId: "2026-07-28_151437__sample__95d41016",
  shortId: "95d41016",
  displayTitle: "AI 도구 블로그 운영안 설계",
  createdAt: "2026-07-28T15:14:37",
  status: "completed",
  requestCompleted: true,
  validationValid: true,
  nextRequiredAction: "none",
};

test("completed workflow run uses concise Korean operational labels", () => {
  assert.equal(workflowStatusLabel(completedRun), "완료");
  assert.equal(workflowValidationLabel(completedRun), "통과");
  assert.equal(workflowNextActionLabel(completedRun), "없음");
});

test("running continuation never displays the previous completion or validation result", () => {
  const continuationRun = {
    status: "running",
    workflowState: "continuation_running",
    requestCompleted: true,
    validationValid: true,
    nextRequiredAction: "none",
  };

  assert.equal(workflowStatusLabel(continuationRun), "진행 중");
  assert.equal(workflowValidationLabel(continuationRun), "대기 중");
  assert.equal(workflowNextActionLabel(continuationRun), "CLI 결과 대기");
});

test("review-pending workflow keeps evidence and user action distinct", () => {
  const reviewRun = {
    status: "waiting_user",
    workflowState: "continuation_waiting_user",
    requestCompleted: false,
    validationValid: false,
    evidenceStatus: "insufficient",
    nextRequiredAction: "실측 기록 제공",
  };

  assert.equal(workflowStatusLabel(reviewRun), "검토 대기");
  assert.equal(workflowValidationLabel(reviewRun), "미통과");
  assert.equal(workflowEvidenceLabel(reviewRun), "근거 부족");
  assert.equal(workflowNextActionLabel(reviewRun), "실측 기록 제공");
});

test("failure and retry states keep operational labels stable", () => {
  assert.equal(
    workflowStatusLabel({
      status: "failed",
      requestCompleted: false,
    }),
    "실패",
  );
  assert.equal(
    workflowRelationLabel({
      relationType: "retry",
      parentRunId: "failed-run",
    }),
    "재시도",
  );
});

test("error surface distinguishes presentation failures from data validation", () => {
  const run = {
    errorSurface: {
      category: "presentation_failure",
      stage: "cli_output",
      code: "CLI_OUTPUT_ENCODING_FAILED",
    },
  };

  assert.equal(workflowErrorSurfaceLabel(run), "표시·출력 문제");
  assert.equal(
    workflowErrorSurfaceCode(run),
    "CLI_OUTPUT_ENCODING_FAILED · cli_output",
  );
});

test("branch workflow explains its relationship without hiding the source run", () => {
  assert.equal(workflowRelationLabel({ relationType: "branch" }), "분기");
  assert.equal(
    workflowRelationLabel({ relationType: "continuation" }),
    "이어가기",
  );
  assert.equal(workflowRelationLabel({ relationType: "independent" }), "독립 작업");
});

test("workflow surface keeps raw identifiers secondary to the session title", () => {
  const surface = createWorkflowSurface(
    { id: "project-1", name: "test4" },
    { id: "session-1", title: "AI 도구 블로그 운영안 설계" },
    completedRun,
  );

  assert.equal(surface.title, "AI 도구 블로그 운영안 설계");
  assert.equal(surface.runId, completedRun.runId);
  assert.match(surface.description, /test4/);
  assert.match(surface.description, /95d41016/);
});

test("workflow activity preserves recorded request and deliverable order", () => {
  const activity = buildWorkflowActivity({
    createdAt: "2026-07-28T15:14:37",
    sourceText: "최초 문제 상황",
    supplementalInputs: [
      {
        text: "기능을 추가해줘.",
        recordedAt: "2026-07-28T15:30:34+09:00",
      },
    ],
    deliverables: [
      {
        path: "deliverables/result.md",
        recordedAt: "2026-07-28T15:20:01+09:00",
      },
    ],
  });

  assert.deepEqual(
    activity.map((entry) => [entry.kind, entry.label]),
    [
      ["request", "최초 요청"],
      ["result", "산출물 등록"],
      ["request", "이어가기 요청"],
    ],
  );
});

test("workflow byte labels stay concise", () => {
  assert.equal(formatWorkflowBytes(3937), "3.8 KB");
  assert.equal(formatWorkflowBytes(null), "크기 기록 없음");
});
