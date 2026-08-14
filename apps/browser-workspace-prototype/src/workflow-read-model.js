export function workflowStatusLabel(run) {
  if (run?.status === "running") return "진행 중";
  if (run?.status === "completed") return "완료";
  if (run?.status === "failed") return "실패";
  if (run?.status === "aborted") return "중단";
  if (
    run?.status === "waiting_user" ||
    run?.workflowState === "continuation_waiting_user" ||
    run?.workflowState === "awaiting_user_review"
  ) {
    return "검토 대기";
  }
  return "확인 필요";
}

export function workflowValidationLabel(run) {
  if (run?.status === "running") return "대기 중";
  if (run?.validationValid === true) return "통과";
  if (run?.validationValid === false) return "미통과";
  return "확인 필요";
}

export function workflowNextActionLabel(run) {
  const action = String(run?.nextRequiredAction ?? "").trim();
  if (
    run?.status === "running" &&
    (!action || action.toLowerCase() === "none")
  ) {
    return "CLI 결과 대기";
  }
  if (!action || action.toLowerCase() === "none") return "없음";
  return action;
}

export function workflowEvidenceLabel(run) {
  const status = String(run?.evidenceStatus ?? "").trim().toLowerCase();
  if (status === "sufficient") return "충분";
  if (status === "insufficient") return "근거 부족";
  if (status === "not_required") return "해당 없음";
  return "확인 필요";
}

export function workflowErrorSurfaceLabel(run) {
  const surface = run?.errorSurface;
  if (!surface) return "";
  const categoryLabels = {
    presentation_failure: "표시·출력 문제",
    data_validation_failure: "데이터 검증 실패",
    execution_failure: "실행 실패",
    authority_failure: "권한·거버넌스 문제",
    unknown_failure: "원인 분류 필요",
  };
  return categoryLabels[surface.category] || "원인 분류 필요";
}

export function workflowErrorSurfaceCode(run) {
  const surface = run?.errorSurface;
  if (!surface) return "";
  const stage = String(surface.stage || "").trim();
  const code = String(surface.code || "").trim();
  if (!stage && !code) return "";
  if (!stage) return code;
  if (!code) return stage;
  return `${code} · ${stage}`;
}

export function workflowRelationLabel(run) {
  const relation = String(run?.relationType ?? "").trim().toLowerCase();
  if (relation === "independent") return "독립 작업";
  if (relation === "continuation") return "이어가기";
  if (relation === "branch") return "분기";
  if (relation === "retry") return "재시도";
  if (relation === "comparison") return "비교";
  return relation || "기록 없음";
}

export function formatWorkflowTimestamp(value) {
  if (!value) return "기록 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);

  const parts = new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const part = (type) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}`;
}

export function formatWorkflowBytes(value) {
  if (value === null || value === undefined) return "크기 기록 없음";
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "크기 기록 없음";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function buildWorkflowActivity(run) {
  const activity = [];
  const sourceText = String(run?.sourceText ?? "").trim();
  if (sourceText) {
    activity.push({
      kind: "request",
      label: "최초 요청",
      text: sourceText,
      timestamp: run.createdAt ?? null,
    });
  }

  for (const input of run?.supplementalInputs ?? []) {
    const text = String(input?.text ?? "").trim();
    if (!text) continue;
    activity.push({
      kind: "request",
      label: "이어가기 요청",
      text,
      timestamp: input.recordedAt ?? null,
    });
  }

  for (const deliverable of run?.deliverables ?? []) {
    activity.push({
      kind: "result",
      label: "산출물 등록",
      text: deliverable.path,
      timestamp: deliverable.recordedAt ?? null,
      deliverable,
    });
  }

  return activity.sort((left, right) => {
    if (!left.timestamp && !right.timestamp) return 0;
    if (!left.timestamp) return 1;
    if (!right.timestamp) return -1;
    return String(left.timestamp).localeCompare(String(right.timestamp));
  });
}

export function createWorkflowSurface(project, session, run) {
  if (!project || !session || !run) return null;
  return {
    kind: "workflow-run",
    title: session.title,
    description: `${project.name} · ${formatWorkflowTimestamp(run.createdAt)} · ${run.shortId}`,
    icon: "file-text",
    projectId: project.id,
    sessionId: session.id,
    runId: run.runId,
    run,
  };
}
