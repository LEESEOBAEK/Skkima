function timestampValue(value) {
  const parsed = Date.parse(String(value || ""));
  return Number.isNaN(parsed) ? 0 : parsed;
}

function revisionValue(record) {
  const revision = Number(record?.revision);
  return Number.isFinite(revision) ? revision : 0;
}

export function groupBrowserEvidenceHistory(records = []) {
  const groups = new Map();

  for (const record of records) {
    if (!record || typeof record !== "object") continue;
    const key = String(record.normalizedUrl || record.url || "").trim();
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }

  return [...groups.entries()]
    .map(([key, groupRecords]) => {
      const sortedRecords = [...groupRecords].sort(
        (left, right) =>
          revisionValue(right) - revisionValue(left) ||
          timestampValue(right.lastCapturedAt || right.capturedAt) -
            timestampValue(left.lastCapturedAt || left.capturedAt),
      );
      const latest = sortedRecords[0];
      return {
        key,
        url: latest.url || key,
        title: latest.title || "제목 없는 페이지",
        records: sortedRecords,
        latestCapturedAt: latest.lastCapturedAt || latest.capturedAt || "",
      };
    })
    .sort(
      (left, right) =>
        timestampValue(right.latestCapturedAt) -
        timestampValue(left.latestCapturedAt),
    );
}
