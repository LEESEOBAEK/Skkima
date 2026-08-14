export const DEFAULT_SKILL_PLATFORMS = [
  {
    platform: "codex",
    label: "Codex",
    sharedPlatforms: ["codex", "antigravity"],
  },
  {
    platform: "claude",
    label: "Claude Code",
    sharedPlatforms: ["claude"],
  },
  {
    platform: "antigravity",
    label: "Antigravity",
    sharedPlatforms: ["codex", "antigravity"],
  },
];

export function skillStatusKey(skillId, platform) {
  return `${skillId}:${platform}`;
}

export function buildSkillStatuses(skillId, platforms, statuses) {
  const selectedPlatforms = platforms.length
    ? platforms
    : DEFAULT_SKILL_PLATFORMS;
  return selectedPlatforms.map((platform) => ({
    platform,
    status: statuses.get(skillStatusKey(skillId, platform.platform)),
  }));
}

export function summarizeSkillStatus(statusEntries) {
  const statuses = statusEntries.map((item) => item.status);
  if (statuses.some((status) => status?.state === "conflict")) {
    return "경로 충돌";
  }
  const installed = statuses.filter(
    (status) => status?.state === "installed",
  ).length;
  if (installed === statuses.length && installed > 0) {
    return `${statuses.length}개 플랫폼 준비`;
  }
  if (installed > 0) return `${installed}/${statuses.length} 설치`;
  if (statuses.every(Boolean)) return "미설치";
  return "확인 전";
}

export function platformStatusLabel(status) {
  if (!status) return "확인 전";
  if (status.state === "installed" && status.cliAvailable) {
    return "사용 준비됨";
  }
  if (status.state === "installed") return "CLI 확인 필요";
  if (status.state === "conflict") return "경로 충돌";
  return "설치 가능";
}

export function smokeTestStateLabel(state) {
  if (state === "passed") return "통과";
  if (state === "failed") return "실패";
  if (state === "running" || state === "prepared") return "실행 중";
  return "미실행";
}

export function filterLibrarySkills(skills, query) {
  const normalizedQuery = String(query ?? "")
    .trim()
    .toLocaleLowerCase("ko");
  if (!normalizedQuery) return skills;
  return skills.filter((skill) =>
    [skill.name, skill.description, skill.skillId]
      .join(" ")
      .toLocaleLowerCase("ko")
      .includes(normalizedQuery),
  );
}

export function pluginSkillIsRegistered(skill, registeredSkills) {
  return Boolean(
    skill.valid &&
      registeredSkills.some(
        (registered) =>
          registered.skillId === skill.skillId &&
          registered.sourceHash === skill.sourceHash,
      ),
  );
}

export function filterPlugins(plugins, query) {
  const normalizedQuery = String(query ?? "")
    .trim()
    .toLocaleLowerCase("ko");
  if (!normalizedQuery) return plugins;
  return plugins.filter((plugin) =>
    [
      plugin.name,
      plugin.owner,
      plugin.repository,
      plugin.sourceUrl,
      ...plugin.skills.flatMap((skill) => [skill.name, skill.description]),
    ]
      .join(" ")
      .toLocaleLowerCase("ko")
      .includes(normalizedQuery),
  );
}
