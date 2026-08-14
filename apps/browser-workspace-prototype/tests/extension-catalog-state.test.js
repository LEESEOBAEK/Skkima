import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_SKILL_PLATFORMS,
  buildSkillStatuses,
  filterLibrarySkills,
  filterPlugins,
  platformStatusLabel,
  pluginSkillIsRegistered,
  skillStatusKey,
  smokeTestStateLabel,
  summarizeSkillStatus,
} from "../src/extension-catalog-state.js";

test("skill status summary preserves the three-platform contract", () => {
  const statuses = new Map(
    DEFAULT_SKILL_PLATFORMS.map(({ platform }) => [
      skillStatusKey("brief", platform),
      { state: "installed", cliAvailable: true },
    ]),
  );
  const entries = buildSkillStatuses("brief", [], statuses);
  assert.equal(entries.length, 3);
  assert.equal(summarizeSkillStatus(entries), "3개 플랫폼 준비");

  statuses.set(skillStatusKey("brief", "claude"), { state: "conflict" });
  assert.equal(
    summarizeSkillStatus(buildSkillStatuses("brief", [], statuses)),
    "경로 충돌",
  );
});

test("platform and smoke-test labels stay operationally explicit", () => {
  assert.equal(platformStatusLabel(null), "확인 전");
  assert.equal(
    platformStatusLabel({ state: "installed", cliAvailable: false }),
    "CLI 확인 필요",
  );
  assert.equal(smokeTestStateLabel("prepared"), "실행 중");
  assert.equal(smokeTestStateLabel("passed"), "통과");
});

test("skill search covers name, description, and stable id", () => {
  const skills = [
    { name: "입력 도우미", description: "요청 정리", skillId: "brief" },
    { name: "검토", description: "코드 리뷰", skillId: "review" },
  ];
  assert.deepEqual(
    filterLibrarySkills(skills, "BRIEF").map((skill) => skill.skillId),
    ["brief"],
  );
  assert.equal(filterLibrarySkills(skills, "").length, 2);
});

test("plugin search includes nested skill metadata", () => {
  const plugins = [
    {
      name: "Operations",
      owner: "sample",
      repository: "workflow-skills",
      sourceUrl: "https://github.com/sample/workflow-skills",
      skills: [{ name: "브레인스토밍", description: "아이디어 확장" }],
    },
  ];
  assert.equal(filterPlugins(plugins, "아이디어").length, 1);
  assert.equal(filterPlugins(plugins, "missing").length, 0);
});

test("plugin skill registration requires validity and the same snapshot hash", () => {
  const registered = [{ skillId: "brief", sourceHash: "abc" }];
  assert.equal(
    pluginSkillIsRegistered(
      { valid: true, skillId: "brief", sourceHash: "abc" },
      registered,
    ),
    true,
  );
  assert.equal(
    pluginSkillIsRegistered(
      { valid: false, skillId: "brief", sourceHash: "abc" },
      registered,
    ),
    false,
  );
});
