import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const styles = readFileSync(join(projectRoot, "src", "styles.css"), "utf8");

test("stylesheet responsibility sections remain in cascade order", () => {
  const sections = [
    "Window shell and title bar",
    "Project navigation",
    "Menus and context surfaces",
    "Workspace context",
    "Extension hub: plugins",
    "Workflow workspace",
    "Inspector",
    "Search and dialogs",
    "Settings",
    "Extension hub: skills",
    "Archive settings",
    "Project and workflow dialogs",
    "Shared accessibility and responsive behavior",
    "Responsive shell",
  ];
  const positions = sections.map((section) =>
    styles.indexOf(`/* ${section} */`),
  );

  assert.ok(positions.every((position) => position >= 0));
  assert.deepEqual(
    positions,
    [...positions].sort((left, right) => left - right),
  );
});

test("skill controls declare their base heights at their own selectors", () => {
  assert.match(
    styles,
    /\.skill-platform-row button\s*\{[^}]*min-height:\s*30px;[^}]*padding:\s*0 11px;/s,
  );
  assert.match(
    styles,
    /\.skill-project-selector select\s*\{[^}]*min-height:\s*34px;[^}]*min-width:\s*0;/s,
  );
});
