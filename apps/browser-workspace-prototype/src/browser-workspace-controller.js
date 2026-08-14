import {
  DEFAULT_BROWSER_VIEWPORT_MODE,
  DEFAULT_BROWSER_ZOOM,
  createBrowserSession,
  loadBrowserSession,
  normalizeBrowserViewportMode,
  normalizeBrowserZoom,
  saveBrowserSession,
  sanitizeBrowserUrlForStorage,
} from "./browser-workspace-state.js";
import {
  browserReadObservationKey,
  createBrowserReadEvidence,
  normalizeBrowserPageSnapshot,
  saveBrowserReadEvidence,
} from "./browser-page-read-state.js";

const DEFAULT_URL = "https://example.com/";
const DESKTOP_VIEWPORT_WIDTH = 1280;

function desktopFitZoom(panelWidth) {
  return Math.min(1, Math.max(0.5, panelWidth / DESKTOP_VIEWPORT_WIDTH));
}

function boundsFor(element) {
  const rect = element.getBoundingClientRect();
  return {
    x: rect.left,
    y: rect.top,
    width: Math.max(80, rect.width),
    height: Math.max(80, rect.height),
  };
}

export function createBrowserWorkspaceController({
  root,
  mount,
  storage = localStorage,
  onEvidence = () => {},
}) {
  const invoke = window.__TAURI__?.core?.invoke;
  const addressForm = root.querySelector("#browser-address-form");
  const addressInput = root.querySelector("#browser-address");
  const zoomValue = root.querySelector("#browser-zoom-value");
  const viewportButton = root.querySelector('[data-browser-action="toggle-viewport"]');
  const errorBox = root.querySelector("#browser-error");
  const inspectButton = root.querySelector('[data-browser-action="inspect-page"]');

  let active = false;
  let blocked = false;
  let created = false;
  let currentContext = {};
  let session = loadBrowserSession(storage) ?? createBrowserSession();
  let pollTimer = null;
  let viewportSignature = "";

  zoomValue.textContent = `${Math.round(session.zoom * 100)}%`;

  function updateViewportButton() {
    const desktop = session.viewportMode === "desktop";
    viewportButton.setAttribute("aria-pressed", String(desktop));
    viewportButton.title = desktop
      ? "데스크톱 화면 폭 사용 중. 반응형 화면으로 전환"
      : "반응형 화면 사용 중. 데스크톱 화면 폭으로 전환";
    viewportButton.setAttribute("aria-label", viewportButton.title);
    viewportButton.querySelector("span").textContent = desktop ? "1280" : "자동";
  }

  updateViewportButton();

  function effectiveZoom() {
    const panelWidth = boundsFor(mount).width;
    const fit = session.viewportMode === "desktop" ? desktopFitZoom(panelWidth) : 1;
    return normalizeBrowserZoom(session.zoom * fit);
  }

  const readStates = {
    idle: ["읽기 대기", "브라우저가 준비되면 현재 페이지를 직접 읽을 수 있습니다.", "neutral"],
    ready: ["읽기 준비", "사용자가 요청할 때만 현재 페이지의 구조를 읽습니다.", "ready"],
    reading: ["읽는 중", "페이지 제목과 조작 요소를 읽고 있습니다.", "active"],
    error: ["읽기 실패", "페이지를 읽지 못했습니다. 로그인과 페이지 상태를 확인하세요.", "warning"],
  };

  function setError(message = "") {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  function setReadState(state, description = "") {
    const definition = readStates[state] || readStates.idle;
    const detail = description || definition[1];
    inspectButton.title = `${definition[0]}: ${detail}`;
    inspectButton.setAttribute("aria-label", `${definition[0]}. ${detail}`);
    inspectButton.dataset.tone = definition[2];
    root.dataset.readState = state;
    inspectButton.disabled = state === "reading";
  }

  async function reconcileNativeWorkspace() {
    if (!invoke) return;
    try {
      const state = await invoke("browser_workspace_state");
      created = state.created === true;
      root.dataset.webviewReady = String(created);
      if (state.url) {
        addressInput.value = state.url;
        remember(state.url);
      }
      await invoke("set_browser_workspace_visible", { visible: false });
      setReadState(created ? "ready" : "idle");
    } catch (error) {
      console.warn("새로고침 후 브라우저 상태를 복구하지 못했습니다.", error);
    }
  }

  function remember(url = addressInput.value) {
    session = saveBrowserSession(storage, {
      ...session,
      url: sanitizeBrowserUrlForStorage(url) || session.url,
      projectId: currentContext.projectId || null,
      projectName: currentContext.projectName || "연결된 프로젝트 없음",
      sessionId: currentContext.sessionId || null,
      sessionName: currentContext.sessionName || "독립 브라우저 작업",
    });
  }

  async function applyZoom(value, persist = true) {
    session.zoom = normalizeBrowserZoom(value);
    zoomValue.textContent = `${Math.round(session.zoom * 100)}%`;
    if (invoke && created) {
      await invoke("set_browser_workspace_zoom", { zoom: effectiveZoom() });
    }
    if (persist) remember();
  }

  async function setVisible(visible) {
    if (!invoke || !created) return;
    try {
      await invoke("set_browser_workspace_visible", { visible });
    } catch (error) {
      console.warn("브라우저 표시 상태를 바꾸지 못했습니다.", error);
    }
  }

  async function focusNativeWorkspace() {
    if (!invoke || !created || !active || blocked) return;
    try {
      await invoke("focus_browser_workspace");
    } catch (error) {
      console.warn("브라우저 WebView2 포커스를 설정하지 못했습니다.", error);
    }
  }

  async function syncBounds() {
    if (!invoke || !created || !active || blocked || root.hidden) return;
    try {
      const bounds = boundsFor(mount);
      await invoke("set_browser_workspace_bounds", {
        bounds,
      });
      await invoke("keep_browser_workspace_on_top");
      const signature = [
        session.viewportMode,
        Math.round(bounds.width),
        Math.round(bounds.height),
      ].join(":");
      if (signature !== viewportSignature) {
        await invoke("set_browser_workspace_viewport", {
          mode: session.viewportMode,
          viewportWidth: DESKTOP_VIEWPORT_WIDTH,
          panelWidth: bounds.width,
          panelHeight: bounds.height,
        });
        await invoke("set_browser_workspace_zoom", { zoom: effectiveZoom() });
        viewportSignature = signature;
      }
    } catch (error) {
      console.warn("브라우저 영역을 맞추지 못했습니다.", error);
    }
  }

  function scheduleBoundsSync() {
    requestAnimationFrame(syncBounds);
    window.setTimeout(syncBounds, 100);
    window.setTimeout(syncBounds, 300);
  }

  async function create(url = DEFAULT_URL) {
    if (!invoke) {
      setError("브라우저 작업 공간은 Windows 데스크톱 앱에서 사용할 수 있습니다.");
      return;
    }
    setError();
    try {
      const state = await invoke("create_browser_workspace", {
        url,
        bounds: boundsFor(mount),
      });
      created = state.created === true;
      root.dataset.webviewReady = String(created);
      addressInput.value = state.url || url;
      await applyZoom(session.zoom ?? DEFAULT_BROWSER_ZOOM, false);
      viewportSignature = "";
      remember(addressInput.value);
      setReadState("ready");
      await setVisible(active && !blocked);
      schedulePolling();
    } catch (error) {
      setError(error?.message || String(error));
    }
  }

  async function navigate(value) {
    if (!created) {
      await create(value);
      return;
    }
    try {
      const state = await invoke("navigate_browser_workspace", { url: value });
      addressInput.value = state.url || value;
      remember(addressInput.value);
      setError();
    } catch (error) {
      setError(error?.message || String(error));
    }
  }

  async function refreshState() {
    if (!invoke || !created || !active) return;
    try {
      const state = await invoke("browser_workspace_state");
      created = state.created === true;
      if (state.url && document.activeElement !== addressInput) {
        addressInput.value = state.url;
        remember(state.url);
      }
    } catch (error) {
      console.warn("브라우저 상태를 읽지 못했습니다.", error);
    }
  }

  function schedulePolling() {
    clearInterval(pollTimer);
    if (!active) return;
    pollTimer = setInterval(refreshState, 1200);
  }

  function updateContext(context = {}) {
    currentContext = context;
    session.projectId = context.projectId || null;
    session.projectName = context.projectName || "연결된 프로젝트 없음";
    session.sessionId = context.sessionId || null;
    session.sessionName = context.sessionName || "독립 브라우저 작업";
  }

  async function showLauncher(context = {}) {
    active = false;
    updateContext(context);
    root.dataset.mode = "launcher";
    clearInterval(pollTimer);
    await setVisible(false);
  }

  async function openBrowser(context = currentContext) {
    active = true;
    updateContext(context);
    root.dataset.mode = "browser";
    setReadState(created ? "ready" : "idle");
    if (!created) await create(session.url || DEFAULT_URL);
    await setVisible(!blocked);
    await syncBounds();
    await focusNativeWorkspace();
    scheduleBoundsSync();
    schedulePolling();
  }

  async function deactivate() {
    active = false;
    root.dataset.mode = "launcher";
    clearInterval(pollTimer);
    await setVisible(false);
  }

  async function clickControl(proposal) {
    if (!invoke || !created) {
      throw new Error("브라우저가 아직 준비되지 않았습니다.");
    }
    if (!proposal || !Number.isInteger(proposal.controlIndex)) {
      throw new Error("승인된 클릭 대상이 없습니다.");
    }
    return invoke("execute_browser_click", {
      expectedUrl: proposal.pageUrl,
      controlIndex: proposal.controlIndex,
      expectedKind: proposal.controlKind,
      expectedLabel: proposal.controlLabel,
    });
  }

  async function inspectPage() {
    if (!created || !invoke) {
      throw new Error("브라우저가 아직 준비되지 않았습니다.");
    }
    setReadState("reading");
    setError();
    const snapshot = normalizeBrowserPageSnapshot(
      await invoke("inspect_browser_workspace"),
    );
    const evidence = createBrowserReadEvidence(snapshot, currentContext);
    let persistence = { status: "local_only" };
    if (currentContext.projectRoot) {
      try {
        persistence = await invoke("save_browser_web_evidence", {
          projectRoot: currentContext.projectRoot,
          evidence,
        });
      } catch (error) {
        persistence = {
          status: "project_save_failed",
          detail: error?.message || String(error),
        };
      }
    }
    const storedEvidence = { ...evidence, persistence };
    const storedRecords = saveBrowserReadEvidence(storage, storedEvidence);
    const displayedEvidence =
      storedRecords.find(
        (record) =>
          (record.observationKey || browserReadObservationKey(record)) ===
          browserReadObservationKey(storedEvidence),
      ) || storedEvidence;
    await onEvidence(displayedEvidence);
    setReadState(
      "ready",
      `최근 진단에서 조작 요소 ${evidence.controls.length}개를 확인했습니다. 다시 누르면 현재 페이지를 새로 진단합니다.`,
    );
    return displayedEvidence;
  }

  async function setObscured(value) {
    blocked = Boolean(value);
    await setVisible(active && !blocked && root.hidden === false);
    if (!blocked) scheduleBoundsSync();
  }

  addressForm.addEventListener("submit", (event) => {
    event.preventDefault();
    navigate(addressInput.value);
  });

  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-browser-action]");
    if (!button) return;
    const action = button.dataset.browserAction;
    try {
      if (action === "open-browser") {
        await openBrowser(currentContext);
      } else if (action === "back" || action === "forward") {
        await invoke?.("browser_workspace_history", { direction: action });
      } else if (action === "reload") {
        await invoke?.("reload_browser_workspace");
      } else if (action === "zoom-out") {
        await applyZoom(session.zoom - 0.1);
      } else if (action === "zoom-reset") {
        await applyZoom(DEFAULT_BROWSER_ZOOM);
      } else if (action === "zoom-in") {
        await applyZoom(session.zoom + 0.1);
      } else if (action === "toggle-viewport") {
        session.viewportMode = normalizeBrowserViewportMode(
          session.viewportMode === "desktop" ? "responsive" : DEFAULT_BROWSER_VIEWPORT_MODE,
        );
        viewportSignature = "";
        updateViewportButton();
        remember();
        await syncBounds();
      } else if (action === "external") {
        const result = await invoke?.("open_browser_workspace_in_chrome", {
          url: addressInput.value || session.url,
        });
        setError(`${result?.browser || "외부 브라우저"}에서 열었습니다.`);
      } else if (action === "inspect-page") {
        await inspectPage();
      }
    } catch (error) {
      setError(error?.message || String(error));
      if (action === "inspect-page") setReadState("error");
    }
  });

  const observer = new ResizeObserver(scheduleBoundsSync);
  observer.observe(mount);
  window.addEventListener("resize", scheduleBoundsSync);
  reconcileNativeWorkspace();

  return {
    showLauncher,
    openBrowser,
    updateContext,
    deactivate,
    setObscured,
    clickControl,
    inspectPage,
    syncBounds,
    scheduleBoundsSync,
  };
}
