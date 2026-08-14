export const BROWSER_READ_EVIDENCE_STORAGE_KEY =
  "skkima.browser.read-evidence.v1";

const MAX_EVIDENCE_RECORDS = 20;
const MAX_CONTROLS = 60;
const DYNAMIC_LINK_THRESHOLD = 25;
const DYNAMIC_CONTROL_THRESHOLD = 40;

function compactText(value, maxLength = 120) {
  return String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
}

function safePageUrl(value) {
  try {
    const url = new URL(String(value ?? ""));
    if (!/^https?:$/.test(url.protocol)) return "";
    url.username = "";
    url.password = "";
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return "";
  }
}

function normalizeControl(value = {}, fallbackOrder = 0) {
  const allowedKinds = new Set(["button", "link", "input", "select", "textarea"]);
  const kind = allowedKinds.has(value.kind) ? value.kind : "button";
  const order = Number.isInteger(value.order) && value.order >= 0
    ? Math.min(value.order, MAX_CONTROLS - 1)
    : fallbackOrder;
  return {
    order,
    kind,
    label: compactText(value.label) || "이름 없음",
    inputType: kind === "input" ? compactText(value.inputType, 32) : "",
    disabled: value.disabled === true,
    href: kind === "link" ? safePageUrl(value.href) : "",
  };
}

export function normalizeBrowserPageSnapshot(value = {}) {
  const controls = Array.isArray(value.controls)
    ? value.controls.slice(0, MAX_CONTROLS).map(normalizeControl)
    : [];
  const counts = value.counts && typeof value.counts === "object" ? value.counts : {};
  return {
    schemaVersion: "1.0.0",
    capturedAt: compactText(value.capturedAt, 40) || new Date().toISOString(),
    title: compactText(value.title, 160) || "제목 없음",
    url: safePageUrl(value.url),
    counts: {
      buttons: Math.max(0, Number(counts.buttons) || 0),
      links: Math.max(0, Number(counts.links) || 0),
      inputs: Math.max(0, Number(counts.inputs) || 0),
      forms: Math.max(0, Number(counts.forms) || 0),
    },
    hasPasswordField: value.hasPasswordField === true,
    controls,
  };
}

export function createBrowserReadEvidence(snapshot, context = {}) {
  const normalized = normalizeBrowserPageSnapshot(snapshot);
  return {
    ...normalized,
    evidenceId:
      compactText(context.evidenceId, 80) ||
      `browser-read-${Date.now().toString(36)}`,
    projectId: compactText(context.projectId, 120) || null,
    projectName: compactText(context.projectName, 160) || "연결된 프로젝트 없음",
    sessionId: compactText(context.sessionId, 120) || null,
    sessionName: compactText(context.sessionName, 160) || "독립 브라우저 작업",
    source: "webview2-devtools-read-only",
  };
}

export function browserReadObservationKey(evidence = {}) {
  const comparisonMode = browserReadComparisonMode(evidence);
  const controls = Array.isArray(evidence.controls) ? evidence.controls : [];
  const structure = controls
    .map((control) => ({
      kind: control.kind,
      inputType: compactText(control.inputType, 32),
      disabled: control.disabled === true,
    }))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  return JSON.stringify({
    comparisonMode,
    url: safePageUrl(evidence.url),
    title:
      comparisonMode === "snapshot" ? compactText(evidence.title, 160) : undefined,
    counts: evidence.counts ?? {},
    hasPasswordField: evidence.hasPasswordField === true,
    controls:
      comparisonMode === "snapshot"
        ? controls.map((control) => ({
            kind: control.kind,
            label: compactText(control.label),
            inputType: compactText(control.inputType, 32),
            disabled: control.disabled === true,
            href: safePageUrl(control.href),
          }))
        : structure,
  });
}

export function browserReadComparisonMode(evidence = {}) {
  const links = Number(evidence.counts?.links) || 0;
  const controls = Array.isArray(evidence.controls) ? evidence.controls.length : 0;
  return links >= DYNAMIC_LINK_THRESHOLD || controls >= DYNAMIC_CONTROL_THRESHOLD
    ? "structure"
    : "snapshot";
}

export function loadBrowserReadEvidence(storage) {
  try {
    const value = JSON.parse(
      storage.getItem(BROWSER_READ_EVIDENCE_STORAGE_KEY) ?? "[]",
    );
    return Array.isArray(value) ? value.slice(0, MAX_EVIDENCE_RECORDS) : [];
  } catch {
    return [];
  }
}

export function saveBrowserReadEvidence(storage, evidence) {
  const current = loadBrowserReadEvidence(storage);
  const observationKey = browserReadObservationKey(evidence);
  const comparisonMode = browserReadComparisonMode(evidence);
  const existingIndex = current.findIndex(
    (item) =>
      (item.observationKey || browserReadObservationKey(item)) === observationKey,
  );
  const capturedAt = evidence.capturedAt || new Date().toISOString();
  let next;
  if (existingIndex >= 0) {
    const previous = current[existingIndex];
    const updated = {
      ...previous,
      observationKey,
      comparisonMode,
      firstCapturedAt: previous.firstCapturedAt || previous.capturedAt || capturedAt,
      lastCapturedAt: capturedAt,
      observationCount: Math.max(1, Number(previous.observationCount) || 1) + 1,
      persistence: evidence.persistence || previous.persistence,
    };
    next = [updated, ...current.filter((_, index) => index !== existingIndex)];
  } else {
    next = [
      {
        ...evidence,
        observationKey,
        comparisonMode,
        firstCapturedAt: capturedAt,
        lastCapturedAt: capturedAt,
        observationCount: 1,
      },
      ...current,
    ].filter(
      (item, index, items) =>
        items.findIndex((candidate) => candidate.evidenceId === item.evidenceId) ===
        index,
    );
  }
  next = next.slice(0, MAX_EVIDENCE_RECORDS);
  storage.setItem(BROWSER_READ_EVIDENCE_STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function clearBrowserReadEvidence(storage) {
  storage.removeItem(BROWSER_READ_EVIDENCE_STORAGE_KEY);
}
