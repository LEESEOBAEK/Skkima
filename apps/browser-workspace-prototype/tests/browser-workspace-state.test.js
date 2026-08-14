import test from "node:test";
import assert from "node:assert/strict";

import {
  BROWSER_SESSION_STORAGE_KEY,
  DEFAULT_BROWSER_VIEWPORT_MODE,
  DEFAULT_BROWSER_ZOOM,
  createBrowserSession,
  loadBrowserSession,
  normalizeBrowserViewportMode,
  normalizeBrowserZoom,
  sanitizeBrowserUrlForStorage,
  saveBrowserSession,
} from "../src/browser-workspace-state.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, value);
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

test("stored browser URLs discard credentials, query strings, and fragments", () => {
  assert.equal(
    sanitizeBrowserUrlForStorage(
      "https://user:secret@example.com/path?token=private#callback",
    ),
    "https://example.com/path",
  );
});

test("browser sessions restore only the safe metadata contract", () => {
  const storage = memoryStorage();
  const saved = saveBrowserSession(
    storage,
    createBrowserSession({
      url: "https://example.com/work?session=secret",
      projectName: "테스트 프로젝트",
      sessionName: "브라우저 검증",
      updatedAt: 100,
    }),
  );
  const restored = loadBrowserSession(storage);

  assert.equal(saved.url, "https://example.com/work");
  assert.equal(restored.projectName, "테스트 프로젝트");
  assert.equal(restored.sessionName, "브라우저 검증");
  assert.equal(restored.zoom, DEFAULT_BROWSER_ZOOM);
  assert.equal(restored.viewportMode, DEFAULT_BROWSER_VIEWPORT_MODE);
  assert.ok(storage.getItem(BROWSER_SESSION_STORAGE_KEY));
});

test("browser zoom stays inside the supported desktop viewing range", () => {
  assert.equal(normalizeBrowserZoom(0.2), 0.5);
  assert.equal(normalizeBrowserZoom(2), 1.25);
  assert.equal(normalizeBrowserZoom("invalid"), DEFAULT_BROWSER_ZOOM);
});

test("browser viewport uses desktop width by default and supports responsive mode", () => {
  assert.equal(normalizeBrowserViewportMode("desktop"), "desktop");
  assert.equal(normalizeBrowserViewportMode("responsive"), "responsive");
  assert.equal(normalizeBrowserViewportMode("invalid"), DEFAULT_BROWSER_VIEWPORT_MODE);
});

test("the former 80 percent default migrates once to the current 100 percent default", () => {
  const storage = memoryStorage();
  storage.setItem(BROWSER_SESSION_STORAGE_KEY, JSON.stringify({
    schemaVersion: "1.0.0",
    url: "https://example.com/",
    zoom: 0.8,
  }));

  const restored = loadBrowserSession(storage);

  assert.equal(restored.zoom, 1);
  assert.equal(restored.viewportMode, "desktop");
  assert.equal(restored.schemaVersion, "1.2.0");
});

test("the previous session contract migrates to desktop viewport mode", () => {
  const storage = memoryStorage();
  storage.setItem(BROWSER_SESSION_STORAGE_KEY, JSON.stringify({
    schemaVersion: "1.1.0",
    url: "https://example.com/",
    zoom: 1,
  }));

  const restored = loadBrowserSession(storage);

  assert.equal(restored.viewportMode, "desktop");
  assert.equal(restored.schemaVersion, "1.2.0");
});
