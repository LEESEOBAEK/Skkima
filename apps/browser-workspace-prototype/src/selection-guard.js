export function createSelectionGuard() {
  let revision = 0;

  return {
    begin(projectId) {
      revision += 1;
      return { projectId, revision };
    },
    isCurrent(token, activeProjectId) {
      return Boolean(
        token &&
          token.revision === revision &&
          token.projectId === activeProjectId,
      );
    },
  };
}
