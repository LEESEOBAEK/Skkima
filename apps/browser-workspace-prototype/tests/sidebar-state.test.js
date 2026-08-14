import test from "node:test";
import assert from "node:assert/strict";

import {
  addOrActivateProject,
  applySidebarEvent,
  archiveTaskSession,
  compactProjectPath,
  createDefaultWorkspaceState,
  createTaskSession,
  deleteArchivedTaskSessions,
  listArchivedTaskSessions,
  listPinnedProjects,
  listRecentProjects,
  loadWorkspaceState,
  removeProject,
  renameTaskSession,
  restoreTaskSession,
  selectProject,
  selectSession,
  syncWorkflowProject,
  toggleProjectPin,
} from "../src/sidebar-state.js";

test("sidebar opens temporarily on edge hover and closes after leaving", () => {
  const peek = applySidebarEvent(
    { mode: "closed", pinned: false },
    "edge-enter",
  );
  assert.deepEqual(peek, { mode: "peek", pinned: false });

  const closed = applySidebarEvent(peek, "pointer-leave");
  assert.deepEqual(closed, { mode: "closed", pinned: false });
});

test("temporary sidebar stays open when the pointer leaves the application", () => {
  const peek = { mode: "peek", pinned: false };
  assert.deepEqual(applySidebarEvent(peek, "app-leave"), peek);
});

test("sidebar pin toggle promotes a temporary panel and persists until toggled", () => {
  const pinned = applySidebarEvent(
    { mode: "peek", pinned: false },
    "toggle-pin",
  );
  assert.deepEqual(pinned, { mode: "pinned", pinned: true });
  assert.deepEqual(applySidebarEvent(pinned, "pointer-leave"), pinned);
  assert.deepEqual(applySidebarEvent(pinned, "toggle-pin"), {
    mode: "closed",
    pinned: false,
  });
});

test("workspace state falls back safely when stored data is invalid", () => {
  assert.deepEqual(loadWorkspaceState("{not-json"), createDefaultWorkspaceState());

  const restored = loadWorkspaceState(
    JSON.stringify({
      schemaVersion: 1,
      sidebar: { pinned: true, width: 4000 },
      projects: [],
      activeProjectId: null,
      activeSessionId: null,
    }),
  );
  assert.equal(restored.sidebar.pinned, true);
  assert.equal("width" in restored.sidebar, false);
});

test("opening the same project path activates one project instead of duplicating it", () => {
  const initial = createDefaultWorkspaceState();
  const first = addOrActivateProject(
    initial,
    "C:\\Work\\Marketing",
    100,
    "project-1",
  );
  const reopened = addOrActivateProject(
    first,
    "c:/work/marketing/",
    200,
    "project-2",
  );

  assert.equal(reopened.projects.length, 1);
  assert.equal(reopened.activeProjectId, "project-1");
  assert.equal(reopened.projects[0].lastOpenedAt, 200);
});

test("removing a project forgets only the app entry and selects no hidden fallback", () => {
  let state = createDefaultWorkspaceState();
  state = addOrActivateProject(state, "C:\\Work\\Keep", 100, "keep");
  state = addOrActivateProject(state, "C:\\Work\\Remove", 200, "remove");
  state = createTaskSession(state, "Mistaken task", 210, "session-1").state;

  const removed = removeProject(state, "remove");

  assert.deepEqual(removed.projects.map((project) => project.id), ["keep"]);
  assert.equal(removed.activeProjectId, null);
  assert.equal(removed.activeSessionId, null);
  assert.equal(state.projects.length, 2);
  assert.equal(state.projects[1].path, "C:\\Work\\Remove");
});

test("removing an inactive project preserves the current project and session", () => {
  let state = createDefaultWorkspaceState();
  state = addOrActivateProject(state, "C:\\Work\\Current", 100, "current");
  state = createTaskSession(state, "Current task", 110, "session-current").state;
  state = addOrActivateProject(state, "C:\\Work\\Other", 200, "other");
  state = selectSession(state, "current", "session-current", 300);

  const removed = removeProject(state, "other");

  assert.equal(removed.activeProjectId, "current");
  assert.equal(removed.activeSessionId, "session-current");
  assert.deepEqual(removed.projects.map((project) => project.id), ["current"]);
});

test("Windows verbatim paths are stored and compared as user-facing paths", () => {
  const restored = loadWorkspaceState(
    JSON.stringify({
      schemaVersion: 1,
      sidebar: { pinned: false },
      projects: [
        {
          id: "project-1",
          name: "Marketing",
          path: "\\\\?\\C:\\Work\\Marketing",
          sessions: [],
        },
      ],
      activeProjectId: "project-1",
      activeSessionId: null,
    }),
  );
  const reopened = addOrActivateProject(
    restored,
    "C:\\Work\\Marketing",
    200,
    "project-2",
  );

  assert.equal(reopened.projects.length, 1);
  assert.equal(reopened.projects[0].path, "C:\\Work\\Marketing");
  assert.equal(reopened.activeProjectId, "project-1");
});

test("CLI preferences stay with their project across persistence and navigation", () => {
  let state = createDefaultWorkspaceState();
  state = addOrActivateProject(state, "C:\\Work\\Claude", 100, "claude-project");
  state.projects[0].cliPreference = {
    platform: "claude",
    approvalMode: "review",
  };
  state = addOrActivateProject(state, "C:\\Work\\Codex", 200, "codex-project");

  assert.deepEqual(
    state.projects.find((project) => project.id === "claude-project")
      .cliPreference,
    { platform: "claude", approvalMode: "review" },
  );
  assert.equal(
    state.projects.find((project) => project.id === "codex-project")
      .cliPreference,
    null,
  );

  const restored = loadWorkspaceState(JSON.stringify(state));
  assert.deepEqual(
    restored.projects.find((project) => project.id === "claude-project")
      .cliPreference,
    { platform: "claude", approvalMode: "review" },
  );
  assert.equal(
    selectProject(restored, "codex-project", 300).projects.find(
      (project) => project.id === "codex-project",
    ).cliPreference,
    null,
  );
});

test("long project paths keep the drive and final two folders for display", () => {
  assert.equal(
    compactProjectPath(
      "C:\\Users\\demo\\SchemaWorkflow\\experiments\\bundle\\project",
    ),
    "C:\\…\\bundle\\project",
  );
  assert.equal(
    compactProjectPath("C:\\Work\\project"),
    "C:\\Work\\project",
  );
});

test("a task session cannot be created before a project is selected", () => {
  const result = createTaskSession(
    createDefaultWorkspaceState(),
    "새 작업",
    100,
    "session-1",
  );
  assert.equal(result.error, "PROJECT_REQUIRED");
  assert.equal(result.state.projects.length, 0);
});

test("task sessions belong to the selected project and can be selected again", () => {
  const projectState = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\Portfolio",
    100,
    "project-1",
  );
  const created = createTaskSession(
    projectState,
    "README 개선",
    200,
    "session-1",
  );

  assert.equal(created.error, null);
  assert.equal(created.state.activeSessionId, "session-1");
  assert.equal(created.state.projects[0].sessions[0].title, "README 개선");

  const selected = selectSession(created.state, "project-1", "session-1", 300);
  assert.equal(selected.activeProjectId, "project-1");
  assert.equal(selected.activeSessionId, "session-1");
  assert.equal(selected.projects[0].lastOpenedAt, 200);
  assert.equal(selected.projects[0].sessions[0].updatedAt, 200);
});

test("sidebar navigation keeps the recent project order stable", () => {
  let state = createDefaultWorkspaceState();
  state = addOrActivateProject(state, "C:\\Work\\Older", 100, "older");
  state = addOrActivateProject(state, "C:\\Work\\Newer", 200, "newer");

  state = selectProject(state, "older", 300);

  assert.equal(state.activeProjectId, "older");
  assert.equal(state.projects.find((project) => project.id === "older").lastOpenedAt, 100);
  assert.deepEqual(listRecentProjects(state).map((project) => project.id), [
    "newer",
    "older",
  ]);
});

test("pinned projects are manually ordered and excluded from recent projects", () => {
  let state = createDefaultWorkspaceState();
  state = addOrActivateProject(state, "C:\\Work\\A", 100, "a");
  state = addOrActivateProject(state, "C:\\Work\\B", 300, "b");
  state = addOrActivateProject(state, "C:\\Work\\C", 200, "c");
  state = toggleProjectPin(state, "a");

  assert.deepEqual(listPinnedProjects(state).map((project) => project.id), ["a"]);
  assert.deepEqual(listRecentProjects(state).map((project) => project.id), [
    "b",
    "c",
  ]);
});

test("renaming a session changes only its display name", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\Docs",
    100,
    "project-1",
  );
  state = createTaskSession(state, "초안", 200, "session-1").state;
  const renamed = renameTaskSession(
    state,
    "project-1",
    "session-1",
    "검토 완료본",
    300,
  );

  assert.equal(renamed.projects[0].sessions[0].title, "검토 완료본");
  assert.equal(renamed.projects[0].sessions[0].updatedAt, 300);
  assert.equal(renamed.projects[0].path, "C:\\Work\\Docs");
});

test("archiving a task preserves its project and session data", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\KeepFiles",
    100,
    "project-1",
  );
  state = createTaskSession(state, "Preserved task", 150, "session-1").state;
  const archived = archiveTaskSession(
    state,
    "project-1",
    "session-1",
    200,
  );

  assert.equal(archived.projects.length, 1);
  assert.equal(archived.projects[0].sessions[0].archived, true);
  assert.equal(archived.projects[0].sessions[0].archivedAt, 200);
  assert.equal(archived.projects[0].sessions.length, 1);
  assert.equal(archived.projects[0].sessions[0].title, "Preserved task");
  assert.equal(archived.activeProjectId, "project-1");
  assert.equal(archived.activeSessionId, null);
  assert.equal(listRecentProjects(archived).length, 1);
  assert.equal(state.projects[0].path, "C:\\Work\\KeepFiles");
});

test("restoring an archived task reactivates that task", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\RestoreMe",
    100,
    "project-1",
  );
  state = createTaskSession(state, "Restored task", 150, "session-1").state;
  state = archiveTaskSession(state, "project-1", "session-1", 200);
  state = restoreTaskSession(state, "project-1", "session-1", 300);

  assert.equal(state.projects[0].sessions[0].archived, false);
  assert.equal(state.projects[0].sessions[0].archivedAt, null);
  assert.equal(state.activeProjectId, "project-1");
  assert.equal(state.activeSessionId, "session-1");
  assert.equal(state.projects[0].sessions[0].title, "Restored task");
  assert.deepEqual(
    listRecentProjects(state).map((project) => project.id),
    ["project-1"],
  );
});

test("archived tasks are listed independently from project navigation", () => {
  let state = createDefaultWorkspaceState();
  state = addOrActivateProject(state, "C:\\Work\\A", 100, "project-a");
  state = createTaskSession(state, "Older task", 110, "session-a").state;
  state = archiveTaskSession(state, "project-a", "session-a", 200);
  state = addOrActivateProject(state, "C:\\Work\\B", 300, "project-b");
  state = createTaskSession(state, "Newer task", 310, "session-b").state;
  state = archiveTaskSession(state, "project-b", "session-b", 400);

  const archived = listArchivedTaskSessions(state);
  assert.deepEqual(
    archived.map(({ project, session }) => [project.id, session.id]),
    [
      ["project-b", "session-b"],
      ["project-a", "session-a"],
    ],
  );
  assert.deepEqual(
    listRecentProjects(state).map((project) => project.id),
    ["project-b", "project-a"],
  );
});

test("workflow synchronization preserves archived task state", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\WorkflowArchive",
    100,
    "project-1",
  );
  state = syncWorkflowProject(
    state,
    "project-1",
    {
      runs: [
        {
          runId: "run-1",
          displayTitle: "Archived workflow task",
          createdAt: "2026-07-28T15:14:37",
        },
      ],
    },
    200,
  );
  state = archiveTaskSession(
    state,
    "project-1",
    "workflow:run-1",
    250,
  );
  state = syncWorkflowProject(
    state,
    "project-1",
    {
      runs: [
        {
          runId: "run-1",
          displayTitle: "Archived workflow task",
          createdAt: "2026-07-28T15:14:37",
        },
      ],
    },
    300,
  );

  assert.equal(state.projects.length, 1);
  assert.equal(state.projects[0].sessions[0].archived, true);
  assert.equal(state.projects[0].sessions[0].archivedAt, 250);
});

test("deleting an archived local task removes only the dashboard session", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\LocalArchive",
    100,
    "project-1",
  );
  state = createTaskSession(state, "Local archived task", 150, "local-1").state;
  state = archiveTaskSession(state, "project-1", "local-1", 200);
  state = deleteArchivedTaskSessions(state, [
    { projectId: "project-1", sessionId: "local-1" },
  ]);

  assert.equal(state.projects.length, 1);
  assert.equal(state.projects[0].path, "C:\\Work\\LocalArchive");
  assert.equal(state.projects[0].sessions.length, 0);
  assert.deepEqual(state.projects[0].dismissedWorkflowRunIds, []);
});

test("deleting an archived workflow task keeps it hidden after synchronization", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\WorkflowDelete",
    100,
    "project-1",
  );
  const snapshot = {
    runs: [
      {
        runId: "run-1",
        displayTitle: "Workflow archived task",
        createdAt: "2026-07-28T15:14:37",
      },
    ],
  };
  state = syncWorkflowProject(state, "project-1", snapshot, 200);
  state = archiveTaskSession(state, "project-1", "workflow:run-1", 250);
  state = deleteArchivedTaskSessions(state, [
    { projectId: "project-1", sessionId: "workflow:run-1" },
  ]);

  assert.equal(state.projects[0].sessions.length, 0);
  assert.deepEqual(state.projects[0].dismissedWorkflowRunIds, ["run-1"]);

  state = syncWorkflowProject(state, "project-1", snapshot, 300);
  assert.equal(state.projects[0].sessions.length, 0);
  assert.deepEqual(state.projects[0].dismissedWorkflowRunIds, ["run-1"]);
});

test("delete ignores task sessions that are not archived", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\LiveTask",
    100,
    "project-1",
  );
  state = createTaskSession(state, "Live task", 150, "local-1").state;
  state = deleteArchivedTaskSessions(state, [
    { projectId: "project-1", sessionId: "local-1" },
  ]);

  assert.equal(state.projects[0].sessions.length, 1);
  assert.equal(state.projects[0].sessions[0].title, "Live task");
});

test("workflow runs synchronize as read-only sessions without removing local sessions", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\Workflow",
    100,
    "project-1",
  );
  state = createTaskSession(state, "내 메모", 200, "local-1").state;
  state = syncWorkflowProject(
    state,
    "project-1",
    {
      projectName: "workflow-project",
      contractFound: true,
      workspaceId: "ws-1",
      schemaVersion: "1.0",
      runsRoot: "outputs/workflows",
      warnings: [],
      runs: [
        {
          runId: "run-1",
          displayTitle: "AI 도구 블로그 운영안 설계",
          createdAt: "2026-07-28T15:14:37",
          updatedAt: "2026-07-28T16:05:53+09:00",
        },
      ],
    },
    300,
  );

  assert.equal(state.projects[0].name, "workflow-project");
  assert.equal(state.projects[0].sessions.length, 2);
  assert.equal(state.projects[0].sessions[0].source, "workflow");
  assert.equal(state.projects[0].sessions[0].runId, "run-1");
  assert.equal(state.projects[0].sessions[1].title, "내 메모");
});

test("failed workflow stays archived across synchronization and restores intact", () => {
  let state = addOrActivateProject(
    createDefaultWorkspaceState(),
    "C:\\Work\\FailureRecovery",
    100,
    "project-1",
  );
  const snapshot = {
    runs: [
      {
        runId: "failed-run",
        displayTitle: "주간 지표 가져오기 실패",
        createdAt: "2026-07-30T10:51:31+09:00",
        status: "failed",
      },
      {
        runId: "recovered-run",
        displayTitle: "주간 지표 가져오기 복구",
        createdAt: "2026-07-30T10:52:32+09:00",
        status: "completed",
        relationType: "retry",
        parentRunId: "failed-run",
      },
    ],
  };

  state = syncWorkflowProject(state, "project-1", snapshot, 200);
  state = archiveTaskSession(
    state,
    "project-1",
    "workflow:failed-run",
    300,
  );
  state = syncWorkflowProject(state, "project-1", snapshot, 400);

  const archived = listArchivedTaskSessions(state);
  assert.equal(archived.length, 1);
  assert.equal(archived[0].session.id, "workflow:failed-run");
  assert.equal(archived[0].session.title, "주간 지표 가져오기 실패");
  assert.equal(archived[0].session.archived, true);
  assert.equal(
    state.projects[0].sessions.find(
      (session) => session.id === "workflow:recovered-run",
    )?.archived,
    false,
  );

  state = restoreTaskSession(
    state,
    "project-1",
    "workflow:failed-run",
    500,
  );
  assert.equal(state.activeSessionId, "workflow:failed-run");
  assert.equal(
    state.projects[0].sessions.find(
      (session) => session.id === "workflow:failed-run",
    )?.archived,
    false,
  );
});
