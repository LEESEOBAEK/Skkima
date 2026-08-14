export const CLI_EXECUTION_STORAGE_KEY = "skkima.cli-execution.v1";

export const CLI_PLATFORMS = Object.freeze([
  { id: "codex", label: "Codex" },
  { id: "claude", label: "Claude Code" },
  { id: "antigravity", label: "Antigravity" },
]);

export const CLI_APPROVAL_MODES = Object.freeze([
  {
    id: "review",
    label: "직접 확인",
    description: "CLI가 권한을 요청할 때 PowerShell에서 사용자가 확인합니다.",
  },
  {
    id: "auto",
    label: "자동 승인",
    description: "신뢰하는 프로젝트에서 파일 수정과 명령 실행을 자동 승인합니다.",
  },
]);

const TERMINAL_STATUSES = new Set(["completed", "failed", "interrupted", "aborted"]);

function normalizeExecutionProjectPath(path) {
  let value = String(path || "").trim();
  if (value.toLocaleLowerCase().startsWith("\\\\?\\unc\\")) {
    value = `\\\\${value.slice(8)}`;
  } else if (value.startsWith("\\\\?\\")) {
    value = value.slice(4);
  }
  return value
    .replaceAll("/", "\\")
    .replace(/\\+$/, "")
    .toLocaleLowerCase();
}

export function executionRecordKey(projectPath, runId) {
  return `${normalizeExecutionProjectPath(projectPath)}::${String(runId || "")}`;
}

export function normalizeExecutionRecord(value) {
  if (!value || typeof value !== "object") return null;
  const launchId = String(value.launch_id || value.launchId || "").trim();
  const projectRoot = String(value.project_root || value.projectRoot || "").trim();
  const runId = String(value.run_id || value.runId || "").trim();
  const status = String(value.status || "unknown").trim();
  if (!launchId || !projectRoot || !runId) return null;
  return {
    launchId,
    projectRoot,
    runId,
    operationId: String(value.operation_id || value.operationId || "").trim(),
    platform: String(value.platform || "").trim(),
    approvalMode: String(
      value.approval_mode || value.approvalMode || "review",
    ).trim(),
    status,
    processId: Number(value.process_id || value.processId) || null,
    createdAt: value.created_at || value.createdAt || null,
    startedAt: value.started_at || value.startedAt || null,
    finishedAt: value.finished_at || value.finishedAt || null,
    promptPath: value.prompt_path || value.promptPath || null,
    logPath: value.log_path || value.logPath || null,
    statusPath: value.status_path || value.statusPath || null,
    error: value.error || null,
  };
}

export function mergeExecutionRecord(records, value) {
  const normalized = normalizeExecutionRecord(value);
  if (!normalized) return null;
  const matchingLaunch = Object.values(records || {}).find(
    (record) =>
      record?.launchId === normalized.launchId &&
      normalizeExecutionProjectPath(record?.projectRoot) ===
        normalizeExecutionProjectPath(normalized.projectRoot),
  );
  if (matchingLaunch?.runId) {
    normalized.runId = matchingLaunch.runId;
  }
  for (const [key, record] of Object.entries(records || {})) {
    if (
      record?.launchId === normalized.launchId &&
      normalizeExecutionProjectPath(record?.projectRoot) ===
        normalizeExecutionProjectPath(normalized.projectRoot)
    ) {
      delete records[key];
    }
  }
  records[executionRecordKey(normalized.projectRoot, normalized.runId)] =
    normalized;
  return normalized;
}

export function executionIsTerminal(record) {
  return TERMINAL_STATUSES.has(record?.status);
}

export function executionCanRestart(record, run) {
  return Boolean(
    record &&
      ["interrupted", "failed", "aborted"].includes(record.status) &&
      workflowRunCanLaunch(run),
  );
}

export function executionCanStop(record) {
  return Boolean(
    record &&
      ["starting", "running"].includes(record.status) &&
      Number(record.processId) > 0,
  );
}

export function workflowRunIsVerifiedComplete(run) {
  return Boolean(
    run &&
      run.status === "completed" &&
      run.requestCompleted === true &&
      run.validationValid === true,
  );
}

export function executionPresentation(record, run) {
  if (
    record &&
    (record.status === "interrupted" || record.status === "failed") &&
    workflowRunIsVerifiedComplete(run)
  ) {
    return {
      label: "작업 완료",
      tone: "success",
      description: "Workflow 완료 및 검증 통과 후 CLI 연결이 종료되었습니다.",
      reconciled: true,
    };
  }
  return {
    ...executionStatusDefinition(record?.status),
    reconciled: false,
  };
}

function executionRecordTimestamp(record) {
  const numeric = Number(record?.createdAt);
  if (Number.isFinite(numeric)) return numeric;
  const parsed = Date.parse(record?.createdAt || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

export function selectReusableExecutionRecord(
  records,
  projectPath,
  anchorRunId = null,
) {
  const normalizedProjectPath = normalizeExecutionProjectPath(projectPath);
  const candidates = Object.values(records || {})
    .filter(
      (record) =>
        normalizeExecutionProjectPath(record?.projectRoot) ===
          normalizedProjectPath && record?.platform,
    )
    .sort(
      (left, right) =>
        executionRecordTimestamp(right) - executionRecordTimestamp(left),
    );
  return (
    candidates.find((record) => anchorRunId && record.runId === anchorRunId) ??
    candidates[0] ??
    null
  );
}

export function workflowRunCanLaunch(run) {
  return Boolean(run && run.status === "running");
}

export function executionStatusDefinition(status) {
  if (status === "starting") {
    return { label: "CLI 준비 중", tone: "warning", description: "PowerShell 실행 창을 준비하고 있습니다." };
  }
  if (status === "running") {
    return { label: "CLI 실행 중", tone: "running", description: "선택한 AI CLI가 이 Run을 처리하고 있습니다." };
  }
  if (status === "completed") {
    return { label: "CLI 종료", tone: "success", description: "CLI 프로세스가 정상 종료되어 Run 기록을 다시 읽었습니다." };
  }
  if (status === "aborted") {
    return {
      label: "CLI stopped",
      tone: "danger",
      description: "The CLI process was stopped by the user.",
    };
  }  if (status === "failed") {
    return { label: "CLI 실패", tone: "danger", description: "CLI 프로세스가 오류로 종료되었습니다." };
  }
  if (status === "interrupted") {
    return { label: "CLI 중단", tone: "danger", description: "실행 창이 결과 기록 없이 종료되었습니다." };
  }
  return { label: "CLI 실행", tone: "neutral", description: "AI CLI를 선택해 준비된 Run 작업을 시작합니다." };
}

export function loadExecutionRecords(storage) {
  try {
    const parsed = JSON.parse(storage.getItem(CLI_EXECUTION_STORAGE_KEY) || "{}");
    return Object.fromEntries(
      Object.entries(parsed)
        .map(([, value]) => normalizeExecutionRecord(value))
        .filter(Boolean)
        .map((value) => [
          executionRecordKey(value.projectRoot, value.runId),
          value,
        ]),
    );
  } catch {
    return {};
  }
}

export function saveExecutionRecords(storage, records) {
  storage.setItem(CLI_EXECUTION_STORAGE_KEY, JSON.stringify(records));
}
