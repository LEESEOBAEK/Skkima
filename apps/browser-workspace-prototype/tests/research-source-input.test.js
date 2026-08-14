import test from "node:test";
import assert from "node:assert/strict";
import { parseResearchSourceLines, validateResearchBinding } from "../src/research-source-input.js";

test("parses a user-owned file source without asking for a SHA", () => {
  const sources = parseResearchSourceLines("weekly_csv | file | 주간 지표 | research_sources/weekly.csv | 2026-08-06 | 4주 데이터 | 효과 비교");
  assert.equal(sources[0].sha256, null);
  assert.equal(sources[0].permissionStatus, "permitted");
  assert.equal(validateResearchBinding("fact", sources), null);
});

test("requires distinct sources for comparative research", () => {
  const sources = parseResearchSourceLines("a | url | A | https://example.com/a | 2026-08-06 | 요약 | 비교\nb | url | B | https://example.com/b | 2026-08-06 | 요약 | 비교");
  assert.equal(validateResearchBinding("comparative", sources), null);
  assert.match(validateResearchBinding("comparative", sources.slice(0, 1)), /2개/);
});

test("rejects incomplete or duplicate source definitions", () => {
  assert.throws(() => parseResearchSourceLines("a | file | 제목"), /7개/);
  assert.throws(() => parseResearchSourceLines("a | note | A | 메모 | 오늘 | 요약 | 목적\na | note | B | 메모2 | 오늘 | 요약 | 목적"), /중복/);
});
