chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

function truncateUtf8(value, maxBytes) {
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
}

function readPageContext(includeBody) {
  // executeScript serializes this function and runs it in the page context.
  // Keep helpers self-contained; service-worker scope is not available there.
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
      text: element.textContent?.replace(/\s+/g, " ").trim().slice(0, 160) || "link",
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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "read-page-context") return undefined;

  (async () => {
    try {
      const tabId = Number(message.tabId);
      if (!Number.isInteger(tabId)) throw new Error("현재 탭을 확인하지 못했습니다.");
      const tab = await chrome.tabs.get(tabId);
      if (!tab.url?.startsWith("http://") && !tab.url?.startsWith("https://")) {
        throw new Error("http 또는 https 페이지에서만 읽을 수 있습니다.");
      }
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: readPageContext,
        args: [Boolean(message.includeBody)],
      });
      const result = results.find((entry) => entry.frameId === 0) || results[0];
      if (!result?.result || typeof result.result !== "object") {
        throw new Error("현재 페이지에서 읽을 수 있는 내용이 없습니다.");
      }
      sendResponse({ ok: true, context: result.result });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      sendResponse({
        ok: false,
        error: detail || "현재 페이지를 읽지 못했습니다.",
      });
    }
  })();

  return true;
});
