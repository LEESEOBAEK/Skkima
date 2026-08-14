const CLICKABLE_KINDS = new Set(["button", "link"]);
const APPROVAL_SCOPES = new Set(["once", "session"]);
const TERMINAL_STATES = new Set(["succeeded", "failed", "blocked", "cancelled"]);

function compactText(value, maxLength = 120) {
  return String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

export function normalizeBrowserActionContext(context = {}) {
  return {
    projectId: compactText(context.projectId, 120) || null,
    sessionId: compactText(context.sessionId, 120) || null,
  };
}

export function browserActionContextMatches(context = {}, proposal = {}) {
  const current = normalizeBrowserActionContext(context);
  const expected = normalizeBrowserActionContext(proposal);
  return current.projectId === expected.projectId && current.sessionId === expected.sessionId;
}

export function canProposeBrowserClick(control = {}) {
  return (
    CLICKABLE_KINDS.has(control.kind) &&
    control.disabled !== true &&
    compactText(control.label) !== "" &&
    Number.isInteger(control.order) &&
    control.order >= 0 &&
    control.order < 60
  );
}

export function createBrowserClickProposal(evidence = {}, control = {}, context = {}) {
  if (!canProposeBrowserClick(control)) {
    return {
      ok: false,
      reason: "현재 페이지에서 승인 가능한 단일 클릭 대상이 아닙니다.",
    };
  }

  const actionType = "click";
  const risk = control.kind === "link" ? "navigation" : "interaction";
  return {
    ok: true,
    proposal: {
      proposalId: compactText(context.proposalId, 80) || `browser-click-${Date.now().toString(36)}`,
      actionType,
      risk,
      pageTitle: compactText(evidence.title, 160),
      pageUrl: compactText(evidence.url, 500),
      evidenceId: compactText(evidence.evidenceId, 120) || null,
      observationKey: compactText(evidence.observationKey, 400) || null,
      controlIndex: control.order,
      controlKind: control.kind,
      controlLabel: compactText(control.label),
      controlHref: compactText(control.href, 500),
      projectId: compactText(context.projectId, 120) || null,
      sessionId: compactText(context.sessionId, 120) || null,
      state: "proposed",
    },
  };
}

export function normalizeApprovalScope(value) {
  return APPROVAL_SCOPES.has(value) ? value : "once";
}

export function approveBrowserClick(proposal, scope = "once") {
  if (!proposal || proposal.state !== "proposed") {
    return { ok: false, reason: "제안 상태에서만 클릭을 승인할 수 있습니다." };
  }
  return {
    ok: true,
    approval: {
      proposalId: proposal.proposalId,
      approvedAt: new Date().toISOString(),
      approvalScope: normalizeApprovalScope(scope),
      plan: {
        pageUrl: proposal.pageUrl,
        evidenceId: proposal.evidenceId,
        observationKey: proposal.observationKey,
        controlIndex: proposal.controlIndex,
        controlKind: proposal.controlKind,
        controlLabel: proposal.controlLabel,
      },
      state: "approved",
    },
  };
}

export function transitionBrowserAction(proposal, nextState) {
  const allowed = {
    proposed: new Set(["approved", "cancelled"]),
    approved: new Set(["executing", "cancelled"]),
    executing: new Set(["succeeded", "failed", "blocked"]),
  };
  if (TERMINAL_STATES.has(proposal?.state) || !allowed[proposal?.state]?.has(nextState)) {
    return { ok: false, reason: `허용되지 않은 브라우저 작업 상태 전이입니다: ${proposal?.state} -> ${nextState}` };
  }
  return { ok: true, proposal: { ...proposal, state: nextState } };
}

export function clickProposalSummary(proposal = {}) {
  const kindLabel = proposal.controlKind === "link" ? "링크" : "버튼";
  return `${kindLabel} "${proposal.controlLabel || "이름 없음"}"을 ${proposal.pageTitle || proposal.pageUrl || "현재 페이지"}에서 한 번 클릭합니다.`;
}
