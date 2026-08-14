import {
  WORKSPACE_STORAGE_KEY,
  addOrActivateProject,
  applySidebarEvent,
  archiveTaskSession,
  compactProjectPath,
  deleteArchivedTaskSessions,
  listArchivedTaskSessions,
  listPinnedProjects,
  listRecentProjects,
  loadWorkspaceState,
  normalizeProjectPath,
  removeProject,
  renameTaskSession,
  restoreTaskSession,
  selectProject,
  selectSession,
  syncWorkflowProject,
  toggleProjectPin,
} from "./sidebar-state.js";
import { createSelectionGuard } from "./selection-guard.js";
import {
  buildWorkflowActivity,
  createWorkflowSurface,
  formatWorkflowBytes,
  formatWorkflowTimestamp,
  workflowEvidenceLabel,
  workflowErrorSurfaceCode,
  workflowErrorSurfaceLabel,
  workflowNextActionLabel,
  workflowRelationLabel,
  workflowStatusLabel,
  workflowValidationLabel,
} from "./workflow-read-model.js";
import { renderLocalEnvironmentMarkup } from "./local-environment-view.js";
import {
  loadSkillViewMode,
  saveSkillViewMode,
} from "./extension-view-state.js";
import {
  archiveTaskKey,
  filterArchivedTasks,
  formatArchiveTimestamp,
  paginateArchivedTasks,
} from "./archive-view-state.js";
import {
  DEFAULT_SKILL_PLATFORMS,
  buildSkillStatuses,
  filterLibrarySkills,
  filterPlugins,
  platformStatusLabel,
  pluginSkillIsRegistered as isPluginSkillRegistered,
  skillStatusKey,
  smokeTestStateLabel,
  summarizeSkillStatus,
} from "./extension-catalog-state.js";
import {
  OPERATION_KINDS,
  buildOperationReview,
  operationRequiresAnchor,
  validateOperationDraft,
} from "./operation-preparation.js";
import {
  parseResearchSourceLines,
  validateResearchBinding,
} from "./research-source-input.js";
import {
  CLI_APPROVAL_MODES,
  CLI_PLATFORMS,
  executionCanRestart,
  executionCanStop,
  executionIsTerminal,
  executionPresentation,
  executionRecordKey,
  loadExecutionRecords,
  mergeExecutionRecord,
  saveExecutionRecords,
  selectReusableExecutionRecord,
  workflowRunCanLaunch,
} from "./cli-execution-state.js";
import { createBrowserWorkspaceController } from "./browser-workspace-controller.js";
import { buildBrowserPageInsight } from "./browser-page-insight.js";
import { groupBrowserEvidenceHistory } from "./browser-evidence-history.js";
import {
  DEFAULT_PROJECT_MONITOR_INTERVAL_MS,
  DEFAULT_PROJECT_MONITOR_LIMIT,
  monitoredProjectIds,
  shouldRefreshProject,
  recordProjectRefresh,
} from "./project-monitor.js";
import {
  approveBrowserClick,
  browserActionContextMatches,
  clickProposalSummary,
  createBrowserClickProposal,
  transitionBrowserAction,
} from "./browser-action-policy.js";

const workspaceSurface = {
  title: "작업 공간",
  description: "프로젝트 폴더를 열어 작업 세션을 시작하세요.",
  icon: "folder",
};

const extensionHubSurface = {
  kind: "extension-hub",
  title: "플러그인",
  description: "플러그인과 스킬을 한곳에서 관리합니다.",
  icon: "plug",
};

let workspaceState = loadWorkspaceState(
  localStorage.getItem(WORKSPACE_STORAGE_KEY),
);
const workflowSnapshots = new Map();
const INSPECTOR_STORAGE_KEY = "skkima.desktop.inspector.v1";
const BROWSER_PANEL_STORAGE_KEY = "skkima.desktop.browser-panel.v1";

function loadInspectorPreferences() {
  try {
    const parsed = JSON.parse(localStorage.getItem(INSPECTOR_STORAGE_KEY) ?? "{}");
    return {
      width: Number.isFinite(parsed.width) ? parsed.width : 340,
      lastBySession:
        parsed.lastBySession && typeof parsed.lastBySession === "object"
          ? parsed.lastBySession
          : {},
    };
  } catch {
    return { width: 340, lastBySession: {} };
  }
}

const inspectorPreferences = loadInspectorPreferences();

function loadBrowserPanelPreferences() {
  try {
    const parsed = JSON.parse(localStorage.getItem(BROWSER_PANEL_STORAGE_KEY) ?? "{}");
    return { width: Number.isFinite(parsed.width) ? parsed.width : 760 };
  } catch {
    return { width: 760 };
  }
}

const browserPanelPreferences = loadBrowserPanelPreferences();

const uiState = {
  zoom: 1,
  splitView: false,
  browserPanelOpen: false,
  browserFocusMode: false,
  browserPanelWidth: Math.max(440, browserPanelPreferences.width),
  workflowView: "flow",
  history: [workspaceSurface],
  historyIndex: 0,
  sidebar: {
    pinned: workspaceState.sidebar.pinned,
    mode: workspaceState.sidebar.pinned ? "pinned" : "closed",
  },
  inspector: {
    kind: "summary",
    payload: {},
    width: Math.min(520, Math.max(280, inspectorPreferences.width)),
  },
  pendingTaskArchive: null,
  pendingProjectRemoval: null,
  pendingArchiveDeletion: [],
};

const archiveSettingsState = {
  query: "",
  projectId: "all",
  source: "all",
  sort: "newest",
  page: 1,
  pageSize: 20,
  selected: new Set(),
};

const skillSettingsState = {
  projectId: null,
  snapshot: null,
  statuses: new Map(),
  platforms: [],
  loading: false,
  error: "",
};

const skillSmokeTestState = {
  tests: new Map(),
  loading: false,
  error: "",
  pendingPlatform: null,
  pollTimer: null,
  panelOpen: false,
};

const extensionHubState = {
  activeTab: "skills",
  query: "",
  skillView: loadSkillViewMode(localStorage),
};

const pluginLibraryState = {
  snapshot: null,
  loading: false,
  error: "",
  query: "",
  sourceUrl: "",
  pendingRemoval: null,
};

const projectMonitorState = {
  timer: null,
  inFlight: false,
  lastRefreshAt: {},
  results: {},
};

const EXTERNAL_CONNECTION_STORAGE_KEY = "skkima.desktop.external-connection.v1";

function loadExternalConnectionState() {
  try {
    const parsed = JSON.parse(
      localStorage.getItem(EXTERNAL_CONNECTION_STORAGE_KEY) ?? "{}",
    );
    return {
      endpoint:
        typeof parsed.endpoint === "string"
          ? parsed.endpoint
          : "http://127.0.0.1:9222",
      status: "unknown",
      detail: "아직 연결 상태를 확인하지 않았습니다.",
      browser: "",
      websocketDebuggerUrl: "",
      checkedAt: "",
      loading: false,
      error: "",
      latestContext: null,
      contextHistory: [],
      selectedContextKey: "",
      contextLoading: false,
      contextError: "",
      contextFetched: false,
      pendingContextDeletion: null,
      pendingContextClear: false,
      mcpStatus: "unknown",
      mcpLoading: false,
      mcpError: "",
      mcpConnection: null,
      mcpReadResult: null,
      mcpEvidenceSaved: null,
      chromeLaunchLoading: false,
      chromeLaunchResult: null,
    };
  } catch {
    return {
      endpoint: "http://127.0.0.1:9222",
      status: "unknown",
      detail: "아직 연결 상태를 확인하지 않았습니다.",
      browser: "",
      websocketDebuggerUrl: "",
      checkedAt: "",
      loading: false,
      error: "",
      latestContext: null,
      contextHistory: [],
      selectedContextKey: "",
      contextLoading: false,
      contextError: "",
      contextFetched: false,
      pendingContextDeletion: null,
      pendingContextClear: false,
      mcpStatus: "unknown",
      mcpLoading: false,
      mcpError: "",
      mcpConnection: null,
      mcpReadResult: null,
      mcpEvidenceSaved: null,
      chromeLaunchLoading: false,
      chromeLaunchResult: null,
    };
  }
}

const externalConnectionState = loadExternalConnectionState();

const onboardingState = {
  parentRoot: "",
  projectRoot: "",
  selectedPlatforms: [],
  preferredPlatform: "",
  skillTemplateProjectId: "",
  skillCopyResult: null,
  environment: null,
  readiness: null,
};

const operationPreparationState = {
  draft: null,
};

const cliExecutionState = {
  records: loadExecutionRecords(localStorage),
  pollTimer: null,
  polling: false,
};

const selectionGuard = createSelectionGuard();
let sidebarCloseTimer = null;

const elements = {
  body: document.body,
  back: document.querySelector("#history-back"),
  forward: document.querySelector("#history-forward"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  sidebar: document.querySelector("#project-sidebar"),
  sidebarEdge: document.querySelector("#sidebar-edge"),
  pinnedSection: document.querySelector("#pinned-section"),
  pinnedProjects: document.querySelector("#pinned-projects"),
  recentSection: document.querySelector("#recent-section"),
  recentProjects: document.querySelector("#recent-projects"),
  contextTitle: document.querySelector("#context-title"),
  taskCount: document.querySelector("#task-count"),
  contextIdentityIcon: document.querySelector("#context-identity-icon"),
  contextNewTask: document.querySelector(
    ".contextbar-leading > [data-action='new-task']",
  ),
  contextTaskMenu: document.querySelector(".contextbar-leading .context-menu"),
  contextActions: document.querySelector(".contextbar-actions"),
  browserPanelToggle: document.querySelector("#browser-panel-toggle"),
  splitToggle: document.querySelector("#split-toggle"),
  inspectorPane: document.querySelector("#inspector-pane"),
  inspectorResizeHandle: document.querySelector("#inspector-resize-handle"),
  inspectorEyebrow: document.querySelector("#inspector-eyebrow"),
  inspectorTitle: document.querySelector("#inspector-title"),
  inspectorContent: document.querySelector("#inspector-content"),
  emptySurface: document.querySelector("#empty-surface"),
  surfaceIcon: document.querySelector("#surface-icon"),
  surfaceTitle: document.querySelector("#surface-title"),
  surfaceDescription: document.querySelector("#surface-description"),
  emptyActions: document.querySelector("#empty-actions"),
  workflowSurface: document.querySelector("#workflow-surface"),
  extensionHub: document.querySelector("#extension-hub"),
  browserWorkspace: document.querySelector("#browser-workspace"),
  browserResizeHandle: document.querySelector("#browser-resize-handle"),
  browserToolLauncher: document.querySelector("#browser-tool-launcher"),
  browserWebviewMount: document.querySelector("#browser-webview-mount"),
  workflowTitle: document.querySelector("#workflow-title"),
  workflowDescription: document.querySelector("#workflow-description"),
  workflowStatusBadge: document.querySelector("#workflow-status-badge"),
  workflowValidationBadge: document.querySelector(
    "#workflow-validation-badge",
  ),
  workflowLaunchButton: document.querySelector("#workflow-launch-button"),
  workflowExecutionBar: document.querySelector("#workflow-execution-bar"),
  workflowExecutionStatus: document.querySelector("#workflow-execution-status"),
  workflowExecutionMeta: document.querySelector("#workflow-execution-meta"),
  workflowStatus: document.querySelector("#workflow-status"),
  workflowValidation: document.querySelector("#workflow-validation"),
  workflowEvidence: document.querySelector("#workflow-evidence"),
  workflowValidationNeeded: document.querySelector(
    "#workflow-validation-needed",
  ),
  workflowFailureCard: document.querySelector("#workflow-failure-card"),
  workflowFailureReason: document.querySelector("#workflow-failure-reason"),
  workflowErrorSurfaceCard: document.querySelector("#workflow-error-surface-card"),
  workflowErrorSurfaceLabel: document.querySelector("#workflow-error-surface-label"),
  workflowErrorSurfaceCode: document.querySelector("#workflow-error-surface-code"),
  workflowRecoveryCard: document.querySelector("#workflow-recovery-card"),
  workflowRecoveryAction: document.querySelector("#workflow-recovery-action"),
  workflowDeliverableCount: document.querySelector(
    "#workflow-deliverable-count",
  ),
  workflowNextAction: document.querySelector("#workflow-next-action"),
  workflowReviewNote: document.querySelector("#workflow-review-note"),
  workflowSummaryStatus: document.querySelector("#workflow-summary-status"),
  workflowSummaryValidation: document.querySelector(
    "#workflow-summary-validation",
  ),
  workflowSummaryDeliverables: document.querySelector(
    "#workflow-summary-deliverables",
  ),
  workflowSummaryNextAction: document.querySelector(
    "#workflow-summary-next-action",
  ),
  workflowConversation: document.querySelector("#workflow-conversation"),
  workflowFileList: document.querySelector("#workflow-file-list"),
  workflowHistoryList: document.querySelector("#workflow-history-list"),
  modal: document.querySelector("#modal-backdrop"),
  dialog: document.querySelector(".dialog"),
  dialogTitle: document.querySelector("#dialog-title"),
  dialogBody: document.querySelector("#dialog-body"),
  dialogClose: document.querySelector("#dialog-close"),
  localEnvironmentTrigger: document.querySelector("#local-environment-trigger"),
  searchBackdrop: document.querySelector("#search-backdrop"),
  globalSearchInput: document.querySelector("#global-search-input"),
  globalSearchResults: document.querySelector("#global-search-results"),
  surfaceContextMenu: document.querySelector("#surface-context-menu"),
};

const workspaceFrame = document.querySelector(".workspace-frame");
workspaceFrame.insertBefore(elements.browserWorkspace, elements.inspectorPane);
elements.body.style.setProperty(
  "--browser-panel-width",
  `${uiState.browserPanelWidth}px`,
);

const browserController = createBrowserWorkspaceController({
  root: elements.browserWorkspace,
  mount: elements.browserWebviewMount,
  onEvidence: showBrowserPageInsight,
});

function iconPath(name) {
  return `./icons/${name}.svg`;
}

function uniqueId(prefix) {
  const random = crypto.randomUUID?.() ?? Math.random().toString(16).slice(2);
  return `${prefix}_${Date.now()}_${random}`;
}

function activeProject() {
  return (
    workspaceState.projects.find(
      (project) => project.id === workspaceState.activeProjectId,
    ) ?? null
  );
}

function activeSession() {
  const project = activeProject();
  return (
    project?.sessions.find(
      (session) => session.id === workspaceState.activeSessionId,
    ) ?? null
  );
}

function activeWorkflowRun() {
  const project = activeProject();
  const session = activeSession();
  if (!project || session?.source !== "workflow" || !session.runId) return null;
  return (
    workflowSnapshots
      .get(project.id)
      ?.runs.find((run) => run.runId === session.runId) ?? null
  );
}

function executionRecordFor(project, run) {
  if (!project || !run) return null;
  return (
    cliExecutionState.records[executionRecordKey(project.path, run.runId)] ??
    Object.values(cliExecutionState.records).find(
      (record) =>
        record.runId === run.runId &&
        normalizeProjectPath(record.projectRoot) ===
          normalizeProjectPath(project.path),
    ) ??
    null
  );
}

function persistCliExecutionRecords() {
  try {
    saveExecutionRecords(localStorage, cliExecutionState.records);
  } catch (error) {
    console.error("CLI 실행 상태를 저장하지 못했습니다.", error);
  }
}

function rememberCliExecution(record) {
  const normalized = mergeExecutionRecord(cliExecutionState.records, record);
  if (!normalized) return null;
  persistCliExecutionRecords();
  return normalized;
}

async function refreshProjectCliExecutions(project) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!project || !invoke) return [];
  try {
    const records = await invoke("list_workflow_cli_launches", {
      projectRoot: project.path,
    });
    for (const record of records ?? []) {
      rememberCliExecution(record);
    }
    if (
      !cliExecutionState.polling &&
      (records ?? []).some((record) => !executionIsTerminal(record))
    ) {
      scheduleCliExecutionPolling(100);
    }
    return records ?? [];
  } catch (error) {
    console.warn("프로젝트의 CLI 실행 기록을 복구하지 못했습니다.", error);
    return [];
  }
}

function cliPlatformLabel(platform) {
  return CLI_PLATFORMS.find((item) => item.id === platform)?.label ?? platform;
}

function projectCliPreference(project) {
  const platform = String(project?.cliPreference?.platform || "").trim();
  const approvalMode = String(
    project?.cliPreference?.approvalMode || "review",
  ).trim();
  if (!CLI_PLATFORMS.some((item) => item.id === platform)) return null;
  return {
    platform,
    approvalMode: CLI_APPROVAL_MODES.some((item) => item.id === approvalMode)
      ? approvalMode
      : "review",
  };
}

function rememberProjectCliPreference(project, platform, approvalMode = "review") {
  if (!project || !CLI_PLATFORMS.some((item) => item.id === platform)) {
    return;
  }
  project.cliPreference = {
    platform,
    approvalMode: CLI_APPROVAL_MODES.some((item) => item.id === approvalMode)
      ? approvalMode
      : "review",
  };
  persistWorkspaceState();
}

function preferredCliSettings(project, tracked, available) {
  const candidates = [
    tracked && {
      platform: tracked.platform,
      approvalMode: tracked.approvalMode,
    },
    projectCliPreference(project),
  ].filter(Boolean);
  const selected = candidates.find((candidate) =>
    available.some((platform) => platform.id === candidate.platform),
  );
  return selected ?? {
    platform: available[0]?.id || "",
    approvalMode: "review",
  };
}

function persistWorkspaceState() {
  workspaceState.sidebar.pinned = uiState.sidebar.pinned;
  try {
    localStorage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify(workspaceState),
    );
  } catch (error) {
    console.error("작업 공간 상태를 저장하지 못했습니다.", error);
  }
}

function closeMenus() {
  document.querySelectorAll(".menu-popover.open").forEach((panel) => {
    panel.classList.remove("open");
  });
  document.querySelectorAll(".menu-trigger[aria-expanded='true']").forEach(
    (trigger) => {
      trigger.setAttribute("aria-expanded", "false");
    },
  );
}

let surfaceContextState = null;

function closeSurfaceContextMenu() {
  elements.surfaceContextMenu.hidden = true;
  elements.surfaceContextMenu.innerHTML = "";
  surfaceContextState = null;
}

function contextMenuItem(label, action, shortcut = "") {
  return `<button type="button" role="menuitem" data-context-action="${action}">
    <span>${label}</span>
    ${shortcut ? `<kbd>${shortcut}</kbd>` : ""}
  </button>`;
}

function contextMenuSeparator() {
  return '<div class="surface-context-separator" role="separator"></div>';
}

function joinProjectPath(projectPath, relativePath) {
  const value = String(relativePath || "").trim();
  if (!value) return projectPath;
  if (/^(?:[a-zA-Z]:[\\/]|\\\\)/.test(value)) return value;
  return `${String(projectPath || "").replace(/[\\/]+$/, "")}\\${value.replace(/^[\\/]+/, "")}`;
}

function workflowSessionPath(project, session) {
  const relativePath = session?.runId
    ? `outputs\\workflows\\${session.runId}`
    : ".";
  return {
    path:
      relativePath === "."
        ? project?.path ?? ""
        : joinProjectPath(project?.path, relativePath),
    relativePath,
  };
}

function openSurfaceContextMenu(event) {
  const editableTarget = event.target.closest(
    "input, textarea, [contenteditable='true']",
  );
  if (editableTarget) return;

  event.preventDefault();
  closeMenus();
  const scope =
    event.target.closest(
      ".dialog-body, .global-search, .project-sidebar, .workbench, .companion-pane",
    ) ?? document.querySelector(".content-shell");
  const selectedText = window.getSelection()?.toString().trim() ?? "";
  const sessionTarget = event.target.closest(".session-line");
  const projectTarget = event.target.closest(".project-line");
  const deliverableTarget = event.target.closest(
    "[data-context-kind='deliverable']",
  );
  let menuItems = [];

  if (sessionTarget) {
    const project = workspaceState.projects.find(
      (item) => item.id === sessionTarget.dataset.projectId,
    );
    const session = project?.sessions.find(
      (item) => item.id === sessionTarget.dataset.sessionId,
    );
    const sessionPath = workflowSessionPath(project, session);
    surfaceContextState = {
      kind: "session",
      scope,
      projectId: sessionTarget.dataset.projectId,
      sessionId: sessionTarget.dataset.sessionId,
      path: sessionPath.path,
      relativePath: sessionPath.relativePath,
    };
    menuItems = [
      contextMenuItem("작업 열기", "open-session"),
      contextMenuItem("이름 바꾸기", "rename-session"),
      contextMenuSeparator(),
      contextMenuItem("아카이브 보관", "archive-session"),
    ];
    menuItems.splice(
      3,
      0,
      contextMenuItem("파일 탐색기에서 열기", "open-context-in-explorer"),
      contextMenuItem("경로 복사", "copy-context-value"),
      contextMenuItem("상대 경로 복사", "copy-context-relative-path"),
      contextMenuSeparator(),
    );
  } else if (projectTarget) {
    const project = workspaceState.projects.find(
      (item) => item.id === projectTarget.dataset.projectId,
    );
    surfaceContextState = {
      kind: "project",
      scope,
      projectId: projectTarget.dataset.projectId,
      path: project?.path ?? "",
      relativePath: project?.name ?? ".",
    };
    menuItems = [
      contextMenuItem("프로젝트 열기", "open-project"),
      contextMenuItem(
        project?.pinned ? "고정 해제" : "프로젝트 고정",
        "toggle-project-pin",
      ),
      contextMenuSeparator(),
      contextMenuItem("경로 복사", "copy-context-value"),
    ];
    menuItems.splice(
      1,
      0,
      contextMenuItem("파일 탐색기에서 열기", "open-context-in-explorer"),
    );
    menuItems.push(
      contextMenuItem("상대 경로 복사", "copy-context-relative-path"),
      contextMenuSeparator(),
      contextMenuItem("앱 목록에서 제거", "remove-project"),
    );
  } else if (deliverableTarget) {
    const project = activeProject();
    const relativePath = deliverableTarget.dataset.filePath ?? "";
    surfaceContextState = {
      kind: "deliverable",
      scope: deliverableTarget,
      path: joinProjectPath(project?.path, relativePath),
      relativePath,
    };
    menuItems = [
      contextMenuItem("경로 복사", "copy-context-value"),
      contextMenuItem("항목 선택", "select-context-content"),
    ];
    menuItems.unshift(
      contextMenuItem("파일 탐색기에서 열기", "open-context-in-explorer"),
    );
    menuItems.splice(
      2,
      0,
      contextMenuItem("상대 경로 복사", "copy-context-relative-path"),
    );
  } else {
    surfaceContextState = {
      kind: selectedText ? "selection" : "surface",
      scope,
      text: selectedText,
    };
    if (selectedText) {
      menuItems.push(contextMenuItem("복사", "copy-context-value", "Ctrl+C"));
    }
    menuItems.push(
      contextMenuItem("전체 선택", "select-context-content", "Ctrl+A"),
    );
  }

  elements.surfaceContextMenu.innerHTML = menuItems.join("");
  elements.surfaceContextMenu.hidden = false;
  const menuWidth = elements.surfaceContextMenu.offsetWidth;
  const menuHeight = elements.surfaceContextMenu.offsetHeight;
  const edgeGap = 8;
  const left = Math.min(
    event.clientX,
    Math.max(edgeGap, window.innerWidth - menuWidth - edgeGap),
  );
  const top = Math.min(
    event.clientY,
    Math.max(edgeGap, window.innerHeight - menuHeight - edgeGap),
  );
  elements.surfaceContextMenu.style.left = `${Math.max(edgeGap, left)}px`;
  elements.surfaceContextMenu.style.top = `${Math.max(edgeGap, top)}px`;
}

function selectContextContent(scope) {
  if (!scope) return;

  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(scope);
  selection.removeAllRanges();
  selection.addRange(range);
}

async function copyContextValue(value) {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const helper = document.createElement("textarea");
    helper.value = value;
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.append(helper);
    helper.select();
    document.execCommand("copy");
    helper.remove();
  }
}

async function openContextPathInExplorer(path) {
  if (!path) return;
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showDialog(
      "Windows 앱에서 사용할 수 있습니다",
      "<p>파일 탐색기 열기는 쓰끼마 데스크톱 앱에서 지원됩니다.</p>",
    );
    return;
  }
  try {
    await invoke("open_path_in_explorer", { path });
  } catch (error) {
    showDialog(
      "파일 탐색기를 열지 못했습니다",
      `<p>${escapeHtml(error?.message || error || "선택한 경로를 확인해 주세요.")}</p>`,
    );
  }
}

async function handleSurfaceContextAction(action) {
  const state = surfaceContextState;
  closeSurfaceContextMenu();
  if (!state) return;

  if (action === "open-session") {
    await selectSidebarSession(state.projectId, state.sessionId);
  } else if (action === "rename-session") {
    await selectSidebarSession(state.projectId, state.sessionId);
    showRenameDialog();
  } else if (action === "archive-session") {
    requestTaskArchive(state.projectId, state.sessionId);
  } else if (action === "open-project") {
    await selectSidebarProject(state.projectId);
  } else if (action === "remove-project") {
    requestProjectRemoval(state.projectId);
  } else if (action === "toggle-project-pin") {
    workspaceState = toggleProjectPin(workspaceState, state.projectId);
    persistWorkspaceState();
    renderSidebar();
  } else if (action === "copy-context-value") {
    await copyContextValue(state.text || state.path);
  } else if (action === "copy-context-relative-path") {
    await copyContextValue(state.relativePath);
  } else if (action === "open-context-in-explorer") {
    await openContextPathInExplorer(state.path);
  } else if (action === "select-context-content") {
    selectContextContent(state.scope);
  }
}

function openMenu(name) {
  const trigger = document.querySelector(`[data-menu="${name}"]`);
  const panel = document.querySelector(`[data-menu-panel="${name}"]`);
  const shouldOpen = !panel?.classList.contains("open");

  closeMenus();
  if (!shouldOpen || !trigger || !panel) return;

  trigger.setAttribute("aria-expanded", "true");
  panel.classList.add("open");
  panel.querySelector("button")?.focus();
}

function updateHistoryButtons() {
  elements.back.disabled = uiState.historyIndex <= 0;
  elements.forward.disabled =
    uiState.historyIndex >= uiState.history.length - 1;
}

function currentSurface() {
  return uiState.history[uiState.historyIndex] ?? workspaceSurface;
}

function currentProjectSurface() {
  const project = activeProject();
  const session = activeSession();

  if (!project) return workspaceSurface;
  if (!session) {
    return {
      title: project.name,
      description: project.path,
      icon: "folder",
      projectId: project.id,
    };
  }

  const workflowSurface = createWorkflowSurface(
    project,
    session,
    activeWorkflowRun(),
  );
  if (workflowSurface) return workflowSurface;

  return {
    title: session.title,
    description: `${project.name} 프로젝트의 작업 세션`,
    icon: "file-text",
    projectId: project.id,
    sessionId: session.id,
  };
}

function updateContextBar(surface = currentSurface()) {
  const project = activeProject();
  const session = activeSession();
  const sessionCount = project?.sessions.length ?? 0;
  const isExtensionHub = surface.kind === "extension-hub";
  const isUtilitySurface = isExtensionHub;

  elements.contextTitle.textContent = surface.title;
  elements.contextIdentityIcon.src = iconPath(
    isExtensionHub
      ? "plug"
      : session
        ? "file-text"
        : "folder",
  );
  elements.taskCount.textContent = isExtensionHub
    ? `(${skillSettingsState.snapshot?.skills?.length ?? 0})`
    : `(${sessionCount})`;
  elements.taskCount.setAttribute(
    "aria-label",
    isExtensionHub
      ? `사용자 스킬 ${skillSettingsState.snapshot?.skills?.length ?? 0}개`
      : `현재 프로젝트 작업 세션 ${sessionCount}개`,
  );
  elements.contextNewTask.hidden = isUtilitySurface;
  elements.contextTaskMenu.hidden = isUtilitySurface;
  elements.contextActions.hidden = isUtilitySurface;
  if (uiState.splitView) renderInspector();
  elements.emptyActions.dataset.mode = session
    ? "session"
    : project
      ? "project"
      : "empty";
}

function renderWorkflowSurface(surface) {
  const run = surface.run;
  renderWorkflowExecutionStatus(activeProject(), run);
  const status = workflowStatusLabel(run);
  const validation = workflowValidationLabel(run);
  elements.workflowTitle.textContent = surface.title;
  elements.workflowDescription.textContent = surface.description;
  elements.workflowStatus.textContent = status;
  elements.workflowValidation.textContent = validation;
  elements.workflowEvidence.textContent = workflowEvidenceLabel(run);
  elements.workflowDeliverableCount.textContent = String(
    run.deliverables.length,
  );
  const nextAction = workflowNextActionLabel(run);
  elements.workflowNextAction.textContent = nextAction;
  elements.workflowSummaryStatus.textContent = status;
  elements.workflowSummaryValidation.textContent = validation;
  elements.workflowSummaryDeliverables.textContent = `${run.deliverables.length}개`;
  elements.workflowSummaryNextAction.textContent = nextAction;
  elements.workflowReviewNote.textContent =
    run.status === "running"
      ? "현재 요청의 CLI 결과와 이행 검증을 기다리고 있습니다. 표시된 기존 산출물은 이전 작업 기록일 수 있습니다."
      : run.qualityGateReason ??
        "원본 파일을 변경하지 않고 Workflow 기록에서 읽었습니다.";
  elements.workflowValidationNeeded.replaceChildren();
  const validationNeeded = run.validationNeeded?.length
    ? run.validationNeeded
    : ["등록된 검토 필요 항목이 없습니다."];
  for (const item of validationNeeded) {
    const entry = document.createElement("li");
    entry.textContent = item;
    elements.workflowValidationNeeded.append(entry);
  }
  const failureReason = String(run.failureReason ?? "").trim();
  elements.workflowFailureCard.hidden = !failureReason;
  elements.workflowFailureReason.textContent = failureReason;
  const errorSurfaceLabel = workflowErrorSurfaceLabel(run);
  const errorSurfaceCode = workflowErrorSurfaceCode(run);
  elements.workflowErrorSurfaceCard.hidden = !errorSurfaceLabel && !errorSurfaceCode;
  elements.workflowErrorSurfaceLabel.textContent = errorSurfaceLabel;
  elements.workflowErrorSurfaceCode.textContent = errorSurfaceCode;
  const recoveryAction = String(run.recoveryAction ?? "").trim();
  elements.workflowRecoveryCard.hidden = !recoveryAction;
  elements.workflowRecoveryAction.textContent = recoveryAction;

  setWorkflowBadge(
    elements.workflowStatusBadge,
    status,
    run.status === "failed"
      ? "danger"
      : run.status === "completed"
        ? "success"
        : "warning",
  );
  setWorkflowBadge(
    elements.workflowValidationBadge,
    `검증 ${validation}`,
    run.status === "running"
      ? "warning"
      : run.validationValid === true
      ? "success"
      : run.validationValid === false
        ? "danger"
        : "warning",
  );

  renderWorkflowConversation(run);
  renderWorkflowFiles(run);
  renderWorkflowHistory(run);
  setWorkflowView(uiState.workflowView);
}

function renderWorkflowExecutionStatus(project, run) {
  const record = executionRecordFor(project, run);
  const definition = executionPresentation(record, run);
  const canRestart = executionCanRestart(record, run);
  elements.workflowLaunchButton.hidden = !record && !workflowRunCanLaunch(run);
  elements.workflowLaunchButton.textContent = record
    ? canRestart
      ? "CLI 다시 실행"
      : executionIsTerminal(record)
        ? "실행 기록"
      : "실행 상태"
    : "CLI 실행";
  elements.workflowLaunchButton.dataset.executionActive = String(
    Boolean(record && !executionIsTerminal(record)),
  );
  elements.workflowExecutionBar.hidden = !record;
  if (!record) return;
  elements.workflowExecutionBar.dataset.tone = definition.tone;
  elements.workflowExecutionStatus.textContent = definition.label;
  elements.workflowExecutionMeta.textContent = `${cliPlatformLabel(record.platform)} · ${definition.reconciled ? definition.description : record.error || definition.description}`;
}

function setWorkflowBadge(element, label, tone) {
  element.className = `workflow-badge ${tone}`;
  element.textContent = label;
}

function workflowFileName(path) {
  return String(path ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "파일";
}

function workflowRoleLabel(role) {
  if (role === "requested_output") return "요청 산출물";
  if (role === "final_output") return "최종 산출물";
  return role ? String(role).replaceAll("_", " ") : "등록 산출물";
}

function renderWorkflowConversation(run) {
  const activity = buildWorkflowActivity(run);
  const entries = activity.map((entry, index) => {
    const timestamp = formatWorkflowTimestamp(entry.timestamp);
    if (entry.kind === "request") {
      return `<article class="workflow-message request" data-inspector-kind="activity" data-activity-index="${index}" tabindex="0">
        <header><strong>${escapeHtml(entry.label)}</strong><time>${escapeHtml(timestamp)}</time></header>
        <p>${escapeHtml(entry.text)}</p>
      </article>`;
    }

    const deliverable = entry.deliverable ?? {};
    return `<article class="workflow-message result">
      <header><strong>${escapeHtml(entry.label)}</strong><time>${escapeHtml(timestamp)}</time></header>
      <button class="workflow-message-file" type="button" data-inspector-kind="deliverable" data-file-path="${escapeHtml(entry.text)}">
        <img src="${iconPath("file-text")}" alt="" />
        <div>
          <strong>${escapeHtml(workflowFileName(entry.text))}</strong>
          <span>${escapeHtml(entry.text)}</span>
        </div>
        <small>${escapeHtml(formatWorkflowBytes(deliverable.totalBytes))}</small>
      </button>
    </article>`;
  });

  entries.push(`<article class="workflow-message system" data-inspector-kind="summary" tabindex="0">
    <header><strong>Workflow 결과</strong><time>${escapeHtml(formatWorkflowTimestamp(run.updatedAt ?? run.createdAt))}</time></header>
    <div class="workflow-result-line">
      <span>상태 <strong>${escapeHtml(workflowStatusLabel(run))}</strong></span>
      <span>검증 <strong>${escapeHtml(workflowValidationLabel(run))}</strong></span>
      <span>다음 행동 <strong>${escapeHtml(workflowNextActionLabel(run))}</strong></span>
    </div>
    <p>${escapeHtml(run.qualityGateReason ?? "저장된 실행 기록을 기준으로 결과를 표시했습니다.")}</p>
  </article>`);

  elements.workflowConversation.innerHTML = entries.join("");
}

function renderWorkflowFiles(run) {
  if (!run.deliverables.length) {
    elements.workflowFileList.innerHTML =
      '<p class="workflow-empty-note">등록된 산출물이 없습니다.</p>';
    return;
  }

  elements.workflowFileList.innerHTML = run.deliverables
    .map((deliverable) => {
      const isFinal = deliverable.path === run.finalDeliverable;
      return `<article class="workflow-file-row${isFinal ? " final" : ""}" data-context-kind="deliverable" data-inspector-kind="deliverable" data-file-path="${escapeHtml(deliverable.path)}" tabindex="0">
        <img src="${iconPath("file-text")}" alt="" />
        <div>
          <div class="workflow-file-title">
            <strong>${escapeHtml(workflowFileName(deliverable.path))}</strong>
            ${isFinal ? "<span>최종</span>" : ""}
          </div>
          <p>${escapeHtml(deliverable.path)}</p>
        </div>
        <dl>
          <div><dt>구분</dt><dd>${escapeHtml(workflowRoleLabel(deliverable.role))}</dd></div>
          <div><dt>크기</dt><dd>${escapeHtml(formatWorkflowBytes(deliverable.totalBytes))}</dd></div>
        </dl>
      </article>`;
    })
    .join("");
}

function workflowLayerLabel(id) {
  const labels = {
    "01_input_structuring": "입력 구조화",
    "02_router": "경로 선택",
    "03_route_validation": "경로 검증",
    "04_direction_lens": "방향 설정",
    "05_situation_context": "상황 맥락",
    "06_human_readable_report": "사람이 읽는 보고서",
    "07_fulfillment": "요청 이행 검증",
  };
  return labels[id] ?? String(id).replaceAll("_", " ");
}

function workflowLayerStatusLabel(status) {
  const labels = {
    valid: "통과",
    ready: "준비됨",
    completed: "완료",
    agent_fill_required: "에이전트 입력 기록",
  };
  return labels[status] ?? String(status ?? "확인 필요").replaceAll("_", " ");
}

function workflowRevisionEventLabel(event) {
  const labels = {
    completed_run_continuation_started:
      "완료된 Run에서 이어가기 작업을 시작했습니다.",
  };
  return (
    labels[event] ??
    String(event ?? "추가 작업 기록").replaceAll("_", " ")
  );
}

function renderWorkflowHistory(run) {
  const items = [
    {
      title: "작업 시작",
      description: "최초 요청이 Workflow에 등록되었습니다.",
      timestamp: run.createdAt,
      tone: "normal",
    },
    ...(run.layers ?? []).map((layer) => ({
      title: workflowLayerLabel(layer.id),
      description: workflowLayerStatusLabel(layer.status),
      timestamp: null,
      tone: ["valid", "ready", "completed"].includes(layer.status)
        ? "success"
        : "normal",
    })),
    ...(run.revisionHistory ?? []).map((revision) => ({
      title: "이어가기 요청",
      description: workflowRevisionEventLabel(revision.event),
      timestamp: revision.timestamp,
      tone: "normal",
    })),
    {
      title: "현재 상태",
      description: `${workflowStatusLabel(run)} · 검증 ${workflowValidationLabel(run)}`,
      timestamp: run.updatedAt,
      tone:
        run.status === "running"
          ? "normal"
          : run.validationValid === false
            ? "danger"
            : "success",
    },
  ];

  elements.workflowHistoryList.innerHTML = items
    .map(
      (item) => `<li class="${item.tone}">
        <span aria-hidden="true"></span>
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <p>${escapeHtml(item.description)}</p>
        </div>
        <time>${item.timestamp ? escapeHtml(formatWorkflowTimestamp(item.timestamp)) : ""}</time>
      </li>`,
    )
    .join("");
}

function setWorkflowView(view) {
  const allowed = new Set(["flow", "review", "deliverables", "history"]);
  uiState.workflowView = allowed.has(view) ? view : "flow";
  document.querySelectorAll("[data-workflow-view]").forEach((button) => {
    button.setAttribute(
      "aria-selected",
      String(button.dataset.workflowView === uiState.workflowView),
    );
  });
  document.querySelectorAll("[data-workflow-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.workflowPanel !== uiState.workflowView;
  });
}

function renderSurface(surface, options = {}) {
  const isWorkflowRun = surface.kind === "workflow-run";
  const isExtensionHub = surface.kind === "extension-hub";
  const isProjectOverview =
    Boolean(surface.projectId) && !surface.sessionId && surface.icon === "folder";
  elements.emptySurface.hidden = isWorkflowRun || isExtensionHub;
  elements.workflowSurface.hidden = !isWorkflowRun;
  elements.extensionHub.hidden = !isExtensionHub;
  if (isWorkflowRun) renderWorkflowSurface(surface);
  if (isExtensionHub) renderExtensionHub();
  if (uiState.browserPanelOpen) {
    browserController.updateContext(activeBrowserContext());
  }

  elements.surfaceTitle.textContent = surface.title;
  elements.surfaceDescription.textContent = isProjectOverview
    ? compactProjectPath(surface.description)
    : surface.description;
  elements.surfaceDescription.classList.toggle(
    "project-path",
    isProjectOverview,
  );
  elements.surfaceDescription.title = isProjectOverview
    ? surface.description
    : "";
  if (isProjectOverview) {
    elements.surfaceDescription.setAttribute(
      "aria-label",
      `프로젝트 위치: ${surface.description}`,
    );
  } else {
    elements.surfaceDescription.removeAttribute("aria-label");
  }
  elements.surfaceIcon.src = iconPath(surface.icon);

  if (options.record !== false) {
    const current = uiState.history[uiState.historyIndex];
    const isSameSurface =
      current?.projectId === surface.projectId &&
      current?.sessionId === surface.sessionId &&
      current?.title === surface.title;

    if (!isSameSurface) {
      uiState.history = uiState.history.slice(0, uiState.historyIndex + 1);
      uiState.history.push(surface);
      uiState.historyIndex = uiState.history.length - 1;
    }
  }

  updateHistoryButtons();
  updateContextBar(surface);
}

async function refreshWorkflowProject(projectId) {
  const project = workspaceState.projects.find((item) => item.id === projectId);
  const invoke = window.__TAURI__?.core?.invoke;
  if (!project || !invoke) return null;

  const snapshot = await invoke("inspect_workflow_project", {
    projectRoot: project.path,
  });
  workflowSnapshots.set(projectId, snapshot);
  workspaceState = syncWorkflowProject(
    workspaceState,
    projectId,
    snapshot,
    Date.now(),
  );
  await refreshProjectCliExecutions(project);
  persistWorkspaceState();
  renderSidebar();
  return snapshot;
}

async function refreshMonitoredProjects() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke || projectMonitorState.inFlight) return;
  projectMonitorState.inFlight = true;
  try {
    const ids = monitoredProjectIds(
      workspaceState.projects,
      workspaceState.activeProjectId,
      DEFAULT_PROJECT_MONITOR_LIMIT,
    );
    for (const projectId of ids) {
      const project = workspaceState.projects.find((item) => item.id === projectId);
      const lastRefreshAt = projectMonitorState.lastRefreshAt[projectId] || 0;
      if (!shouldRefreshProject(project, lastRefreshAt, Date.now(), DEFAULT_PROJECT_MONITOR_INTERVAL_MS)) {
        continue;
      }
      try {
        await refreshWorkflowProject(projectId);
        projectMonitorState.results = recordProjectRefresh(
          projectMonitorState.results,
          projectId,
          { status: "refreshed" },
        );
      } catch (error) {
        projectMonitorState.results = recordProjectRefresh(
          projectMonitorState.results,
          projectId,
          { status: "failed", error: error?.message || String(error) },
        );
      }
      projectMonitorState.lastRefreshAt[projectId] = Date.now();
    }
  } finally {
    projectMonitorState.inFlight = false;
  }
}

function scheduleProjectMonitoring() {
  if (projectMonitorState.timer) window.clearInterval(projectMonitorState.timer);
  projectMonitorState.timer = window.setInterval(
    () => void refreshMonitoredProjects(),
    DEFAULT_PROJECT_MONITOR_INTERVAL_MS,
  );
}

function activateLatestWorkflowSession(projectId) {
  const project = workspaceState.projects.find((item) => item.id === projectId);
  const session = project?.sessions.find((item) => item.source === "workflow");
  if (!project || !session) return false;
  workspaceState = selectSession(
    workspaceState,
    project.id,
    session.id,
    Date.now(),
  );
  persistWorkspaceState();
  renderSidebar();
  return true;
}

function activateWorkflowSession(projectId, runId) {
  const project = workspaceState.projects.find((item) => item.id === projectId);
  const session = project?.sessions.find(
    (item) => item.source === "workflow" && item.runId === runId,
  );
  if (!project || !session) return false;
  workspaceState = selectSession(
    workspaceState,
    project.id,
    session.id,
    Date.now(),
  );
  persistWorkspaceState();
  renderSidebar();
  return true;
}

function navigateHistory(direction) {
  const nextIndex = uiState.historyIndex + direction;
  if (nextIndex < 0 || nextIndex >= uiState.history.length) return;
  uiState.historyIndex = nextIndex;
  const surface = uiState.history[uiState.historyIndex];
  selectionGuard.begin(surface.projectId ?? null);

  if (surface.projectId && surface.sessionId) {
    workspaceState = selectSession(
      workspaceState,
      surface.projectId,
      surface.sessionId,
      Date.now(),
    );
  } else if (surface.projectId) {
    workspaceState = selectProject(
      workspaceState,
      surface.projectId,
      Date.now(),
    );
  } else {
    workspaceState.activeProjectId = null;
    workspaceState.activeSessionId = null;
  }

  persistWorkspaceState();
  renderSidebar();
  renderSurface(surface, { record: false });
}

function setZoom(nextZoom) {
  uiState.zoom = Math.min(1.3, Math.max(0.8, nextZoom));
  document.documentElement.style.fontSize = `${14 * uiState.zoom}px`;
}

let environmentRequestId = 0;

function showDialog(title, content, options = {}) {
  if (!elements.searchBackdrop.hidden) closeGlobalSearch();
  if (!options.environment) environmentRequestId += 1;
  elements.dialogTitle.textContent = title;
  elements.dialogBody.innerHTML = content;
  elements.dialogBody.scrollTop = 0;
  elements.dialogBody.scrollLeft = 0;
  elements.dialog.classList.toggle("dialog-wide", options.wide === true);
  elements.dialog.classList.toggle("dialog-environment", options.environment === true);
  elements.modal.hidden = false;
  browserController.setObscured(true);
  elements.dialogClose.focus();
}

function closeDialog() {
  environmentRequestId += 1;
  elements.modal.hidden = true;
  elements.dialog.classList.remove("dialog-wide");
  elements.dialog.classList.remove("dialog-environment");
  uiState.pendingTaskArchive = null;
  uiState.pendingProjectRemoval = null;
  uiState.pendingArchiveDeletion = [];
  pluginLibraryState.pendingRemoval = null;
  externalConnectionState.pendingContextDeletion = null;
  externalConnectionState.pendingContextClear = false;
  browserController.setObscured(false);
}

function closeGlobalSearch() {
  elements.searchBackdrop.hidden = true;
  elements.globalSearchInput.value = "";
  elements.globalSearchResults.replaceChildren();
  browserController.setObscured(false);
}

function globalSearchEntries(query) {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const entries = [];

  workspaceState.projects.forEach((project) => {
    const projectSearchText =
      `${project.name} ${project.path}`.toLocaleLowerCase();
    if (!normalizedQuery || projectSearchText.includes(normalizedQuery)) {
      entries.push({
        type: "project",
        project,
        session: null,
        label: project.name,
        description: project.path,
        score: projectSearchText.startsWith(normalizedQuery) ? 0 : 1,
      });
    }

    project.sessions
      .filter((session) => !session.archived)
      .forEach((session) => {
      const sessionSearchText =
        `${session.title} ${project.name}`.toLocaleLowerCase();
      if (!normalizedQuery || sessionSearchText.includes(normalizedQuery)) {
        entries.push({
          type: "session",
          project,
          session,
          label: session.title,
          description: project.name,
          score: sessionSearchText.startsWith(normalizedQuery) ? 0 : 1,
        });
      }
      });
  });

  return entries
    .sort(
      (left, right) =>
        left.score - right.score ||
        Number(right.project.pinned) - Number(left.project.pinned) ||
        right.project.lastOpenedAt - left.project.lastOpenedAt,
    )
    .slice(0, 14);
}

function createGlobalSearchResult(entry) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "global-search-result";
  button.setAttribute("role", "option");
  button.dataset.searchProjectId = entry.project.id;
  if (entry.session) button.dataset.searchSessionId = entry.session.id;

  const icon = document.createElement("img");
  icon.src = iconPath(entry.type === "project" ? "folder" : "file-text");
  icon.alt = "";

  const copy = document.createElement("span");
  copy.className = "global-search-result-copy";
  const title = document.createElement("strong");
  title.textContent = entry.label;
  const description = document.createElement("span");
  description.textContent = entry.description;
  copy.append(title, description);

  const kind = document.createElement("span");
  kind.className = "global-search-result-kind";
  kind.textContent = entry.type === "project" ? "프로젝트" : "작업";

  button.append(icon, copy, kind);
  return button;
}

function renderGlobalSearchResults() {
  const entries = globalSearchEntries(elements.globalSearchInput.value);
  elements.globalSearchResults.replaceChildren();

  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "global-search-empty";
    empty.textContent = workspaceState.projects.length
      ? "검색 결과가 없습니다."
      : "먼저 프로젝트 폴더를 열어주세요.";
    elements.globalSearchResults.append(empty);
    return;
  }

  entries.forEach((entry) => {
    elements.globalSearchResults.append(createGlobalSearchResult(entry));
  });
}

function openGlobalSearch() {
  closeMenus();
  if (!elements.modal.hidden) closeDialog();
  elements.searchBackdrop.hidden = false;
  browserController.setObscured(true);
  elements.globalSearchInput.value = "";
  renderGlobalSearchResults();
  requestAnimationFrame(() => elements.globalSearchInput.focus());
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderBrowserInsightItems(items, emptyMessage) {
  if (!items.length) {
    return `<p class="browser-insight-empty">${escapeHtml(emptyMessage)}</p>`;
  }

  return `<ul class="browser-insight-list">${items
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("")}</ul>`;
}

function formatBrowserEvidenceTime(value) {
  if (!value) return "확인 불가";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function showBrowserPageInsight(evidence, options = {}) {
  const insight = buildBrowserPageInsight(evidence);
  const persistence = evidence.persistence ?? {};
  const observationMetadata = [
    [
      "관찰 횟수",
      Math.max(
        1,
        Number(evidence.observationCount ?? persistence.observationCount) || 1,
      ),
    ],
    [
      "첫 관찰",
      formatBrowserEvidenceTime(
        evidence.firstCapturedAt ?? evidence.capturedAt,
      ),
    ],
    [
      "최근 관찰",
      formatBrowserEvidenceTime(
        evidence.lastCapturedAt ?? evidence.capturedAt,
      ),
    ],
    [
      "revision",
      `v${Math.max(1, Number(evidence.revision ?? persistence.revision) || 1)}`,
    ],
  ];
  const metrics = [
    ["버튼", evidence.counts.buttons],
    ["링크", evidence.counts.links],
    ["입력", evidence.counts.inputs],
    ["폼", evidence.counts.forms],
  ];
  const controls = insight.controls.length
    ? insight.controls
        .map(
          (control) => `<li class="browser-insight-control">
            <span class="browser-insight-control-kind">${escapeHtml(control.kindLabel)}</span>
            <strong>${escapeHtml(control.label)}</strong>
            ${control.disabled
              ? '<span class="browser-insight-control-state">사용 불가</span>'
              : ["button", "link"].includes(control.kind)
                ? `<button type="button" class="browser-insight-click" data-browser-click-order="${control.order}">승인 후 클릭</button>`
                : '<span class="browser-insight-control-state">읽기 전용</span>'}
          </li>`,
        )
        .join("")
    : '<li class="browser-insight-empty">표시할 조작 요소가 없습니다.</li>';

  showDialog(
    "페이지 작업 진단",
    `<article class="browser-page-insight">
      <header class="browser-insight-heading">
        <div>
          <span class="browser-insight-eyebrow">PAGE DIAGNOSTIC</span>
          <h3>${escapeHtml(evidence.title || "제목 없는 페이지")}</h3>
          <p class="browser-insight-url">${escapeHtml(evidence.url || insight.host)}</p>
        </div>
        <div class="browser-insight-state" data-mode="${escapeHtml(insight.mode.id)}">
          <strong>${escapeHtml(insight.mode.label)}</strong>
          <span>${escapeHtml(insight.mode.description)}</span>
        </div>
      </header>

      <dl class="browser-insight-metrics">
        ${metrics
          .map(
            ([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`,
          )
          .join("")}
      </dl>

      <dl class="browser-insight-observation" aria-label="브라우저 근거 기록">
        ${observationMetadata
          .map(
            ([label, value]) => `<div>
              <dt>${escapeHtml(label)}</dt>
              <dd>${escapeHtml(value)}</dd>
            </div>`,
          )
          .join("")}
      </dl>

      <div class="browser-insight-sections">
        <section class="browser-insight-section">
          <h4>가능한 작업</h4>
          ${renderBrowserInsightItems(insight.capabilities, "현재 확인된 작업이 없습니다.")}
        </section>
        <section class="browser-insight-section">
          <h4>주의 항목</h4>
          ${renderBrowserInsightItems(insight.warnings, "추가로 확인할 주의 항목이 없습니다.")}
        </section>
      </div>

      <section class="browser-insight-section browser-insight-control-section">
        <div class="browser-insight-section-heading">
          <h4>주요 조작 요소</h4>
          <span>최대 12개</span>
        </div>
        <ul class="browser-insight-controls">${controls}</ul>
      </section>

      <footer class="browser-insight-actions">
        <p><strong>근거 연결</strong><span>${escapeHtml(evidence.projectName)} · ${escapeHtml(evidence.sessionName)}</span></p>
        <button type="button" id="copy-browser-agent-brief">에이전트용 요약 복사</button>
      </footer>
    </article>`,
    { wide: true },
  );

  if (typeof options.onBack === "function") {
    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.textContent = "이력으로 돌아가기";
    backButton.className = "browser-insight-back-button";
    backButton.addEventListener("click", options.onBack);
    elements.dialogBody
      .querySelector(".browser-insight-actions")
      ?.prepend(backButton);
  }

  const copyButton = elements.dialogBody.querySelector(
    "#copy-browser-agent-brief",
  );
  copyButton?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(insight.agentBrief);
      copyButton.textContent = "복사됨";
    } catch {
      copyButton.textContent = "복사 실패";
    }
  });

  elements.dialogBody
    .querySelectorAll("[data-browser-click-order]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        const control = insight.controls.find(
          (item) => Number(item.order) === Number(button.dataset.browserClickOrder),
        );
        if (control) requestBrowserClickApproval(evidence, control);
      });
    });
}

const browserSessionClickApprovals = new Map();

function browserClickApprovalKey(proposal) {
  return JSON.stringify([
    proposal.pageUrl,
    proposal.evidenceId,
    proposal.observationKey,
    proposal.controlIndex,
    proposal.controlKind,
    proposal.controlLabel,
  ]);
}

async function executeApprovedBrowserClick(proposal, approval) {
  const context = activeBrowserContext();
  if (!browserActionContextMatches(context, proposal)) {
    throw new Error("승인 후 현재 프로젝트 또는 작업 세션이 바뀌어 실행을 중단했습니다.");
  }
  const approved = transitionBrowserAction(proposal, "approved");
  if (!approved.ok) throw new Error(approved.reason);
  const executing = transitionBrowserAction(approved.proposal, "executing");
  if (!executing.ok) throw new Error(executing.reason);

  try {
    const result = await browserController.clickControl(proposal);
    if (result?.status !== "succeeded") {
      const blocked = transitionBrowserAction(executing.proposal, "blocked");
      const error = new Error(result?.reason || blocked.reason || "브라우저 작업이 차단되었습니다.");
      error.browserActionStatus = "blocked";
      throw error;
    }
    const succeeded = transitionBrowserAction(executing.proposal, "succeeded");
    await persistBrowserActionRecord(proposal, approval, result, "succeeded");
    return { result, action: succeeded.proposal, approval };
  } catch (error) {
    transitionBrowserAction(executing.proposal, "failed");
    await persistBrowserActionRecord(
      proposal,
      approval,
      { status: "failed", reason: error?.message || String(error) },
      error?.browserActionStatus || "failed",
    );
    throw error;
  }
}

async function persistBrowserActionRecord(proposal, approval, result, status) {
  const context = activeBrowserContext();
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke || !context.projectRoot) return { status: "local_only" };
  if (context.projectId !== proposal.projectId || context.sessionId !== proposal.sessionId) {
    return { status: "context_changed" };
  }
  try {
    return await invoke("save_browser_action_record", {
      projectRoot: context.projectRoot,
      action: {
        schemaVersion: "1.0.0",
        actionId: proposal.proposalId,
        createdAt: new Date().toISOString(),
        actionType: proposal.actionType,
        status,
        risk: proposal.risk,
        pageTitle: proposal.pageTitle,
        pageUrl: proposal.pageUrl,
        controlIndex: proposal.controlIndex,
        controlKind: proposal.controlKind,
        controlLabel: proposal.controlLabel,
        approvalScope: approval.approvalScope,
        approvedAt: approval.approvedAt,
        executedAt: status === "succeeded" ? new Date().toISOString() : null,
        resultUrl: result?.url || null,
        reason: result?.reason || (status === "failed" ? "브라우저 작업 실행 중 오류" : null),
        projectId: proposal.projectId,
        projectName: context.projectName || "연결된 프로젝트 없음",
        sessionId: proposal.sessionId,
        sessionName: context.sessionName || "독립 브라우저 작업",
        source: "webview2-devtools-click",
      },
    });
  } catch (error) {
    return { status: "project_save_failed", detail: error?.message || String(error) };
  }
}

function requestBrowserClickApproval(evidence, control) {
  const draft = createBrowserClickProposal(evidence, control, activeBrowserContext());
  if (!draft.ok) {
    showDialog("브라우저 작업을 시작할 수 없습니다", `<p>${escapeHtml(draft.reason)}</p>`);
    return;
  }
  const proposal = draft.proposal;
  const sessionApproval = browserSessionClickApprovals.get(browserClickApprovalKey(proposal));
  if (sessionApproval) {
    void executeApprovedBrowserClick(proposal, sessionApproval)
      .then(() => completeBrowserClickAndRefresh())
      .catch((error) => showBrowserClickFailure(error));
    return;
  }

  showDialog(
    "브라우저 작업 승인",
    `<section class="browser-click-approval">
      <span class="browser-insight-eyebrow">ACTION PROPOSAL</span>
      <h3>이 작업을 실행할까요?</h3>
      <p>${escapeHtml(clickProposalSummary(proposal))}</p>
      <dl>
        <div><dt>페이지</dt><dd>${escapeHtml(proposal.pageUrl)}</dd></div>
        <div><dt>대상</dt><dd>${escapeHtml(proposal.controlLabel)}</dd></div>
        <div><dt>범위</dt><dd>입력·폼 제출 없이 단일 클릭만 실행</dd></div>
      </dl>
      <p class="browser-click-approval-warning">페이지가 이동하거나 외부 상태가 바뀔 수 있습니다. 비밀번호·입력값·폼 제출은 이 단계에서 처리하지 않습니다.</p>
      <div class="dialog-actions">
        <button type="button" data-browser-click-approval="cancel">취소</button>
        <button type="button" data-browser-click-approval="once" class="primary">이번 작업만 허용</button>
        <button type="button" data-browser-click-approval="session" class="secondary">현재 세션에서 허용</button>
      </div>
    </section>`,
    { wide: false },
  );

  elements.dialogBody
    .querySelectorAll("[data-browser-click-approval]")
    .forEach((button) => {
      button.addEventListener("click", async () => {
        const scope = button.dataset.browserClickApproval;
        if (scope === "cancel") {
          closeDialog();
          return;
        }
        const approved = approveBrowserClick(proposal, scope);
        if (!approved.ok) {
          showBrowserClickFailure(new Error(approved.reason));
          return;
        }
        if (scope === "session") {
          browserSessionClickApprovals.set(browserClickApprovalKey(proposal), approved.approval);
        }
        button.disabled = true;
        try {
          await executeApprovedBrowserClick(proposal, approved.approval);
          await completeBrowserClickAndRefresh();
        } catch (error) {
          showBrowserClickFailure(error);
        }
      });
    });
}

async function completeBrowserClickAndRefresh() {
  closeDialog();
  await new Promise((resolve) => window.setTimeout(resolve, 350));
  try {
    await browserController.inspectPage();
  } catch (error) {
    showBrowserClickFailure(error);
  }
}

function showBrowserClickFailure(error) {
  showDialog(
    "브라우저 작업 결과",
    `<section class="browser-click-result browser-click-result-failed">
      <h3>실행하지 못했습니다.</h3>
      <p>${escapeHtml(error?.message || String(error))}</p>
      <p>페이지를 다시 읽은 뒤 대상이 같은지 확인하고 재승인할 수 있습니다.</p>
    </section>`,
  );
}

function browserEvidenceComparisonLabel(mode) {
  return mode === "structure" ? "구조 비교" : "페이지 스냅샷";
}

function renderBrowserEvidenceHistory(records = []) {
  const groups = groupBrowserEvidenceHistory(records);
  const totalRecords = groups.reduce(
    (total, group) => total + group.records.length,
    0,
  );

  if (!groups.length) {
    showDialog(
      "브라우저 근거 이력",
      `<section class="browser-evidence-history browser-evidence-history-empty">
        <span class="browser-insight-eyebrow">READ-ONLY ARCHIVE</span>
        <h3>아직 저장된 페이지 진단 기록이 없습니다.</h3>
        <p>브라우저에서 페이지 진단을 실행하면 이곳에 URL별 revision이 쌓입니다.</p>
      </section>`,
      { wide: true },
    );
    return;
  }

  showDialog(
    "브라우저 근거 이력",
    `<section class="browser-evidence-history">
      <header class="browser-evidence-history-heading">
        <div>
          <span class="browser-insight-eyebrow">READ-ONLY ARCHIVE</span>
          <h3>브라우저 근거 이력</h3>
          <p>현재 프로젝트에 저장된 페이지 진단 기록입니다. 기존 JSON 기록은 변경하지 않습니다.</p>
        </div>
        <div class="browser-evidence-history-heading-actions">
          <dl class="browser-evidence-history-summary">
            <div><dt>URL 그룹</dt><dd>${groups.length}</dd></div>
            <div><dt>전체 revision</dt><dd>${totalRecords}</dd></div>
          </dl>
          <button
            type="button"
            class="browser-evidence-history-clear danger"
            data-browser-history-action="request-clear"
          >근거 이력 초기화</button>
        </div>
      </header>

      <div class="browser-evidence-history-groups">
        ${groups
          .map(
            (group, groupIndex) => `<section class="browser-evidence-history-group">
              <header>
                <div class="browser-evidence-history-group-title">
                  <h4>${escapeHtml(group.title)}</h4>
                  <p>${escapeHtml(group.url)}</p>
                </div>
                <span>${group.records.length}개 revision</span>
              </header>
              <ol class="browser-evidence-history-list">
                ${group.records
                  .map(
                    (record, recordIndex) => `<li class="browser-evidence-history-item">
                      <div class="browser-evidence-history-item-copy">
                        <strong>v${Math.max(1, Number(record.revision) || 1)}</strong>
                        <span>${Math.max(1, Number(record.observationCount) || 1)}회 관찰 · ${escapeHtml(browserEvidenceComparisonLabel(record.comparisonMode))}</span>
                        <small>최근 ${escapeHtml(formatBrowserEvidenceTime(record.lastCapturedAt || record.capturedAt))}</small>
                      </div>
                      <button
                        type="button"
                        class="browser-evidence-history-detail"
                        data-browser-history-group="${groupIndex}"
                        data-browser-history-record="${recordIndex}"
                      >상세 보기</button>
                    </li>`,
                  )
                  .join("")}
              </ol>
            </section>`,
          )
          .join("")}
      </div>
    </section>`,
    { wide: true },
  );

  elements.dialogBody
    .querySelectorAll("[data-browser-history-group]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        const group = groups[Number(button.dataset.browserHistoryGroup)];
        const record = group?.records[
          Number(button.dataset.browserHistoryRecord)
        ];
        if (record) {
          showBrowserPageInsight(record, {
            onBack: () => renderBrowserEvidenceHistory(records),
          });
        }
      });
    });

  elements.dialogBody
    .querySelector("[data-browser-history-action='request-clear']")
    ?.addEventListener("click", () => {
      requestBrowserEvidenceHistoryClear(totalRecords);
    });
}

function requestBrowserEvidenceHistoryClear(recordCount) {
  const project = activeProject();
  const invoke = window.__TAURI__?.core?.invoke;
  if (!project?.path || !invoke) return;

  showDialog(
    "브라우저 근거 이력 초기화",
    `<section class="browser-evidence-history-clear-confirm">
      <h3>페이지 진단 이력을 초기화할까요?</h3>
      <p>현재 프로젝트의 ${Math.max(0, Number(recordCount) || 0)}개 revision을 삭제합니다.</p>
      <p class="dialog-note">페이지 진단 JSON만 삭제되며, 브라우저 조작 감사 기록과 MCP 기록은 보존됩니다.</p>
      <div class="dialog-actions">
        <button type="button" data-action="close-dialog">취소</button>
        <button type="button" class="danger" data-browser-history-action="confirm-clear">초기화</button>
      </div>
    </section>`,
    { wide: true },
  );

  elements.dialogBody
    .querySelector("[data-browser-history-action='confirm-clear']")
    ?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await invoke("clear_browser_web_evidence", {
          projectRoot: project.path,
        });
        showBrowserEvidenceHistory();
      } catch (error) {
        showDialog(
          "브라우저 근거 이력 초기화",
          `<p class="browser-evidence-history-error">이력을 초기화하지 못했습니다: ${escapeHtml(error?.message || String(error))}</p>`,
          { wide: true },
        );
      }
    });
}

async function showBrowserEvidenceHistory() {
  const project = activeProject();
  const invoke = window.__TAURI__?.core?.invoke;
  if (!project?.path) {
    showDialog(
      "브라우저 근거 이력",
      "<p>프로젝트를 먼저 선택하면 저장된 페이지 진단 이력을 확인할 수 있습니다.</p>",
      { wide: true },
    );
    return;
  }
  if (!invoke) {
    showDialog(
      "브라우저 근거 이력",
      "<p>이 기능은 Skkima 데스크톱 앱에서 사용할 수 있습니다.</p>",
      { wide: true },
    );
    return;
  }

  showDialog(
    "브라우저 근거 이력",
    '<p class="browser-evidence-history-loading">저장된 페이지 진단 이력을 읽고 있습니다.</p>',
    { wide: true },
  );

  try {
    const records = await invoke("list_browser_web_evidence", {
      projectRoot: project.path,
    });
    renderBrowserEvidenceHistory(records);
  } catch (error) {
    showDialog(
      "브라우저 근거 이력",
      `<p class="browser-evidence-history-error">이력을 읽지 못했습니다: ${escapeHtml(error?.message || String(error))}</p>`,
      { wide: true },
    );
  }
}

function renderLocalEnvironment(environment) {
  elements.dialogBody.innerHTML = renderLocalEnvironmentMarkup(
    environment,
    escapeHtml,
  );
}

async function showLocalEnvironment() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showDialog(
      "로컬 개발 환경",
      "<p>시스템 진단은 쓰끼마 Windows 앱에서 사용할 수 있습니다.</p>",
    );
    return;
  }

  const requestId = ++environmentRequestId;
  elements.localEnvironmentTrigger.setAttribute("aria-busy", "true");
  showDialog(
    "로컬 개발 환경",
    `<div class="environment-loading" role="status">
      <span aria-hidden="true"></span>
      <div>
        <strong>이 PC의 환경을 확인하고 있습니다.</strong>
        <p>설치된 도구 수에 따라 잠시 걸릴 수 있습니다.</p>
      </div>
    </div>`,
    { environment: true, wide: true },
  );

  try {
    const environment = await invoke("get_local_environment");
    if (requestId !== environmentRequestId) return;
    renderLocalEnvironment(environment);
  } catch (error) {
    if (requestId !== environmentRequestId) return;
    elements.dialogBody.innerHTML = `<div class="environment-error">
      <strong>환경 정보를 확인하지 못했습니다.</strong>
      <p>${escapeHtml(error?.message || error || "알 수 없는 오류")}</p>
      <button type="button" data-action="refresh-local-environment">다시 시도</button>
    </div>`;
  } finally {
    if (requestId === environmentRequestId) {
      elements.localEnvironmentTrigger.removeAttribute("aria-busy");
    }
  }
}

function showRenameDialog() {
  const project = activeProject();
  const session = activeSession();
  if (!project || !session) {
    showDialog(
      "작업 세션이 필요합니다",
      "<p>이름을 변경하려면 프로젝트 안에서 작업 세션을 먼저 선택하세요.</p>",
    );
    return;
  }

  showDialog(
    "작업 이름 바꾸기",
    `<form class="dialog-form" id="rename-task-form">
      <label for="rename-task-input">표시 이름</label>
      <input id="rename-task-input" name="title" maxlength="80" autocomplete="off" />
      <div class="dialog-actions">
        <button type="button" id="rename-cancel">취소</button>
        <button class="primary" type="submit">저장</button>
      </div>
    </form>`,
  );

  const form = document.querySelector("#rename-task-form");
  const input = document.querySelector("#rename-task-input");
  input.value = session.title;
  input.focus();
  input.select();

  document.querySelector("#rename-cancel").addEventListener("click", closeDialog);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const nextTitle = input.value.trim();
    if (!nextTitle) {
      input.focus();
      return;
    }

    workspaceState = renameTaskSession(
      workspaceState,
      project.id,
      session.id,
      nextTitle,
      Date.now(),
    );
    persistWorkspaceState();
    renderSidebar();
    const nextSurface = currentProjectSurface();
    uiState.history[uiState.historyIndex] = nextSurface;
    renderSurface(nextSurface, { record: false });
    closeDialog();
  });
}

function showTaskInfo() {
  openInspector("task");
}

function persistInspectorPreferences() {
  try {
    localStorage.setItem(
      INSPECTOR_STORAGE_KEY,
      JSON.stringify(inspectorPreferences),
    );
  } catch (error) {
    console.error("상세 패널 설정을 저장하지 못했습니다.", error);
  }
}

function inspectorSessionKey() {
  const project = activeProject();
  const session = activeSession();
  if (project && session) return `${project.id}:${session.id}`;
  if (project) return `${project.id}:project`;
  return "workspace";
}

function rememberInspectorSelection() {
  inspectorPreferences.width = uiState.inspector.width;
  inspectorPreferences.lastBySession[inspectorSessionKey()] = {
    kind: uiState.inspector.kind,
    payload: uiState.inspector.payload,
  };
  persistInspectorPreferences();
}

function renderInspectorDetails(rows, options = {}) {
  const className = options.technical
    ? "inspector-detail-list technical"
    : "inspector-detail-list";
  return `<dl class="${className}">${rows
    .map(
      ([label, value]) =>
        `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? "기록 없음")}</dd></div>`,
    )
    .join("")}</dl>`;
}

function renderInspectorDeliverables(run) {
  if (!run?.deliverables?.length) {
    return '<p class="inspector-empty">등록된 산출물이 없습니다.</p>';
  }

  return `<div class="inspector-file-list">${run.deliverables
    .map(
      (deliverable) =>
        `<button type="button" class="inspector-file-button" data-inspector-kind="deliverable" data-file-path="${escapeHtml(deliverable.path)}">
          <img src="${iconPath("file-text")}" alt="" />
          <span>
            <strong>${escapeHtml(workflowFileName(deliverable.path))}</strong>
            <small>${escapeHtml(workflowRoleLabel(deliverable.role))} · ${escapeHtml(formatWorkflowBytes(deliverable.totalBytes))}</small>
          </span>
        </button>`,
    )
    .join("")}</div>`;
}

function renderInspector() {
  const project = activeProject();
  const session = activeSession();
  const surface = currentProjectSurface();
  const run = activeWorkflowRun();
  let kind = uiState.inspector.kind;
  const payload = uiState.inspector.payload ?? {};

  if (!run && !["task"].includes(kind)) {
    kind = "task";
    uiState.inspector.kind = kind;
  }

  if (kind === "task") {
    elements.inspectorEyebrow.textContent = "WORK SESSION";
    elements.inspectorTitle.textContent = surface.title;
    elements.inspectorContent.innerHTML = `
      <section class="inspector-section">
        <header><h3>작업 위치</h3><p>현재 프로젝트와 작업 세션의 로컬 저장 위치입니다.</p></header>
        ${renderInspectorDetails([
          ["프로젝트", project?.name ?? "선택되지 않음"],
          ["프로젝트 폴더", project?.path ?? "선택되지 않음"],
          ["작업 세션", session?.title ?? "선택되지 않음"],
          ["작업 유형", session?.source === "workflow" ? "Workflow 기록" : "로컬 작업"],
          ["탐색 위치", `${uiState.historyIndex + 1} / ${uiState.history.length}`],
          ["저장 범위", "이 Windows 사용자"],
        ])}
      </section>`;
  } else if (kind === "deliverable") {
    const deliverable = run.deliverables.find(
      (item) => item.path === payload.path,
    );
    elements.inspectorEyebrow.textContent = "DELIVERABLE";
    elements.inspectorTitle.textContent = workflowFileName(
      deliverable?.path ?? payload.path,
    );
    elements.inspectorContent.innerHTML = `
      <section class="inspector-section">
        <header><h3>산출물 정보</h3><p>Workflow manifest에 등록된 읽기 전용 정보입니다.</p></header>
        ${renderInspectorDetails([
          ["경로", deliverable?.path ?? payload.path ?? "기록 없음"],
          ["구분", workflowRoleLabel(deliverable?.role)],
          ["파일 수", deliverable?.fileCount ? String(deliverable.fileCount) : "기록 없음"],
          ["크기", formatWorkflowBytes(deliverable?.totalBytes)],
          ["등록 시각", formatWorkflowTimestamp(deliverable?.recordedAt)],
          ["최종 산출물", deliverable?.path === run.finalDeliverable ? "예" : "아니요"],
        ])}
      </section>`;
  } else if (kind === "activity") {
    const activity = buildWorkflowActivity(run);
    const entry = activity[Number(payload.index)] ?? activity[0];
    elements.inspectorEyebrow.textContent =
      entry?.kind === "request" ? "REQUEST" : "WORKFLOW EVENT";
    elements.inspectorTitle.textContent = entry?.label ?? "작업 기록";
    elements.inspectorContent.innerHTML = `
      <section class="inspector-section">
        <header><h3>기록 내용</h3><p>${escapeHtml(formatWorkflowTimestamp(entry?.timestamp))}</p></header>
        <div class="inspector-text">${escapeHtml(entry?.text ?? "기록 없음")}</div>
      </section>`;
  } else if (kind === "deliverables") {
    elements.inspectorEyebrow.textContent = "DELIVERABLES";
    elements.inspectorTitle.textContent = `산출물 ${run.deliverables.length}개`;
    elements.inspectorContent.innerHTML = `
      <section class="inspector-section">
        <header><h3>등록된 산출물</h3><p>항목을 선택하면 경로와 등록 정보를 확인합니다.</p></header>
        ${renderInspectorDeliverables(run)}
      </section>`;
  } else if (kind === "run") {
    elements.inspectorEyebrow.textContent = "WORKFLOW RUN";
    elements.inspectorTitle.textContent = session?.title ?? project?.name ?? "실행 정보";
    elements.inspectorContent.innerHTML = `
      <section class="inspector-section">
        <header><h3>실행 요약</h3><p>현재 판단에 필요한 결과를 먼저 표시합니다.</p></header>
        ${renderInspectorDetails([
          ["관계", workflowRelationLabel(run)],
          ["상태", workflowStatusLabel(run)],
          ["검증", workflowValidationLabel(run)],
          ["근거", workflowEvidenceLabel(run)],
          ["실패 원인", run.failureReason ?? "해당 없음"],
          ["오류 분류", workflowErrorSurfaceCode(run) || "해당 없음"],
          ["복구 행동", run.recoveryAction ?? "해당 없음"],
          ["다음 행동", workflowNextActionLabel(run)],
          ["최종 산출물", run.finalDeliverable ?? "등록 없음"],
        ])}
      </section>
      <section class="inspector-section">
        <header><h3>실행 식별 정보</h3><p>연결과 추적이 필요할 때 사용하는 원본 값입니다.</p></header>
        ${renderInspectorDetails(
          [
            ["Run ID", run.runId],
            ["기준 Run ID", run.parentRunId ?? "해당 없음"],
            ["Operation ID", run.operationId ?? "기록 없음"],
            ["Session reference", run.sessionReference ?? "기록 없음"],
          ],
          { technical: true },
        )}
      </section>
      <section class="inspector-section">
        <header><h3>등록된 산출물</h3><p>${run.deliverables.length}개</p></header>
        ${renderInspectorDeliverables(run)}
      </section>`;
  } else {
    elements.inspectorEyebrow.textContent = "WORK SUMMARY";
    elements.inspectorTitle.textContent = session?.title ?? project?.name ?? "작업 요약";
    elements.inspectorContent.innerHTML = `
      <section class="inspector-section">
        <header><h3>운영 판단</h3><p>다음 행동을 결정하는 데 필요한 핵심 상태입니다.</p></header>
        ${renderInspectorDetails([
          ["상태", workflowStatusLabel(run)],
          ["검증", workflowValidationLabel(run)],
          ["근거", workflowEvidenceLabel(run)],
          ["산출물", `${run.deliverables.length}개`],
          ["다음 행동", workflowNextActionLabel(run)],
        ])}
      </section>
      <section class="inspector-section">
        <header><h3>판단 근거</h3><p>Workflow 품질 게이트 기록입니다.</p></header>
        <div class="inspector-text">${escapeHtml(run.qualityGateReason ?? "저장된 실행 기록에서 추가 판단 근거를 찾지 못했습니다.")}</div>
      </section>`;
  }

  elements.inspectorContent.scrollTop = 0;
}

function openInspector(kind = "summary", payload = {}) {
  closeBrowserPanel();
  uiState.inspector.kind = kind;
  uiState.inspector.payload = payload;
  uiState.splitView = true;
  elements.body.classList.add("split-view");
  elements.body.style.setProperty(
    "--inspector-width",
    `${uiState.inspector.width}px`,
  );
  elements.inspectorPane.setAttribute("aria-hidden", "false");
  elements.splitToggle.setAttribute(
    "aria-pressed",
    "true",
  );
  elements.splitToggle.title = "상세 패널 닫기";
  elements.splitToggle.setAttribute("aria-label", "상세 패널 닫기");
  renderInspector();
  rememberInspectorSelection();
}

function closeInspector() {
  uiState.splitView = false;
  elements.body.classList.remove("split-view");
  elements.inspectorPane.setAttribute("aria-hidden", "true");
  elements.splitToggle.setAttribute("aria-pressed", "false");
  elements.splitToggle.title = "상세 패널";
  elements.splitToggle.setAttribute("aria-label", "상세 패널");
}

function showWorkflowDetails() {
  openInspector(activeWorkflowRun() ? "run" : "task");
}

function setInspectorWidth(width) {
  const frameWidth =
    document.querySelector(".workspace-frame")?.getBoundingClientRect().width ??
    window.innerWidth;
  const maxWidth = Math.max(280, Math.min(520, frameWidth * 0.52));
  uiState.inspector.width = Math.round(
    Math.min(maxWidth, Math.max(280, width)),
  );
  elements.body.style.setProperty(
    "--inspector-width",
    `${uiState.inspector.width}px`,
  );
}

function toggleSplitView() {
  if (uiState.splitView) {
    closeInspector();
    return;
  }

  const remembered = inspectorPreferences.lastBySession[inspectorSessionKey()];
  openInspector(
    remembered?.kind ?? (activeWorkflowRun() ? "summary" : "task"),
    remembered?.payload ?? {},
  );
}

function activeBrowserContext() {
  const project = activeProject();
  const session = activeSession();
  return {
    // Keep the live context aligned with browser action proposals. A missing
    // selection is represented as null in both places so a valid approval is
    // not rejected as a context change.
    projectId: project?.id ?? null,
    projectName: project?.name ?? null,
    projectRoot: project?.path ?? null,
    sessionId: session?.id ?? null,
    sessionName: session?.title ?? null,
  };
}

function openBrowserPanel() {
  closeInspector();
  uiState.browserPanelOpen = true;
  elements.body.classList.add("browser-side-open");
  setBrowserPanelWidth(uiState.browserPanelWidth);
  elements.browserWorkspace.hidden = false;
  elements.browserWorkspace.setAttribute("aria-hidden", "false");
  elements.browserPanelToggle?.setAttribute("aria-pressed", "true");
  browserController.showLauncher(activeBrowserContext());
  if (uiState.sidebar.mode === "peek") applySidebarUi("pointer-leave");
}

function setBrowserPanelWidth(width) {
  const frameWidth = workspaceFrame.getBoundingClientRect().width || window.innerWidth;
  const maxWidth = Math.max(320, frameWidth - 326);
  const minWidth = Math.min(440, maxWidth);
  uiState.browserPanelWidth = Math.round(
    Math.min(maxWidth, Math.max(minWidth, width)),
  );
  elements.body.style.setProperty(
    "--browser-panel-width",
    `${uiState.browserPanelWidth}px`,
  );
}

function setBrowserFocusMode(enabled) {
  uiState.browserFocusMode = Boolean(enabled);
  elements.body.classList.toggle("browser-focus-mode", uiState.browserFocusMode);
  const button = document.querySelector("#browser-focus-toggle");
  button?.setAttribute("aria-pressed", String(uiState.browserFocusMode));
  const label = uiState.browserFocusMode
    ? "분할 화면으로 돌아가기"
    : "브라우저 집중 보기";
  if (button) {
    button.title = label;
    button.setAttribute("aria-label", label);
  }
  // Focus mode changes the grid placement of the browser workspace. Wait for
  // the new layout to settle before moving the native WebView2 child window.
  browserController.scheduleBoundsSync();
}

function toggleBrowserFocusMode() {
  if (!uiState.browserPanelOpen) openBrowserPanel();
  setBrowserFocusMode(!uiState.browserFocusMode);
}

function closeBrowserPanel() {
  if (!uiState.browserPanelOpen && elements.browserWorkspace.hidden) return;
  uiState.browserPanelOpen = false;
  setBrowserFocusMode(false);
  elements.body.classList.remove("browser-side-open");
  elements.browserWorkspace.hidden = true;
  elements.browserWorkspace.setAttribute("aria-hidden", "true");
  elements.browserPanelToggle?.setAttribute("aria-pressed", "false");
  browserController.deactivate();
}

function toggleBrowserPanel() {
  if (uiState.browserPanelOpen) {
    closeBrowserPanel();
  } else {
    openBrowserPanel();
  }
}

let browserResizeActive = false;

elements.browserResizeHandle.addEventListener("pointerdown", (event) => {
  if (window.innerWidth < 900) return;
  browserResizeActive = true;
  elements.browserResizeHandle.setPointerCapture(event.pointerId);
  elements.body.classList.add("browser-resizing");
  event.preventDefault();
});

elements.browserResizeHandle.addEventListener("pointermove", (event) => {
  if (!browserResizeActive) return;
  const frame = workspaceFrame.getBoundingClientRect();
  setBrowserPanelWidth(frame.right - event.clientX);
});

function finishBrowserResize(event) {
  if (!browserResizeActive) return;
  browserResizeActive = false;
  elements.body.classList.remove("browser-resizing");
  if (
    event?.pointerId !== undefined &&
    elements.browserResizeHandle.hasPointerCapture(event.pointerId)
  ) {
    elements.browserResizeHandle.releasePointerCapture(event.pointerId);
  }
  localStorage.setItem(
    BROWSER_PANEL_STORAGE_KEY,
    JSON.stringify({ width: uiState.browserPanelWidth }),
  );
}

elements.browserResizeHandle.addEventListener("pointerup", finishBrowserResize);
elements.browserResizeHandle.addEventListener("pointercancel", finishBrowserResize);
elements.browserResizeHandle.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const delta = event.key === "ArrowLeft" ? 24 : -24;
  setBrowserPanelWidth(uiState.browserPanelWidth + delta);
  localStorage.setItem(
    BROWSER_PANEL_STORAGE_KEY,
    JSON.stringify({ width: uiState.browserPanelWidth }),
  );
});

let inspectorResizeActive = false;

elements.inspectorResizeHandle.addEventListener("pointerdown", (event) => {
  if (window.innerWidth < 960) return;
  inspectorResizeActive = true;
  elements.inspectorResizeHandle.setPointerCapture(event.pointerId);
  elements.body.classList.add("inspector-resizing");
  event.preventDefault();
});

elements.inspectorResizeHandle.addEventListener("pointermove", (event) => {
  if (!inspectorResizeActive) return;
  const frame = document
    .querySelector(".workspace-frame")
    ?.getBoundingClientRect();
  if (!frame) return;
  setInspectorWidth(frame.right - event.clientX);
});

function finishInspectorResize(event) {
  if (!inspectorResizeActive) return;
  inspectorResizeActive = false;
  elements.body.classList.remove("inspector-resizing");
  if (
    event?.pointerId !== undefined &&
    elements.inspectorResizeHandle.hasPointerCapture(event.pointerId)
  ) {
    elements.inspectorResizeHandle.releasePointerCapture(event.pointerId);
  }
  rememberInspectorSelection();
}

elements.inspectorResizeHandle.addEventListener(
  "pointerup",
  finishInspectorResize,
);
elements.inspectorResizeHandle.addEventListener(
  "pointercancel",
  finishInspectorResize,
);
elements.inspectorResizeHandle.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  const delta = event.key === "ArrowLeft" ? 16 : -16;
  setInspectorWidth(uiState.inspector.width + delta);
  rememberInspectorSelection();
});

function renderSidebarVisibility() {
  elements.body.classList.toggle(
    "sidebar-pinned",
    uiState.sidebar.mode === "pinned",
  );
  elements.body.classList.toggle(
    "sidebar-peek",
    uiState.sidebar.mode === "peek",
  );
  const isVisible = uiState.sidebar.mode !== "closed";
  elements.sidebar.setAttribute("aria-hidden", String(!isVisible));
  elements.sidebarToggle.setAttribute(
    "aria-pressed",
    String(uiState.sidebar.pinned),
  );
  elements.sidebarToggle.title = uiState.sidebar.pinned
    ? "사이드 패널 고정 해제 (Ctrl+B)"
    : "사이드 패널 고정 (Ctrl+B)";
  browserController.setObscured(false);
}

function applySidebarUi(event) {
  uiState.sidebar = applySidebarEvent(uiState.sidebar, event);
  renderSidebarVisibility();
  workspaceState.sidebar.pinned = uiState.sidebar.pinned;
  persistWorkspaceState();
}

function scheduleSidebarOpen() {
  clearTimeout(sidebarCloseTimer);
  if (uiState.sidebar.pinned || uiState.sidebar.mode === "peek") return;
  applySidebarUi("edge-enter");
}

function scheduleSidebarClose() {
  if (uiState.sidebar.pinned) return;
  clearTimeout(sidebarCloseTimer);
  sidebarCloseTimer = setTimeout(() => {
    if (!elements.sidebar.matches(":hover") && !elements.sidebar.matches(":focus-within")) {
      applySidebarUi("pointer-leave");
    }
  }, 220);
}

function handleSidebarLeave(event) {
  const nextTarget = event.relatedTarget;
  const leftApplication =
    !(nextTarget instanceof Node) ||
    !document.documentElement.contains(nextTarget);

  if (leftApplication) {
    clearTimeout(sidebarCloseTimer);
    return;
  }

  scheduleSidebarClose();
}

function createSessionRow(project, session) {
  const line = document.createElement("div");
  line.className = "session-line";
  line.dataset.projectId = project.id;
  line.dataset.sessionId = session.id;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "session-row";
  button.dataset.sidebarAction = "select-session";
  button.dataset.projectId = project.id;
  button.dataset.sessionId = session.id;
  button.title = session.title;
  button.classList.toggle(
    "active",
    project.id === workspaceState.activeProjectId &&
      session.id === workspaceState.activeSessionId,
  );

  const label = document.createElement("span");
  label.textContent = session.title;
  button.append(label);

  const archiveButton = document.createElement("button");
  archiveButton.type = "button";
  archiveButton.className = "project-icon-action session-icon-action";
  archiveButton.dataset.sidebarAction = "request-task-archive";
  archiveButton.dataset.projectId = project.id;
  archiveButton.dataset.sessionId = session.id;
  archiveButton.title = "작업 아카이브 보관";
  archiveButton.setAttribute(
    "aria-label",
    `${session.title} 작업 아카이브 보관`,
  );
  const archiveIcon = document.createElement("img");
  archiveIcon.src = iconPath("archive");
  archiveIcon.alt = "";
  archiveButton.append(archiveIcon);

  line.append(button, archiveButton);
  return line;
}

function createProjectGroup(project) {
  const group = document.createElement("div");
  group.className = "project-group";

  const line = document.createElement("div");
  line.className = "project-line";
  line.dataset.projectId = project.id;

  const selectButton = document.createElement("button");
  selectButton.type = "button";
  selectButton.className = "project-row";
  selectButton.dataset.sidebarAction = "select-project";
  selectButton.dataset.projectId = project.id;
  selectButton.title = project.path;
  selectButton.classList.toggle(
    "active",
    project.id === workspaceState.activeProjectId &&
      !workspaceState.activeSessionId,
  );

  const folderIcon = document.createElement("img");
  folderIcon.src = iconPath("folder");
  folderIcon.alt = "";
  const label = document.createElement("span");
  label.textContent = project.name;
  selectButton.append(folderIcon, label);

  const pinButton = document.createElement("button");
  pinButton.type = "button";
  pinButton.className = "project-icon-action";
  pinButton.dataset.sidebarAction = "toggle-project-pin";
  pinButton.dataset.projectId = project.id;
  pinButton.title = project.pinned ? "고정 해제" : "프로젝트 고정";
  pinButton.setAttribute(
    "aria-label",
    project.pinned ? `${project.name} 고정 해제` : `${project.name} 고정`,
  );
  const pinIcon = document.createElement("img");
  pinIcon.src = iconPath("pin");
  pinIcon.alt = "";
  pinButton.append(pinIcon);

  line.append(selectButton, pinButton);
  group.append(line);

  const visibleSessions = project.sessions
    .filter((session) => !session.archived);
  if (
    project.id === workspaceState.activeProjectId ||
    project.pinned
  ) {
    const sessionList = document.createElement("div");
    sessionList.className = "session-list";
    visibleSessions.forEach((session) => {
      sessionList.append(createSessionRow(project, session));
    });
    if (!visibleSessions.length) {
      const empty = document.createElement("p");
      empty.className = "session-empty";
      empty.textContent = "아직 작업 세션이 없습니다.";
      sessionList.append(empty);
    }

    group.append(sessionList);
  }

  return group;
}

function renderProjectSection(section, container, projects, emptyText) {
  section.hidden = false;
  container.replaceChildren();

  if (!projects.length) {
    const empty = document.createElement("p");
    empty.className = "project-empty";
    empty.textContent = emptyText;
    container.append(empty);
    return;
  }

  projects.forEach((project) => {
    container.append(createProjectGroup(project));
  });
}

function renderSidebar() {
  renderProjectSection(
    elements.pinnedSection,
    elements.pinnedProjects,
    listPinnedProjects(workspaceState),
    "고정한 프로젝트가 없습니다.",
  );
  renderProjectSection(
    elements.recentSection,
    elements.recentProjects,
    listRecentProjects(workspaceState),
    "최근 프로젝트가 없습니다.",
  );

}

const onboardingPlatforms = CLI_PLATFORMS;

function onboardingTool(platform) {
  return onboardingState.environment?.tools?.find(
    (tool) => tool.id === platform,
  );
}

function onboardingPlatformMarkup(platform) {
  const tool = onboardingTool(platform.id);
  const installed = tool?.installed === true;
  return `<label class="onboarding-platform-option">
    <input
      type="checkbox"
      name="platform"
      value="${platform.id}"
      ${installed ? "checked" : ""}
    />
    <strong>${platform.label}</strong>
    <small>${installed ? `CLI ${escapeHtml(tool.version || "감지됨")}` : "CLI 감지 안 됨"}</small>
  </label>`;
}

function onboardingPrimaryPlatformMarkup(platform, defaultPlatform) {
  const tool = onboardingTool(platform.id);
  const installed = tool?.installed === true;
  return `<label class="onboarding-platform-option">
    <input
      type="radio"
      name="preferredPlatform"
      value="${platform.id}"
      ${platform.id === defaultPlatform ? "checked" : ""}
      ${installed ? "" : "disabled"}
    />
    <strong>${platform.label}</strong>
    <small>${installed ? "첫 Run 자동 실행" : "CLI 설치 필요"}</small>
  </label>`;
}

function onboardingSkillTemplateOptions() {
  const projects = workspaceState.projects.filter((project) => project.path);
  return [
    '<option value="">추가 스킬 없음</option>',
    ...projects.map(
      (project) =>
        `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)} 구성 복사</option>`,
    ),
  ].join("");
}

function syncOnboardingPrimaryPlatform(form) {
  const selectedPlatforms = new Set(
    new FormData(form).getAll("platform").map(String),
  );
  const primaryInputs = [
    ...form.querySelectorAll('input[name="preferredPlatform"]'),
  ];
  for (const input of primaryInputs) {
    const installed = onboardingTool(input.value)?.installed === true;
    input.disabled = !installed || !selectedPlatforms.has(input.value);
    if (input.disabled) input.checked = false;
  }
  if (!primaryInputs.some((input) => input.checked && !input.disabled)) {
    const fallback = primaryInputs.find((input) => !input.disabled);
    if (fallback) fallback.checked = true;
  }
}

function renderNewProjectForm() {
  const defaultPlatform =
    onboardingPlatforms.find(
      (platform) => onboardingTool(platform.id)?.installed === true,
    )?.id ?? "";
  elements.dialogBody.innerHTML = `<form class="dialog-form onboarding-form" id="new-project-form">
    <div class="onboarding-field">
      <label for="new-project-parent">저장 위치</label>
      <div class="onboarding-path-row">
        <input
          id="new-project-parent"
          name="parentRoot"
          value="${escapeHtml(onboardingState.parentRoot)}"
          placeholder="새 프로젝트를 만들 상위 폴더"
          readonly
        />
        <button type="button" id="choose-project-parent">찾아보기</button>
      </div>
    </div>
    <div class="onboarding-field">
      <label for="new-project-name">프로젝트 이름</label>
      <input
        id="new-project-name"
        name="projectName"
        maxlength="80"
        autocomplete="off"
        placeholder="예: 마케팅 콘텐츠 운영"
      />
    </div>
    <fieldset class="onboarding-platforms">
      <legend>프로젝트에 준비할 AI 플랫폼 · 복수 선택</legend>
      ${onboardingPlatforms.map(onboardingPlatformMarkup).join("")}
    </fieldset>
    <fieldset class="onboarding-platforms onboarding-primary-platforms">
      <legend>첫 Workflow Run 실행 CLI · 단일 선택</legend>
      ${onboardingPlatforms
        .map((platform) =>
          onboardingPrimaryPlatformMarkup(platform, defaultPlatform),
        )
        .join("")}
    </fieldset>
    <div class="onboarding-field">
      <label for="new-project-skill-template">추가 스킬 구성</label>
      <select id="new-project-skill-template" name="skillTemplateProjectId">
        ${onboardingSkillTemplateOptions()}
      </select>
      <small>플러그인과 등록 스킬 원본은 사용자 공용입니다. 기존 프로젝트를 선택하면 그 프로젝트에 설치된 추가 스킬만 새 프로젝트에 복사합니다.</small>
    </div>
    <label class="onboarding-consent">
      <input type="checkbox" name="approved" />
      <span>선택한 위치에 새 프로젝트 폴더와 계약 파일을 만들고, 선택한 플랫폼용 Schema Workflow 스킬과 선택한 추가 스킬 구성을 프로젝트 안에 설치하는 작업을 승인합니다. 공용 엔진과 원본 스킬은 변경하지 않습니다.</span>
    </label>
    <p class="onboarding-error" id="new-project-error" hidden></p>
    <div class="dialog-actions">
      <button type="button" data-action="close-dialog">취소</button>
      <button class="primary" type="submit">프로젝트 준비</button>
    </div>
  </form>`;

  const form = document.querySelector("#new-project-form");
  const parentInput = document.querySelector("#new-project-parent");
  const nameInput = document.querySelector("#new-project-name");
  document
    .querySelector("#choose-project-parent")
    .addEventListener("click", async () => {
      const invoke = window.__TAURI__?.core?.invoke;
      if (!invoke) return;
      const selectedPath = await invoke("pick_project_parent_folder");
      if (!selectedPath) return;
      onboardingState.parentRoot = selectedPath;
      parentInput.value = selectedPath;
      nameInput.focus();
    });
  form.addEventListener("change", (event) => {
    if (event.target.name === "platform") {
      syncOnboardingPrimaryPlatform(form);
    }
  });
  form.addEventListener("submit", prepareNewProject);
  syncOnboardingPrimaryPlatform(form);
  (onboardingState.parentRoot ? nameInput : parentInput).focus();
}

function setOnboardingError(message) {
  const errorElement = document.querySelector("#new-project-error");
  if (!errorElement) return;
  errorElement.textContent = message;
  errorElement.hidden = false;
}

async function showNewProjectDialog() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showDialog(
      "새 프로젝트 만들기",
      "<p>새 프로젝트 준비는 쓰끼마 Windows 앱에서 사용할 수 있습니다.</p>",
    );
    return;
  }

  onboardingState.parentRoot = "";
  onboardingState.projectRoot = "";
  onboardingState.selectedPlatforms = [];
  onboardingState.preferredPlatform = "";
  onboardingState.skillTemplateProjectId = "";
  onboardingState.skillCopyResult = null;
  onboardingState.readiness = null;
  showDialog(
    "새 프로젝트 만들기",
    `<div class="onboarding-loading" role="status">
      <span aria-hidden="true"></span>
      <p>설치된 AI 플랫폼을 확인하고 있습니다.</p>
    </div>`,
    { wide: true },
  );
  try {
    onboardingState.environment = await invoke("get_local_environment");
  } catch (error) {
    console.warn("AI 플랫폼 감지 결과 없이 프로젝트 준비를 계속합니다.", error);
    onboardingState.environment = { tools: [] };
  }
  renderNewProjectForm();
}

async function prepareNewProject(event) {
  event.preventDefault();
  const invoke = window.__TAURI__?.core?.invoke;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const parentRoot = String(formData.get("parentRoot") || "").trim();
  const projectName = String(formData.get("projectName") || "").trim();
  const platforms = formData.getAll("platform").map(String);
  const preferredPlatform = String(
    formData.get("preferredPlatform") || "",
  ).trim();
  const skillTemplateProjectId = String(
    formData.get("skillTemplateProjectId") || "",
  ).trim();
  if (!parentRoot) {
    setOnboardingError("새 프로젝트를 만들 상위 폴더를 선택해 주세요.");
    return;
  }
  if (!projectName) {
    setOnboardingError("프로젝트 이름을 입력해 주세요.");
    return;
  }
  if (!platforms.length) {
    setOnboardingError("사용할 AI 플랫폼을 하나 이상 선택해 주세요.");
    return;
  }
  const runnablePlatforms = platforms.filter(
    (platform) => onboardingTool(platform)?.installed === true,
  );
  if (
    runnablePlatforms.length &&
    (!preferredPlatform || !runnablePlatforms.includes(preferredPlatform))
  ) {
    setOnboardingError("첫 Workflow Run을 실행할 CLI를 하나 선택해 주세요.");
    return;
  }
  if (formData.get("approved") !== "on") {
    setOnboardingError("프로젝트 폴더와 스킬 준비 작업을 확인하고 승인해 주세요.");
    return;
  }

  elements.dialogBody.innerHTML = `<div class="onboarding-loading" role="status">
    <span aria-hidden="true"></span>
    <div>
      <strong>프로젝트를 준비하고 있습니다.</strong>
      <p>계약 파일과 프로젝트용 스킬을 확인합니다.</p>
    </div>
  </div>`;

  try {
    onboardingState.selectedPlatforms = platforms;
    onboardingState.preferredPlatform = preferredPlatform;
    onboardingState.skillTemplateProjectId = skillTemplateProjectId;
    onboardingState.skillCopyResult = null;
    const result = await invoke("prepare_new_project", {
      parentRoot,
      projectName,
      platforms,
      approved: true,
    });
    onboardingState.projectRoot = result.project_root || result.projectRoot;
    onboardingState.skillCopyResult = await copyProjectSkillConfiguration(
      skillTemplateProjectId,
      onboardingState.projectRoot,
      platforms,
    );
    onboardingState.readiness = await invoke("inspect_project_readiness", {
      projectRoot: onboardingState.projectRoot,
    });

    workspaceState = addOrActivateProject(
      workspaceState,
      onboardingState.projectRoot,
      Date.now(),
      uniqueId("project"),
    );
    const projectId = workspaceState.activeProjectId;
    rememberProjectCliPreference(
      workspaceState.projects.find((item) => item.id === projectId),
      preferredPlatform,
      "review",
    );
    selectionGuard.begin(projectId);
    persistWorkspaceState();
    await refreshWorkflowProject(projectId);
    renderProjectReady(projectId);
  } catch (error) {
    renderNewProjectForm();
    setOnboardingError(error?.message || error || "프로젝트를 준비하지 못했습니다.");
  }
}

async function copyProjectSkillConfiguration(
  sourceProjectId,
  targetProjectRoot,
  selectedPlatforms,
) {
  if (!sourceProjectId) return null;
  const invoke = window.__TAURI__?.core?.invoke;
  const sourceProject = workspaceState.projects.find(
    (project) => project.id === sourceProjectId,
  );
  if (!invoke || !sourceProject) {
    return {
      sourceName: "선택한 프로젝트",
      copied: 0,
      failures: ["원본 프로젝트를 찾지 못했습니다."],
    };
  }
  let snapshot;
  try {
    snapshot = await invoke("inspect_project_skill_installations", {
      projectRoot: sourceProject.path,
    });
  } catch (error) {
    return {
      sourceName: sourceProject.name,
      copied: 0,
      failures: [`기존 설치 구성을 읽지 못했습니다: ${String(error)}`],
    };
  }
  const candidates = (snapshot.installations ?? []).filter(
    (installation) =>
      installation.state === "installed" &&
      selectedPlatforms.includes(installation.platform),
  );
  const uniqueCandidates = [
    ...new Map(
      candidates.map((installation) => [
        `${installation.skillId}:${installation.platform}`,
        installation,
      ]),
    ).values(),
  ];
  let copied = 0;
  const failures = [];
  for (const installation of uniqueCandidates) {
    try {
      const result = await invoke("install_project_skill", {
        projectRoot: targetProjectRoot,
        skillId: installation.skillId,
        platform: installation.platform,
      });
      if (result?.state === "installed") {
        copied += 1;
      } else {
        failures.push(
          `${installation.skillId} · ${cliPlatformLabel(installation.platform)}`,
        );
      }
    } catch (error) {
      failures.push(
        `${installation.skillId} · ${cliPlatformLabel(installation.platform)}: ${String(error)}`,
      );
    }
  }
  return {
    sourceName: sourceProject.name,
    copied,
    failures,
  };
}

function readinessItemMarkup(platform) {
  const tool = onboardingTool(platform.id);
  const skill = onboardingState.readiness?.skills?.find(
    (item) => item.platform === platform.id,
  );
  const cliReady = tool?.installed === true;
  const skillReady = skill?.state === "installed";
  return `<article class="onboarding-readiness-item">
    <strong>${platform.label}</strong>
    <span class="${cliReady ? "onboarding-status-ready" : "onboarding-status-warning"}">
      CLI ${cliReady ? tool.version || "감지됨" : "감지 안 됨"}
    </span>
    <span class="${skillReady ? "onboarding-status-ready" : "onboarding-status-warning"}">
      스킬 ${skillReady ? skill.skillVersion || "설치됨" : "설치 확인 필요"}
    </span>
  </article>`;
}

function renderProjectReady(projectId) {
  const readiness = onboardingState.readiness;
  const doctorReady = readiness?.doctor?.status === "normal";
  const preferredPlatformLabel = onboardingState.preferredPlatform
    ? cliPlatformLabel(onboardingState.preferredPlatform)
    : "첫 Run에서 직접 선택";
  const skillCopyResult = onboardingState.skillCopyResult;
  const skillCopySummary = skillCopyResult
    ? `<p class="dialog-note ${skillCopyResult.failures.length ? "onboarding-status-warning" : "onboarding-status-ready"}">
        ${escapeHtml(skillCopyResult.sourceName)}에서 추가 스킬 설치 ${skillCopyResult.copied}건을 복사했습니다.${skillCopyResult.failures.length ? ` 확인 필요 ${skillCopyResult.failures.length}건` : ""}
      </p>`
    : "";
  elements.dialogTitle.textContent = "프로젝트 준비 완료";
  elements.dialogBody.innerHTML = `<div class="onboarding-ready-summary">
    <div class="onboarding-ready-header">
      <span>프로젝트 경로</span>
      <strong>${escapeHtml(onboardingState.projectRoot)}</strong>
    </div>
    <div class="onboarding-readiness-list">
      ${onboardingPlatforms
        .filter((platform) =>
          onboardingState.selectedPlatforms.includes(platform.id),
        )
        .map(readinessItemMarkup)
        .join("")}
    </div>
    <p class="dialog-note ${doctorReady ? "onboarding-status-ready" : "onboarding-status-warning"}">
      ${doctorReady ? "안정 채널 엔진과 프로젝트 계약 검사가 통과했습니다." : "엔진 또는 프로젝트 계약을 다시 확인해야 합니다."}
    </p>
    <p class="dialog-note">첫 실행 CLI: <strong>${escapeHtml(preferredPlatformLabel)}</strong></p>
    ${skillCopySummary}
    <div class="onboarding-divider" aria-hidden="true"></div>
    <form class="dialog-form onboarding-form" id="first-workflow-form">
      <div class="onboarding-field">
        <label for="first-workflow-title">첫 작업 제목</label>
        <input
          id="first-workflow-title"
          name="taskTitle"
          maxlength="120"
          autocomplete="off"
          placeholder="예: 주간 콘텐츠 운영안 만들기"
        />
      </div>
      <div class="onboarding-field">
        <label for="first-workflow-situation">현재 상황</label>
        <textarea
          id="first-workflow-situation"
          name="currentSituation"
          placeholder="현재 문제, 목표, 이미 알고 있는 정보와 제약을 적어 주세요."
        ></textarea>
      </div>
      <p class="dialog-note">Workflow Run과 입력 기록을 만든 뒤, <strong>${escapeHtml(preferredPlatformLabel)}</strong>를 사용자 확인형으로 시작합니다.</p>
      <p class="onboarding-error" id="new-project-error" hidden></p>
      <div class="dialog-actions">
        <button type="button" id="finish-project-only">나중에 만들기</button>
        <button class="primary" type="submit">첫 Run 만들기</button>
      </div>
    </form>
  </div>`;

  document
    .querySelector("#finish-project-only")
    .addEventListener("click", () => {
      closeDialog();
      renderSidebar();
      renderSurface(currentProjectSurface());
    });
  document
    .querySelector("#first-workflow-form")
    .addEventListener("submit", (event) =>
      startFirstWorkflowRun(event, projectId),
    );
  document.querySelector("#first-workflow-title").focus();
}

async function startFirstWorkflowRun(event, projectId) {
  event.preventDefault();
  const invoke = window.__TAURI__?.core?.invoke;
  const formData = new FormData(event.currentTarget);
  const taskTitle = String(formData.get("taskTitle") || "").trim();
  const currentSituation = String(
    formData.get("currentSituation") || "",
  ).trim();
  if (!taskTitle || !currentSituation) {
    setOnboardingError("첫 작업 제목과 현재 상황을 모두 입력해 주세요.");
    return;
  }

  elements.dialogBody.innerHTML = `<div class="onboarding-loading" role="status">
    <span aria-hidden="true"></span>
    <div>
      <strong>첫 Workflow Run을 만들고 있습니다.</strong>
      <p>입력 기록을 저장한 뒤 프로젝트 화면에 연결합니다.</p>
    </div>
  </div>`;

  try {
    const result = await invoke("start_first_workflow_run", {
      projectRoot: onboardingState.projectRoot,
      taskTitle,
      currentSituation,
      operationId: uniqueId("op_desktop"),
    });
    const runId = result.run_id || result.runId;
    await refreshWorkflowProject(projectId);
    if (!activateWorkflowSession(projectId, runId)) {
      throw new Error("생성된 Run을 프로젝트 목록에서 찾지 못했습니다.");
    }
    renderSurface(currentProjectSurface());
    const preparedRun = activeWorkflowRun();
    const preparedProject = activeProject();
    const preferredPlatform = onboardingState.preferredPlatform;
    if (preparedProject && preparedRun && preferredPlatform) {
      try {
        await startWorkflowCli(
          preparedProject,
          preparedRun,
          preferredPlatform,
          "review",
        );
        return;
      } catch (launchError) {
        console.warn(
          "첫 Run의 CLI 자동 시작을 다시 선택할 수 있게 전환합니다.",
          launchError,
        );
      }
    }
    await showCliLaunchDialog(projectId, runId);
  } catch (error) {
    renderProjectReady(projectId);
    setOnboardingError(error?.message || error || "첫 Run을 만들지 못했습니다.");
  }
}

async function openProjectFolder() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showDialog(
      "Windows 앱에서 사용할 수 있습니다",
      "<p>프로젝트 폴더 선택은 쓰끼마 데스크톱 앱에서 지원됩니다.</p>",
    );
    return;
  }

  try {
    const selectedPath = await invoke("pick_project_folder");
    if (!selectedPath) return;
    workspaceState = addOrActivateProject(
      workspaceState,
      selectedPath,
      Date.now(),
      uniqueId("project"),
    );
    const projectId = workspaceState.activeProjectId;
    selectionGuard.begin(projectId);
    persistWorkspaceState();
    renderSidebar();
    try {
      await refreshWorkflowProject(projectId);
      activateLatestWorkflowSession(projectId);
    } catch (error) {
      console.error(error);
      showDialog(
        "Workflow 기록을 읽지 못했습니다",
        `<p>${escapeHtml(error?.message || error || "알 수 없는 오류")}</p>
         <p class="dialog-note">프로젝트 파일은 변경하지 않았습니다.</p>`,
      );
    }
    renderSurface(currentProjectSurface());
    if (uiState.sidebar.mode === "peek") applySidebarUi("pointer-leave");
  } catch (error) {
    console.error(error);
    showDialog(
      "프로젝트 폴더를 열지 못했습니다",
      "<p>폴더 선택 창을 준비하지 못했습니다. 앱을 다시 실행한 뒤 시도하세요.</p>",
    );
  }
}

function pathsEqual(left, right) {
  return normalizeProjectPath(left) === normalizeProjectPath(right);
}

function launchPlatformMarkup(platform, environment, readiness, selectedId) {
  const tool = environment?.tools?.find((item) => item.id === platform.id);
  const skill = readiness?.skills?.find((item) => item.platform === platform.id);
  const available = tool?.installed === true;
  const skillReady = skill?.state === "installed";
  const checked = available && platform.id === selectedId;
  return `<label class="cli-platform-option${available ? "" : " unavailable"}">
    <input type="radio" name="platform" value="${platform.id}" ${checked ? "checked" : ""} ${available ? "" : "disabled"} />
    <span class="cli-platform-copy">
      <strong>${platform.label}</strong>
      <small>${available ? `CLI ${escapeHtml(tool.version || "감지됨")}` : "CLI를 찾지 못함"}</small>
    </span>
    <span class="cli-skill-state ${skillReady ? "ready" : "pending"}">
      ${skillReady ? "스킬 준비됨" : available ? "실행 시 스킬 준비" : "실행 불가"}
    </span>
  </label>`;
}

function approvalModeMarkup(mode, selectedId = "review") {
  return `<label class="cli-approval-option">
    <input type="radio" name="approvalMode" value="${mode.id}" ${mode.id === selectedId ? "checked" : ""} />
    <span><strong>${escapeHtml(mode.label)}</strong><small>${escapeHtml(mode.description)}</small></span>
  </label>`;
}

function updateCliLaunchPolicy(form) {
  const mode = form.querySelector("input[name='approvalMode']:checked")?.value ?? "review";
  const platform = form.querySelector("input[name='platform']:checked")?.value ?? "";
  const policy = form.querySelector("#cli-launch-policy");
  const consent = form.querySelector("#cli-launch-consent-copy");
  if (!policy || !consent) return;

  const isAuto = mode === "auto";
  policy.dataset.mode = mode;
  policy.querySelector("strong").textContent = isAuto ? "자동 승인 실행" : "사용자 확인형 실행";
  policy.querySelector("p").textContent = isAuto
    ? platform === "antigravity"
      ? "Antigravity의 권한 확인을 건너뜁니다. 신뢰하는 프로젝트에서만 사용하세요."
      : "프로젝트 범위에서 CLI 권한 요청을 자동 승인합니다. 신뢰하는 프로젝트에서만 사용하세요."
    : "별도의 PowerShell 창에서 플랫폼의 권한 요청을 직접 확인합니다.";
  consent.textContent = isAuto
    ? "선택한 프로젝트에서 AI CLI의 파일 수정과 명령 실행을 자동 승인하는 데 동의합니다."
    : "프로젝트 스킬과 준비된 Run 정보를 CLI에 전달하고 권한 요청을 직접 확인하는 데 동의합니다.";
}

async function showCliLaunchDialog(projectId = activeProject()?.id, runId = activeWorkflowRun()?.runId) {
  const project = workspaceState.projects.find((item) => item.id === projectId);
  const run = workflowSnapshots
    .get(projectId)
    ?.runs.find((item) => item.runId === runId);
  if (!project || !run) {
    showDialog("Workflow Run이 필요합니다", "<p>CLI를 실행할 작업 Run을 먼저 선택하세요.</p>");
    return;
  }
  const tracked = executionRecordFor(project, run);
  if (tracked && !executionIsTerminal(tracked)) {
    showCliExecutionDetails(tracked);
    return;
  }
  if (!workflowRunCanLaunch(run)) {
    showDialog(
      "새 작업 준비가 필요합니다",
      "<p>완료·실패·검토 대기 상태의 Run은 다시 실행하지 않습니다. 새 작업에서 독립·이어가기·분기 관계를 선택해 실행용 Run을 준비하세요.</p>",
    );
    return;
  }
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showDialog("Windows 앱에서 사용할 수 있습니다", "<p>CLI 실행은 쓰끼마 데스크톱 앱에서 지원됩니다.</p>");
    return;
  }

  showDialog(
    "AI CLI 실행",
    `<div class="onboarding-loading" role="status">
       <span aria-hidden="true"></span>
       <div><strong>실행 가능한 AI CLI를 확인하고 있습니다.</strong><p>프로젝트 계약과 스킬 상태도 함께 확인합니다.</p></div>
     </div>`,
  );
  try {
    const [environment, readiness] = await Promise.all([
      invoke("get_local_environment"),
      invoke("inspect_project_readiness", { projectRoot: project.path }),
    ]);
    const available = CLI_PLATFORMS.filter((platform) =>
      environment?.tools?.some((tool) => tool.id === platform.id && tool.installed),
    );
    const selectedSettings = preferredCliSettings(project, tracked, available);
    elements.dialogBody.innerHTML = `<form class="dialog-form cli-launch-form" id="cli-launch-form">
      <section class="cli-launch-run">
        <span>준비된 Run</span>
        <strong>${escapeHtml(run.displayTitle || run.runId)}</strong>
        <code>${escapeHtml(run.runId)}</code>
      </section>
      <fieldset class="cli-platforms">
        <legend>작업을 수행할 AI CLI</legend>
        ${CLI_PLATFORMS.map((platform) =>
          launchPlatformMarkup(
            platform,
            environment,
            readiness,
            selectedSettings.platform,
          ),
        ).join("")}
      </fieldset>
      <fieldset class="cli-approval-modes">
        <legend>권한 처리 방식</legend>
        ${CLI_APPROVAL_MODES.map((mode) =>
          approvalModeMarkup(mode, selectedSettings.approvalMode),
        ).join("")}
      </fieldset>
      <div class="cli-launch-policy" id="cli-launch-policy" data-mode="review">
        <strong>사용자 확인형 실행</strong>
        <p>별도의 PowerShell 창에서 플랫폼의 권한 요청을 직접 확인합니다.</p>
      </div>
      <label class="onboarding-consent">
        <input type="checkbox" name="approved" ${available.length ? "" : "disabled"} />
        <span id="cli-launch-consent-copy">프로젝트 스킬과 준비된 Run 정보를 CLI에 전달하고 권한 요청을 직접 확인하는 데 동의합니다.</span>
      </label>
      <p class="onboarding-error" id="cli-launch-error" ${available.length ? "hidden" : ""}>사용 가능한 AI CLI가 없습니다. 로컬 개발 환경에서 설치 상태를 확인하세요.</p>
      <div class="dialog-actions">
        <button type="button" data-action="close-dialog">취소</button>
        <button class="primary" type="submit" ${available.length ? "" : "disabled"}>PowerShell에서 실행</button>
      </div>
    </form>`;
    const launchForm = document.querySelector("#cli-launch-form");
    launchForm.addEventListener("submit", (event) =>
      launchPreparedWorkflowCli(event, project, run),
    );
    launchForm.addEventListener("change", () => updateCliLaunchPolicy(launchForm));
  } catch (error) {
    elements.dialogBody.innerHTML = `<div class="environment-error">
      <strong>CLI 실행 준비 상태를 확인하지 못했습니다.</strong>
      <p>${escapeHtml(error?.message || error || "알 수 없는 오류")}</p>
      <button type="button" data-action="launch-workflow-cli">다시 확인</button>
    </div>`;
  }
}

async function launchPreparedWorkflowCli(event, project, run) {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  const platform = String(formData.get("platform") || "");
  const approvalMode = String(formData.get("approvalMode") || "review");
  if (!platform || formData.get("approved") !== "on") {
    const error = document.querySelector("#cli-launch-error");
    error.textContent = "실행 플랫폼과 사용자 확인 항목을 확인해 주세요.";
    error.hidden = false;
    return;
  }
  elements.dialogBody.innerHTML = `<div class="onboarding-loading" role="status">
    <span aria-hidden="true"></span>
    <div><strong>PowerShell 실행 창을 준비하고 있습니다.</strong><p>Run 계약과 프로젝트 스킬을 확인한 뒤 CLI를 시작합니다.</p></div>
  </div>`;
  try {
    await startWorkflowCli(project, run, platform, approvalMode);
  } catch (error) {
    await showCliLaunchDialog(project.id, run.runId);
    const errorElement = document.querySelector("#cli-launch-error");
    if (errorElement) {
      errorElement.textContent = error?.message || error || "CLI를 시작하지 못했습니다.";
      errorElement.hidden = false;
    }
  }
}

async function startWorkflowCli(project, run, platform, approvalMode) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) throw new Error("CLI 실행은 쓰끼마 Windows 앱에서 지원됩니다.");
  const result = await invoke("launch_workflow_cli", {
    projectRoot: project.path,
    runId: run.runId,
    platform,
    approvalMode,
    approved: true,
  });
  rememberProjectCliPreference(project, platform, approvalMode);
  rememberCliExecution(result);
  closeDialog();
  renderSurface(currentProjectSurface(), { record: false });
  scheduleCliExecutionPolling(300);
  return result;
}

function executionDetailsMarkup(record, run, canRestart = false, canStop = false) {
  const definition = executionPresentation(record, run);
  const executionNote = definition.reconciled
    ? `<div><dt>CLI connection</dt><dd>${record.status === "interrupted" || record.status === "aborted" ? "Stopped" : "Ended"}<br><small>${escapeHtml(
        record.error || "The CLI connection ended after the workflow completed.",
      )}</small></dd></div>`
    : "";
  const closeLabel = "\uB2EB\uAE30";
  const stopLabel = "CLI \uC2E4\uD589 \uC911\uB2E8";
  const retryLabel = "CLI \uB2E4\uC2DC \uC2E4\uD589";
  return `<section class="cli-execution-details">
    <header data-tone="${definition.tone}">
      <span class="workflow-execution-dot" aria-hidden="true"></span>
      <div><strong>${escapeHtml(definition.label)}</strong><p>${escapeHtml(definition.reconciled ? definition.description : record.error || definition.description)}</p></div>
    </header>
    <dl class="operation-review-list">
      <div><dt>Platform</dt><dd>${escapeHtml(cliPlatformLabel(record.platform))}</dd></div>
      <div><dt>Approval</dt><dd>${record.approvalMode === "auto" ? "Auto" : "Review"}</dd></div>
      <div><dt>Run ID</dt><dd><code>${escapeHtml(record.runId)}</code></dd></div>
      <div><dt>Operation ID</dt><dd><code>${escapeHtml(record.operationId || "No record")}</code></dd></div>
      <div><dt>Process ID</dt><dd>${escapeHtml(record.processId || "Ended")}</dd></div>
      <div><dt>Execution log</dt><dd><code>${escapeHtml(record.logPath || "No record")}</code></dd></div>
      <div><dt>Prompt</dt><dd><code>${escapeHtml(record.promptPath || "No record")}</code></dd></div>
      ${executionNote}
    </dl>
    <div class="dialog-actions">
      <button type="button" data-action="close-dialog">${closeLabel}</button>
      ${canStop ? `<button class="danger" type="button" data-action="stop-cli-execution">${stopLabel}</button>` : ""}
      ${canRestart ? `<button class="primary" type="button" data-action="retry-cli-execution">${retryLabel}</button>` : ""}
    </div>
  </section>`;
}

async function showCliExecutionDetails(record = executionRecordFor(activeProject(), activeWorkflowRun())) {
  if (!record) {
    showCliLaunchDialog();
    return;
  }
  const invoke = window.__TAURI__?.core?.invoke;
  let current = record;
  try {
    current = rememberCliExecution(
      await invoke("inspect_workflow_cli_launch", {
        projectRoot: record.projectRoot,
        launchId: record.launchId,
      }),
    ) || record;
  } catch (error) {
    console.warn("Failed to refresh CLI execution status.", error);
  }
  showDialog(
    "CLI execution",
    executionDetailsMarkup(
      current,
      activeWorkflowRun(),
      executionCanRestart(current, activeWorkflowRun()),
      executionCanStop(current),
    ),
  );
}

async function stopCliExecution() {
  const project = activeProject();
  const run = activeWorkflowRun();
  const record = executionRecordFor(project, run);
  if (!project || !run || !executionCanStop(record)) return;
  if (!window.confirm("Stop the active CLI process?")) return;
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return;
  try {
    const result = await invoke("stop_workflow_cli", {
      projectRoot: record.projectRoot,
      launchId: record.launchId,
      approved: true,
    });
    rememberCliExecution(result);
    renderSurface(currentProjectSurface(), { record: false });
    showCliExecutionDetails(result);
  } catch (error) {
    showDialog(
      "CLI stop failed",
      `<p>${escapeHtml(error?.message || error || "The CLI process could not be stopped.")}</p><div class="dialog-actions"><button type="button" data-action="close-dialog">${"\uB2EB\uAE30"}</button></div>`,
    );
  }
}

function retryCliExecution() {
  const project = activeProject();
  const run = activeWorkflowRun();
  const record = executionRecordFor(project, run);
  if (!project || !run || !executionCanRestart(record, run)) return;
  closeDialog();
  showCliLaunchDialog(project.id, run.runId);
}
function scheduleCliExecutionPolling(delay = 2500) {
  clearTimeout(cliExecutionState.pollTimer);
  const hasActive = Object.values(cliExecutionState.records).some(
    (record) => !executionIsTerminal(record),
  );
  if (!hasActive) return;
  cliExecutionState.pollTimer = setTimeout(pollTrackedCliExecutions, delay);
}

async function pollTrackedCliExecutions() {
  if (cliExecutionState.polling) return;
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return;
  cliExecutionState.polling = true;
  const activeRecords = Object.values(cliExecutionState.records).filter(
    (record) => !executionIsTerminal(record),
  );
  const refreshedProjects = new Set();
  try {
    for (const record of activeRecords) {
      try {
        const updated = rememberCliExecution(
          await invoke("inspect_workflow_cli_launch", {
            projectRoot: record.projectRoot,
            launchId: record.launchId,
          }),
        );
        if (!updated) continue;
        const project = workspaceState.projects.find((item) =>
          pathsEqual(item.path, updated.projectRoot),
        );
        if (project && !refreshedProjects.has(project.id)) {
          await refreshWorkflowProject(project.id);
          refreshedProjects.add(project.id);
        }
      } catch (error) {
        console.warn("CLI 실행 상태 확인을 다시 시도합니다.", error);
      }
    }
    if (currentSurface().kind !== "extension-hub") {
      renderSurface(currentProjectSurface(), { record: false });
    }
  } finally {
    cliExecutionState.polling = false;
    scheduleCliExecutionPolling();
  }
}

function operationRunCandidates(projectId) {
  return (workflowSnapshots.get(projectId)?.runs ?? []).filter(
    (run) => run?.runId,
  );
}

function operationKindMarkup(selectedKind, hasRuns) {
  return OPERATION_KINDS.map((kind) => {
    const needsAnchor = operationRequiresAnchor(kind.id);
    const disabled = needsAnchor && !hasRuns;
    return `<label class="operation-kind-option${disabled ? " disabled" : ""}">
      <input
        type="radio"
        name="operationKind"
        value="${kind.id}"
        ${selectedKind === kind.id ? "checked" : ""}
        ${disabled ? "disabled" : ""}
      />
      <span>
        <strong>${escapeHtml(kind.label)}</strong>
        <small>${escapeHtml(kind.description)}</small>
      </span>
    </label>`;
  }).join("");
}

function operationAnchorOptions(runs, selectedRunId) {
  return runs
    .map((run) => {
      const label = `${run.displayTitle || run.runId} · ${run.shortId || run.runId}`;
      return `<option value="${escapeHtml(run.runId)}"${run.runId === selectedRunId ? " selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function updateOperationAnchorVisibility(form) {
  const selectedKind = form.querySelector(
    'input[name="operationKind"]:checked',
  )?.value;
  const anchorField = form.querySelector("#operation-anchor-field");
  const anchorSelect = form.querySelector("#operation-anchor-run");
  const needsAnchor = operationRequiresAnchor(selectedKind);
  anchorField.hidden = !needsAnchor;
  anchorSelect.required = needsAnchor;
}

function showOperationInputDialog(project, draft = null) {
  const runs = operationRunCandidates(project.id);
  const nextDraft = {
    operationKind: draft?.operationKind || "independent",
    taskTitle: draft?.taskTitle || "",
    currentSituation: draft?.currentSituation || "",
    anchorRunId: draft?.anchorRunId || runs[0]?.runId || "",
    researchEnabled: draft?.researchEnabled === true,
    researchClaimKind: draft?.researchClaimKind || "fact",
    researchSourceLines: draft?.researchSourceLines || "",
  };
  if (!runs.length && operationRequiresAnchor(nextDraft.operationKind)) {
    nextDraft.operationKind = "independent";
  }
  operationPreparationState.draft = nextDraft;

  showDialog(
    "작업 시작",
    `<form class="dialog-form operation-form" id="operation-input-form">
       <fieldset class="operation-kind-fieldset">
         <legend>작업 방식</legend>
         <div class="operation-kind-options">
           ${operationKindMarkup(nextDraft.operationKind, runs.length > 0)}
         </div>
       </fieldset>
       <div class="onboarding-field operation-anchor-field" id="operation-anchor-field">
         <label for="operation-anchor-run">기준 Run</label>
         <select id="operation-anchor-run" name="anchorRunId">
           ${operationAnchorOptions(runs, nextDraft.anchorRunId)}
         </select>
         <p class="dialog-note">이어가기는 같은 Run에 요청을 추가하고, 분기는 기준 Run에서 새 Run을 만듭니다.</p>
       </div>
       <div class="onboarding-field">
         <label for="operation-task-title">작업 제목</label>
         <input id="operation-task-title" name="taskTitle" maxlength="120" autocomplete="off" value="${escapeHtml(nextDraft.taskTitle)}" placeholder="예: 주간 지표 자동화 개선" />
       </div>
       <label class="onboarding-consent">
         <input id="operation-research-enabled" name="researchEnabled" type="checkbox" ${nextDraft.researchEnabled ? "checked" : ""} />
         <span>이 작업은 리서치 근거를 등록하고 CLI 실행 전에 검증합니다.</span>
       </label>
       <section class="onboarding-field" id="operation-research-fields" ${nextDraft.researchEnabled ? "" : "hidden"}>
         <label for="operation-research-claim">판단 유형</label>
         <select id="operation-research-claim" name="researchClaimKind">
           <option value="fact" ${nextDraft.researchClaimKind === "fact" ? "selected" : ""}>사실 확인 (출처 1개 이상)</option>
           <option value="comparative" ${nextDraft.researchClaimKind === "comparative" ? "selected" : ""}>비교·권고·효과 판단 (독립 출처 2개 이상)</option>
         </select>
         <label for="operation-research-sources">자료 목록</label>
         <textarea id="operation-research-sources" name="researchSourceLines" placeholder="source_id | file|url|note | 제목 | ProjectRoot 상대 경로 또는 URL | 수집 시각 | 인용 또는 요약 | 사용 목적">${escapeHtml(nextDraft.researchSourceLines)}</textarea>
         <p class="dialog-note">한 줄에 자료 하나를 입력합니다. 파일은 먼저 <code>research_sources/</code> 안에 보관하세요. URL에는 자격 증명·쿼리·프래그먼트를 넣지 않습니다.</p>
       </section>
       <div class="onboarding-field">
         <label for="operation-current-situation">현재 상황</label>
         <textarea id="operation-current-situation" name="currentSituation" placeholder="현재 문제, 목표, 알고 있는 정보와 제약을 적어 주세요.">${escapeHtml(nextDraft.currentSituation)}</textarea>
       </div>
       <p class="onboarding-error" id="operation-error" hidden></p>
       <div class="dialog-actions">
         <button type="button" data-action="close-dialog">취소</button>
         <button class="primary" type="submit">관계 검토</button>
       </div>
     </form>`,
  );

  const form = document.querySelector("#operation-input-form");
  form.addEventListener("change", (event) => {
    if (event.target.name === "operationKind") {
      updateOperationAnchorVisibility(form);
    }
    if (event.target.name === "researchEnabled") {
      form.querySelector("#operation-research-fields").hidden = !event.target.checked;
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    let researchSources = [];
    const researchEnabled = formData.get("researchEnabled") === "on";
    try {
      if (researchEnabled) {
        researchSources = parseResearchSourceLines(formData.get("researchSourceLines"));
        const researchError = validateResearchBinding(formData.get("researchClaimKind"), researchSources);
        if (researchError) throw new Error(researchError);
      }
    } catch (error) {
      const errorElement = document.querySelector("#operation-error");
      errorElement.textContent = error.message;
      errorElement.hidden = false;
      return;
    }
    const submittedDraft = {
      operationKind: String(formData.get("operationKind") || ""),
      anchorRunId: String(formData.get("anchorRunId") || ""),
      taskTitle: String(formData.get("taskTitle") || "").trim(),
      currentSituation: String(
        formData.get("currentSituation") || "",
      ).trim(),
      researchEnabled,
      researchClaimKind: String(formData.get("researchClaimKind") || "fact"),
      researchSourceLines: String(formData.get("researchSourceLines") || ""),
      researchSources,
    };
    const validationError = validateOperationDraft(
      submittedDraft,
      runs.map((run) => run.runId),
    );
    if (validationError) {
      const error = document.querySelector("#operation-error");
      error.textContent = validationError;
      error.hidden = false;
      return;
    }
    operationPreparationState.draft = submittedDraft;
    showOperationReviewDialog(project, submittedDraft, runs);
  });
  updateOperationAnchorVisibility(form);
  document.querySelector("#operation-task-title").focus();
}

function showOperationReviewDialog(project, draft, runs) {
  const anchorRun = runs.find((run) => run.runId === draft.anchorRunId) ?? null;
  const previousExecution = selectReusableExecutionRecord(
    cliExecutionState.records,
    project.path,
    anchorRun?.runId,
  );
  const savedCliPreference = projectCliPreference(project);
  const reusableCli = previousExecution ?? savedCliPreference;
  const review = buildOperationReview(draft, anchorRun);
  const anchorMarkup = review.anchorRunId
    ? `<div><dt>기준 Run</dt><dd><strong>${escapeHtml(review.anchorTitle)}</strong><code>${escapeHtml(review.anchorRunId)}</code></dd></div>`
    : `<div><dt>기준 Run</dt><dd>없음 · 독립된 새 Run</dd></div>`;
  const researchMarkup = draft.researchEnabled
    ? `<div><dt>리서치 근거</dt><dd>${escapeHtml(draft.researchClaimKind === "comparative" ? "비교·권고·효과" : "사실 확인")} · ${draft.researchSources.length}개 자료</dd></div>`
    : "";

  showDialog(
    "작업 관계 검토",
    `<section class="operation-review">
       <header>
         <span>${escapeHtml(review.operationLabel)}</span>
         <h3>${escapeHtml(review.taskTitle)}</h3>
         <p>${escapeHtml(review.operationDescription)}</p>
       </header>
       <dl class="operation-review-list">
         <div><dt>프로젝트</dt><dd>${escapeHtml(project.name)}</dd></div>
         ${anchorMarkup}
         ${researchMarkup}
         <div><dt>현재 상황</dt><dd class="operation-review-situation">${escapeHtml(review.currentSituation)}</dd></div>
       </dl>
       <label class="onboarding-consent">
         <input id="operation-review-approved" type="checkbox" />
         <span>표시된 관계와 기준 Run을 확인했습니다. ${reusableCli ? `저장된 ${escapeHtml(cliPlatformLabel(reusableCli.platform))} 실행 설정을 사용할 수 있습니다.` : "Workflow 준비 후 실행할 AI CLI를 선택합니다."}</span>
       </label>
       ${reusableCli ? `<label class="onboarding-consent cli-reuse-choice">
         <input id="operation-reuse-cli" type="checkbox" checked />
         <span>저장된 CLI 설정으로 자동 시작합니다. 해제하면 다른 CLI와 권한 방식을 선택할 수 있습니다.</span>
       </label>` : ""}
       <p class="onboarding-error" id="operation-error" hidden></p>
       <div class="dialog-actions">
         <button type="button" id="operation-review-back">이전</button>
         <button class="primary" type="button" id="operation-review-submit">실행 준비</button>
       </div>
     </section>`,
  );
  document
    .querySelector("#operation-review-back")
    .addEventListener("click", () => showOperationInputDialog(project, draft));
  document
    .querySelector("#operation-review-submit")
    .addEventListener("click", () => prepareWorkflowOperation(project, draft));
}

async function prepareWorkflowOperation(project, draft) {
  const approved = document.querySelector("#operation-review-approved")?.checked;
  if (!approved) {
    const error = document.querySelector("#operation-error");
    error.textContent = "작업 관계와 기준 Run을 확인해 주세요.";
    error.hidden = false;
    return;
  }
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showDialog("Windows 앱에서 사용할 수 있습니다", "<p>Workflow 실행 준비는 쓰끼마 데스크톱 앱에서 지원됩니다.</p>");
    return;
  }

  elements.dialogBody.innerHTML = `<div class="onboarding-loading" role="status">
    <span aria-hidden="true"></span>
    <div>
      <strong>Workflow 관계를 기록하고 있습니다.</strong>
      <p>안정 릴리스와 프로젝트 계약을 확인한 뒤 실행 준비 상태로 연결합니다.</p>
    </div>
  </div>`;

  try {
    if (draft.researchEnabled) {
      await invoke("save_research_sources", {
        projectRoot: project.path,
        sources: draft.researchSources,
      });
    }
    const anchorRun = operationRunCandidates(project.id).find(
      (run) => run.runId === draft.anchorRunId,
    );
    const previousExecution = selectReusableExecutionRecord(
      cliExecutionState.records,
      project.path,
      anchorRun?.runId,
    );
    const savedCliPreference = projectCliPreference(project);
    const reusableCli = previousExecution ?? savedCliPreference;
    const reusePreviousCli = reusableCli
      ? document.querySelector("#operation-reuse-cli")?.checked !== false
      : false;
    const result = await invoke("prepare_workflow_operation", {
      projectRoot: project.path,
      taskTitle: draft.taskTitle,
      currentSituation: draft.currentSituation,
      operationKind: draft.operationKind,
      anchorRunId: operationRequiresAnchor(draft.operationKind)
        ? draft.anchorRunId
        : null,
      operationId: uniqueId("op_desktop"),
      sessionReference: uniqueId("session_desktop"),
      researchBinding: draft.researchEnabled
        ? {
            claimKind: draft.researchClaimKind,
            sourceIds: draft.researchSources.map((source) => source.sourceId),
          }
        : null,
    });
    const runId = result.prepared_run_id || result.preparedRunId || result.run_id;
    await refreshWorkflowProject(project.id);
    if (!runId || !activateWorkflowSession(project.id, runId)) {
      throw new Error("준비된 Run을 프로젝트 목록에서 찾지 못했습니다.");
    }
    operationPreparationState.draft = null;
    renderSurface(currentProjectSurface());
    const preparedRun = activeWorkflowRun();
    const preparedProject = activeProject();
    if (
      reusePreviousCli &&
      reusableCli?.platform &&
      preparedProject &&
      preparedRun &&
      workflowRunCanLaunch(preparedRun)
    ) {
      try {
        await startWorkflowCli(
          preparedProject,
          preparedRun,
          reusableCli.platform,
          reusableCli.approvalMode || "review",
        );
        return;
      } catch (launchError) {
        console.warn(
          "이어가기 CLI 자동 시작을 수동 선택으로 전환합니다.",
          launchError,
        );
      }
    }
    await showCliLaunchDialog(project.id, runId);
  } catch (error) {
    showOperationReviewDialog(
      workspaceState.projects.find((item) => item.id === project.id) ?? project,
      draft,
      operationRunCandidates(project.id),
    );
    const errorElement = document.querySelector("#operation-error");
    errorElement.textContent = error?.message || error || "Workflow 실행 준비에 실패했습니다.";
    errorElement.hidden = false;
  }
}

async function createNewTask() {
  const project = activeProject();
  if (!project) {
    showDialog(
      "프로젝트 폴더가 필요합니다",
      `<p>새 작업은 프로젝트 폴더 안에 생성됩니다.</p>
       <div class="dialog-actions">
         <button class="primary" type="button" data-action="open-project-folder">
           프로젝트 폴더 열기
         </button>
       </div>`,
    );
    return;
  }

  if (!workflowSnapshots.has(project.id)) {
    try {
      await refreshWorkflowProject(project.id);
    } catch (error) {
      console.error(error);
    }
  }
  const refreshedProject =
    workspaceState.projects.find((item) => item.id === project.id) ?? project;
  showOperationInputDialog(refreshedProject);
}

async function selectSidebarProject(projectId) {
  const selection = selectionGuard.begin(projectId);
  workspaceState = selectProject(workspaceState, projectId, Date.now());
  persistWorkspaceState();
  renderSidebar();
  renderSurface(currentProjectSurface());
  try {
    await refreshWorkflowProject(projectId);
    if (!selectionGuard.isCurrent(selection, workspaceState.activeProjectId)) {
      return;
    }
    activateLatestWorkflowSession(projectId);
    renderSurface(currentProjectSurface());
  } catch (error) {
    console.error(error);
  }
  if (uiState.sidebar.mode === "peek") applySidebarUi("pointer-leave");
}

async function selectSidebarSession(projectId, sessionId) {
  const selection = selectionGuard.begin(projectId);
  workspaceState = selectSession(
    workspaceState,
    projectId,
    sessionId,
    Date.now(),
  );
  const session = activeSession();
  if (session?.source === "workflow" && !workflowSnapshots.has(projectId)) {
    try {
      await refreshWorkflowProject(projectId);
      if (!selectionGuard.isCurrent(selection, workspaceState.activeProjectId)) {
        return;
      }
      workspaceState = selectSession(
        workspaceState,
        projectId,
        sessionId,
        Date.now(),
      );
    } catch (error) {
      console.error(error);
    }
  }
  persistWorkspaceState();
  renderSidebar();
  renderSurface(currentProjectSurface());
  if (uiState.sidebar.mode === "peek") applySidebarUi("pointer-leave");
}

function requestTaskArchive(projectId, sessionId) {
  const project = workspaceState.projects.find((item) => item.id === projectId);
  const session = project?.sessions.find((item) => item.id === sessionId);
  if (!project || !session) return;
  uiState.pendingTaskArchive = { projectId, sessionId };
  showDialog(
    "작업을 아카이브에 보관할까요?",
    `<p><strong id="archive-task-name"></strong> 작업을 현재 목록에서 숨기고 설정의 아카이브 보관함으로 이동합니다.</p>
     <p class="dialog-note">프로젝트 폴더와 Workflow 기록은 삭제되지 않으며 언제든 복원할 수 있습니다.</p>
     <div class="dialog-actions">
       <button type="button" data-action="close-dialog">취소</button>
       <button class="primary" type="button" data-action="confirm-task-archive">
          아카이브 보관
       </button>
     </div>`,
  );
  document.querySelector("#archive-task-name").textContent = session.title;
}

function requestProjectRemoval(projectId) {
  const project = workspaceState.projects.find((item) => item.id === projectId);
  if (!project) return;
  uiState.pendingProjectRemoval = projectId;
  showDialog(
    "프로젝트를 앱 목록에서 제거할까요?",
    `<div class="confirmation-copy">
       <strong>${escapeHtml(project.name)}</strong>
       <p>쓰끼마의 최근·고정 프로젝트 목록에서만 제거합니다. 실제 폴더, Workflow Run, 산출물은 삭제하지 않습니다.</p>
       <code>${escapeHtml(project.path)}</code>
     </div>
     <div class="dialog-actions">
       <button type="button" data-action="close-dialog">취소</button>
       <button class="danger" type="button" data-action="confirm-project-removal">목록에서 제거</button>
     </div>`,
  );
}

function confirmProjectRemoval() {
  const projectId = uiState.pendingProjectRemoval;
  if (!projectId) return;
  const removedActiveProject = workspaceState.activeProjectId === projectId;
  workspaceState = removeProject(workspaceState, projectId);
  workflowSnapshots.delete(projectId);
  uiState.pendingProjectRemoval = null;
  if (removedActiveProject) selectionGuard.begin(null);
  persistWorkspaceState();
  renderSidebar();
  renderSurface(currentProjectSurface());
  closeDialog();
}

function confirmTaskArchive() {
  if (!uiState.pendingTaskArchive) return;
  workspaceState = archiveTaskSession(
    workspaceState,
    uiState.pendingTaskArchive.projectId,
    uiState.pendingTaskArchive.sessionId,
    Date.now(),
  );
  uiState.pendingTaskArchive = null;
  persistWorkspaceState();
  renderSidebar();
  renderSurface(currentProjectSurface());
  closeDialog();
}

function restoreArchivedTask(projectId, sessionId) {
  workspaceState = restoreTaskSession(
    workspaceState,
    projectId,
    sessionId,
    Date.now(),
  );
  persistWorkspaceState();
  archiveSettingsState.selected.delete(`${projectId}:${sessionId}`);
  renderSidebar();
  renderSurface(currentProjectSurface());
  showSettings("archive");
}

function restoreSelectedArchivedTasks() {
  const archivedTasks = listArchivedTaskSessions(workspaceState);
  const selectedTasks = archivedTasks.filter(({ project, session }) =>
    archiveSettingsState.selected.has(`${project.id}:${session.id}`),
  );
  if (!selectedTasks.length) return;

  const now = Date.now();
  selectedTasks.forEach(({ project, session }) => {
    workspaceState = restoreTaskSession(
      workspaceState,
      project.id,
      session.id,
      now,
    );
  });
  archiveSettingsState.selected.clear();
  persistWorkspaceState();
  renderSidebar();
  renderSurface(currentProjectSurface());
  showSettings("archive");
}

function requestArchivedTaskDeletion(taskReferences) {
  const requested = new Set(
    taskReferences.map(
      ({ projectId, sessionId }) => archiveTaskKey(projectId, sessionId),
    ),
  );
  const targets = listArchivedTaskSessions(workspaceState)
    .filter(({ project, session }) =>
      requested.has(archiveTaskKey(project.id, session.id)),
    )
    .map(({ project, session }) => ({
      projectId: project.id,
      sessionId: session.id,
      title: session.title,
      source: session.source,
    }));
  if (!targets.length) return;

  uiState.pendingArchiveDeletion = targets;
  const targetLabel =
    targets.length === 1
      ? `<strong>${escapeHtml(targets[0].title)}</strong>`
      : `<strong>선택한 작업 ${targets.length}개</strong>`;
  showDialog(
    "보관 기록 삭제",
    `<p>${targetLabel}를 아카이브 보관함에서 삭제합니다.</p>
     <p class="dialog-note">대시보드의 보관 기록만 삭제되며 실제 프로젝트 폴더, Workflow Run, 산출물 파일은 유지됩니다.</p>
     <p class="dialog-note">삭제한 로컬 작업 기록은 복원할 수 없습니다. Workflow 작업은 이후 동기화에서도 다시 표시되지 않습니다.</p>
     <div class="dialog-actions">
       <button type="button" data-action="close-dialog">취소</button>
       <button class="danger" type="button" data-action="confirm-archive-deletion">삭제</button>
     </div>`,
  );
}

function confirmArchivedTaskDeletion() {
  if (!uiState.pendingArchiveDeletion.length) return;
  const deletedKeys = new Set(
    uiState.pendingArchiveDeletion.map(({ projectId, sessionId }) =>
      archiveTaskKey(projectId, sessionId),
    ),
  );
  workspaceState = deleteArchivedTaskSessions(
    workspaceState,
    uiState.pendingArchiveDeletion,
  );
  deletedKeys.forEach((key) => archiveSettingsState.selected.delete(key));
  uiState.pendingArchiveDeletion = [];
  persistWorkspaceState();
  renderSidebar();
  renderSurface(currentProjectSurface());
  closeDialog();
  showSettings("archive");
}

function renderArchiveSettings() {
  const container = document.querySelector("#settings-archive-list");
  const count = document.querySelector("#settings-archive-count");
  const pageLabel = document.querySelector("#settings-archive-page-label");
  const previousButton = document.querySelector("#settings-archive-previous");
  const nextButton = document.querySelector("#settings-archive-next");
  const selectedCount = document.querySelector("#settings-selected-count");
  const restoreSelectedButton = document.querySelector(
    "#settings-restore-selected",
  );
  const deleteSelectedButton = document.querySelector(
    "#settings-delete-selected",
  );
  if (
    !container ||
    !count ||
    !pageLabel ||
    !previousButton ||
    !nextButton ||
    !selectedCount ||
    !restoreSelectedButton ||
    !deleteSelectedButton
  ) {
    return;
  }

  const archivedTasks = listArchivedTaskSessions(workspaceState);
  const existingKeys = new Set(
    archivedTasks.map(({ project, session }) =>
      archiveTaskKey(project.id, session.id),
    ),
  );
  archiveSettingsState.selected.forEach((key) => {
    if (!existingKeys.has(key)) archiveSettingsState.selected.delete(key);
  });

  const filteredTasks = filterArchivedTasks(
    archivedTasks,
    archiveSettingsState,
  );
  const pagination = paginateArchivedTasks(
    filteredTasks,
    archiveSettingsState.page,
    archiveSettingsState.pageSize,
  );
  archiveSettingsState.page = pagination.page;
  const pageCount = pagination.pageCount;
  const pageTasks = pagination.tasks;

  count.textContent =
    filteredTasks.length === archivedTasks.length
      ? String(archivedTasks.length)
      : `${filteredTasks.length} / ${archivedTasks.length}`;
  pageLabel.textContent = `${archiveSettingsState.page} / ${pageCount}`;
  previousButton.disabled = archiveSettingsState.page <= 1;
  nextButton.disabled = archiveSettingsState.page >= pageCount;
  selectedCount.textContent = `선택 ${archiveSettingsState.selected.size}개`;
  restoreSelectedButton.disabled = archiveSettingsState.selected.size === 0;
  deleteSelectedButton.disabled = archiveSettingsState.selected.size === 0;
  container.replaceChildren();

  if (!pageTasks.length) {
    const empty = document.createElement("p");
    empty.className = "archive-empty";
    empty.textContent = archivedTasks.length
      ? "검색 조건에 맞는 보관 작업이 없습니다."
      : "아카이브에 보관된 작업이 없습니다.";
    container.append(empty);
    return;
  }

  pageTasks.forEach(({ project, session }) => {
    const item = document.createElement("article");
    item.className = "archive-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "archive-select";
    checkbox.dataset.archiveSelection = "task";
    checkbox.dataset.projectId = project.id;
    checkbox.dataset.sessionId = session.id;
    checkbox.checked = archiveSettingsState.selected.has(
      archiveTaskKey(project.id, session.id),
    );
    checkbox.setAttribute("aria-label", `${session.title} 작업 선택`);

    const copy = document.createElement("div");
    copy.className = "archive-item-copy";
    const title = document.createElement("strong");
    title.textContent = session.title;
    const meta = document.createElement("span");
    meta.textContent = `${project.name} · ${session.source === "workflow" ? "Workflow 작업" : "로컬 작업"} · ${formatArchiveTimestamp(session.archivedAt)}`;
    copy.append(title, meta);

    const restoreButton = document.createElement("button");
    restoreButton.type = "button";
    restoreButton.className = "archive-restore-button";
    restoreButton.dataset.settingsAction = "restore-archived-task";
    restoreButton.dataset.projectId = project.id;
    restoreButton.dataset.sessionId = session.id;
    restoreButton.textContent = "복원";
    restoreButton.setAttribute("aria-label", `${session.title} 작업 복원`);

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "archive-delete-button";
    deleteButton.dataset.settingsAction = "request-delete-archived-task";
    deleteButton.dataset.projectId = project.id;
    deleteButton.dataset.sessionId = session.id;
    deleteButton.textContent = "삭제";
    deleteButton.setAttribute("aria-label", `${session.title} 보관 기록 삭제`);

    const actions = document.createElement("div");
    actions.className = "archive-item-actions";
    actions.append(restoreButton, deleteButton);

    item.append(checkbox, copy, actions);
    container.append(item);
  });
}

function skillStatuses(skillId) {
  return buildSkillStatuses(
    skillId,
    skillSettingsState.platforms,
    skillSettingsState.statuses,
  );
}

function skillStatusLabel(skillId) {
  return summarizeSkillStatus(skillStatuses(skillId));
}

function skillPlatformMarkup(skill, platform, status) {
  const installed = status?.state === "installed";
  const conflict = status?.state === "conflict";
  const shared = (status?.sharedPlatforms ?? platform.sharedPlatforms ?? []).length > 1;
  const version = status?.cliVersion ? ` ${status.cliVersion}` : "";
  return `<div class="skill-platform-row">
    <div class="skill-platform-identity">
      <strong>${escapeHtml(status?.platformLabel ?? platform.label)}</strong>
      <span>${status?.cliAvailable ? `CLI${escapeHtml(version)}` : "CLI 미감지"}</span>
    </div>
    <div class="skill-platform-state">
      <span class="skill-state ${escapeHtml(status?.state ?? "unknown")}">${escapeHtml(platformStatusLabel(status))}</span>
      <small>${shared ? "Codex·Antigravity 공유 경로" : "전용 프로젝트 경로"}</small>
    </div>
    <button type="button" data-extension-action="${installed ? "uninstall-platform-skill" : "install-platform-skill"}" data-skill-id="${escapeHtml(skill.skillId)}" data-platform="${escapeHtml(platform.platform)}" ${!skillSettingsState.projectId || conflict ? "disabled" : ""}>${installed ? "제거" : "설치"}</button>
  </div>`;
}

function smokeTestPlatformMarkup(platform) {
  const test = skillSmokeTestState.tests.get(platform.platform);
  const cliReady = skillSettingsState.platforms.find(
    (item) => item.platform === platform.platform,
  )?.available;
  const running = ["running", "prepared"].includes(test?.state);
  const state = test?.state ?? "not_run";
  const action = test
    ? `<button type="button" data-extension-action="refresh-smoke-tests">확인</button>
       <button type="button" data-extension-action="cleanup-smoke-test" data-test-id="${escapeHtml(test.testId)}" ${running ? "disabled" : ""}>정리</button>`
    : `<button type="button" data-extension-action="request-smoke-test" data-platform="${escapeHtml(platform.platform)}" ${!skillSettingsState.projectId || !cliReady || skillSmokeTestState.loading ? "disabled" : ""}>테스트</button>`;
  const detail = test?.unexpectedChanges?.length
    ? `예상 밖 변경 ${test.unexpectedChanges.length}건`
    : test?.message ?? (cliReady ? "전용 무해 스킬로 실제 인식을 확인합니다." : "CLI를 먼저 설치해 주세요.");
  return `<article class="skill-smoke-platform">
    <div>
      <strong>${escapeHtml(platform.label)}</strong>
      <span class="skill-smoke-state ${escapeHtml(state)}">${escapeHtml(smokeTestStateLabel(state))}</span>
    </div>
    <p>${escapeHtml(detail)}</p>
    <div class="skill-smoke-actions">${action}</div>
  </article>`;
}

function skillSmokeTestMarkup() {
  const platforms = skillSettingsState.platforms.length
    ? skillSettingsState.platforms
    : DEFAULT_SKILL_PLATFORMS;
  return `<details class="skill-smoke-panel" ${skillSmokeTestState.panelOpen ? "open" : ""}>
    <summary>
      <div>
        <h3>프로젝트 설치 상태</h3>
        <p>선택한 프로젝트에서 세 플랫폼의 설치와 실제 인식을 확인합니다.</p>
      </div>
      <span>${platforms.length}개 플랫폼</span>
    </summary>
    <div class="skill-smoke-content">
      <div class="skill-smoke-toolbar">
        <p>전용 테스트 스킬이 지정 토큰 파일 하나만 만드는지 확인합니다. 프로젝트의 다른 변경은 실패로 판정합니다.</p>
        <button type="button" data-extension-action="refresh-smoke-tests" ${!skillSettingsState.projectId ? "disabled" : ""}>상태 새로 고침</button>
      </div>
      ${skillSmokeTestState.error ? `<p class="skill-library-error">${escapeHtml(skillSmokeTestState.error)}</p>` : ""}
      <div class="skill-smoke-grid">${platforms.map(smokeTestPlatformMarkup).join("")}</div>
    </div>
  </details>`;
}

function ensureSkillProjectSelection() {
  const projects = workspaceState.projects.filter((project) => project.path);
  if (
    !skillSettingsState.projectId ||
    !projects.some((project) => project.id === skillSettingsState.projectId)
  ) {
    skillSettingsState.projectId = activeProject()?.id ?? projects[0]?.id ?? null;
  }
  return projects;
}

function skillProjectOptionsMarkup(projects) {
  return projects
    .map(
      (project) =>
        `<option value="${escapeHtml(project.id)}"${project.id === skillSettingsState.projectId ? " selected" : ""}>${escapeHtml(project.name)}</option>`,
    )
    .join("");
}

function skillCardMarkup(skill) {
  const statuses = skillStatuses(skill.skillId);
  const overallState = statuses.some((item) => item.status?.state === "conflict")
    ? "conflict"
    : statuses.some((item) => item.status?.state === "installed")
      ? "installed"
      : "available";
  return `<article class="skill-library-item">
    <div class="skill-library-mark" aria-hidden="true">
      <img src="${iconPath("plug")}" alt="" />
    </div>
    <div class="skill-library-copy">
      <div class="skill-library-title-row">
        <strong>${escapeHtml(skill.name)}</strong>
        <span class="skill-state ${overallState}">${escapeHtml(skillStatusLabel(skill.skillId))}</span>
      </div>
      <p>${escapeHtml(skill.description)}</p>
      <span class="skill-library-meta">${escapeHtml(skill.sourceKind === "local_file" ? "SKILL.md" : skill.sourceKind === "github_plugin" ? "GitHub 플러그인" : "로컬 폴더")} · ${escapeHtml(skill.sourceHash.slice(0, 10))}</span>
    </div>
    <details class="skill-platform-disclosure">
      <summary>
        <span>프로젝트 설치</span>
        <small>Codex · Claude Code · Antigravity</small>
      </summary>
      <div class="skill-platform-grid">${statuses.map(({ platform, status }) => skillPlatformMarkup(skill, platform, status)).join("")}</div>
    </details>
  </article>`;
}

function skillCollectionMarkup(title, skills) {
  if (!skills.length) return "";
  return `<section class="skill-collection">
    <header>
      <h3>${escapeHtml(title)}</h3>
      <span>${skills.length}</span>
    </header>
    <div class="skill-collection-grid">${skills.map(skillCardMarkup).join("")}</div>
  </section>`;
}

function renderSkillHubList() {
  const container = document.querySelector("#extension-skill-list");
  if (!container) return;
  if (skillSettingsState.loading) {
    container.innerHTML =
      '<div class="skill-library-empty"><strong>스킬 상태를 확인하고 있습니다.</strong></div>';
    return;
  }
  const skills = filterLibrarySkills(
    skillSettingsState.snapshot?.skills ?? [],
    extensionHubState.query,
  );
  if (!skills.length) {
    const hasRegisteredSkills = Boolean(skillSettingsState.snapshot?.skills?.length);
    container.innerHTML = `<div class="skill-library-empty">
      <strong>${hasRegisteredSkills ? "검색 결과가 없습니다." : "등록된 스킬이 없습니다."}</strong>
      <p>${hasRegisteredSkills ? "검색어를 바꾸거나 초기화해 보세요." : "스킬 Markdown 또는 스킬 폴더를 등록하면 원본을 보존한 표준 스냅샷으로 관리합니다."}</p>
    </div>`;
    return;
  }
  const installed = skills.filter(
    (skill) => skillStatuses(skill.skillId).some(({ status }) => status?.state === "installed"),
  );
  const available = skills.filter(
    (skill) => !skillStatuses(skill.skillId).some(({ status }) => status?.state === "installed"),
  );
  container.innerHTML = [
    skillCollectionMarkup("설치됨", installed),
    skillCollectionMarkup("라이브러리", available),
  ].join("");
}

function pluginSkillIsRegistered(skill) {
  return isPluginSkillRegistered(
    skill,
    skillSettingsState.snapshot?.skills ?? [],
  );
}

function pluginSkillMarkup(plugin, skill) {
  const registered = pluginSkillIsRegistered(skill);
  return `<div class="plugin-skill-row">
    <div class="plugin-skill-copy">
      <div class="plugin-skill-title">
        <strong>${escapeHtml(skill.name)}</strong>
        <span class="skill-state ${skill.valid ? registered ? "installed" : "available" : "conflict"}">${skill.valid ? registered ? "라이브러리 등록됨" : "등록 가능" : "검증 필요"}</span>
      </div>
      <p>${escapeHtml(skill.description)}</p>
      <code>${escapeHtml(skill.relativePath || "저장소 루트")}</code>
      ${skill.valid ? "" : `<span class="plugin-skill-warning">${escapeHtml(skill.validationMessage)}</span>`}
    </div>
    <button type="button" data-extension-action="register-plugin-skill" data-plugin-id="${escapeHtml(plugin.pluginId)}" data-relative-path="${escapeHtml(skill.relativePath)}" ${!skill.valid || registered ? "disabled" : ""}>${registered ? "등록됨" : "스킬 등록"}</button>
  </div>`;
}

function pluginCardMarkup(plugin) {
  const validCount = plugin.skills.filter((skill) => skill.valid).length;
  const registeredCount = plugin.skills.filter((skill) => pluginSkillIsRegistered(skill)).length;
  return `<article class="plugin-library-item">
    <header>
      <div class="plugin-library-identity">
        <div class="skill-library-mark" aria-hidden="true"><img src="${iconPath("plug")}" alt="" /></div>
        <div>
          <strong>${escapeHtml(plugin.owner)} / ${escapeHtml(plugin.repository)}</strong>
          <span class="plugin-library-source">${escapeHtml(plugin.sourceUrl)}</span>
        </div>
      </div>
      <div class="plugin-library-actions">
        <span>${validCount}/${plugin.skills.length} 스킬</span>
        <button type="button" data-extension-action="request-remove-plugin" data-plugin-id="${escapeHtml(plugin.pluginId)}">보관 해제</button>
      </div>
    </header>
    <div class="plugin-library-meta-line">
      <span>revision ${escapeHtml(plugin.revision.slice(0, 10))}</span>
      <span>스냅샷 ${escapeHtml(plugin.sourceHash.slice(0, 10))}</span>
    </div>
    <details class="plugin-skill-disclosure"${plugin.skills.length <= 3 ? " open" : ""}>
      <summary>
        <span>포함된 스킬 ${plugin.skills.length}개</span>
        <small>${registeredCount ? `${registeredCount}개 등록됨` : "선택하여 등록"}</small>
      </summary>
      <div class="plugin-skill-list">
        ${plugin.skills.length ? plugin.skills.map((skill) => pluginSkillMarkup(plugin, skill)).join("") : '<p class="plugin-no-skills">이 저장소에서 SKILL.md를 찾지 못했습니다.</p>'}
      </div>
    </details>
  </article>`;
}

function renderPluginHubList() {
  const container = document.querySelector("#extension-plugin-list");
  if (!container) return;
  if (pluginLibraryState.loading) {
    container.innerHTML = '<div class="skill-library-empty"><strong>GitHub 저장소를 확인하고 있습니다.</strong><p>코드는 실행하지 않고 안전한 로컬 스냅샷만 만듭니다.</p></div>';
    return;
  }
  const plugins = filterPlugins(
    pluginLibraryState.snapshot?.plugins ?? [],
    pluginLibraryState.query,
  );
  if (!plugins.length) {
    const imported = Boolean(pluginLibraryState.snapshot?.plugins?.length);
    container.innerHTML = `<div class="skill-library-empty"><strong>${imported ? "검색 결과가 없습니다." : "가져온 플러그인이 없습니다."}</strong><p>${imported ? "검색어를 바꿔 보세요." : "공개 GitHub 저장소를 가져오면 포함된 SKILL.md를 검토하고 필요한 항목만 등록할 수 있습니다."}</p></div>`;
    return;
  }
  container.innerHTML = plugins.map(pluginCardMarkup).join("");
}

function pluginHubMarkup() {
  const snapshot = pluginLibraryState.snapshot;
  const pluginCount = snapshot?.plugins?.length ?? 0;
  const plugins = snapshot?.plugins ?? [];
  const skillCount = plugins.reduce((count, plugin) => count + plugin.skills.length, 0);
  const registeredCount = plugins.reduce(
    (count, plugin) => count + plugin.skills.filter((skill) => pluginSkillIsRegistered(skill)).length,
    0,
  );
  return `<div class="extension-page-heading">
      <div>
        <h2>플러그인</h2>
        <p>공개 저장소에서 필요한 스킬을 찾아 쓰끼마 작업 환경에 연결합니다.</p>
      </div>
      <div class="extension-page-actions">
        <button type="button" data-extension-action="refresh-plugin-library">새로 고침</button>
      </div>
    </div>
    <label class="extension-search plugin-primary-search">
      <img src="${iconPath("search")}" alt="" />
      <input id="extension-plugin-search" type="search" value="${escapeHtml(pluginLibraryState.query)}" placeholder="플러그인 또는 스킬 검색" aria-label="플러그인 또는 스킬 검색" />
    </label>
    <section class="plugin-overview" aria-label="플러그인 라이브러리 요약">
      <header>
        <div>
          <h3>보관됨</h3>
          <span>${pluginCount}개 저장소 · ${skillCount}개 스킬 · ${registeredCount}개 등록</span>
        </div>
        <span class="plugin-git-state">${snapshot?.gitAvailable === false ? "Git CLI 없음" : `Git ${escapeHtml(snapshot?.gitVersion?.replace(/^git version\s+/i, "") ?? "확인 중")}`}</span>
      </header>
      <div class="plugin-overview-icons">
        ${plugins.slice(0, 12).map((plugin) => `<span title="${escapeHtml(`${plugin.owner}/${plugin.repository}`)}"><img src="${iconPath("plug")}" alt="" /></span>`).join("") || '<small>아직 보관된 저장소가 없습니다.</small>'}
      </div>
    </section>
    <details class="plugin-import-panel">
      <summary>
        <span>GitHub 저장소 가져오기</span>
        <small>owner/repository 또는 전체 링크</small>
      </summary>
      <div class="plugin-import-content">
        <form class="plugin-import-form" id="plugin-import-form">
          <label>
            <span>저장소 주소</span>
            <input id="plugin-source-url" type="text" inputmode="url" autocomplete="off" spellcheck="false" value="${escapeHtml(pluginLibraryState.sourceUrl)}" placeholder="owner/repository 또는 GitHub 저장소 링크" required />
          </label>
          <button type="submit" ${pluginLibraryState.loading || snapshot?.gitAvailable === false ? "disabled" : ""}>가져오기</button>
        </form>
        <p class="plugin-import-note">저장소 루트와 tree/blob 링크를 지원합니다. 코드를 실행하지 않고 SKILL.md만 찾아 각각 검증합니다.</p>
      </div>
    </details>
    <div class="plugin-library-toolbar">
      <div>
        <h3>저장소</h3>
        <span>${pluginCount}</span>
      </div>
    </div>
    ${pluginLibraryState.error ? `<p class="skill-library-error">${escapeHtml(pluginLibraryState.error)}</p>` : ""}
    <div class="plugin-library-list" id="extension-plugin-list"></div>
    <p class="skill-library-root">플러그인 스냅샷: <code>${escapeHtml(snapshot?.libraryRoot ?? "확인 전")}</code></p>`;
}

function externalConnectionStatusLabel(status) {
  return {
    connected: "연결됨",
    needs_setup: "추가 설정 필요",
    error: "확인 실패",
  }[status] ?? "확인 전";
}

function isSkkimaBridgeEndpoint(endpoint) {
  try {
    const url = new URL(endpoint.trim());
    return url.protocol === "http:" && url.hostname === "127.0.0.1" && url.port === "3217";
  } catch {
    return false;
  }
}

function externalConnectionCheckedAt(value) {
  if (!value) return "아직 확인하지 않음";
  const timestamp = Number(value);
  if (!Number.isFinite(timestamp)) return "확인 시각을 알 수 없음";
  return new Date(timestamp * 1000).toLocaleString("ko-KR");
}

function legacyChromeBridgeContextMarkup() {
  const state = externalConnectionState;
  const context = state.latestContext;
  if (state.contextLoading) {
    return `<section class="external-bridge-context" aria-label="Chrome 확장 전달 기록">
      <header><div><strong>최근 전달 기록</strong><span>Chrome Bridge</span></div></header>
      <p class="external-bridge-empty">최근 전달 기록을 확인하고 있습니다.</p>
    </section>`;
  }
  if (!context) {
    return `<section class="external-bridge-context" aria-label="Chrome 확장 전달 기록">
      <header>
        <div><strong>최근 전달 기록</strong><span>Chrome Bridge</span></div>
        <button type="button" data-extension-action="refresh-chrome-context">새로 고침</button>
      </header>
      <p class="external-bridge-empty">아직 Chrome 확장 프로그램에서 전달된 페이지가 없습니다.</p>
    </section>`;
  }
  const headings = Array.isArray(context.headings) ? context.headings : [];
  const links = Array.isArray(context.links) ? context.links : [];
  return `<section class="external-bridge-context" aria-label="Chrome 확장 전달 기록">
      <header>
        <div><strong>최근 전달 기록</strong><span>사용자 요청으로 읽은 페이지 근거</span></div>
        <button type="button" data-extension-action="refresh-chrome-context">새로 고침</button>
      </header>
      <dl class="external-bridge-summary">
        <div><dt>페이지</dt><dd>${escapeHtml(context.pageTitle || "제목 없음")}</dd></div>
        <div><dt>주소</dt><dd><code>${escapeHtml(context.pageUrl || "-")}</code></dd></div>
        <div><dt>구조</dt><dd>${headings.length}개 제목 · ${links.length}개 링크${context.bodyExcerpt ? " · 본문 요약 포함" : ""}</dd></div>
      </dl>
      ${context.selectedText ? `<div class="external-bridge-selection"><span>선택 영역</span><p>${escapeHtml(context.selectedText)}</p></div>` : ""}
      <small class="external-bridge-received">전달 시각: ${escapeHtml(context.capturedAt || "확인 불가")}</small>
    </section>`;
}

function chromeContextKey(snapshot) {
  const context = snapshot?.context ?? {};
  return [snapshot?.receivedAt, context.capturedAt, context.pageUrl].join("|");
}

function selectChromeBridgeContext(key) {
  const selected = (externalConnectionState.contextHistory ?? []).find(
    (snapshot) => chromeContextKey(snapshot) === key,
  );
  if (!selected) return;
  externalConnectionState.selectedContextKey = key;
  externalConnectionState.latestContext = selected.context;
  renderExtensionHubIfVisible();
}

function requestChromeBridgeContextDeletion(key) {
  const selected = (externalConnectionState.contextHistory ?? []).find(
    (snapshot) => chromeContextKey(snapshot) === key,
  );
  if (!selected) return;
  externalConnectionState.pendingContextDeletion = selected;
  showDialog(
    "전달 기록 삭제",
    `<div class="confirmation-copy">
       <strong>${escapeHtml(selected.context?.pageTitle || "선택한 페이지")}</strong>
       <p>선택한 Chrome Bridge 전달 기록만 삭제합니다. 원본 페이지나 Chrome의 로그인 정보는 변경하지 않습니다.</p>
     </div>
     <div class="dialog-actions">
       <button type="button" data-action="close-dialog">취소</button>
       <button class="danger" type="button" data-action="confirm-chrome-context-delete">삭제</button>
     </div>`,
  );
}

function requestClearChromeBridgeContextHistory() {
  const records = externalConnectionState.contextHistory ?? [];
  if (!records.length && !externalConnectionState.latestContext) return;
  externalConnectionState.pendingContextClear = true;
  showDialog(
    "전달 기록 전체 삭제",
    `<div class="confirmation-copy">
       <strong>Chrome Bridge 전달 기록 전체</strong>
       <p>저장된 페이지 전달 기록과 최신 전달 상태를 모두 삭제합니다. 원본 페이지나 Chrome의 로그인 정보는 변경하지 않습니다.</p>
     </div>
     <div class="dialog-actions">
       <button type="button" data-action="close-dialog">취소</button>
       <button class="danger" type="button" data-action="confirm-chrome-context-clear">전체 삭제</button>
     </div>`,
  );
}

function chromeBridgeContextMarkup() {
  const state = externalConnectionState;
  const history = Array.isArray(state.contextHistory)
    ? state.contextHistory
    : [];
  const fallback = state.latestContext
    ? [{ receivedAt: "", context: state.latestContext }]
    : [];
  const records = history.length ? history : fallback;
  if (state.contextLoading) {
    return `<section class="external-bridge-context" aria-label="Chrome 확장 전달 기록">
      <header><div><strong>최근 전달 기록</strong><span>Chrome Bridge</span></div></header>
      <p class="external-bridge-empty">최근 전달 기록을 확인하고 있습니다.</p>
    </section>`;
  }
  if (!records.length) {
    return `<section class="external-bridge-context" aria-label="Chrome 확장 전달 기록">
      <header>
        <div><strong>최근 전달 기록</strong><span>Chrome Bridge</span></div>
        <button type="button" data-extension-action="refresh-chrome-context">새로 고침</button>
      </header>
      <p class="external-bridge-empty">아직 Chrome 확장 프로그램에서 전달된 페이지가 없습니다.</p>
    </section>`;
  }
  const selectedKey = state.selectedContextKey || chromeContextKey(records[0]);
  const selectedRecord = records.find((record) => chromeContextKey(record) === selectedKey) ?? records[0];
  const context = selectedRecord.context;
  const headings = Array.isArray(context.headings) ? context.headings : [];
  const links = Array.isArray(context.links) ? context.links : [];
  return `<section class="external-bridge-context" aria-label="Chrome 확장 전달 기록">
      <header>
        <div><strong>최근 전달 기록</strong><span>최근 ${records.length}건 · 선택한 페이지 근거</span></div>
        <div class="external-bridge-actions">
          <button type="button" data-extension-action="refresh-chrome-context">새로 고침</button>
          ${history.length ? '<button class="danger" type="button" data-extension-action="clear-chrome-context-history">전체 삭제</button>' : ""}
        </div>
      </header>
      <div class="external-bridge-records" role="list" aria-label="Chrome 전달 기록 목록">${records
        .map((record) => {
          const key = chromeContextKey(record);
          const itemContext = record.context ?? {};
          const active = key === chromeContextKey(selectedRecord);
          return `<div class="external-bridge-record-row" role="listitem">
            <button type="button" class="external-bridge-record${active ? " is-selected" : ""}" data-extension-action="select-chrome-context" data-context-key="${escapeHtml(key)}">
              <strong>${escapeHtml(itemContext.pageTitle || "제목 없음")}</strong>
              <span>${escapeHtml(itemContext.pageUrl || "-")}</span>
              <small>${escapeHtml(itemContext.capturedAt || record.receivedAt || "확인 불가")}</small>
            </button>
            ${record.receivedAt && itemContext.capturedAt && itemContext.pageUrl ? `<button type="button" class="external-bridge-record-delete" data-extension-action="delete-chrome-context" data-context-key="${escapeHtml(key)}" aria-label="${escapeHtml(itemContext.pageTitle || "선택한 기록")} 삭제">삭제</button>` : ""}
          </div>`;
        })
        .join("")}</div>
      <div class="external-bridge-selected">
        <span class="external-bridge-selected-label">선택한 기록</span>
        <dl class="external-bridge-summary">
          <div><dt>페이지</dt><dd>${escapeHtml(context.pageTitle || "제목 없음")}</dd></div>
          <div><dt>주소</dt><dd><code>${escapeHtml(context.pageUrl || "-")}</code></dd></div>
          <div><dt>구조</dt><dd>${headings.length}개 제목 · ${links.length}개 링크${context.bodyExcerpt ? " · 본문 요약 포함" : ""}</dd></div>
        </dl>
        ${context.selectedText ? `<div class="external-bridge-selection"><span>선택 영역</span><p>${escapeHtml(context.selectedText)}</p></div>` : ""}
        <small class="external-bridge-received">전달 시각: ${escapeHtml(context.capturedAt || selectedRecord.receivedAt || "확인 불가")}</small>
      </div>
    </section>`;
}

function chromeDevtoolsMcpMarkup() {
  const state = externalConnectionState;
  const connection = state.mcpConnection;
  const readResult = state.mcpReadResult;
  const mcpStatus = state.mcpError ? "error" : state.mcpStatus;
  const statusLabel = {
    connected: "MCP 연결됨",
    reading: "읽는 중",
    error: "MCP 오류",
  }[mcpStatus] ?? "MCP 연결 전";
  const toolNames = Array.isArray(connection?.readOnlyTools)
    ? connection.readOnlyTools
    : [];
  const snapshotText = String(readResult?.snapshotText ?? "").trim();
  const pagesText = readResult?.pages
    ? JSON.stringify(readResult.pages, null, 2).slice(0, 1400)
    : "";
  return `<section class="chrome-mcp-card" aria-label="Chrome DevTools MCP">
      <header class="chrome-mcp-header">
        <div>
          <strong>Chrome DevTools MCP</strong>
          <span>실행 중인 Chrome의 읽기 근거를 가져옵니다.</span>
        </div>
        <span class="external-connection-status ${escapeHtml(mcpStatus)}">${statusLabel}</span>
      </header>
      <div class="chrome-mcp-actions">
        <button type="button" data-extension-action="start-chrome-devtools-mcp" ${state.mcpLoading ? "disabled" : ""}>${state.mcpLoading ? "연결 중" : "MCP 연결"}</button>
        <button type="button" data-extension-action="read-chrome-devtools-mcp" ${state.mcpLoading || !connection ? "disabled" : ""}>현재 탭 읽기</button>
        <button type="button" data-extension-action="stop-chrome-devtools-mcp" ${state.mcpLoading || !connection ? "disabled" : ""}>연결 종료</button>
        <button type="button" data-extension-action="save-chrome-devtools-mcp-evidence" ${!readResult || !activeProject() ? "disabled" : ""}>근거 저장</button>
      </div>
      <div class="chrome-mcp-result ${escapeHtml(mcpStatus)}">
        <span class="external-connection-dot" aria-hidden="true"></span>
        <div>
          <strong>${escapeHtml(state.mcpError || connection?.detail || "MCP 연결 전")}</strong>
          <small>${connection ? `읽기 허용 도구: ${escapeHtml(toolNames.join(", ") || "없음")}` : "npx chrome-devtools-mcp@latest를 프로젝트의 요청으로 실행합니다."}</small>
        </div>
      </div>
      ${readResult ? `<div class="chrome-mcp-readout">
        <div class="chrome-mcp-readout-heading"><strong>최근 MCP 읽기</strong><small>${escapeHtml(readResult.capturedAt || "")}</small></div>
        <div class="chrome-mcp-readout-grid">
          <div><span>페이지 목록 응답</span><pre>${escapeHtml(pagesText || "응답 없음")}</pre></div>
          <div><span>현재 탭 스냅샷</span><pre>${escapeHtml(snapshotText.slice(0, 3200) || "스냅샷 없음")}</pre></div>
        </div>
        ${state.mcpEvidenceSaved ? `<p class="chrome-mcp-saved">저장됨: <code>${escapeHtml(state.mcpEvidenceSaved.relativePath || "")}</code></p>` : ""}
      </div>` : ""}
      <p class="chrome-mcp-note">이 단계에서는 list_pages와 take_snapshot만 허용합니다. 클릭·입력·로그인·제출 도구는 연결되어 있어도 실행하지 않습니다.</p>
    </section>`;
}

function connectionHubMarkup() {
  const state = externalConnectionState;
  const status = state.error ? "error" : state.status;
  const statusLabel = externalConnectionStatusLabel(status);
  const bridgeEndpoint = isSkkimaBridgeEndpoint(state.endpoint);
  const connectionTitle = bridgeEndpoint ? "Chrome Bridge 연결" : "Chrome DevTools 연결";
  const connectionDescription = bridgeEndpoint
    ? "Chrome 확장 프로그램의 읽기 전용 전달을 받습니다."
    : "전용 Chrome의 로컬 DevTools 연결 상태를 확인합니다.";
  const endpointLabel = bridgeEndpoint ? "브리지 주소" : "연결 주소";
  const endpointPlaceholder = bridgeEndpoint ? "http://127.0.0.1:3217" : "http://127.0.0.1:9222";
  const checkLabel = bridgeEndpoint ? "브리지 연결 확인" : "브라우저 연결 확인";
  return `<div class="extension-page-heading">
      <div>
        <h2>외부 연결</h2>
        <p>쓰끼마가 별도 도구와 연결될 때 사용하는 상태를 관리합니다.</p>
      </div>
      <div class="extension-page-actions">
        <button type="button" data-extension-action="check-chrome-devtools-connection" ${state.loading ? "disabled" : ""}>${state.loading ? "확인 중" : "연결 확인"}</button>
      </div>
    </div>
    <div class="external-connection-note">
      <strong>첫 연결은 읽기 전용으로 확인합니다.</strong>
      <p>내장 브라우저(WebView2)와 별도 Chrome 연결은 분리되어 있습니다. 이 화면에서는 로그인 정보나 페이지 내용을 저장하지 않고, 로컬 연결 상태만 확인합니다.</p>
    </div>
    <section class="chrome-debug-launch" aria-label="전용 디버깅 Chrome 시작">
      <div>
        <strong>전용 Chrome 세션</strong>
        <span>기존 Chrome과 분리된 프로필로 열고 localhost:9222 연결을 준비합니다.</span>
        <small>로그인 상태는 이 전용 프로필에만 저장됩니다.</small>
      </div>
      <button type="button" data-extension-action="launch-chrome-debug-session" ${state.chromeLaunchLoading ? "disabled" : ""}>${state.chromeLaunchLoading ? "Chrome 준비 중" : "디버깅 Chrome 열기"}</button>
    </section>
    ${state.chromeLaunchResult ? `<p class="chrome-debug-launch-result"><strong>${escapeHtml(state.chromeLaunchResult.detail || "전용 Chrome 세션을 준비했습니다.")}</strong><span>연결 주소: <code>${escapeHtml(state.chromeLaunchResult.endpoint || "http://127.0.0.1:9222")}</code></span><span>프로필: <code>${escapeHtml(state.chromeLaunchResult.profilePath || "")}</code></span></p>` : ""}
    <section class="external-connection-card" aria-label="Chrome DevTools MCP 연결">
      <header class="external-connection-card-header">
        <div class="external-connection-identity">
          <div class="skill-library-mark" aria-hidden="true"><img src="${iconPath("globe")}" alt="" /></div>
          <div>
            <strong>${connectionTitle}</strong>
            <span>${connectionDescription}</span>
          </div>
        </div>
        <span class="external-connection-status ${escapeHtml(status)}">${statusLabel}</span>
      </header>
      <div class="external-connection-body">
        <label class="external-connection-endpoint">
          <span>${endpointLabel}</span>
          <input id="external-connection-endpoint" type="url" inputmode="url" value="${escapeHtml(state.endpoint)}" placeholder="${endpointPlaceholder}" aria-label="${connectionTitle} 주소" />
        </label>
        <button type="button" class="external-connection-check" data-extension-action="check-chrome-devtools-connection" ${state.loading ? "disabled" : ""}>${state.loading ? "확인 중" : checkLabel}</button>
      </div>
      <div class="external-connection-result ${escapeHtml(status)}">
        <span class="external-connection-dot" aria-hidden="true"></span>
        <div>
          <strong>${escapeHtml(state.error || state.detail)}</strong>
          <small>${state.browser ? `브라우저 ${escapeHtml(state.browser)} · ` : ""}${externalConnectionCheckedAt(state.checkedAt)}</small>
        </div>
      </div>
      ${state.contextError ? `<p class="external-bridge-error">${escapeHtml(state.contextError)}</p>` : ""}
      ${chromeBridgeContextMarkup()}
      ${chromeDevtoolsMcpMarkup()}
      <details class="external-connection-scope">
        <summary>현재 연결 범위</summary>
        <ul>
          <li>지원 대상: 별도 Chrome의 localhost DevTools endpoint</li>
          <li>현재 단계: DevTools 연결 상태와 브라우저 버전 확인</li>
          <li>다음 단계: 승인된 읽기 결과를 프로젝트 근거로 저장</li>
          <li>제외 대상: 내장 WebView2 직접 조작, 자동 클릭, 로그인·제출 작업</li>
        </ul>
      </details>
    </section>`;
}

function skillHubMarkup() {
  const projects = ensureSkillProjectSelection();
  const projectOptions = skillProjectOptionsMarkup(projects);
  const selectedProject = projects.find(
    (project) => project.id === skillSettingsState.projectId,
  );
  const library = skillSettingsState.snapshot;
  return `<div class="extension-page-heading">
      <div>
        <h2>스킬</h2>
        <p>등록된 원본은 사용자 라이브러리에 한 번만 보관하고, 설치만 프로젝트별로 안전하게 격리합니다.</p>
      </div>
      <div class="extension-page-actions">
        <div class="skill-view-toggle" role="group" aria-label="스킬 보기 방식">
          <button type="button" data-extension-action="show-skill-list" aria-pressed="${String(extensionHubState.skillView === "list")}" aria-label="목록 보기" title="목록 보기"><img src="${iconPath("list-checks")}" alt="" /></button>
          <button type="button" data-extension-action="show-skill-grid" aria-pressed="${String(extensionHubState.skillView === "grid")}" aria-label="자동 그리드 보기" title="자동 그리드 보기"><img src="${iconPath("columns-2")}" alt="" /></button>
        </div>
        <button type="button" data-extension-action="refresh-skill-library">새로 고침</button>
        <button type="button" data-extension-action="register-skill-file">스킬 파일 등록</button>
        <button type="button" data-extension-action="register-skill-folder">폴더 등록</button>
      </div>
    </div>
    <div class="extension-skill-controls">
      <label class="extension-search">
        <img src="${iconPath("search")}" alt="" />
        <input id="extension-skill-search" type="search" value="${escapeHtml(extensionHubState.query)}" placeholder="스킬 검색" aria-label="스킬 검색" />
      </label>
      <label class="skill-project-selector">
        <span>현재 프로젝트</span>
        <select id="extension-skill-project" title="${escapeHtml(selectedProject?.name ?? "열린 프로젝트 없음")}" ${projects.length ? "" : "disabled"}>
          ${projects.length ? projectOptions : '<option value="">열린 프로젝트 없음</option>'}
        </select>
      </label>
    </div>
    ${skillSmokeTestMarkup()}
    <p class="skill-library-scope-note">플러그인과 스킬 원본은 모든 프로젝트에서 공유됩니다. YAML 머리말에 name과 description이 있는 Markdown은 원본을 변경하지 않고 표준 SKILL.md 스냅샷으로 등록합니다. 실제 설치 파일만 선택한 프로젝트 내부에 저장됩니다.</p>
    ${skillSettingsState.error ? `<p class="skill-library-error">${escapeHtml(skillSettingsState.error)}</p>` : ""}
    <div class="skill-library-list" id="extension-skill-list" data-view="${escapeHtml(extensionHubState.skillView)}"></div>
    <p class="skill-library-root">사용자 라이브러리: <code>${escapeHtml(library?.libraryRoot ?? "확인 전")}</code></p>`;
}

function renderExtensionHub() {
  if (!elements.extensionHub) return;
  const skillsActive = extensionHubState.activeTab === "skills";
  const pluginsActive = extensionHubState.activeTab === "plugins";
  const connectionsActive = extensionHubState.activeTab === "connections";
  elements.extensionHub.innerHTML = `<nav class="extension-tabs" aria-label="확장 기능 종류">
      <button type="button" data-extension-action="show-plugins" aria-selected="${String(pluginsActive)}">플러그인</button>
      <button type="button" data-extension-action="show-skills" aria-selected="${String(skillsActive)}">스킬</button>
      <button type="button" data-extension-action="show-connections" aria-selected="${String(connectionsActive)}">외부 연결</button>
    </nav>
    <div class="extension-page">${connectionsActive ? connectionHubMarkup() : skillsActive ? skillHubMarkup() : pluginHubMarkup()}</div>`;
  updateContextBar(extensionHubSurface);
  if (connectionsActive) {
    document
      .querySelector("#external-connection-endpoint")
      ?.addEventListener("input", (event) => {
        externalConnectionState.endpoint = event.target.value;
        externalConnectionState.status = "unknown";
        externalConnectionState.error = "";
        localStorage.setItem(
          EXTERNAL_CONNECTION_STORAGE_KEY,
          JSON.stringify({ endpoint: externalConnectionState.endpoint }),
        );
      });
    return;
  }
  if (!skillsActive) {
    renderPluginHubList();
    document
      .querySelector("#extension-plugin-search")
      ?.addEventListener("input", (event) => {
        pluginLibraryState.query = event.target.value;
        renderPluginHubList();
      });
    document
      .querySelector("#plugin-source-url")
      ?.addEventListener("input", (event) => {
        pluginLibraryState.sourceUrl = event.target.value;
      });
    document
      .querySelector("#plugin-import-form")
      ?.addEventListener("submit", (event) => {
        event.preventDefault();
        importGitHubPlugin();
      });
    return;
  }
  renderSkillHubList();
  document
    .querySelector("#extension-skill-search")
    ?.addEventListener("input", (event) => {
      extensionHubState.query = event.target.value;
      renderSkillHubList();
    });
  document
    .querySelector("#extension-skill-project")
    ?.addEventListener("change", (event) => {
      skillSettingsState.projectId = event.target.value;
      refreshSkillLibrarySettings();
    });
  document.querySelector(".skill-smoke-panel")?.addEventListener("toggle", (event) => {
    skillSmokeTestState.panelOpen = event.currentTarget.open;
  });
}

function renderExtensionHubIfVisible() {
  if (currentSurface().kind === "extension-hub") renderExtensionHub();
}

async function launchChromeDebugSession() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke || externalConnectionState.chromeLaunchLoading) return;
  externalConnectionState.chromeLaunchLoading = true;
  externalConnectionState.error = "";
  externalConnectionState.detail = "Skkima 전용 Chrome을 준비하고 있습니다.";
  renderExtensionHubIfVisible();
  try {
    const result = await invoke("launch_chrome_debug_session");
    externalConnectionState.chromeLaunchResult = result;
    externalConnectionState.endpoint = result.endpoint || "http://127.0.0.1:9222";
    externalConnectionState.status = "unknown";
    externalConnectionState.detail = result.detail || "전용 Chrome 세션을 준비했습니다.";
    localStorage.setItem(
      EXTERNAL_CONNECTION_STORAGE_KEY,
      JSON.stringify({ endpoint: externalConnectionState.endpoint }),
    );
    renderExtensionHubIfVisible();

    for (let attempt = 0; attempt < 4; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 350));
      await checkChromeDevtoolsConnection();
      if (externalConnectionState.status === "connected") break;
    }
  } catch (error) {
    externalConnectionState.status = "error";
    externalConnectionState.error = String(error);
    externalConnectionState.detail = "전용 Chrome을 준비하지 못했습니다.";
  } finally {
    externalConnectionState.chromeLaunchLoading = false;
    renderExtensionHubIfVisible();
  }
}

function setSkillViewMode(mode) {
  extensionHubState.skillView = saveSkillViewMode(localStorage, mode);
  renderExtensionHubIfVisible();
}

async function refreshSkillLibrarySettings() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    skillSettingsState.error = "스킬 관리는 쓰끼마 Windows 앱에서 사용할 수 있습니다.";
    renderExtensionHubIfVisible();
    return;
  }
  skillSettingsState.loading = true;
  skillSettingsState.error = "";
  renderExtensionHubIfVisible();
  try {
    const snapshot = await invoke("list_skill_library");
    skillSettingsState.snapshot = snapshot;
    skillSettingsState.statuses = new Map();
    skillSettingsState.platforms = [];
    const project = workspaceState.projects.find(
      (item) => item.id === skillSettingsState.projectId,
    );
    if (project) {
      const [installationSnapshot, smokeSnapshot] = await Promise.all([
        invoke("inspect_project_skill_installations", { projectRoot: project.path }),
        invoke("inspect_skill_smoke_tests", { projectRoot: project.path }),
      ]);
      skillSettingsState.platforms = installationSnapshot.platforms ?? [];
      skillSettingsState.statuses = new Map(
        (installationSnapshot.installations ?? []).map((status) => [
          skillStatusKey(status.skillId, status.platform),
          status,
        ]),
      );
      skillSmokeTestState.tests = new Map(
        (smokeSnapshot.tests ?? []).map((test) => [test.platform, test]),
      );
    }
  } catch (error) {
    skillSettingsState.error = String(error);
  } finally {
    skillSettingsState.loading = false;
    renderExtensionHubIfVisible();
    scheduleSkillSmokeTestPoll();
  }
}

function scheduleSkillSmokeTestPoll() {
  if (skillSmokeTestState.pollTimer) {
    window.clearTimeout(skillSmokeTestState.pollTimer);
    skillSmokeTestState.pollTimer = null;
  }
  const active = [...skillSmokeTestState.tests.values()].some((test) =>
    ["prepared", "running"].includes(test.state),
  );
  if (!active || currentSurface().kind !== "extension-hub") return;
  skillSmokeTestState.pollTimer = window.setTimeout(
    () => refreshSkillSmokeTests(),
    2000,
  );
}

async function refreshSkillSmokeTests() {
  const invoke = window.__TAURI__?.core?.invoke;
  const project = workspaceState.projects.find(
    (item) => item.id === skillSettingsState.projectId,
  );
  if (!invoke || !project || skillSmokeTestState.loading) return;
  skillSmokeTestState.loading = true;
  skillSmokeTestState.error = "";
  try {
    const snapshot = await invoke("inspect_skill_smoke_tests", {
      projectRoot: project.path,
    });
    skillSmokeTestState.tests = new Map(
      (snapshot.tests ?? []).map((test) => [test.platform, test]),
    );
  } catch (error) {
    skillSmokeTestState.error = String(error);
  } finally {
    skillSmokeTestState.loading = false;
    renderExtensionHubIfVisible();
    scheduleSkillSmokeTestPoll();
  }
}

function requestSkillSmokeTest(platform) {
  const project = workspaceState.projects.find(
    (item) => item.id === skillSettingsState.projectId,
  );
  const platformInfo = skillSettingsState.platforms.find(
    (item) => item.platform === platform,
  );
  if (!project || !platformInfo) return;
  skillSmokeTestState.panelOpen = true;
  skillSmokeTestState.pendingPlatform = platform;
  showDialog(
    "실제 스킬 인식 테스트",
    `<div class="confirmation-copy">
       <strong>${escapeHtml(platformInfo.label)} · ${escapeHtml(project.name)}</strong>
       <p>플랫폼의 비대화형 안전 모드로 전용 테스트 스킬을 호출합니다. 지정된 증명 파일 한 개 외에 프로젝트가 변경되면 실패로 판정합니다.</p>
     </div>
     <div class="dialog-actions">
       <button type="button" data-action="close-dialog">취소</button>
       <button class="primary" type="button" data-action="confirm-skill-smoke-test">테스트 시작</button>
     </div>`,
  );
}

async function confirmSkillSmokeTest() {
  const invoke = window.__TAURI__?.core?.invoke;
  const project = workspaceState.projects.find(
    (item) => item.id === skillSettingsState.projectId,
  );
  const platform = skillSmokeTestState.pendingPlatform;
  if (!invoke || !project || !platform) return;
  closeDialog();
  skillSmokeTestState.panelOpen = true;
  skillSmokeTestState.loading = true;
  skillSmokeTestState.error = "";
  renderExtensionHubIfVisible();
  try {
    const test = await invoke("launch_skill_smoke_test", {
      projectRoot: project.path,
      platform,
      approved: true,
    });
    skillSmokeTestState.tests.set(platform, test);
  } catch (error) {
    skillSmokeTestState.error = String(error);
  } finally {
    skillSmokeTestState.pendingPlatform = null;
    skillSmokeTestState.loading = false;
    renderExtensionHubIfVisible();
    scheduleSkillSmokeTestPoll();
  }
}

async function cleanupSkillSmokeTest(testId) {
  const invoke = window.__TAURI__?.core?.invoke;
  const project = workspaceState.projects.find(
    (item) => item.id === skillSettingsState.projectId,
  );
  if (!invoke || !project || !testId) return;
  skillSmokeTestState.loading = true;
  skillSmokeTestState.error = "";
  try {
    const snapshot = await invoke("cleanup_skill_smoke_test", {
      projectRoot: project.path,
      testId,
    });
    skillSmokeTestState.tests = new Map(
      (snapshot.tests ?? []).map((test) => [test.platform, test]),
    );
    await refreshSkillLibrarySettings();
  } catch (error) {
    skillSmokeTestState.error = String(error);
  } finally {
    skillSmokeTestState.loading = false;
    renderExtensionHubIfVisible();
  }
}

async function refreshPluginLibrary() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    pluginLibraryState.error = "플러그인 관리는 쓰끼마 Windows 앱에서 사용할 수 있습니다.";
    renderExtensionHubIfVisible();
    return;
  }
  pluginLibraryState.loading = true;
  pluginLibraryState.error = "";
  renderExtensionHubIfVisible();
  try {
    pluginLibraryState.snapshot = await invoke("list_plugin_library");
    if (!skillSettingsState.snapshot) {
      skillSettingsState.snapshot = await invoke("list_skill_library");
    }
  } catch (error) {
    pluginLibraryState.error = String(error);
  } finally {
    pluginLibraryState.loading = false;
    renderExtensionHubIfVisible();
  }
}

async function checkChromeDevtoolsConnection() {
  const invoke = window.__TAURI__?.core?.invoke;
  const endpoint = externalConnectionState.endpoint.trim();
  if (!invoke || !endpoint || externalConnectionState.loading) return;
  externalConnectionState.loading = true;
  externalConnectionState.error = "";
  externalConnectionState.detail = "Chrome DevTools 연결을 확인하고 있습니다.";
  renderExtensionHubIfVisible();
  try {
    const result = isSkkimaBridgeEndpoint(endpoint)
      ? await invoke("inspect_chrome_bridge_connection")
      : await invoke("inspect_chrome_devtools_connection", { endpoint });
    Object.assign(externalConnectionState, {
      endpoint: result.endpoint ?? endpoint,
      status: result.status ?? "unknown",
      detail: result.detail ?? "연결 상태를 확인했습니다.",
      browser: result.browser ?? "",
      websocketDebuggerUrl: result.websocketDebuggerUrl ?? "",
      checkedAt: result.checkedAt ?? "",
      error: "",
    });
  } catch (error) {
    externalConnectionState.status = "error";
    externalConnectionState.error = String(error);
    externalConnectionState.detail = "연결 상태를 확인하지 못했습니다.";
    externalConnectionState.browser = "";
    externalConnectionState.websocketDebuggerUrl = "";
  } finally {
    externalConnectionState.loading = false;
    renderExtensionHubIfVisible();
  }
}

async function startChromeDevtoolsMcp() {
  const invoke = window.__TAURI__?.core?.invoke;
  const endpoint = externalConnectionState.endpoint.trim();
  if (!invoke || !endpoint || externalConnectionState.mcpLoading) return;
  if (isSkkimaBridgeEndpoint(endpoint)) {
    externalConnectionState.mcpStatus = "error";
    externalConnectionState.mcpError = "3217 is the Chrome Bridge address. MCP uses a Chrome DevTools endpoint such as 9222.";
    renderExtensionHubIfVisible();
    return;
  }
  externalConnectionState.mcpLoading = true;
  externalConnectionState.mcpError = "";
  externalConnectionState.mcpStatus = "unknown";
  externalConnectionState.mcpEvidenceSaved = null;
  renderExtensionHubIfVisible();
  try {
    const result = await invoke("start_chrome_devtools_mcp", { endpoint });
    externalConnectionState.mcpConnection = result;
    externalConnectionState.mcpStatus = result.status ?? "connected";
    externalConnectionState.mcpError = "";
  } catch (error) {
    externalConnectionState.mcpConnection = null;
    externalConnectionState.mcpStatus = "error";
    externalConnectionState.mcpError = String(error);
  } finally {
    externalConnectionState.mcpLoading = false;
    renderExtensionHubIfVisible();
  }
}

async function stopChromeDevtoolsMcp() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke || externalConnectionState.mcpLoading) return;
  try {
    await invoke("stop_chrome_devtools_mcp");
    externalConnectionState.mcpConnection = null;
    externalConnectionState.mcpReadResult = null;
    externalConnectionState.mcpEvidenceSaved = null;
    externalConnectionState.mcpStatus = "unknown";
    externalConnectionState.mcpError = "";
  } catch (error) {
    externalConnectionState.mcpStatus = "error";
    externalConnectionState.mcpError = String(error);
  } finally {
    renderExtensionHubIfVisible();
  }
}

async function readChromeDevtoolsMcp() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke || externalConnectionState.mcpLoading || !externalConnectionState.mcpConnection) return;
  externalConnectionState.mcpLoading = true;
  externalConnectionState.mcpStatus = "reading";
  externalConnectionState.mcpError = "";
  renderExtensionHubIfVisible();
  try {
    externalConnectionState.mcpReadResult = await invoke("read_chrome_devtools_mcp");
    externalConnectionState.mcpStatus = "connected";
    externalConnectionState.mcpEvidenceSaved = null;
  } catch (error) {
    externalConnectionState.mcpStatus = "error";
    externalConnectionState.mcpError = String(error);
  } finally {
    externalConnectionState.mcpLoading = false;
    renderExtensionHubIfVisible();
  }
}

async function saveChromeDevtoolsMcpEvidence() {
  const invoke = window.__TAURI__?.core?.invoke;
  const project = activeProject();
  const result = externalConnectionState.mcpReadResult;
  if (!invoke || !project || !result || externalConnectionState.mcpLoading) return;
  externalConnectionState.mcpLoading = true;
  externalConnectionState.mcpError = "";
  renderExtensionHubIfVisible();
  try {
    const session = activeSession();
    const evidence = {
      schemaVersion: "1.0.0",
      evidenceId: `mcp-read-${Date.now().toString(36)}`,
      capturedAt: result.capturedAt || new Date().toISOString(),
      endpoint: result.endpoint,
      pages: result.pages,
      snapshotText: result.snapshotText,
      projectId: project.id ?? null,
      projectName: project.name ?? project.path,
      sessionId: session?.id ?? null,
      sessionName: session?.title ?? "독립 브라우저 작업",
      source: "chrome-devtools-mcp-read-only",
    };
    externalConnectionState.mcpEvidenceSaved = await invoke(
      "save_chrome_devtools_mcp_evidence",
      { projectRoot: project.path, evidence },
    );
  } catch (error) {
    externalConnectionState.mcpError = String(error);
  } finally {
    externalConnectionState.mcpLoading = false;
    renderExtensionHubIfVisible();
  }
}

async function refreshChromeBridgeContext() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke || externalConnectionState.contextLoading) return;
  externalConnectionState.contextLoading = true;
  externalConnectionState.contextError = "";
  renderExtensionHubIfVisible();
  try {
    const history = await invoke("get_chrome_bridge_history");
    const records = Array.isArray(history) ? history : [];
    externalConnectionState.contextHistory = records;
    externalConnectionState.latestContext = records[0]?.context ?? null;
    externalConnectionState.selectedContextKey = records[0]
      ? chromeContextKey(records[0])
      : "";
    externalConnectionState.contextFetched = true;
  } catch (error) {
    externalConnectionState.contextError = String(error);
    externalConnectionState.contextFetched = true;
  } finally {
    externalConnectionState.contextLoading = false;
    renderExtensionHubIfVisible();
  }
}

async function confirmChromeBridgeContextDeletion() {
  const invoke = window.__TAURI__?.core?.invoke;
  const snapshot = externalConnectionState.pendingContextDeletion;
  if (!invoke || !snapshot) return;
  try {
    const history = await invoke("delete_chrome_bridge_context_record", {
      receivedAt: snapshot.receivedAt,
      capturedAt: snapshot.context?.capturedAt,
      pageUrl: snapshot.context?.pageUrl,
    });
    const records = Array.isArray(history) ? history : [];
    externalConnectionState.contextHistory = records;
    externalConnectionState.latestContext = records[0]?.context ?? null;
    externalConnectionState.selectedContextKey = records[0]
      ? chromeContextKey(records[0])
      : "";
    externalConnectionState.contextError = "";
  } catch (error) {
    externalConnectionState.contextError = String(error);
  } finally {
    closeDialog();
    renderExtensionHubIfVisible();
  }
}

async function confirmClearChromeBridgeContextHistory() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke || !externalConnectionState.pendingContextClear) return;
  try {
    await invoke("clear_chrome_bridge_context_history");
    externalConnectionState.contextHistory = [];
    externalConnectionState.latestContext = null;
    externalConnectionState.selectedContextKey = "";
    externalConnectionState.contextError = "";
  } catch (error) {
    externalConnectionState.contextError = String(error);
  } finally {
    closeDialog();
    renderExtensionHubIfVisible();
  }
}

async function importGitHubPlugin() {
  const invoke = window.__TAURI__?.core?.invoke;
  const sourceUrl = pluginLibraryState.sourceUrl.trim();
  if (!invoke || !sourceUrl || pluginLibraryState.loading) return;
  pluginLibraryState.loading = true;
  pluginLibraryState.error = "";
  renderExtensionHubIfVisible();
  try {
    await invoke("import_github_plugin", { sourceUrl });
    pluginLibraryState.sourceUrl = "";
    pluginLibraryState.snapshot = await invoke("list_plugin_library");
  } catch (error) {
    pluginLibraryState.error = String(error);
  } finally {
    pluginLibraryState.loading = false;
    renderExtensionHubIfVisible();
  }
}

async function registerPluginSkill(pluginId, relativePath) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return;
  pluginLibraryState.loading = true;
  pluginLibraryState.error = "";
  renderExtensionHubIfVisible();
  try {
    await invoke("register_plugin_skill", { pluginId, relativePath });
    skillSettingsState.snapshot = await invoke("list_skill_library");
  } catch (error) {
    pluginLibraryState.error = String(error);
  } finally {
    pluginLibraryState.loading = false;
    renderExtensionHubIfVisible();
  }
}

function requestPluginRemoval(pluginId) {
  const plugin = pluginLibraryState.snapshot?.plugins?.find(
    (item) => item.pluginId === pluginId,
  );
  if (!plugin) return;
  pluginLibraryState.pendingRemoval = pluginId;
  showDialog(
    "플러그인 보관 해제",
    `<div class="confirmation-copy"><strong>${escapeHtml(plugin.owner)} / ${escapeHtml(plugin.repository)}</strong><p>가져온 저장소 스냅샷을 제거합니다. 이미 사용자 스킬 라이브러리에 등록한 스킬 복사본은 유지됩니다.</p></div><div class="dialog-actions"><button type="button" data-action="close-dialog">취소</button><button class="danger" type="button" data-action="confirm-plugin-removal">보관 해제</button></div>`,
  );
}

async function confirmPluginRemoval() {
  const invoke = window.__TAURI__?.core?.invoke;
  const pluginId = pluginLibraryState.pendingRemoval;
  if (!invoke || !pluginId) return;
  try {
    await invoke("remove_plugin", { pluginId });
    closeDialog();
    await refreshPluginLibrary();
  } catch (error) {
    pluginLibraryState.error = String(error);
    closeDialog();
    renderExtensionHubIfVisible();
  }
}

async function registerLocalSkill(kind) {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return;
  try {
    const sourcePath = await invoke(
      kind === "folder" ? "pick_local_skill_folder" : "pick_local_skill",
    );
    if (!sourcePath) return;
    await invoke("register_local_skill", { sourcePath });
    await refreshSkillLibrarySettings();
  } catch (error) {
    skillSettingsState.error = String(error);
    renderExtensionHubIfVisible();
  }
}

async function changeProjectSkillInstallation(skillId, platform, action) {
  const invoke = window.__TAURI__?.core?.invoke;
  const project = workspaceState.projects.find(
    (item) => item.id === skillSettingsState.projectId,
  );
  if (!invoke || !project) return;
  skillSettingsState.loading = true;
  renderExtensionHubIfVisible();
  try {
    const command =
      action === "install"
        ? "install_project_skill"
        : action === "uninstall"
          ? "uninstall_project_skill"
          : "inspect_project_skill_installations";
    await invoke(command, { projectRoot: project.path, skillId, platform });
    await refreshSkillLibrarySettings();
  } catch (error) {
    skillSettingsState.error = String(error);
  } finally {
    skillSettingsState.loading = false;
    renderExtensionHubIfVisible();
  }
}

function showSettings(view = "archive") {
  const archiveActive = view === "archive";
  const archivedProjects = [
    ...new Map(
      listArchivedTaskSessions(workspaceState).map(({ project }) => [
        project.id,
        project,
      ]),
    ).values(),
  ].sort((left, right) => left.name.localeCompare(right.name, "ko"));
  if (
    archiveSettingsState.projectId !== "all" &&
    !archivedProjects.some(
      (project) => project.id === archiveSettingsState.projectId,
    )
  ) {
    archiveSettingsState.projectId = "all";
  }
  const projectOptions = archivedProjects
    .map(
      (project) =>
        `<option value="${escapeHtml(project.id)}"${archiveSettingsState.projectId === project.id ? " selected" : ""}>${escapeHtml(project.name)}</option>`,
    )
    .join("");
  let content = archiveActive
    ? `<div class="settings-heading">
         <div>
           <h3>아카이브 보관함</h3>
           <p>프로젝트에서 숨긴 작업을 확인하고 복원하거나 보관 기록에서 삭제합니다.</p>
         </div>
         <span class="archive-count" id="settings-archive-count">0</span>
       </div>
       <div class="archive-controls">
         <input class="archive-search" id="settings-archive-search" type="search" value="${escapeHtml(archiveSettingsState.query)}" placeholder="보관된 작업 검색" aria-label="보관된 작업 검색" />
         <select id="settings-archive-project" aria-label="프로젝트 필터">
           <option value="all">모든 프로젝트</option>
           ${projectOptions}
         </select>
         <select id="settings-archive-source" aria-label="작업 유형 필터">
           <option value="all">모든 유형</option>
           <option value="workflow"${archiveSettingsState.source === "workflow" ? " selected" : ""}>Workflow</option>
           <option value="local"${archiveSettingsState.source === "local" ? " selected" : ""}>로컬</option>
         </select>
         <select id="settings-archive-sort" aria-label="정렬 방식">
           <option value="newest"${archiveSettingsState.sort === "newest" ? " selected" : ""}>최근 보관순</option>
           <option value="oldest"${archiveSettingsState.sort === "oldest" ? " selected" : ""}>오래된 보관순</option>
           <option value="title"${archiveSettingsState.sort === "title" ? " selected" : ""}>작업 이름순</option>
           <option value="project"${archiveSettingsState.sort === "project" ? " selected" : ""}>프로젝트순</option>
         </select>
         <button type="button" data-settings-action="clear-archive-filters">초기화</button>
       </div>
       <div class="archive-selection-bar">
         <span id="settings-selected-count">선택 0개</span>
         <div class="archive-selection-actions">
           <button type="button" id="settings-restore-selected" data-settings-action="restore-selected-archives" disabled>선택 복원</button>
           <button class="danger" type="button" id="settings-delete-selected" data-settings-action="request-delete-selected-archives" disabled>선택 삭제</button>
         </div>
       </div>
       <div class="archive-list" id="settings-archive-list"></div>
       <div class="archive-pagination">
         <button type="button" id="settings-archive-previous" data-settings-action="archive-previous-page">이전</button>
         <span id="settings-archive-page-label">1 / 1</span>
         <button type="button" id="settings-archive-next" data-settings-action="archive-next-page">다음</button>
       </div>`
    : `<div class="settings-heading">
         <div>
           <h3>앱 정보</h3>
            <p>쓰끼마 Desktop</p>
         </div>
       </div>
       <dl class="shortcut-list">
         <dt>버전</dt><dd>0.1.1</dd>
         <dt>저장 방식</dt><dd>이 PC의 로컬 상태</dd>
         <dt>프로젝트 파일</dt><dd>읽기 전용 연결</dd>
       </dl>`;

  showDialog(
    "설정",
    `<div class="settings-shell">
       <nav class="settings-nav" aria-label="설정 항목">
         <button type="button" class="${archiveActive ? "active" : ""}" data-settings-action="show-archive-settings">
           <img src="${iconPath("archive")}" alt="" />
           <span>아카이브 보관함</span>
         </button>
         <button type="button" class="${view === "about" ? "active" : ""}" data-settings-action="show-about-settings">
           <img src="${iconPath("settings")}" alt="" />
           <span>정보</span>
         </button>
       </nav>
       <section class="settings-content ${archiveActive ? "archive-settings-content" : "about-settings-content"}">${content}</section>
     </div>`,
    { wide: true },
  );

  if (archiveActive) {
    renderArchiveSettings();
    const searchInput = document.querySelector("#settings-archive-search");
    searchInput?.addEventListener("input", (event) => {
      archiveSettingsState.query = event.target.value;
      archiveSettingsState.page = 1;
      renderArchiveSettings();
    });
    document
      .querySelector("#settings-archive-project")
      ?.addEventListener("change", (event) => {
        archiveSettingsState.projectId = event.target.value;
        archiveSettingsState.page = 1;
        renderArchiveSettings();
      });
    document
      .querySelector("#settings-archive-source")
      ?.addEventListener("change", (event) => {
        archiveSettingsState.source = event.target.value;
        archiveSettingsState.page = 1;
        renderArchiveSettings();
      });
    document
      .querySelector("#settings-archive-sort")
      ?.addEventListener("change", (event) => {
        archiveSettingsState.sort = event.target.value;
        archiveSettingsState.page = 1;
        renderArchiveSettings();
      });
  }
}

function getAppWindow() {
  return window.__TAURI__?.window?.getCurrentWindow?.() ?? null;
}

let windowMaximizeRequestInFlight = false;

async function toggleWindowMaximize(event) {
  event?.preventDefault();
  event?.stopPropagation();
  if (windowMaximizeRequestInFlight) return;

  const appWindow = getAppWindow();
  if (!appWindow) return;

  windowMaximizeRequestInFlight = true;
  try {
    const maximized = await appWindow.isMaximized();
    if (maximized) {
      await appWindow.unmaximize();
    } else {
      await appWindow.maximize();
    }
  } catch (error) {
    console.error(error);
  } finally {
    window.setTimeout(() => {
      windowMaximizeRequestInFlight = false;
    }, 180);
  }
}

function showExtensionHub(tab = extensionHubState.activeTab) {
  const currentProjectId = workspaceState.activeProjectId;
  selectionGuard.begin(null);
  extensionHubState.activeTab = ["plugins", "connections"].includes(tab)
    ? tab
    : "skills";
  if (extensionHubState.activeTab === "skills" && currentProjectId) {
    skillSettingsState.projectId = currentProjectId;
  }
  closeInspector();
  renderSurface(extensionHubSurface);
  if (
    extensionHubState.activeTab === "connections" &&
    !externalConnectionState.contextFetched &&
    !externalConnectionState.contextLoading
  ) {
    refreshChromeBridgeContext();
  }
  if (
    extensionHubState.activeTab === "skills" &&
    !skillSettingsState.snapshot &&
    !skillSettingsState.loading
  ) {
    refreshSkillLibrarySettings();
  }
  if (
    extensionHubState.activeTab === "plugins" &&
    !pluginLibraryState.snapshot &&
    !pluginLibraryState.loading
  ) {
    refreshPluginLibrary();
  }
  if (uiState.sidebar.mode === "peek") applySidebarUi("pointer-leave");
}

async function runWindowAction(action) {
  if (action === "toggleMaximize") {
    await toggleWindowMaximize();
    return;
  }
  const appWindow = getAppWindow();
  if (!appWindow) return;

  try {
    await appWindow[action]();
  } catch (error) {
    console.error(error);
  }
}

const actions = {
  "toggle-sidebar": () => applySidebarUi("toggle-pin"),
  "open-global-search": openGlobalSearch,
  "show-extensions": () => showExtensionHub(),
  "show-browser-workspace": toggleBrowserPanel,
  "toggle-browser-panel": toggleBrowserPanel,
  "close-browser-panel": closeBrowserPanel,
  "toggle-browser-focus": toggleBrowserFocusMode,
  "show-browser-evidence-history": showBrowserEvidenceHistory,
  "new-project": showNewProjectDialog,
  "open-project-folder": openProjectFolder,
  "new-task": createNewTask,
  "show-workspaces": () => {
    selectionGuard.begin(null);
    workspaceState.activeProjectId = null;
    workspaceState.activeSessionId = null;
    persistWorkspaceState();
    renderSidebar();
    renderSurface(workspaceSurface);
  },
  "close-window": () => runWindowAction("close"),
  "navigate-back": () => navigateHistory(-1),
  "navigate-forward": () => navigateHistory(1),
  "zoom-in": () => setZoom(uiState.zoom + 0.1),
  "zoom-out": () => setZoom(uiState.zoom - 0.1),
  "zoom-reset": () => setZoom(1),
  "rename-task": showRenameDialog,
  "show-task-info": showTaskInfo,
  "show-workflow-details": showWorkflowDetails,
  "launch-workflow-cli": () => showCliLaunchDialog(),
  "show-cli-execution": () => showCliExecutionDetails(),
  "retry-cli-execution": retryCliExecution,
  "stop-cli-execution": stopCliExecution,
  "show-local-environment": showLocalEnvironment,
  "refresh-local-environment": showLocalEnvironment,
  "show-settings": () => showSettings("archive"),
  "toggle-split": toggleSplitView,
  "close-inspector": closeInspector,
  "close-dialog": closeDialog,
  "confirm-task-archive": confirmTaskArchive,
  "confirm-project-removal": confirmProjectRemoval,
  "confirm-archive-deletion": confirmArchivedTaskDeletion,
  "confirm-chrome-context-delete": confirmChromeBridgeContextDeletion,
  "confirm-chrome-context-clear": confirmClearChromeBridgeContextHistory,
  "confirm-plugin-removal": confirmPluginRemoval,
  "confirm-skill-smoke-test": confirmSkillSmokeTest,
  "show-shortcuts": () =>
    showDialog(
      "키보드 단축키",
      `<dl class="shortcut-list">
        <dt>사이드 패널</dt><dd>Ctrl+B</dd>
        <dt>프로젝트 검색</dt><dd>Ctrl+P</dd>
        <dt>새 프로젝트</dt><dd>Ctrl+Shift+N</dd>
        <dt>프로젝트 폴더 열기</dt><dd>Ctrl+O</dd>
        <dt>새 작업</dt><dd>Ctrl+N</dd>
        <dt>작업 공간</dt><dd>Ctrl+1</dd>
        <dt>뒤로 / 앞으로</dt><dd>Alt+← / Alt+→</dd>
        <dt>화면 배율</dt><dd>Ctrl++ / Ctrl+-</dd>
      </dl>`,
    ),
  "show-about": () =>
    showDialog(
      "쓰끼마",
      `<p>쓰끼마 Desktop</p>
        <p>버전 0.1.1</p>
        <p>Schema Workflow 엔진의 프로젝트 폴더, 작업 세션, 실행 기록을 하나의 흐름으로 관리하는 Windows 앱입니다.</p>`,
    ),
};

document.querySelectorAll(".menu-trigger").forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    openMenu(trigger.dataset.menu);
  });
});

document.addEventListener("click", (event) => {
  const contextActionButton = event.target.closest("[data-context-action]");
  if (contextActionButton) {
    handleSurfaceContextAction(contextActionButton.dataset.contextAction);
    return;
  }

  if (!event.target.closest("#surface-context-menu")) {
    closeSurfaceContextMenu();
  }

  const workflowViewButton = event.target.closest("[data-workflow-view]");
  if (workflowViewButton) {
    setWorkflowView(workflowViewButton.dataset.workflowView);
    return;
  }

  const inspectorTarget = event.target.closest("[data-inspector-kind]");
  if (inspectorTarget) {
    const { inspectorKind, filePath, activityIndex } =
      inspectorTarget.dataset;
    openInspector(inspectorKind, {
      path: filePath,
      index: activityIndex,
    });
    return;
  }

  const extensionButton = event.target.closest("[data-extension-action]");
  if (extensionButton) {
    const {
      extensionAction,
      skillId,
      pluginId,
      relativePath,
      platform,
      testId,
      contextKey,
    } =
      extensionButton.dataset;
    if (extensionAction === "show-plugins") {
      showExtensionHub("plugins");
    } else if (extensionAction === "show-skills") {
      showExtensionHub("skills");
    } else if (extensionAction === "show-connections") {
      showExtensionHub("connections");
    } else if (extensionAction === "launch-chrome-debug-session") {
      launchChromeDebugSession();
    } else if (extensionAction === "check-chrome-devtools-connection") {
      checkChromeDevtoolsConnection();
    } else if (extensionAction === "start-chrome-devtools-mcp") {
      startChromeDevtoolsMcp();
    } else if (extensionAction === "stop-chrome-devtools-mcp") {
      stopChromeDevtoolsMcp();
    } else if (extensionAction === "read-chrome-devtools-mcp") {
      readChromeDevtoolsMcp();
    } else if (extensionAction === "save-chrome-devtools-mcp-evidence") {
      saveChromeDevtoolsMcpEvidence();
    } else if (extensionAction === "refresh-chrome-context") {
      refreshChromeBridgeContext();
    } else if (extensionAction === "select-chrome-context") {
      selectChromeBridgeContext(contextKey);
    } else if (extensionAction === "delete-chrome-context") {
      requestChromeBridgeContextDeletion(contextKey);
    } else if (extensionAction === "clear-chrome-context-history") {
      requestClearChromeBridgeContextHistory();
    } else if (extensionAction === "show-skill-list") {
      setSkillViewMode("list");
    } else if (extensionAction === "show-skill-grid") {
      setSkillViewMode("grid");
    } else if (extensionAction === "register-skill-file") {
      registerLocalSkill("file");
    } else if (extensionAction === "register-skill-folder") {
      registerLocalSkill("folder");
    } else if (extensionAction === "refresh-skill-library") {
      refreshSkillLibrarySettings();
    } else if (extensionAction === "refresh-plugin-library") {
      refreshPluginLibrary();
    } else if (extensionAction === "register-plugin-skill") {
      registerPluginSkill(pluginId, relativePath);
    } else if (extensionAction === "request-remove-plugin") {
      requestPluginRemoval(pluginId);
    } else if (extensionAction === "install-platform-skill") {
      changeProjectSkillInstallation(skillId, platform, "install");
    } else if (extensionAction === "uninstall-platform-skill") {
      changeProjectSkillInstallation(skillId, platform, "uninstall");
    } else if (extensionAction === "request-smoke-test") {
      requestSkillSmokeTest(platform);
    } else if (extensionAction === "refresh-smoke-tests") {
      refreshSkillSmokeTests();
    } else if (extensionAction === "cleanup-smoke-test") {
      cleanupSkillSmokeTest(testId);
    }
    return;
  }

  const settingsButton = event.target.closest("[data-settings-action]");
  if (settingsButton) {
    const { settingsAction, projectId, sessionId } = settingsButton.dataset;
    if (settingsAction === "show-archive-settings") {
      showSettings("archive");
    } else if (settingsAction === "show-about-settings") {
      showSettings("about");
    } else if (settingsAction === "restore-archived-task") {
      restoreArchivedTask(projectId, sessionId);
    } else if (settingsAction === "restore-selected-archives") {
      restoreSelectedArchivedTasks();
    } else if (settingsAction === "request-delete-archived-task") {
      requestArchivedTaskDeletion([{ projectId, sessionId }]);
    } else if (settingsAction === "request-delete-selected-archives") {
      const selectedTasks = listArchivedTaskSessions(workspaceState)
        .filter(({ project, session }) =>
          archiveSettingsState.selected.has(
            archiveTaskKey(project.id, session.id),
          ),
        )
        .map(({ project, session }) => ({
          projectId: project.id,
          sessionId: session.id,
        }));
      requestArchivedTaskDeletion(selectedTasks);
    } else if (settingsAction === "clear-archive-filters") {
      archiveSettingsState.query = "";
      archiveSettingsState.projectId = "all";
      archiveSettingsState.source = "all";
      archiveSettingsState.sort = "newest";
      archiveSettingsState.page = 1;
      showSettings("archive");
    } else if (settingsAction === "archive-previous-page") {
      archiveSettingsState.page = Math.max(1, archiveSettingsState.page - 1);
      renderArchiveSettings();
    } else if (settingsAction === "archive-next-page") {
      archiveSettingsState.page += 1;
      renderArchiveSettings();
    }
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    const action = actions[actionButton.dataset.action];
    closeMenus();
    action?.();
    return;
  }

  const sidebarButton = event.target.closest("[data-sidebar-action]");
  if (sidebarButton) {
    const { sidebarAction, projectId, sessionId } = sidebarButton.dataset;
    if (sidebarAction === "select-project") {
      selectSidebarProject(projectId);
    } else if (sidebarAction === "select-session") {
      selectSidebarSession(projectId, sessionId);
    } else if (sidebarAction === "toggle-project-pin") {
      workspaceState = toggleProjectPin(workspaceState, projectId);
      persistWorkspaceState();
      renderSidebar();
    } else if (sidebarAction === "request-task-archive") {
      requestTaskArchive(projectId, sessionId);
    } else if (sidebarAction === "restore-task") {
      restoreArchivedTask(projectId, sessionId);
    }
    return;
  }

  if (!event.target.closest(".menu")) closeMenus();
});

document.addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-archive-selection='task']");
  if (!checkbox) return;
  const key = archiveTaskKey(
    checkbox.dataset.projectId,
    checkbox.dataset.sessionId,
  );
  if (checkbox.checked) {
    archiveSettingsState.selected.add(key);
  } else {
    archiveSettingsState.selected.delete(key);
  }
  renderArchiveSettings();
});

elements.back.addEventListener("click", () => navigateHistory(-1));
elements.forward.addEventListener("click", () => navigateHistory(1));
elements.dialogClose.addEventListener("click", closeDialog);
elements.modal.addEventListener("click", (event) => {
  if (event.target === elements.modal) closeDialog();
});

elements.searchBackdrop.addEventListener("click", (event) => {
  if (event.target === elements.searchBackdrop) closeGlobalSearch();
});
elements.globalSearchInput.addEventListener("input", renderGlobalSearchResults);
elements.globalSearchInput.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown") return;
  const firstResult = elements.globalSearchResults.querySelector(
    ".global-search-result",
  );
  if (firstResult) {
    event.preventDefault();
    firstResult.focus();
  }
});
elements.globalSearchResults.addEventListener("click", (event) => {
  const result = event.target.closest(".global-search-result");
  if (!result) return;

  if (result.dataset.searchSessionId) {
    selectSidebarSession(
      result.dataset.searchProjectId,
      result.dataset.searchSessionId,
    );
  } else {
    selectSidebarProject(result.dataset.searchProjectId);
  }
  closeGlobalSearch();
});
elements.globalSearchResults.addEventListener("keydown", (event) => {
  if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
  const results = [
    ...elements.globalSearchResults.querySelectorAll(".global-search-result"),
  ];
  const currentIndex = results.indexOf(document.activeElement);
  if (currentIndex < 0) return;

  event.preventDefault();
  const offset = event.key === "ArrowDown" ? 1 : -1;
  const nextIndex = Math.min(
    results.length - 1,
    Math.max(0, currentIndex + offset),
  );
  results[nextIndex]?.focus();
});

elements.sidebarEdge.addEventListener("mouseenter", scheduleSidebarOpen);
elements.sidebar.addEventListener("mouseenter", () => {
  clearTimeout(sidebarCloseTimer);
});
elements.sidebar.addEventListener("mouseleave", handleSidebarLeave);
elements.sidebar.addEventListener("focusin", () => {
  clearTimeout(sidebarCloseTimer);
});
elements.sidebar.addEventListener("focusout", handleSidebarLeave);

document.querySelector("#window-minimize").addEventListener("click", () => {
  runWindowAction("minimize");
});
document.querySelector("#window-maximize").addEventListener("click", () => {
  runWindowAction("toggleMaximize");
});
document.querySelector("#window-close").addEventListener("click", () => {
  runWindowAction("close");
});

document.querySelector(".title-drag-region").addEventListener("dblclick", (event) => {
  toggleWindowMaximize(event);
});

document.addEventListener("contextmenu", openSurfaceContextMenu);
window.addEventListener("blur", closeSurfaceContextMenu);
window.addEventListener("resize", closeSurfaceContextMenu);
window.addEventListener("resize", () => {
  if (uiState.browserPanelOpen) {
    setBrowserPanelWidth(uiState.browserPanelWidth);
  }
});

document.addEventListener("keydown", (event) => {
  const inspectorTarget = event.target.closest?.("[data-inspector-kind]");
  if (
    inspectorTarget &&
    !["BUTTON", "A"].includes(inspectorTarget.tagName) &&
    ["Enter", " "].includes(event.key)
  ) {
    event.preventDefault();
    openInspector(inspectorTarget.dataset.inspectorKind, {
      path: inspectorTarget.dataset.filePath,
      index: inspectorTarget.dataset.activityIndex,
    });
    return;
  }

  const key = event.key.toLowerCase();

  if (event.key === "Escape") {
    closeSurfaceContextMenu();
    closeMenus();
    if (!elements.searchBackdrop.hidden) {
      closeGlobalSearch();
    } else if (!elements.modal.hidden) {
      closeDialog();
    } else if (uiState.browserPanelOpen) {
      closeBrowserPanel();
    } else if (uiState.splitView) {
      closeInspector();
    } else {
      applySidebarUi("escape");
    }
    return;
  }

  if (event.ctrlKey && key === "b") {
    event.preventDefault();
    applySidebarUi("toggle-pin");
  } else if (event.ctrlKey && key === "p") {
    event.preventDefault();
    openGlobalSearch();
  } else if (event.ctrlKey && key === "o") {
    event.preventDefault();
    openProjectFolder();
  } else if (event.ctrlKey && event.shiftKey && key === "n") {
    event.preventDefault();
    showNewProjectDialog();
  } else if (event.ctrlKey && key === "n") {
    event.preventDefault();
    createNewTask();
  } else if (event.ctrlKey && key === "1") {
    event.preventDefault();
    actions["show-workspaces"]();
  } else if (event.altKey && event.key === "ArrowLeft") {
    event.preventDefault();
    navigateHistory(-1);
  } else if (event.altKey && event.key === "ArrowRight") {
    event.preventDefault();
    navigateHistory(1);
  } else if (event.ctrlKey && (event.key === "+" || event.key === "=")) {
    event.preventDefault();
    setZoom(uiState.zoom + 0.1);
  } else if (event.ctrlKey && event.key === "-") {
    event.preventDefault();
    setZoom(uiState.zoom - 0.1);
  } else if (event.ctrlKey && event.key === "0") {
    event.preventDefault();
    setZoom(1);
  } else if (event.ctrlKey && event.key === "/") {
    event.preventDefault();
    actions["show-shortcuts"]();
  }
});

renderSidebarVisibility();
renderSidebar();
const initialSurface = currentProjectSurface();
uiState.history = [initialSurface];
uiState.historyIndex = 0;
renderSurface(initialSurface, { record: false });

async function hydrateInitialWorkflowProject() {
  const project = activeProject();
  if (!project) return;
  const selection = selectionGuard.begin(project.id);
  const selectedSessionId = workspaceState.activeSessionId;
  try {
    await refreshWorkflowProject(project.id);
    if (!selectionGuard.isCurrent(selection, workspaceState.activeProjectId)) {
      return;
    }
    if (selectedSessionId) {
      workspaceState = selectSession(
        workspaceState,
        project.id,
        selectedSessionId,
        Date.now(),
      );
    }
    if (!workspaceState.activeSessionId) {
      activateLatestWorkflowSession(project.id);
    }
    persistWorkspaceState();
    renderSidebar();
    const surface = currentProjectSurface();
    uiState.history = [surface];
    uiState.historyIndex = 0;
    renderSurface(surface, { record: false });
  } catch (error) {
    console.error(error);
  }
}

hydrateInitialWorkflowProject();
scheduleCliExecutionPolling(500);
scheduleProjectMonitoring();
