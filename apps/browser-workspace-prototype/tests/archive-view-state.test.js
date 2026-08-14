import test from "node:test";
import assert from "node:assert/strict";
import {
  archiveTaskKey,
  filterArchivedTasks,
  formatArchiveTimestamp,
  paginateArchivedTasks,
} from "../src/archive-view-state.js";

const archivedTasks = [
  {
    project: { id: "project-b", name: "베타", path: "C:\\work\\beta" },
    session: {
      id: "session-2",
      title: "주간 보고",
      source: "workflow",
      archivedAt: 200,
    },
  },
  {
    project: { id: "project-a", name: "알파", path: "C:\\work\\alpha" },
    session: {
      id: "session-1",
      title: "자료 정리",
      source: "local",
      archivedAt: 100,
    },
  },
];

test("archive task identity is stable across archive controls", () => {
  assert.equal(archiveTaskKey("project-a", "session-1"), "project-a:session-1");
});

test("archive filtering applies project, source, and text without mutating input", () => {
  const originalOrder = archivedTasks.map((item) => item.session.id);
  const result = filterArchivedTasks(archivedTasks, {
    query: "alpha",
    projectId: "all",
    source: "local",
    sort: "newest",
  });

  assert.deepEqual(result.map((item) => item.session.id), ["session-1"]);
  assert.deepEqual(
    archivedTasks.map((item) => item.session.id),
    originalOrder,
  );
});

test("archive sorting and pagination clamp invalid pages", () => {
  const sorted = filterArchivedTasks(archivedTasks, {
    projectId: "all",
    source: "all",
    sort: "oldest",
  });
  assert.deepEqual(sorted.map((item) => item.session.id), ["session-1", "session-2"]);

  const page = paginateArchivedTasks(sorted, 9, 1);
  assert.equal(page.page, 2);
  assert.equal(page.pageCount, 2);
  assert.equal(page.tasks[0].session.id, "session-2");
});

test("archive timestamp keeps an explicit fallback", () => {
  assert.equal(formatArchiveTimestamp(undefined), "보관 시각 없음");
  assert.match(formatArchiveTimestamp(0), /1970/);
});
