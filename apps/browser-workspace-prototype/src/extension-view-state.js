export const SKILL_VIEW_STORAGE_KEY = "skkima.desktop.skill-view.v1";

const SKILL_VIEW_MODES = new Set(["list", "grid"]);

export function normalizeSkillViewMode(value) {
  return SKILL_VIEW_MODES.has(value) ? value : "list";
}

export function loadSkillViewMode(storage) {
  try {
    return normalizeSkillViewMode(storage.getItem(SKILL_VIEW_STORAGE_KEY));
  } catch {
    return "list";
  }
}

export function saveSkillViewMode(storage, mode) {
  const normalized = normalizeSkillViewMode(mode);
  storage.setItem(SKILL_VIEW_STORAGE_KEY, normalized);
  return normalized;
}
