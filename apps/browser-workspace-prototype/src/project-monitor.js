export const DEFAULT_PROJECT_MONITOR_INTERVAL_MS = 5000;
export const DEFAULT_PROJECT_MONITOR_LIMIT = 8;

function projectKey(project) {
  return String(project?.id || project?.path || "").trim();
}

export function monitoredProjectIds(
  projects = [],
  activeProjectId = null,
  limit = DEFAULT_PROJECT_MONITOR_LIMIT,
) {
  const safeLimit = Number.isInteger(limit) && limit > 0 ? limit : DEFAULT_PROJECT_MONITOR_LIMIT;
  const ordered = [
    ...projects.filter((project) => project?.id === activeProjectId),
    ...projects.filter((project) => project?.pinned === true),
    ...[...projects]
      .filter((project) => project?.id !== activeProjectId && project?.pinned !== true)
      .sort((left, right) => Number(right?.lastOpenedAt || 0) - Number(left?.lastOpenedAt || 0)),
  ];
  const seen = new Set();
  return ordered
    .filter((project) => {
      const key = projectKey(project);
      if (!key || seen.has(key) || !String(project?.path || "").trim()) return false;
      seen.add(key);
      return true;
    })
    .slice(0, safeLimit)
    .map((project) => project.id);
}

export function shouldRefreshProject(
  project,
  lastRefreshAt = 0,
  now = Date.now(),
  intervalMs = DEFAULT_PROJECT_MONITOR_INTERVAL_MS,
) {
  if (!project?.id || !String(project.path || "").trim()) return false;
  const interval = Number.isFinite(intervalMs) && intervalMs > 0
    ? intervalMs
    : DEFAULT_PROJECT_MONITOR_INTERVAL_MS;
  return now - Number(lastRefreshAt || 0) >= interval;
}

export function recordProjectRefresh(state = {}, projectId, result, at = Date.now()) {
  const next = { ...state };
  next[projectId] = {
    at,
    status: result?.status || "refreshed",
    error: result?.error || null,
  };
  return next;
}
