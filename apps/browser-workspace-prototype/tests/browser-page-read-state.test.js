import test from "node:test";
import assert from "node:assert/strict";

import {
  BROWSER_READ_EVIDENCE_STORAGE_KEY,
  browserReadComparisonMode,
  clearBrowserReadEvidence,
  createBrowserReadEvidence,
  loadBrowserReadEvidence,
  normalizeBrowserPageSnapshot,
  saveBrowserReadEvidence,
} from "../src/browser-page-read-state.js";

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

test("read-only snapshots strip URL secrets and never retain input values", () => {
  const snapshot = normalizeBrowserPageSnapshot({
    title: "  Account   settings  ",
    url: "https://user:secret@example.com/settings?token=private#profile",
    counts: { buttons: 2, links: 1, inputs: 3, forms: 1 },
    hasPasswordField: true,
    controls: [
      { kind: "input", label: "Email", inputType: "email", value: "private@example.com" },
      { kind: "link", label: "Home", href: "https://example.com/?token=secret" },
    ],
  });

  assert.equal(snapshot.title, "Account settings");
  assert.equal(snapshot.url, "https://example.com/settings");
  assert.equal(snapshot.controls[0].value, undefined);
  assert.equal(snapshot.controls[1].href, "https://example.com/");
  assert.equal(snapshot.hasPasswordField, true);
});

test("evidence records remain linked to the selected project and task", () => {
  const evidence = createBrowserReadEvidence(
    { title: "Example", url: "https://example.com/" },
    {
      evidenceId: "evidence-001",
      projectId: "project-1",
      projectName: "Browser Lab",
      sessionId: "session-1",
      sessionName: "Read page",
    },
  );

  assert.equal(evidence.evidenceId, "evidence-001");
  assert.equal(evidence.projectName, "Browser Lab");
  assert.equal(evidence.sessionName, "Read page");
  assert.equal(evidence.source, "webview2-devtools-read-only");
});

test("repeated reads of the same page structure become one observation record", () => {
  const storage = memoryStorage();
  const first = createBrowserReadEvidence(
    {
      capturedAt: "2026-08-02T00:00:00.000Z",
      title: "Example",
      url: "https://example.com/?session=one",
      counts: { buttons: 1, links: 1, inputs: 0, forms: 0 },
      controls: [{ kind: "link", label: "More", href: "https://example.com/more" }],
    },
    { evidenceId: "first-read" },
  );
  const second = createBrowserReadEvidence(
    {
      capturedAt: "2026-08-02T00:01:00.000Z",
      title: "Example",
      url: "https://example.com/#changed-fragment",
      counts: { buttons: 1, links: 1, inputs: 0, forms: 0 },
      controls: [{ kind: "link", label: "More", href: "https://example.com/more" }],
    },
    { evidenceId: "second-read" },
  );

  saveBrowserReadEvidence(storage, first);
  const records = saveBrowserReadEvidence(storage, second);

  assert.equal(records.length, 1);
  assert.equal(records[0].evidenceId, "first-read");
  assert.equal(records[0].observationCount, 2);
  assert.equal(records[0].firstCapturedAt, "2026-08-02T00:00:00.000Z");
  assert.equal(records[0].lastCapturedAt, "2026-08-02T00:01:00.000Z");
});

test("a changed page structure remains a separate local observation record", () => {
  const storage = memoryStorage();
  saveBrowserReadEvidence(
    storage,
    createBrowserReadEvidence(
      { title: "Example", url: "https://example.com/", counts: { links: 1 } },
      { evidenceId: "first-read" },
    ),
  );
  const records = saveBrowserReadEvidence(
    storage,
    createBrowserReadEvidence(
      { title: "Example", url: "https://example.com/", counts: { links: 2 } },
      { evidenceId: "second-read" },
    ),
  );

  assert.equal(records.length, 2);
  assert.equal(records[0].observationCount, 1);
  assert.equal(records[1].observationCount, 1);
});

test("dynamic pages merge rotating labels and links into one structure record", () => {
  const storage = memoryStorage();
  const first = createBrowserReadEvidence(
    {
      capturedAt: "2026-08-02T00:00:00.000Z",
      title: "News headline A",
      url: "https://www.naver.com/",
      counts: { buttons: 10, links: 49, inputs: 1, forms: 1 },
      controls: [
        { kind: "link", label: "Headline A", href: "https://news.example/a" },
      ],
    },
    { evidenceId: "first-dynamic-read" },
  );
  const second = createBrowserReadEvidence(
    {
      capturedAt: "2026-08-02T00:01:00.000Z",
      title: "News headline B",
      url: "https://www.naver.com/?refresh=two",
      counts: { buttons: 10, links: 49, inputs: 1, forms: 1 },
      controls: [
        { kind: "link", label: "Headline B", href: "https://news.example/b" },
      ],
    },
    { evidenceId: "second-dynamic-read" },
  );

  assert.equal(browserReadComparisonMode(first), "structure");
  saveBrowserReadEvidence(storage, first);
  const records = saveBrowserReadEvidence(storage, second);

  assert.equal(records.length, 1);
  assert.equal(records[0].evidenceId, "first-dynamic-read");
  assert.equal(records[0].comparisonMode, "structure");
  assert.equal(records[0].observationCount, 2);
});

test("dynamic pages create a new record when their structure changes", () => {
  const storage = memoryStorage();
  const base = {
    title: "News",
    url: "https://www.naver.com/",
    counts: { buttons: 10, links: 49, inputs: 1, forms: 1 },
    controls: [{ kind: "link", label: "A", href: "https://example.com/a" }],
  };
  saveBrowserReadEvidence(storage, createBrowserReadEvidence(base, { evidenceId: "first" }));
  const changed = saveBrowserReadEvidence(
    storage,
    createBrowserReadEvidence(
      { ...base, counts: { ...base.counts, inputs: 2 } },
      { evidenceId: "second" },
    ),
  );

  assert.equal(changed.length, 2);
  assert.equal(changed[0].comparisonMode, "structure");
  assert.equal(changed[1].observationCount, 1);
});

test("local evidence storage is bounded and can be cleared", () => {
  const storage = memoryStorage();
  for (let index = 0; index < 24; index += 1) {
    saveBrowserReadEvidence(
      storage,
      createBrowserReadEvidence(
        { title: `Page ${index}`, url: `https://example.com/${index}` },
        { evidenceId: `evidence-${index}` },
      ),
    );
  }

  assert.equal(loadBrowserReadEvidence(storage).length, 20);
  assert.ok(storage.getItem(BROWSER_READ_EVIDENCE_STORAGE_KEY));
  clearBrowserReadEvidence(storage);
  assert.deepEqual(loadBrowserReadEvidence(storage), []);
});
