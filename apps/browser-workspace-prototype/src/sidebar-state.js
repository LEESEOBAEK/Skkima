export const WORKSPACE_STORAGE_KEY = "schema-workflow.desktop.workspace.v1";

export function createDefaultWorkspaceState() {
  return {
    schemaVersion: 1,
    sidebar: {
      pinned: false,
    },
    projects: [],
    activeProjectId: null,
    activeSessionId: null,
  };
}

function copyState(state) {
  return {
    ...state,
    sidebar: { ...state.sidebar },
    projects: state.projects.map((project) => ({
      ...project,
      cliPreference: project.cliPreference
        ? { ...project.cliPreference }
        : null,
      dismissedWorkflowRunIds: [
        ...(project.dismissedWorkflowRunIds ?? []),
      ],
      sessions: project.sessions.map((session) => ({ ...session })),
    })),
  };
}

export function normalizeProjectPath(path) {
  return userProjectPath(path)
    .trim()
    .replaceAll("/", "\\")
    .replace(/\\+$/, "")
    .toLocaleLowerCase();
}

export function userProjectPath(path) {
  const value = String(path ?? "").trim();
  if (value.toLocaleLowerCase().startsWith("\\\\?\\unc\\")) {
    return `\\\\${value.slice(8)}`;
  }
  if (value.startsWith("\\\\?\\")) {
    return value.slice(4);
  }
  return value;
}

export function compactProjectPath(path) {
  const value = userProjectPath(path).replaceAll("/", "\\");
  const driveMatch = value.match(/^([A-Za-z]:\\)(.*)$/);
  if (driveMatch) {
    const segments = driveMatch[2].split("\\").filter(Boolean);
    return segments.length > 2
      ? `${driveMatch[1]}…\\${segments.slice(-2).join("\\")}`
      : value;
  }

  if (value.startsWith("\\\\")) {
    const segments = value.slice(2).split("\\").filter(Boolean);
    if (segments.length > 4) {
      return `\\\\${segments.slice(0, 2).join("\\")}\\…\\${segments.slice(-2).join("\\")}`;
    }
  }
  return value;
}

function projectNameFromPath(path) {
  const parts = userProjectPath(path)
    .replaceAll("/", "\\")
    .split("\\")
    .filter(Boolean);
  return parts.at(-1) || "프로젝트";
}

function sanitizeSession(session) {
  if (!session || typeof session !== "object" || !session.id) return null;
  return {
    id: String(session.id),
    title: String(session.title || "새 작업"),
    createdAt: Number.isFinite(session.createdAt) ? session.createdAt : 0,
    updatedAt: Number.isFinite(session.updatedAt) ? session.updatedAt : 0,
    source: session.source === "workflow" ? "workflow" : "local",
    runId: session.runId ? String(session.runId) : null,
    archived: Boolean(session.archived),
    archivedAt: Number.isFinite(session.archivedAt)
      ? session.archivedAt
      : null,
  };
}

function sanitizeProject(project) {
  if (!project || typeof project !== "object" || !project.id || !project.path) {
    return null;
  }

  const path = userProjectPath(project.path);
  return {
    id: String(project.id),
    name: String(project.name || projectNameFromPath(path)),
    path,
    pinned: Boolean(project.pinned),
    pinOrder: Number.isFinite(project.pinOrder) ? project.pinOrder : null,
    lastOpenedAt: Number.isFinite(project.lastOpenedAt)
      ? project.lastOpenedAt
      : 0,
    cliPreference:
      project.cliPreference && typeof project.cliPreference === "object"
        ? {
            platform: project.cliPreference.platform
              ? String(project.cliPreference.platform)
              : null,
            approvalMode: project.cliPreference.approvalMode
              ? String(project.cliPreference.approvalMode)
              : "review",
          }
        : null,
    dismissedWorkflowRunIds: Array.isArray(project.dismissedWorkflowRunIds)
      ? [...new Set(project.dismissedWorkflowRunIds.map(String))]
      : [],
    sessions: Array.isArray(project.sessions)
      ? project.sessions.map(sanitizeSession).filter(Boolean)
      : [],
    workflow:
      project.workflow && typeof project.workflow === "object"
        ? {
            contractFound: Boolean(project.workflow.contractFound),
            workspaceId: project.workflow.workspaceId
              ? String(project.workflow.workspaceId)
              : null,
            schemaVersion: project.workflow.schemaVersion
              ? String(project.workflow.schemaVersion)
              : null,
            runsRoot: project.workflow.runsRoot
              ? String(project.workflow.runsRoot)
              : null,
            warnings: Array.isArray(project.workflow.warnings)
              ? project.workflow.warnings.map(String)
              : [],
          }
        : null,
  };
}

export function loadWorkspaceState(rawValue) {
  if (!rawValue) return createDefaultWorkspaceState();

  try {
    const parsed = JSON.parse(rawValue);
    if (parsed?.schemaVersion !== 1) return createDefaultWorkspaceState();

    const projects = Array.isArray(parsed.projects)
      ? parsed.projects.map(sanitizeProject).filter(Boolean)
      : [];
    const projectIds = new Set(projects.map((project) => project.id));
    const activeProjectId = projectIds.has(parsed.activeProjectId)
      ? parsed.activeProjectId
      : null;
    const activeProject = projects.find(
      (project) => project.id === activeProjectId,
    );
    const activeSessionId = activeProject?.sessions.some(
      (session) => session.id === parsed.activeSessionId,
    )
      ? parsed.activeSessionId
      : null;

    return {
      schemaVersion: 1,
      sidebar: {
        pinned: Boolean(parsed.sidebar?.pinned),
      },
      projects,
      activeProjectId,
      activeSessionId,
    };
  } catch {
    return createDefaultWorkspaceState();
  }
}

export function applySidebarEvent(sidebarState, event) {
  if (event === "toggle-pin") {
    return sidebarState.pinned
      ? { mode: "closed", pinned: false }
      : { mode: "pinned", pinned: true };
  }

  if (event === "edge-enter" && !sidebarState.pinned) {
    return { mode: "peek", pinned: false };
  }

  if (
    (event === "pointer-leave" || event === "escape") &&
    sidebarState.mode === "peek"
  ) {
    return { mode: "closed", pinned: false };
  }

  return { ...sidebarState };
}

export function addOrActivateProject(state, path, now, projectId) {
  const next = copyState(state);
  const userPath = userProjectPath(path);
  const normalizedPath = normalizeProjectPath(userPath);
  const existing = next.projects.find(
    (project) => normalizeProjectPath(project.path) === normalizedPath,
  );

  if (existing) {
    existing.lastOpenedAt = now;
    next.activeProjectId = existing.id;
    next.activeSessionId = null;
    return next;
  }

  const project = {
    id: projectId,
    name: projectNameFromPath(userPath),
    path: userPath,
    pinned: false,
    pinOrder: null,
    lastOpenedAt: now,
    cliPreference: null,
    dismissedWorkflowRunIds: [],
    sessions: [],
  };
  next.projects.push(project);
  next.activeProjectId = project.id;
  next.activeSessionId = null;
  return next;
}

export function selectProject(state, projectId, _now) {
  const next = copyState(state);
  const project = next.projects.find((item) => item.id === projectId);
  if (!project) return next;
  next.activeProjectId = project.id;
  next.activeSessionId = null;
  return next;
}

export function removeProject(state, projectId) {
  const next = copyState(state);
  const projectIndex = next.projects.findIndex(
    (project) => project.id === projectId,
  );
  if (projectIndex < 0) return next;

  next.projects.splice(projectIndex, 1);
  if (next.activeProjectId === projectId) {
    next.activeProjectId = null;
    next.activeSessionId = null;
  }
  return next;
}

export function createTaskSession(state, title, now, sessionId) {
  const next = copyState(state);
  const project = next.projects.find(
    (item) => item.id === next.activeProjectId,
  );
  if (!project) return { state: next, error: "PROJECT_REQUIRED" };

  const session = {
    id: sessionId,
    title: String(title || "새 작업"),
    createdAt: now,
    updatedAt: now,
    source: "local",
    runId: null,
    archived: false,
    archivedAt: null,
  };
  project.sessions.unshift(session);
  project.lastOpenedAt = now;
  next.activeSessionId = session.id;
  return { state: next, error: null };
}

export function syncWorkflowProject(state, projectId, snapshot, _now) {
  const next = copyState(state);
  const project = next.projects.find((item) => item.id === projectId);
  if (!project) return next;

  const localSessions = project.sessions.filter(
    (session) => session.source !== "workflow",
  );
  const existingWorkflowSessions = new Map(
    project.sessions
      .filter((session) => session.source === "workflow")
      .map((session) => [session.id, session]),
  );
  const dismissedWorkflowRunIds = new Set(
    project.dismissedWorkflowRunIds ?? [],
  );
  const workflowSessions = (snapshot?.runs ?? [])
    .filter((run) => !dismissedWorkflowRunIds.has(String(run.runId)))
    .map((run) => {
      const createdAt = Date.parse(run.createdAt ?? "");
      const updatedAt = Date.parse(run.updatedAt ?? run.createdAt ?? "");
      const id = `workflow:${run.runId}`;
      const existing = existingWorkflowSessions.get(id);
      return {
        id,
        title: String(run.displayTitle || run.runId),
        createdAt: Number.isFinite(createdAt) ? createdAt : 0,
        updatedAt: Number.isFinite(updatedAt) ? updatedAt : 0,
        source: "workflow",
        runId: String(run.runId),
        archived: Boolean(existing?.archived),
        archivedAt: existing?.archivedAt ?? null,
      };
    });

  project.name = String(snapshot?.projectName || project.name);
  project.sessions = [...workflowSessions, ...localSessions];
  project.workflow = {
    contractFound: Boolean(snapshot?.contractFound),
    workspaceId: snapshot?.workspaceId ? String(snapshot.workspaceId) : null,
    schemaVersion: snapshot?.schemaVersion
      ? String(snapshot.schemaVersion)
      : null,
    runsRoot: snapshot?.runsRoot ? String(snapshot.runsRoot) : null,
    warnings: Array.isArray(snapshot?.warnings)
      ? snapshot.warnings.map(String)
      : [],
  };

  if (
    next.activeProjectId === projectId &&
    next.activeSessionId &&
    !project.sessions.some((session) => session.id === next.activeSessionId)
  ) {
    next.activeSessionId = null;
  }
  return next;
}

export function renameTaskSession(state, projectId, sessionId, title, now) {
  const next = copyState(state);
  const project = next.projects.find((item) => item.id === projectId);
  const session = project?.sessions.find((item) => item.id === sessionId);
  if (!session) return next;
  session.title = String(title || session.title);
  session.updatedAt = now;
  return next;
}

export function selectSession(state, projectId, sessionId, _now) {
  const next = copyState(state);
  const project = next.projects.find((item) => item.id === projectId);
  const session = project?.sessions.find((item) => item.id === sessionId);
  if (!project || !session) return next;
  next.activeProjectId = project.id;
  next.activeSessionId = session.id;
  return next;
}

export function toggleProjectPin(state, projectId) {
  const next = copyState(state);
  const project = next.projects.find((item) => item.id === projectId);
  if (!project) return next;

  project.pinned = !project.pinned;
  if (project.pinned) {
    const highestOrder = next.projects.reduce(
      (highest, item) =>
        item.pinned && Number.isFinite(item.pinOrder)
          ? Math.max(highest, item.pinOrder)
          : highest,
      0,
    );
    project.pinOrder = highestOrder + 1;
  } else {
    project.pinOrder = null;
  }
  return next;
}

export function archiveTaskSession(state, projectId, sessionId, now) {
  const next = copyState(state);
  const project = next.projects.find((item) => item.id === projectId);
  const session = project?.sessions.find((item) => item.id === sessionId);
  if (!project || !session) return next;

  session.archived = true;
  session.archivedAt = now;
  if (
    next.activeProjectId === projectId &&
    next.activeSessionId === sessionId
  ) {
    next.activeSessionId = null;
  }
  return next;
}

export function restoreTaskSession(state, projectId, sessionId, now) {
  const next = copyState(state);
  const project = next.projects.find((item) => item.id === projectId);
  const session = project?.sessions.find((item) => item.id === sessionId);
  if (!project || !session) return next;

  session.archived = false;
  session.archivedAt = null;
  session.updatedAt = now;
  project.lastOpenedAt = now;
  next.activeProjectId = project.id;
  next.activeSessionId = session.id;
  return next;
}

export function deleteArchivedTaskSessions(state, taskReferences) {
  const next = copyState(state);
  const requested = new Set(
    (Array.isArray(taskReferences) ? taskReferences : []).map(
      ({ projectId, sessionId }) => `${projectId}:${sessionId}`,
    ),
  );

  next.projects.forEach((project) => {
    const dismissedWorkflowRunIds = new Set(
      project.dismissedWorkflowRunIds ?? [],
    );
    project.sessions = project.sessions.filter((session) => {
      const shouldDelete =
        session.archived && requested.has(`${project.id}:${session.id}`);
      if (!shouldDelete) return true;

      if (session.source === "workflow" && session.runId) {
        dismissedWorkflowRunIds.add(session.runId);
      }
      if (
        next.activeProjectId === project.id &&
        next.activeSessionId === session.id
      ) {
        next.activeSessionId = null;
      }
      return false;
    });
    project.dismissedWorkflowRunIds = [...dismissedWorkflowRunIds];
  });

  return next;
}

export function listPinnedProjects(state) {
  return state.projects
    .filter((project) => project.pinned)
    .sort((left, right) => (left.pinOrder ?? 0) - (right.pinOrder ?? 0));
}

export function listRecentProjects(state, limit = 7) {
  return state.projects
    .filter((project) => !project.pinned)
    .sort((left, right) => right.lastOpenedAt - left.lastOpenedAt)
    .slice(0, limit);
}

export function listArchivedTaskSessions(state) {
  return state.projects
    .flatMap((project) =>
      project.sessions
        .filter((session) => session.archived)
        .map((session) => ({ project, session })),
    )
    .sort(
      (left, right) =>
        (right.session.archivedAt ?? 0) - (left.session.archivedAt ?? 0),
    );
}
