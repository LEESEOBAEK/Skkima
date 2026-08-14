const DEFAULT_ENDPOINT = "http://127.0.0.1:3217";
const TRANSFER_HISTORY_KEY = "transferHistory";
const TRANSFER_HISTORY_LIMIT = 12;

let currentContext = null;
let bridgeEnabled = true;
let appConnected = false;
let transferHistory = [];

const elements = {
  endpoint: document.querySelector("#endpoint"),
  includeBody: document.querySelector("#include-body"),
  readPage: document.querySelector("#read-page"),
  send: document.querySelector("#send-to-skkima"),
  copy: document.querySelector("#copy-context"),
  result: document.querySelector("#result"),
  dot: document.querySelector("#status-dot"),
  toggle: document.querySelector("#bridge-toggle"),
  shell: document.querySelector(".bridge-shell"),
  modeLabel: document.querySelector("#bridge-mode-label"),
  state: document.querySelector("#bridge-state"),
  stageTitle: document.querySelector("#bridge-stage-title"),
  stageDescription: document.querySelector("#bridge-stage-description"),
  badge: document.querySelector("#context-badge"),
  title: document.querySelector("#page-title"),
  url: document.querySelector("#page-url"),
  selection: document.querySelector("#selection-text"),
  structure: document.querySelector("#structure-summary"),
  historyCount: document.querySelector("#history-count"),
  historyEmpty: document.querySelector("#history-empty"),
  historyList: document.querySelector("#history-list"),
  clearHistory: document.querySelector("#clear-history"),
};

function formatTransferTime(value) {
  if (!value) return "시간 확인 불가";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "시간 확인 불가";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function createHistoryItem(record, index) {
  const item = document.createElement("div");
  item.className = "history-item";
  item.setAttribute("role", "listitem");

  const button = document.createElement("button");
  button.className = "history-item-button";
  button.type = "button";
  button.dataset.historyIndex = String(index);
  button.title = "이 전달 기록 다시 보기";

  const title = document.createElement("strong");
  title.textContent = record.context?.pageTitle || "제목 없음";
  const url = document.createElement("span");
  url.textContent = record.context?.pageUrl || "주소 없음";
  const meta = document.createElement("small");
  meta.textContent = `전달 ${formatTransferTime(record.sentAt)}`;

  button.append(title, url, meta);
  item.append(button);
  return item;
}

function renderTransferHistory() {
  elements.historyCount.textContent = String(transferHistory.length);
  elements.historyList.replaceChildren(
    ...transferHistory.map((record, index) => createHistoryItem(record, index)),
  );
  elements.historyEmpty.hidden = transferHistory.length > 0;
  elements.clearHistory.disabled = transferHistory.length === 0;
}

async function loadTransferHistory() {
  const saved = await chrome.storage.local.get({ [TRANSFER_HISTORY_KEY]: [] });
  transferHistory = Array.isArray(saved[TRANSFER_HISTORY_KEY])
    ? saved[TRANSFER_HISTORY_KEY]
        .filter((record) => record?.context?.pageUrl)
        .slice(0, TRANSFER_HISTORY_LIMIT)
    : [];
  renderTransferHistory();
}

async function saveTransferHistory() {
  await chrome.storage.local.set({
    [TRANSFER_HISTORY_KEY]: transferHistory.slice(0, TRANSFER_HISTORY_LIMIT),
  });
}

async function rememberTransfer(context) {
  transferHistory = [
    { sentAt: new Date().toISOString(), context },
    ...transferHistory,
  ].slice(0, TRANSFER_HISTORY_LIMIT);
  await saveTransferHistory();
  renderTransferHistory();
}

function setResult(message, kind = "") {
  elements.result.textContent = message;
  elements.result.className = `result ${kind}`.trim();
  elements.dot.className = `status-dot ${kind}`.trim();
}

function renderBridgeState() {
  elements.shell.classList.toggle("bridge-disabled", !bridgeEnabled);
  elements.toggle.setAttribute("aria-pressed", String(bridgeEnabled));
  elements.modeLabel.textContent = bridgeEnabled ? (appConnected ? "ON" : "WAIT") : "OFF";
  elements.state.textContent = !bridgeEnabled
    ? "읽기 연결 꺼짐"
    : appConnected
      ? "쓰끼마 앱 연결됨"
      : "쓰끼마 앱 연결 확인 필요";
  elements.stageTitle.textContent = bridgeEnabled
    ? appConnected
      ? "Skkima Bridge가 연결되었습니다"
      : "쓰끼마 앱 연결을 확인하세요"
    : "Skkima Bridge가 꺼져 있습니다";
  elements.stageDescription.textContent = bridgeEnabled
    ? appConnected
      ? "사용자가 요청할 때만 현재 페이지를 읽습니다."
      : "쓰끼마 앱을 실행한 뒤 다시 확인합니다."
    : "브리지를 켜면 현재 페이지를 읽을 수 있습니다.";
  elements.dot.className = `status-dot ${!bridgeEnabled ? "off" : appConnected ? "ready" : "waiting"}`;
  elements.readPage.disabled = !bridgeEnabled;
  elements.send.disabled = !bridgeEnabled || !appConnected || !currentContext;
  elements.copy.disabled = !bridgeEnabled || !currentContext;
}

function renderContext(context) {
  elements.title.textContent = context.pageTitle || "제목 없음";
  elements.url.textContent = context.pageUrl || "-";
  elements.selection.textContent = context.selectedText || "선택한 영역 없음";
  elements.structure.textContent = `${context.headings.length}개 제목 · ${context.links.length}개 링크${context.bodyExcerpt ? " · 본문 요약 포함" : ""}`;
  elements.badge.textContent = "읽기 완료";
  elements.send.disabled = !bridgeEnabled || !appConnected;
  elements.copy.disabled = !bridgeEnabled ? true : false;
}

async function checkSkkimaConnection(showResult = true) {
  if (!bridgeEnabled) {
    appConnected = false;
    renderBridgeState();
    return false;
  }
  let timer;
  try {
    const controller = new AbortController();
    timer = window.setTimeout(() => controller.abort(), 1500);
    const response = await fetch(`${elements.endpoint.value}/api/chrome-context/health`, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    const health = await response.json().catch(() => ({}));
    if (!response.ok || health.status !== "ready") {
      const legacy = await fetch(`${elements.endpoint.value}/api/chrome-context`, {
        method: "OPTIONS",
        cache: "no-store",
        signal: controller.signal,
      });
      if (!legacy.ok && legacy.status !== 204) {
        throw new Error("Skkima 앱이 준비되지 않았습니다.");
      }
    }
    appConnected = true;
    renderBridgeState();
    if (showResult) setResult("쓰끼마 앱에 연결되었습니다. 페이지를 읽을 수 있습니다.", "ready");
    return true;
  } catch (error) {
    appConnected = false;
    renderBridgeState();
    if (showResult) {
      setResult("쓰끼마 앱을 실행하고 로컬 주소를 확인하세요.", "error");
    }
    return false;
  } finally {
    if (timer) window.clearTimeout(timer);
  }
}

function readPageContext(includeBody) {
  const truncateUtf8 = (value, maxBytes) => {
    const text = String(value || "");
    const encoder = new TextEncoder();
    if (encoder.encode(text).length <= maxBytes) return text;
    let low = 0;
    let high = text.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (encoder.encode(text.slice(0, middle)).length <= maxBytes) {
        low = middle;
      } else {
        high = middle - 1;
      }
    }
    return text.slice(0, low);
  };
  const selection = window.getSelection?.()?.toString?.() || "";
  const headings = Array.from(document.querySelectorAll("h1, h2, h3"))
    .map((element) => element.textContent?.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .slice(0, 24);
  const links = Array.from(document.querySelectorAll("a[href]"))
    .map((element) => ({
      text: element.textContent?.replace(/\s+/g, " ").trim().slice(0, 160) || "링크",
      url: element.href,
    }))
    .filter((link) => link.url.startsWith("http://") || link.url.startsWith("https://"))
    .slice(0, 24);
  const bodyExcerpt = includeBody
    ? truncateUtf8(document.body?.innerText?.replace(/\s+/g, " ").trim() || "", 6 * 1024)
    : null;
  return {
    schemaVersion: "0.1.0",
    source: "skkima-chrome-bridge",
    capturedAt: new Date().toISOString(),
    pageUrl: location.href,
    pageTitle: truncateUtf8(document.title, 512),
    selectedText: truncateUtf8(selection.trim(), 8 * 1024),
    headings,
    links,
    bodyExcerpt,
  };
}

async function activeTab() {
  const candidates = [];
  try {
    const lastFocused = await chrome.windows.getLastFocused({ populate: true });
    candidates.push(...(lastFocused?.tabs || []).filter((tab) => tab.active));
  } catch {
    // Fall back to the tab query below when the browser does not expose
    // populated window metadata to the side panel.
  }
  if (!candidates.length) {
    candidates.push(...(await chrome.tabs.query({ active: true, lastFocusedWindow: true })));
  }
  const tab = candidates.find(
    (candidate) =>
      candidate?.id &&
      (candidate.url?.startsWith("http://") || candidate.url?.startsWith("https://")),
  );
  if (tab) return tab;
  if (candidates[0]?.url && !candidates[0].url.startsWith("http")) {
    throw new Error("http 또는 https 웹 페이지에서만 읽을 수 있습니다.");
  }
  throw new Error("현재 활성화된 웹 탭을 확인할 수 없습니다.");
}

async function readCurrentPage() {
  if (!bridgeEnabled) return;
  elements.readPage.disabled = true;
  setResult("현재 페이지를 읽는 중입니다.");
  try {
    const tab = await activeTab();
    const response = await chrome.runtime.sendMessage({
      type: "read-page-context",
      tabId: tab.id,
      includeBody: elements.includeBody.checked,
    });
    if (!response?.ok) {
      throw new Error(response?.error || "현재 페이지에서 읽을 수 있는 내용이 없습니다.");
    }
    currentContext = response.context;
    renderContext(currentContext);
    setResult("읽기 완료. Skkima로 보내거나 JSON을 복사할 수 있습니다.", "ready");
  } catch (error) {
    currentContext = null;
    elements.badge.textContent = "읽기 대기";
    elements.send.disabled = true;
    elements.copy.disabled = true;
    setResult(error instanceof Error ? error.message : String(error), "error");
  } finally {
    elements.readPage.disabled = !bridgeEnabled;
  }
}

async function saveSettings() {
  const endpoint = elements.endpoint.value.trim().replace(/\/$/, "") || DEFAULT_ENDPOINT;
  elements.endpoint.value = endpoint;
  await chrome.storage.local.set({ endpoint, bridgeEnabled });
}

async function sendToSkkima() {
  if (!bridgeEnabled || !appConnected || !currentContext) return;
  await saveSettings();
  setResult("Skkima로 전송하는 중입니다.");
  try {
    const response = await fetch(`${elements.endpoint.value}/api/chrome-context`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(currentContext),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.message || `전송 실패 (${response.status})`);
    await rememberTransfer(currentContext);
    setResult("Skkima에 전송했습니다. 앱의 플러그인 연결 화면에서 확인하세요.", "ready");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setResult(
      message === "Failed to fetch"
        ? "Skkima 앱이 실행 중인지, 로컬 주소가 맞는지 확인하세요."
        : message,
      "error",
    );
  }
}

async function copyContext() {
  if (!bridgeEnabled || !currentContext) return;
  await navigator.clipboard.writeText(JSON.stringify(currentContext, null, 2));
  setResult("읽은 정보를 JSON으로 복사했습니다.", "ready");
}

function selectTransferHistory(index) {
  const record = transferHistory[index];
  if (!record?.context) return;
  currentContext = record.context;
  renderContext(currentContext);
  setResult("최근 전달 기록을 다시 표시했습니다.", "ready");
}

async function clearTransferHistory() {
  if (!transferHistory.length) return;
  transferHistory = [];
  await saveTransferHistory();
  renderTransferHistory();
  setResult("최근 전달 기록을 모두 삭제했습니다.");
}

async function toggleBridge() {
  bridgeEnabled = !bridgeEnabled;
  appConnected = false;
  currentContext = bridgeEnabled ? currentContext : null;
  if (!bridgeEnabled) {
    elements.badge.textContent = "읽기 대기";
    elements.send.disabled = true;
    elements.copy.disabled = true;
  }
  await saveSettings();
  renderBridgeState();
  if (bridgeEnabled) {
    await checkSkkimaConnection();
  } else {
    setResult("읽기 연결을 껐습니다.");
  }
}

async function loadSettings() {
  const saved = await chrome.storage.local.get({
    endpoint: DEFAULT_ENDPOINT,
    bridgeEnabled: true,
  });
  elements.endpoint.value = saved.endpoint || DEFAULT_ENDPOINT;
  bridgeEnabled = saved.bridgeEnabled !== false;
  renderBridgeState();
  await checkSkkimaConnection();
}

elements.readPage.addEventListener("click", readCurrentPage);
elements.send.addEventListener("click", sendToSkkima);
elements.copy.addEventListener("click", copyContext);
elements.historyList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-history-index]");
  if (button) selectTransferHistory(Number(button.dataset.historyIndex));
});
elements.clearHistory.addEventListener("click", clearTransferHistory);
elements.toggle.addEventListener("click", toggleBridge);
elements.endpoint.addEventListener("change", async () => {
  await saveSettings();
  await checkSkkimaConnection();
});
loadSettings();
loadTransferHistory();
