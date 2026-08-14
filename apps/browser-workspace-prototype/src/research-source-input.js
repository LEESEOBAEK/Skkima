const SOURCE_TYPES = new Set(["file", "url", "note"]);

export function parseResearchSourceLines(value) {
  const sources = [];
  for (const [index, rawLine] of String(value || "").split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const fields = line.split("|").map((field) => field.trim());
    if (fields.length !== 7 || fields.some((field) => !field)) {
      throw new Error(`${index + 1}번째 자료는 7개 항목을 | 로 구분해 입력해 주세요.`);
    }
    const [sourceId, sourceType, title, locator, collectedAt, quote, purpose] = fields;
    if (!/^[A-Za-z0-9_-]+$/.test(sourceId)) {
      throw new Error(`${index + 1}번째 source ID는 영문·숫자·-·_만 사용할 수 있습니다.`);
    }
    if (!SOURCE_TYPES.has(sourceType)) {
      throw new Error(`${index + 1}번째 자료 유형은 file, url, note 중 하나여야 합니다.`);
    }
    sources.push({
      sourceId,
      sourceType,
      title,
      locator,
      collectedAt,
      sha256: null,
      quote,
      purpose,
      permissionStatus: "permitted",
    });
  }
  if (!sources.length) throw new Error("리서치 자료를 한 개 이상 등록해 주세요.");
  if (new Set(sources.map((source) => source.sourceId)).size !== sources.length) {
    throw new Error("리서치 source ID는 중복될 수 없습니다.");
  }
  return sources;
}

export function validateResearchBinding(claimKind, sources) {
  if (!sources?.length) return "리서치 자료를 한 개 이상 등록해 주세요.";
  if (!["fact", "comparative"].includes(claimKind)) return "판단 유형을 선택해 주세요.";
  if (claimKind === "comparative" && sources.length < 2) {
    return "비교·권고·효과 판단에는 독립 출처 2개 이상이 필요합니다.";
  }
  if (new Set(sources.map((source) => source.locator)).size !== sources.length) {
    return "같은 원문 또는 URL을 독립 출처로 중복 사용할 수 없습니다.";
  }
  return null;
}
