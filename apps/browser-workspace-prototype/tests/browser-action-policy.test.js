import test from "node:test";
import assert from "node:assert/strict";

import {
  approveBrowserClick,
  browserActionContextMatches,
  canProposeBrowserClick,
  clickProposalSummary,
  createBrowserClickProposal,
  transitionBrowserAction,
} from "../src/browser-action-policy.js";

test("missing project and session selections use one stable null context", () => {
  assert.equal(
    browserActionContextMatches(
      { projectId: undefined, sessionId: undefined },
      { projectId: null, sessionId: null },
    ),
    true,
  );
  assert.equal(
    browserActionContextMatches(
      { projectId: "project-1", sessionId: "session-1" },
      { projectId: "project-2", sessionId: "session-1" },
    ),
    false,
  );
});

const evidence = {
  title: "Example",
  url: "https://example.com/",
  projectId: "project-1",
  sessionId: "session-1",
};

test("only visible, enabled buttons and links become click proposals", () => {
  assert.equal(canProposeBrowserClick({ kind: "button", label: "계속", order: 0 }), true);
  assert.equal(canProposeBrowserClick({ kind: "link", label: "다음", order: 1 }), true);
  assert.equal(canProposeBrowserClick({ kind: "input", label: "이메일", order: 2 }), false);
  assert.equal(canProposeBrowserClick({ kind: "button", label: "삭제", order: 3, disabled: true }), false);
});

test("click proposals preserve the page and exact control identity", () => {
  const result = createBrowserClickProposal(
    evidence,
    { kind: "button", label: "계속", order: 4 },
    evidence,
  );
  assert.equal(result.ok, true);
  assert.equal(result.proposal.controlIndex, 4);
  assert.equal(result.proposal.pageUrl, evidence.url);
  assert.equal(result.proposal.state, "proposed");
  assert.match(clickProposalSummary(result.proposal), /계속/);
});

test("approval is explicit and defaults to one action", () => {
  const proposal = createBrowserClickProposal(evidence, {
    kind: "button",
    label: "계속",
    order: 0,
  }).proposal;
  const approved = approveBrowserClick(proposal);
  assert.equal(approved.ok, true);
  assert.equal(approved.approval.approvalScope, "once");
  assert.equal(approved.approval.plan.controlIndex, 0);
});

test("browser action transitions reject skipping approval", () => {
  const proposal = createBrowserClickProposal(evidence, {
    kind: "button",
    label: "계속",
    order: 0,
  }).proposal;
  assert.equal(transitionBrowserAction(proposal, "executing").ok, false);
  const approved = transitionBrowserAction(proposal, "approved");
  assert.equal(approved.ok, true);
  assert.equal(transitionBrowserAction(approved.proposal, "executing").ok, true);
  assert.equal(transitionBrowserAction(approved.proposal, "succeeded").ok, false);
});
