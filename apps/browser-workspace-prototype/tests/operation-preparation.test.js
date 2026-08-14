import test from "node:test";
import assert from "node:assert/strict";
import {
  buildOperationReview,
  operationKindDefinition,
  operationRequiresAnchor,
  validateOperationDraft,
} from "../src/operation-preparation.js";

const completeDraft = {
  operationKind: "independent",
  taskTitle: "주간 지표 자동화",
  currentSituation: "반복 집계 작업을 새 Workflow로 시작한다.",
  anchorRunId: "",
};

test("independent work does not require an anchor Run", () => {
  assert.equal(operationRequiresAnchor("independent"), false);
  assert.equal(validateOperationDraft(completeDraft), null);
});

test("continuation and branch work require a valid anchor Run", () => {
  for (const operationKind of ["continuation", "branch"]) {
    assert.equal(operationRequiresAnchor(operationKind), true);
    assert.match(
      validateOperationDraft({ ...completeDraft, operationKind }, ["run-1"]),
      /기준 Run/,
    );
    assert.equal(
      validateOperationDraft(
        { ...completeDraft, operationKind, anchorRunId: "run-1" },
        ["run-1"],
      ),
      null,
    );
  }
});

test("the review keeps the human label and exact anchor identity", () => {
  const review = buildOperationReview(
    { ...completeDraft, operationKind: "branch", anchorRunId: "run-1" },
    { runId: "run-1", displayTitle: "기준 분석" },
  );
  assert.equal(operationKindDefinition("branch").label, "분기");
  assert.equal(review.operationLabel, "분기");
  assert.equal(review.anchorRunId, "run-1");
  assert.equal(review.anchorTitle, "기준 분석");
});

test("unknown operation kinds are rejected instead of inferred", () => {
  assert.match(
    validateOperationDraft({ ...completeDraft, operationKind: "automatic" }),
    /작업 방식/,
  );
});
