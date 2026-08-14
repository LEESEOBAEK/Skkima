import test from "node:test";
import assert from "node:assert/strict";
import { groupBrowserEvidenceHistory } from "../src/browser-evidence-history.js";

test("browser evidence history groups revisions by normalized URL", () => {
  const groups = groupBrowserEvidenceHistory([
    {
      normalizedUrl: "https://example.com",
      url: "https://example.com/?session=1",
      title: "Example",
      revision: 1,
      lastCapturedAt: "2026-08-02T01:00:00.000Z",
    },
    {
      normalizedUrl: "https://example.com",
      url: "https://example.com/?session=2",
      title: "Example updated",
      revision: 2,
      lastCapturedAt: "2026-08-02T02:00:00.000Z",
    },
    {
      normalizedUrl: "https://other.example.com",
      url: "https://other.example.com/",
      title: "Other",
      revision: 1,
      lastCapturedAt: "2026-08-02T03:00:00.000Z",
    },
  ]);

  assert.equal(groups.length, 2);
  assert.equal(groups[0].key, "https://other.example.com");
  assert.equal(groups[1].records.length, 2);
  assert.equal(groups[1].records[0].revision, 2);
  assert.equal(groups[1].title, "Example updated");
});

test("browser evidence history ignores records without a usable URL", () => {
  const groups = groupBrowserEvidenceHistory([
    null,
    {},
    { title: "No URL" },
    { url: "https://example.com", revision: 1 },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].key, "https://example.com");
});
