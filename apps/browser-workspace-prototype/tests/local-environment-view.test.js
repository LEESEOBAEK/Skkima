import test from "node:test";
import assert from "node:assert/strict";

import {
  environmentToolPresentation,
  formatMemory,
  renderLocalEnvironmentMarkup,
} from "../src/local-environment-view.js";

const escapeHtml = (value) => String(value ?? "");

test("local environment view formats memory and tool probe states", () => {
  assert.equal(formatMemory(16 * 1024 ** 3), "16.0 GB");
  assert.equal(formatMemory(null), "확인할 수 없음");
  assert.deepEqual(environmentToolPresentation({ status: "timeout" }), {
    status: "timeout",
    label: "응답 시간 초과",
  });
  assert.deepEqual(environmentToolPresentation({ status: "error" }), {
    status: "error",
    label: "확인 실패",
  });
});

test("local environment view renders system and tool information", () => {
  const markup = renderLocalEnvironmentMarkup(
    {
      system: {
        operatingSystem: "Windows",
        osVersion: "11",
        architecture: "64비트",
        cpu: "Example CPU",
        memoryBytes: 32 * 1024 ** 3,
        gpu: [{ name: "Example GPU", vramBytes: 16 * 1024 ** 3 }],
      },
      tools: [
        {
          label: "Codex",
          installed: true,
          version: "0.145.0",
          status: "available",
        },
      ],
      checkedAtUnix: 0,
    },
    escapeHtml,
  );

  assert.match(markup, /Windows 11/);
  assert.match(markup, /Example GPU: 16\.0 GB/);
  assert.match(markup, /Codex/);
  assert.match(markup, /0\.145\.0/);
});
