import test from "node:test";
import assert from "node:assert/strict";
import {
  SKILL_VIEW_STORAGE_KEY,
  loadSkillViewMode,
  normalizeSkillViewMode,
  saveSkillViewMode,
} from "../src/extension-view-state.js";

test("skill view supports list and responsive grid modes", () => {
  assert.equal(normalizeSkillViewMode("list"), "list");
  assert.equal(normalizeSkillViewMode("grid"), "grid");
  assert.equal(normalizeSkillViewMode("three-columns"), "list");
});

test("skill view preference persists and falls back safely", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };

  assert.equal(loadSkillViewMode(storage), "list");
  assert.equal(saveSkillViewMode(storage, "grid"), "grid");
  assert.equal(values.get(SKILL_VIEW_STORAGE_KEY), "grid");
  assert.equal(loadSkillViewMode(storage), "grid");
});
