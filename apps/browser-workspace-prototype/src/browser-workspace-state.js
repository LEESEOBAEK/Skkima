export const BROWSER_SESSION_STORAGE_KEY = "skkima.browser.workspace.session.v1";
export const DEFAULT_BROWSER_ZOOM = 1;
export const DEFAULT_BROWSER_VIEWPORT_MODE = "desktop";

export function normalizeBrowserViewportMode(value) {
  return value === "responsive" ? "responsive" : DEFAULT_BROWSER_VIEWPORT_MODE;
}

export function normalizeBrowserZoom(value) {
  const zoom = Number(value);
  if (!Number.isFinite(zoom)) return DEFAULT_BROWSER_ZOOM;
  return Math.round(Math.min(1.25, Math.max(0.5, zoom)) * 100) / 100;
}

export function sanitizeBrowserUrlForStorage(value) {
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

export function createBrowserSession(input = {}) {
  return {
    schemaVersion: "1.2.0",
    url: sanitizeBrowserUrlForStorage(input.url) || "https://example.com/",
    projectId: input.projectId || null,
    projectName: input.projectName || "연결된 프로젝트 없음",
    sessionId: input.sessionId || null,
    sessionName: input.sessionName || "독립 브라우저 작업",
    zoom: normalizeBrowserZoom(input.zoom),
    viewportMode: normalizeBrowserViewportMode(input.viewportMode),
    updatedAt: Number.isFinite(input.updatedAt) ? input.updatedAt : Date.now(),
  };
}

export function loadBrowserSession(storage) {
  try {
    const value = JSON.parse(storage.getItem(BROWSER_SESSION_STORAGE_KEY) ?? "null");
    if (!value || !["1.0.0", "1.1.0", "1.2.0"].includes(value.schemaVersion)) return null;
    const migrated = value.schemaVersion === "1.0.0" && value.zoom === 0.8
      ? { ...value, zoom: DEFAULT_BROWSER_ZOOM }
      : value;
    return createBrowserSession(migrated);
  } catch {
    return null;
  }
}

export function saveBrowserSession(storage, session) {
  const safe = createBrowserSession({ ...session, updatedAt: Date.now() });
  storage.setItem(BROWSER_SESSION_STORAGE_KEY, JSON.stringify(safe));
  return safe;
}
