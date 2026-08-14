const LOGIN_PATTERN = /로그인|log\s*in|sign\s*in|계정|account/i;

const KIND_LABELS = {
  button: "버튼",
  link: "링크",
  input: "입력",
  select: "선택",
  textarea: "긴 글 입력",
};

function pageHost(url) {
  try {
    return new URL(url).host;
  } catch {
    return "주소 확인 필요";
  }
}

function loginControlExists(evidence) {
  return evidence.controls.some((control) => LOGIN_PATTERN.test(control.label));
}

function pageMode(evidence) {
  if (evidence.hasPasswordField || loginControlExists(evidence)) {
    return {
      id: "authentication",
      label: "로그인 확인 필요",
      description: "계정 상태를 사용자가 확인한 뒤 작업을 진행해야 합니다.",
    };
  }
  if (evidence.counts.forms > 0 || evidence.counts.inputs > 0) {
    return {
      id: "form",
      label: "입력 작업 가능",
      description: "입력 요소가 있으며 제출 전 사용자 확인이 필요합니다.",
    };
  }
  if (evidence.counts.buttons > 0) {
    return {
      id: "interactive",
      label: "조작 작업 가능",
      description: "버튼 조작이 가능한 페이지입니다.",
    };
  }
  if (evidence.counts.links > 0) {
    return {
      id: "navigation",
      label: "탐색 작업 가능",
      description: "확인된 링크를 따라 정보를 탐색할 수 있습니다.",
    };
  }
  return {
    id: "read-only",
    label: "읽기 중심 페이지",
    description: "현재 화면에서 확인된 조작 요소가 없습니다.",
  };
}

function capabilitiesFor(evidence) {
  const capabilities = [];
  if (evidence.counts.links > 0) {
    capabilities.push(`링크 ${evidence.counts.links}개 탐색`);
  }
  if (evidence.counts.buttons > 0) {
    capabilities.push(`버튼 ${evidence.counts.buttons}개 확인`);
  }
  if (evidence.counts.inputs > 0) {
    capabilities.push(`입력 요소 ${evidence.counts.inputs}개 확인`);
  }
  if (evidence.counts.forms > 0) {
    capabilities.push(`폼 ${evidence.counts.forms}개 검토`);
  }
  if (capabilities.length === 0) capabilities.push("화면 내용 읽기");
  return capabilities;
}

function warningsFor(evidence) {
  const warnings = [];
  if (evidence.hasPasswordField) {
    warnings.push("비밀번호 입력창이 있습니다. 계정 정보는 읽거나 저장하지 않습니다.");
  }
  if (evidence.counts.forms > 0) {
    warnings.push("폼 제출은 현재 읽기 범위 밖이며 명시적 승인 없이 실행하지 않습니다.");
  }
  const disabledCount = evidence.controls.filter((control) => control.disabled).length;
  if (disabledCount > 0) {
    warnings.push(`현재 사용할 수 없는 조작 요소가 ${disabledCount}개 있습니다.`);
  }
  const unnamedCount = evidence.controls.filter(
    (control) => control.label === "이름 없음",
  ).length;
  if (unnamedCount > 0) {
    warnings.push(`이름을 확인할 수 없는 조작 요소가 ${unnamedCount}개 있습니다.`);
  }
  return warnings;
}

function agentBriefFor(evidence, mode, capabilities, warnings) {
  return [
    `현재 페이지: ${evidence.title}`,
    `주소: ${evidence.url || "확인되지 않음"}`,
    `페이지 호스트: ${pageHost(evidence.url)}`,
    `작업 준비 상태: ${mode.label}`,
    `확인된 요소: 버튼 ${evidence.counts.buttons}, 링크 ${evidence.counts.links}, 입력 ${evidence.counts.inputs}, 폼 ${evidence.counts.forms}`,
    `가능한 작업: ${capabilities.join(", ")}`,
    `주의 사항: ${warnings.length ? warnings.join(" / ") : "추가 주의 항목 없음"}`,
    `근거 연결: ${evidence.projectName} / ${evidence.sessionName}`,
    "실행 원칙: 사용자의 별도 요청과 승인 전에는 클릭, 입력, 제출을 수행하지 않는다.",
  ].join("\n");
}

export function buildBrowserPageInsight(evidence) {
  const mode = pageMode(evidence);
  const capabilities = capabilitiesFor(evidence);
  const warnings = warningsFor(evidence);
  const controls = evidence.controls
    .map((control, index) => ({
      ...control,
      order: Number.isInteger(control.order) ? control.order : index,
      kindLabel: KIND_LABELS[control.kind] || "조작 요소",
    }))
    .sort((left, right) => Number(left.disabled) - Number(right.disabled) || left.order - right.order)
    .slice(0, 12);

  return {
    mode,
    host: pageHost(evidence.url),
    capabilities,
    warnings,
    controls,
    agentBrief: agentBriefFor(evidence, mode, capabilities, warnings),
  };
}
