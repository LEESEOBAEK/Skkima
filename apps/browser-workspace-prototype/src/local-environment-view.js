function environmentDetail(escapeHtml, label, value, wide = false) {
  return `<div class="environment-detail${wide ? " wide" : ""}">
    <dt>${escapeHtml(label)}</dt>
    <dd>${escapeHtml(value || "확인할 수 없음")}</dd>
  </div>`;
}

export function formatMemory(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "확인할 수 없음";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function environmentToolPresentation(tool) {
  const status =
    tool?.status || (tool?.installed ? "available" : "missing");
  const labels = {
    available: tool?.version || "설치됨",
    missing: "찾지 못함",
    timeout: "응답 시간 초과",
    error: "확인 실패",
  };

  return {
    status,
    label: labels[status] || labels.error,
  };
}

export function renderLocalEnvironmentMarkup(environment, escapeHtml) {
  const system = environment?.system ?? {};
  const gpuItems = Array.isArray(system.gpu) ? system.gpu : [];
  const gpu =
    gpuItems.length > 0
      ? gpuItems.map((item) => item.name).join(" / ")
      : "확인할 수 없음";
  const vram =
    gpuItems.length > 0
      ? gpuItems
          .map(
            (item) =>
              `${item.name}: ${
                item.vramBytes
                  ? formatMemory(item.vramBytes)
                  : "확인할 수 없음"
              }`,
          )
          .join(" / ")
      : "확인할 수 없음";
  const tools = Array.isArray(environment?.tools) ? environment.tools : [];
  const checkedAt = Number.isFinite(environment?.checkedAtUnix)
    ? new Date(environment.checkedAtUnix * 1000).toLocaleString("ko-KR")
    : "방금";

  const toolRows = tools
    .map((tool) => {
      const presentation = environmentToolPresentation(tool);
      return `<li class="environment-tool">
        <span class="environment-tool-state ${presentation.status}" aria-hidden="true"></span>
        <strong>${escapeHtml(tool.label)}</strong>
        <span class="environment-tool-version">${escapeHtml(presentation.label)}</span>
      </li>`;
    })
    .join("");

  return `<div class="environment-dialog-content">
    <p class="environment-summary">
      이 PC에서 확인된 시스템 성능과 개발 도구입니다. 설치나 설정은 변경하지 않았습니다.
    </p>

    <section class="environment-section" aria-labelledby="system-profile-title">
      <div class="environment-section-heading">
        <h3 id="system-profile-title">시스템</h3>
        <span>${escapeHtml(checkedAt)} 확인</span>
      </div>
      <dl class="environment-details">
        ${environmentDetail(
          escapeHtml,
          "운영체제",
          [system.operatingSystem, system.osVersion].filter(Boolean).join(" "),
        )}
        ${environmentDetail(escapeHtml, "아키텍처", system.architecture)}
        ${environmentDetail(escapeHtml, "CPU", system.cpu)}
        ${environmentDetail(
          escapeHtml,
          "메모리",
          formatMemory(system.memoryBytes),
        )}
        ${environmentDetail(escapeHtml, "GPU", gpu, true)}
        ${environmentDetail(escapeHtml, "VRAM", vram, true)}
      </dl>
    </section>

    <section class="environment-section" aria-labelledby="tool-profile-title">
      <div class="environment-section-heading">
        <h3 id="tool-profile-title">개발 및 AI 도구</h3>
        <span>감지 ${tools.filter((tool) => tool.installed).length}/${tools.length}</span>
      </div>
      <ul class="environment-tools">${toolRows}</ul>
    </section>

    <div class="environment-footer">
      <p>버전 명령만 조회하며 계정, API 키, 사용자 경로는 수집하지 않습니다.</p>
      <button type="button" data-action="refresh-local-environment">다시 확인</button>
    </div>
  </div>`;
}
