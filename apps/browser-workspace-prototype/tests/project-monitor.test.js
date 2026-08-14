import test from "node:test";
import assert from "node:assert/strict";
import {
  monitoredProjectIds,
  recordProjectRefresh,
  shouldRefreshProject,
} from "../src/project-monitor.js";

test("project monitor prioritizes the active project and bounds the list", () => {
  const projects = [
    { id: "recent", path: "C:\\recent", lastOpenedAt: 30 },
    { id: "active", path: "C:\\active", lastOpenedAt: 1 },
    { id: "pinned", path: "C:\\pinned", pinned: true },
    { id: "missing", path: "" },
  ];
  assert.deepEqual(monitoredProjectIds(projects, "active", 2), ["active", "pinned"]);
});

test("project monitor skips invalid and recently refreshed projects", () => {
  assert.equal(shouldRefreshProject({ id: "p", path: "C:\\p" }, 9_000, 10_000, 2_000), false);
  assert.equal(shouldRefreshProject({ id: "p", path: "C:\\p" }, 7_000, 10_000, 2_000), true);
  assert.equal(shouldRefreshProject({ id: "p", path: "" }, 0, 10_000, 1), false);
});

test("project monitor records each project result independently", () => {
  const state = recordProjectRefresh({}, "p", { status: "refreshed" }, 100);
  const next = recordProjectRefresh(state, "q", { status: "failed", error: "offline" }, 200);
  assert.deepEqual(next.p, { at: 100, status: "refreshed", error: null });
  assert.deepEqual(next.q, { at: 200, status: "failed", error: "offline" });
});
