import test from "node:test";
import assert from "node:assert/strict";

import { createSelectionGuard } from "../src/selection-guard.js";

test("a late project refresh cannot reactivate a previously selected project", () => {
  const guard = createSelectionGuard();
  const first = guard.begin("project-a");
  const second = guard.begin("project-b");

  assert.equal(guard.isCurrent(first, "project-b"), false);
  assert.equal(guard.isCurrent(second, "project-b"), true);
});

test("the current project refresh remains valid while non-project views are open", () => {
  const guard = createSelectionGuard();
  const token = guard.begin("project-a");

  assert.equal(guard.isCurrent(token, "project-a"), true);
});
