export function archiveTaskKey(projectId, sessionId) {
  return `${projectId}:${sessionId}`;
}

export function formatArchiveTimestamp(value) {
  if (!Number.isFinite(value)) return "보관 시각 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function filterArchivedTasks(tasks, filters) {
  const normalizedQuery = String(filters.query ?? "")
    .trim()
    .toLocaleLowerCase();
  const projectId = filters.projectId ?? "all";
  const source = filters.source ?? "all";
  const sort = filters.sort ?? "newest";

  return tasks
    .filter(({ project, session }) => {
      if (projectId !== "all" && project.id !== projectId) return false;
      if (source !== "all" && session.source !== source) return false;
      if (!normalizedQuery) return true;
      return `${session.title} ${project.name} ${project.path}`
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    })
    .sort((left, right) => {
      if (sort === "oldest") {
        return (
          (left.session.archivedAt ?? 0) - (right.session.archivedAt ?? 0)
        );
      }
      if (sort === "title") {
        return left.session.title.localeCompare(right.session.title, "ko");
      }
      if (sort === "project") {
        return (
          left.project.name.localeCompare(right.project.name, "ko") ||
          left.session.title.localeCompare(right.session.title, "ko")
        );
      }
      return (right.session.archivedAt ?? 0) - (left.session.archivedAt ?? 0);
    });
}

export function paginateArchivedTasks(tasks, page, pageSize) {
  const normalizedPageSize = Math.max(1, Number(pageSize) || 1);
  const pageCount = Math.max(1, Math.ceil(tasks.length / normalizedPageSize));
  const normalizedPage = Math.min(
    pageCount,
    Math.max(1, Number(page) || 1),
  );
  const pageStart = (normalizedPage - 1) * normalizedPageSize;
  return {
    page: normalizedPage,
    pageCount,
    tasks: tasks.slice(pageStart, pageStart + normalizedPageSize),
  };
}
