export const OPERATION_KINDS = Object.freeze([
  {
    id: "independent",
    label: "새 작업",
    description: "기존 Run과 분리된 새 Workflow Run을 만듭니다.",
  },
  {
    id: "continuation",
    label: "이어가기",
    description: "기존 Run에 새 요청과 Operation을 이어서 기록합니다.",
  },
  {
    id: "branch",
    label: "분기",
    description: "기존 Run을 기준으로 별도의 새 Workflow Run을 만듭니다.",
  },
]);

const OPERATION_KIND_IDS = new Set(OPERATION_KINDS.map((item) => item.id));

export function operationKindDefinition(kind) {
  return OPERATION_KINDS.find((item) => item.id === kind) ?? OPERATION_KINDS[0];
}

export function operationRequiresAnchor(kind) {
  return kind === "continuation" || kind === "branch";
}

export function validateOperationDraft(draft, availableRunIds = []) {
  const kind = String(draft?.operationKind || "").trim();
  const taskTitle = String(draft?.taskTitle || "").trim();
  const currentSituation = String(draft?.currentSituation || "").trim();
  const anchorRunId = String(draft?.anchorRunId || "").trim();

  if (!OPERATION_KIND_IDS.has(kind)) return "작업 방식을 선택해 주세요.";
  if (!taskTitle) return "작업 제목을 입력해 주세요.";
  if (taskTitle.length > 120) return "작업 제목은 120자 이하여야 합니다.";
  if (!currentSituation) return "현재 상황을 입력해 주세요.";
  if (operationRequiresAnchor(kind) && !anchorRunId) {
    return "이어가기와 분기는 기준 Run을 선택해야 합니다.";
  }
  if (
    operationRequiresAnchor(kind) &&
    availableRunIds.length > 0 &&
    !availableRunIds.includes(anchorRunId)
  ) {
    return "선택한 기준 Run을 현재 프로젝트에서 찾지 못했습니다.";
  }
  return null;
}

export function buildOperationReview(draft, anchorRun = null) {
  const definition = operationKindDefinition(draft.operationKind);
  return {
    operationKind: definition.id,
    operationLabel: definition.label,
    operationDescription: definition.description,
    taskTitle: String(draft.taskTitle || "").trim(),
    currentSituation: String(draft.currentSituation || "").trim(),
    anchorRunId: operationRequiresAnchor(definition.id)
      ? String(draft.anchorRunId || "").trim()
      : null,
    anchorTitle: operationRequiresAnchor(definition.id)
      ? String(anchorRun?.displayTitle || anchorRun?.runId || "")
      : null,
  };
}
