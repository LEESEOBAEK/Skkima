import test from "node:test";
import assert from "node:assert/strict";

import { buildBrowserPageInsight } from "../src/browser-page-insight.js";

function evidence(overrides = {}) {
  return {
    title: "회원 관리",
    url: "https://example.com/account",
    projectName: "테스트 프로젝트",
    sessionName: "로그인 점검",
    counts: { buttons: 1, links: 1, inputs: 2, forms: 1 },
    hasPasswordField: true,
    controls: [
      { kind: "input", label: "이메일", inputType: "email", disabled: false, href: "" },
      { kind: "input", label: "비밀번호", inputType: "password", disabled: false, href: "" },
      { kind: "button", label: "로그인", inputType: "", disabled: false, href: "" },
    ],
    ...overrides,
  };
}

test("page insight flags authentication and keeps execution behind approval", () => {
  const insight = buildBrowserPageInsight(evidence());

  assert.equal(insight.mode.id, "authentication");
  assert.match(insight.agentBrief, /사용자의 별도 요청과 승인 전에는 클릭, 입력, 제출을 수행하지 않는다/);
  assert.ok(insight.warnings.some((warning) => warning.includes("비밀번호")));
  assert.equal(insight.host, "example.com");
});

test("page insight presents deterministic capabilities and useful controls", () => {
  const insight = buildBrowserPageInsight(
    evidence({
      title: "문서 모음",
      counts: { buttons: 0, links: 2, inputs: 0, forms: 0 },
      hasPasswordField: false,
      controls: [
        { kind: "link", label: "가이드", inputType: "", disabled: false, href: "https://example.com/guide" },
        { kind: "link", label: "비활성 링크", inputType: "", disabled: true, href: "" },
      ],
    }),
  );

  assert.equal(insight.mode.id, "navigation");
  assert.deepEqual(insight.capabilities, ["링크 2개 탐색"]);
  assert.equal(insight.controls[0].label, "가이드");
  assert.ok(insight.warnings.some((warning) => warning.includes("사용할 수 없는")));
});
