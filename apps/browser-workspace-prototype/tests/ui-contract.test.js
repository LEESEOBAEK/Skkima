import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(projectRoot, "src", "index.html"), "utf8");
const styles = readFileSync(join(projectRoot, "src", "styles.css"), "utf8");
const appJavaScript = readFileSync(join(projectRoot, "src", "app.js"), "utf8");
const browserControllerJavaScript = readFileSync(
  join(projectRoot, "src", "browser-workspace-controller.js"),
  "utf8",
);
const browserShellRust = readFileSync(
  join(projectRoot, "src-tauri", "src", "browser_shell.rs"),
  "utf8",
);
const tauriMain = readFileSync(
  join(projectRoot, "src-tauri", "src", "main.rs"),
  "utf8",
);
const cliExecutionRust = readFileSync(
  join(projectRoot, "src-tauri", "src", "cli_execution.rs"),
  "utf8",
);
const skillLibraryRust = readFileSync(
  join(projectRoot, "src-tauri", "src", "skill_library.rs"),
  "utf8",
);
const skillSmokeTestRust = readFileSync(
  join(projectRoot, "src-tauri", "src", "skill_smoke_test.rs"),
  "utf8",
);
const pluginLibraryRust = readFileSync(
  join(projectRoot, "src-tauri", "src", "plugin_library.rs"),
  "utf8",
);
const externalConnectionsRust = readFileSync(
  join(projectRoot, "src-tauri", "src", "external_connections.rs"),
  "utf8",
);
const chromeBridgeManifest = readFileSync(
  join(projectRoot, "chrome-extension", "manifest.json"),
  "utf8",
);
const chromeBridgeSidepanel = readFileSync(
  join(projectRoot, "chrome-extension", "sidepanel.js"),
  "utf8",
);
const chromeBridgeServiceWorker = readFileSync(
  join(projectRoot, "chrome-extension", "service-worker.js"),
  "utf8",
);
const chromeBridgeHtml = readFileSync(
  join(projectRoot, "chrome-extension", "sidepanel.html"),
  "utf8",
);
const environmentStyles = readFileSync(
  join(projectRoot, "src", "local-environment.css"),
  "utf8",
);
const environmentJavaScript = readFileSync(
  join(projectRoot, "src", "local-environment-view.js"),
  "utf8",
);

test("HTML ids stay unique", () => {
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);
  assert.deepEqual(duplicates, []);
});

test("the desktop app uses the Sseukkima product name without changing engine contracts", () => {
  assert.match(html, /<title>쓰끼마<\/title>/);
  assert.match(html, /aria-label="쓰끼마"/);
  assert.match(html, /쓰끼마 정보/);
  assert.doesNotMatch(html, />Schema Workflow</);
});

test("each dialog starts at the top instead of inheriting an earlier scroll position", () => {
  assert.match(
    appJavaScript,
    /elements\.dialogBody\.innerHTML = content;\s*elements\.dialogBody\.scrollTop = 0;\s*elements\.dialogBody\.scrollLeft = 0;/s,
  );
});

test("all referenced local icons exist", () => {
  const iconPaths = [...html.matchAll(/src="\.\/icons\/([^"]+)"/g)].map(
    (match) => match[1],
  );
  const missing = iconPaths.filter(
    (iconPath) => !existsSync(join(projectRoot, "src", "icons", iconPath)),
  );
  assert.deepEqual(missing, []);
});

test("sidebar accessibility contract is present", () => {
  assert.match(html, /id="sidebar-toggle"/);
  assert.match(html, /aria-controls="project-sidebar"/);
  assert.match(html, /id="project-sidebar"/);
  assert.match(html, /id="sidebar-edge"/);
  assert.match(html, /class="sidebar-search-button"/);
  assert.match(html, /class="sidebar-brand-logo" aria-label="쓰끼마"/);
  assert.match(html, /src="\.\/brand-logo\.png"/);
  assert.match(html, /id="search-backdrop"/);
  assert.match(html, /id="global-search-input"/);
  assert.doesNotMatch(html, /id="project-search"/);
});

test("project search uses a dedicated centered search surface", () => {
  assert.match(html, /data-action="open-global-search"/);
  assert.match(html, /class="global-search"/);
  assert.match(appJavaScript, /function openGlobalSearch\(\)/);
  assert.match(styles, /\.search-backdrop\s*\{/);
  assert.doesNotMatch(styles, /\.sidebar-search\s*\{/);
});

test("new projects are prepared before their first workflow run is linked", () => {
  assert.match(html, /data-action="new-project"/);
  assert.match(html, /새 프로젝트 만들기/);
  assert.match(appJavaScript, /invoke\("prepare_new_project"/);
  assert.match(appJavaScript, /approved:\s*true/);
  assert.match(appJavaScript, /invoke\("inspect_project_readiness"/);
  assert.match(appJavaScript, /invoke\("start_first_workflow_run"/);
  assert.match(appJavaScript, /await startWorkflowCli\(/);
  assert.match(appJavaScript, /name="preferredPlatform"/);
  assert.match(appJavaScript, /onboardingState\.preferredPlatform/);
  assert.match(appJavaScript, /첫 Workflow Run 실행 CLI · 단일 선택/);
  assert.match(appJavaScript, /name="skillTemplateProjectId"/);
  assert.match(appJavaScript, /copyProjectSkillConfiguration/);
  assert.match(appJavaScript, /invoke\("install_project_skill"/);
  assert.match(appJavaScript, /activateWorkflowSession\(projectId, runId\)/);
  assert.match(styles, /\.onboarding-platforms\s*\{/);
  assert.match(styles, /\.onboarding-readiness-list\s*\{/);
});

test("new work is reviewed as independent, continuation, or branch before preparation", () => {
  assert.match(appJavaScript, /OPERATION_KINDS/);
  assert.match(appJavaScript, /draft\?\.operationKind \|\| "independent"/);
  assert.match(appJavaScript, /showOperationReviewDialog/);
  assert.match(appJavaScript, /invoke\("prepare_workflow_operation"/);
  assert.match(appJavaScript, /anchorRunId:\s*operationRequiresAnchor/);
  assert.match(appJavaScript, /researchBinding:\s*draft\.researchEnabled/);
  assert.match(appJavaScript, /sourceIds:\s*draft\.researchSources\.map/);
  assert.match(appJavaScript, /저장된 .* 실행 설정을 사용할 수 있습니다/);
  assert.match(appJavaScript, /operation-reuse-cli/);
  assert.match(appJavaScript, /다른 CLI와 권한 방식을 선택할 수 있습니다/);
  assert.match(appJavaScript, /previousExecution/);
  assert.match(styles, /\.operation-kind-options\s*\{/);
  assert.match(styles, /\.operation-review-list\s*\{/);
  assert.doesNotMatch(
    appJavaScript,
    /createTaskSession\(\s*workspaceState,\s*`새 작업/,
  );
});

test("prepared workflow runs expose review and explicit auto approval modes", () => {
  assert.match(html, /data-action="launch-workflow-cli"/);
  assert.match(html, /data-action="show-cli-execution"/);
  assert.match(appJavaScript, /사용자 확인형 실행/);
  assert.match(appJavaScript, /자동 승인 실행/);
  assert.match(appJavaScript, /name="approvalMode"/);
  assert.match(appJavaScript, /approvalMode,/);
  assert.match(appJavaScript, /invoke\("launch_workflow_cli"/);
  assert.match(appJavaScript, /invoke\("inspect_workflow_cli_launch"/);
  assert.match(appJavaScript, /CLI_PLATFORMS/);
  assert.match(appJavaScript, /scheduleCliExecutionPolling\(500\)/);
  assert.match(tauriMain, /cli_execution::launch_workflow_cli/);
  assert.match(tauriMain, /cli_execution::inspect_workflow_cli_launch/);
});

test("browser focus mode pins the workspace and resynchronizes native bounds", () => {
  assert.match(
    styles,
    /body\.browser-focus-mode \.browser-workspace\s*\{[^}]*grid-row:\s*1;[^}]*height:\s*100%;/s,
  );
  assert.match(
    browserControllerJavaScript,
    /function scheduleBoundsSync\(\)[\s\S]*return \{[\s\S]*scheduleBoundsSync,/,
  );
  assert.match(appJavaScript, /browserController\.scheduleBoundsSync\(\);/);
});

test("interactive CLI consoles keep standard input available", () => {
  assert.match(cliExecutionRust, /Start-Process -FilePath 'powershell\.exe'/);
  assert.match(cliExecutionRust, /-NoExit/);
  assert.match(cliExecutionRust, /-EncodedCommand/);
  assert.match(cliExecutionRust, /starting_record_is_stale/);
  assert.doesNotMatch(cliExecutionRust, /\.stdin\(Stdio::null\(\)\)/);
  assert.doesNotMatch(cliExecutionRust, /use std::process::\{Command, Stdio\};/);
});

test("desktop menu labels never collapse into a compact ellipsis menu", () => {
  for (const label of ["파일", "편집", "보기", "도움말"]) {
    assert.match(html, new RegExp(`>\\s*${label}\\s*<`));
  }
  assert.doesNotMatch(html, /compact-menu|data-menu="compact"/);
});

test("sidebar visibility changes without slide transitions", () => {
  const contentShellRule = styles.match(/\.content-shell\s*\{[^}]*\}/s)?.[0] ?? "";
  const projectSidebarRule =
    styles.match(/\.project-sidebar\s*\{[^}]*\}/s)?.[0] ?? "";
  const visibleSidebarRule =
    styles.match(
      /body\.sidebar-pinned \.project-sidebar,\s*body\.sidebar-peek \.project-sidebar\s*\{[^}]*\}/s,
    )?.[0] ?? "";

  assert.doesNotMatch(contentShellRule, /transition\s*:/);
  assert.doesNotMatch(projectSidebarRule, /transition\s*:/);
  assert.doesNotMatch(visibleSidebarRule, /transition\s*:/);
});

test("all scrollable app surfaces hide native drag bars", () => {
  for (const selector of [
    "sidebar-scroll",
    "workbench",
    "companion-pane",
    "global-search-results",
    "dialog",
    "dialog-body",
    "archive-list",
  ]) {
    assert.match(
      styles,
      new RegExp(`\\.${selector}(?:,|\\s*\\{)[\\s\\S]*?scrollbar-width:\\s*none;`),
    );
    assert.match(styles, new RegExp(`\\.${selector}::\\-webkit-scrollbar`));
  }
});

test("titlebar keeps the compact desktop height", () => {
  assert.match(styles, /--titlebar-height:\s*36px;/);
});

test("titlebar menus stay above a pinned sidebar", () => {
  const titlebarRule = styles.match(/\.titlebar\s*\{[^}]*\}/s)?.[0] ?? "";
  const sidebarRule =
    styles.match(/\.project-sidebar\s*\{[^}]*\}/s)?.[0] ?? "";
  const titlebarZIndex = Number(
    titlebarRule.match(/z-index:\s*(\d+)/)?.[1] ?? -1,
  );
  const sidebarZIndex = Number(
    sidebarRule.match(/z-index:\s*(\d+)/)?.[1] ?? -1,
  );

  assert.ok(titlebarZIndex > sidebarZIndex);
});

test("all application menus open below their own left edge", () => {
  for (const menu of ["file", "edit", "view", "help"]) {
    assert.match(
      html,
      new RegExp(
        `class="menu-popover"\\s+data-menu-panel="${menu}"`,
      ),
    );
  }
  assert.doesNotMatch(html, /menu-popover align-right/);
  assert.match(styles, /\.menu-popover\s*\{[^}]*top:\s*calc\(100% \+ 3px\);[^}]*left:\s*0;/s);
});

test("context menus adapt to projects, sessions, files, text, and blank surfaces", () => {
  assert.match(html, /id="surface-context-menu"/);
  assert.doesNotMatch(html, /data-action="select-all-content"/);
  assert.match(appJavaScript, /document\.addEventListener\("contextmenu", openSurfaceContextMenu\)/);
  assert.match(
    appJavaScript,
    /input, textarea, \[contenteditable='true'\][\s\S]*if \(editableTarget\) return;/,
  );
  for (const action of [
    "open-session",
    "rename-session",
    "archive-session",
    "open-project",
    "toggle-project-pin",
    "open-context-in-explorer",
    "copy-context-value",
    "copy-context-relative-path",
    "select-context-content",
  ]) {
    assert.match(appJavaScript, new RegExp(`"${action}"`));
  }
  assert.match(appJavaScript, /range\.selectNodeContents\(scope\)/);
  assert.match(appJavaScript, /navigator\.clipboard\.writeText\(value\)/);
  assert.match(appJavaScript, /invoke\("open_path_in_explorer"/);
  assert.match(tauriMain, /fn open_path_in_explorer/);
  assert.match(tauriMain, /Command::new\("explorer\.exe"\)/);
  assert.match(appJavaScript, /data-context-kind="deliverable"/);
  assert.match(styles, /\.surface-context-menu\s*\{[^}]*position:\s*fixed;[^}]*z-index:\s*400;/s);
});

test("task archive is hover-revealed inside its project and restorable", () => {
  assert.doesNotMatch(html, /id="archived-section"/);
  assert.match(appJavaScript, /request-task-archive/);
  assert.doesNotMatch(appJavaScript, /session-section-label/);
  assert.match(appJavaScript, /showSettings\("archive"\)/);
  assert.match(appJavaScript, /restore-archived-task/);
  assert.match(
    appJavaScript,
    /작업을 현재 목록에서 숨기고 설정의 아카이브 보관함으로 이동합니다/,
  );
  assert.match(
    styles,
    /\.session-line:hover \.project-icon-action,\s*\.project-icon-action:focus-visible\s*\{[^}]*opacity:\s*1;/s,
  );
  assert.match(
    styles,
    /\.session-row span\s*\{[^}]*display:\s*block;[^}]*width:\s*100%;/s,
  );
});

test("settings exposes a dedicated archive repository", () => {
  assert.match(html, /data-action="show-settings"/);
  assert.match(html, /src="\.\/icons\/settings\.svg"/);
  assert.match(appJavaScript, /아카이브 보관함/);
  assert.match(appJavaScript, /id="settings-archive-list"/);
  assert.match(appJavaScript, /id="settings-archive-search"/);
  assert.match(appJavaScript, /id="settings-archive-project"/);
  assert.match(appJavaScript, /id="settings-archive-source"/);
  assert.match(appJavaScript, /id="settings-archive-sort"/);
  assert.match(appJavaScript, /restore-selected-archives/);
  assert.match(appJavaScript, /request-delete-archived-task/);
  assert.match(appJavaScript, /request-delete-selected-archives/);
  assert.match(appJavaScript, /confirm-archive-deletion/);
  assert.match(
    appJavaScript,
    /실제 프로젝트 폴더, Workflow Run, 산출물 파일은 유지됩니다/,
  );
  assert.match(appJavaScript, /archive-previous-page/);
  assert.match(appJavaScript, /archive-next-page/);
  assert.match(appJavaScript, /pageSize:\s*20/);
  assert.match(appJavaScript, /paginateArchivedTasks\(/);
  assert.match(appJavaScript, /archive-settings-content/);
  assert.match(appJavaScript, /about-settings-content/);
  assert.match(
    styles,
    /\.dialog\.dialog-wide:has\(\.settings-shell\)\s*\{[^}]*height:\s*min\(700px,\s*calc\(100vh - 32px\)\)/s,
  );
  assert.match(
    styles,
    /\.archive-list\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*auto;/s,
  );
  assert.doesNotMatch(styles, /\.archive-list\s*\{[^}]*max-height:\s*300px;/s);
  assert.match(styles, /\.archive-item\s*\{[^}]*min-height:\s*48px;/s);
  assert.match(
    styles,
    /\.about-settings-content \.shortcut-list\s*\{[^}]*grid-template-columns:\s*112px minmax\(0,\s*1fr\);[^}]*border-top:\s*1px solid var\(--border\);/s,
  );
});

test("plugin hub manages local skills separately from workflow project contracts", () => {
  assert.match(html, /data-action="show-extensions"/);
  assert.match(html, /id="extension-hub"/);
  assert.match(appJavaScript, /data-extension-action="show-plugins"/);
  assert.match(appJavaScript, /data-extension-action="show-skills"/);
  assert.match(appJavaScript, /스킬 파일 등록/);
  assert.match(
    appJavaScript,
    /원본을 변경하지 않고 표준 SKILL\.md 스냅샷으로 등록합니다/,
  );
  assert.match(appJavaScript, /id="extension-skill-search"/);
  assert.match(appJavaScript, /data-extension-action="show-skill-list"/);
  assert.match(appJavaScript, /data-extension-action="show-skill-grid"/);
  assert.match(styles, /\.skill-library-list\[data-view="grid"\] \.skill-collection-grid/);
  assert.match(styles, /repeat\(auto-fill, minmax\(min\(100%, 480px\), 1fr\)\)/);
  assert.match(styles, /\.extension-page:has\(\.skill-library-list\[data-view="grid"\]\)/);
  assert.match(appJavaScript, /id="extension-skill-project"/);
  assert.match(appJavaScript, /title="\$\{escapeHtml\(selectedProject\?\.name/);
  assert.match(
    styles,
    /\.extension-skill-controls\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/s,
  );
  assert.match(appJavaScript, /invoke\("register_local_skill"/);
  assert.match(appJavaScript, /"install_project_skill"/);
  assert.match(appJavaScript, /"uninstall_project_skill"/);
  assert.match(appJavaScript, /"inspect_project_skill_installations"/);
  assert.match(appJavaScript, /원본은 모든 프로젝트에서 공유됩니다/);
  assert.match(
    appJavaScript,
    /skillSettingsState\.projectId = currentProjectId/,
  );
  assert.match(styles, /\.extension-tabs\s*\{/);
  assert.match(styles, /\.skill-collection-grid\s*\{/);
  assert.match(styles, /\.skill-library-list\s*\{/);
  assert.match(tauriMain, /mod skill_library;/);
  assert.match(tauriMain, /skill_library::register_local_skill/);
  assert.match(skillLibraryRust, /\.agents/);
  assert.match(skillLibraryRust, /\.skkima-install\.json/);
  assert.match(skillLibraryRust, /기존 스킬 폴더를 자동으로 덮어쓰지 않습니다/);
  assert.doesNotMatch(skillLibraryRust, /project-contract\.json/);
  assert.doesNotMatch(appJavaScript, /data-settings-action="show-skill-settings"/);
});

test("project skills expose Codex, Claude Code, and Antigravity compatibility", () => {
  assert.match(appJavaScript, /Codex·Antigravity 공유 경로/);
  assert.match(appJavaScript, /data-platform=/);
  assert.match(appJavaScript, /install-platform-skill/);
  assert.match(appJavaScript, /uninstall-platform-skill/);
  assert.match(styles, /\.skill-platform-grid\s*\{/);
  assert.match(styles, /\.skill-platform-row\s*\{/);
  assert.match(tauriMain, /skill_library::inspect_project_skill_installations/);
  assert.match(tauriMain, /skill_library::install_project_skill/);
  assert.match(tauriMain, /skill_library::uninstall_project_skill/);
  assert.match(skillLibraryRust, /\.claude\/skills/);
  assert.match(skillLibraryRust, /\.agents\/skills/);
  assert.match(skillLibraryRust, /compatible_platforms/);
});

test("project skills can prove real CLI recognition with a bounded smoke test", () => {
  assert.match(appJavaScript, /프로젝트 설치 상태/);
  assert.match(appJavaScript, /class="skill-platform-disclosure"/);
  assert.match(appJavaScript, /request-smoke-test/);
  assert.match(appJavaScript, /skillSmokeTestState\.panelOpen/);
  assert.match(appJavaScript, /skill-smoke-panel[\s\S]*?\? "open"/);
  assert.match(appJavaScript, /invoke\("launch_skill_smoke_test"/);
  assert.match(appJavaScript, /invoke\("inspect_skill_smoke_tests"/);
  assert.match(appJavaScript, /invoke\("cleanup_skill_smoke_test"/);
  assert.match(styles, /\.skill-smoke-panel\s*\{/);
  assert.match(styles, /\.skill-smoke-grid\s*\{/);
  assert.match(tauriMain, /mod skill_smoke_test;/);
  assert.match(tauriMain, /skill_smoke_test::launch_skill_smoke_test/);
  assert.match(skillSmokeTestRust, /skkima-smoke-test/);
  assert.match(skillSmokeTestRust, /MAX_BASELINE_FILES:\s*usize = 10_000/);
  assert.match(skillSmokeTestRust, /unexpected_changes/);
  assert.match(skillSmokeTestRust, /persist_preparation_failure/);
  assert.match(skillSmokeTestRust, /CREATE_NEW_CONSOLE/);
  assert.match(skillSmokeTestRust, /Do not modify, delete, execute, or inspect/);
});

test("plugin hub imports public GitHub snapshots and registers only selected skills", () => {
  assert.match(appJavaScript, /id="plugin-import-form"/);
  assert.match(appJavaScript, /class="plugin-import-panel"/);
  assert.match(appJavaScript, /class="plugin-overview"/);
  assert.match(appJavaScript, /class="plugin-skill-disclosure"/);
  assert.match(appJavaScript, /class="plugin-library-toolbar"/);
  assert.match(appJavaScript, /owner\/repository 또는 GitHub 저장소 링크/);
  assert.match(appJavaScript, /tree\/blob 링크를 지원합니다/);
  assert.match(appJavaScript, /invoke\("import_github_plugin"/);
  assert.match(appJavaScript, /invoke\("register_plugin_skill"/);
  assert.match(appJavaScript, /invoke\("remove_plugin"/);
  assert.match(appJavaScript, /필요한 스킬을 찾아 쓰끼마 작업 환경에 연결합니다/);
  assert.match(styles, /\.plugin-library-item\s*\{/);
  assert.match(styles, /\.plugin-skill-row\s*\{/);
  assert.match(tauriMain, /mod plugin_library;/);
  assert.match(tauriMain, /plugin_library::import_github_plugin/);
  assert.match(tauriMain, /plugin_library::register_plugin_skill/);
  assert.match(pluginLibraryRust, /--no-recurse-submodules/);
  assert.match(pluginLibraryRust, /MAX_PLUGIN_FILES:\s*usize = 2_000/);
  assert.match(pluginLibraryRust, /MAX_PLUGIN_BYTES:\s*u64 = 64 \* 1024 \* 1024/);
  assert.match(pluginLibraryRust, /inspect_skill_source/);
  assert.match(pluginLibraryRust, /register_github_skill/);
  assert.doesNotMatch(pluginLibraryRust, /project-contract\.json/);
});

test("external connections provide a bounded Chrome DevTools read-only check", () => {
  assert.match(appJavaScript, /data-extension-action="show-connections"/);
  assert.match(appJavaScript, /Chrome DevTools MCP/);
  assert.match(appJavaScript, /id="external-connection-endpoint"/);
  assert.match(appJavaScript, /invoke\("inspect_chrome_devtools_connection"/);
  assert.match(appJavaScript, /내장 브라우저\(WebView2\)와 별도 Chrome 연결은 분리되어 있습니다/);
  assert.match(styles, /\.external-connection-card\s*\{/);
  assert.match(styles, /\.external-connection-result\.connected/);
  assert.match(tauriMain, /mod external_connections;/);
  assert.match(
    tauriMain,
    /external_connections::inspect_chrome_devtools_connection/,
  );
  assert.match(externalConnectionsRust, /localhost, 127\.0\.0\.1 또는 ::1/);
  assert.match(externalConnectionsRust, /\/json\/version/);
  assert.match(externalConnectionsRust, /MAX_RESPONSE_BYTES/);
  assert.doesNotMatch(externalConnectionsRust, /document\.cookie|localStorage|sessionStorage/);
});

test("Chrome Bridge is an explicit read-only extension with a local handoff", () => {
  assert.match(chromeBridgeManifest, /"manifest_version":\s*3/);
  assert.match(chromeBridgeManifest, /"sidePanel"/);
  assert.match(chromeBridgeManifest, /"activeTab"/);
  assert.match(chromeBridgeManifest, /"http:\/\/\*\/\*"/);
  assert.match(chromeBridgeManifest, /"https:\/\/\*\/\*"/);
  assert.match(chromeBridgeManifest, /127\.0\.0\.1:3217/);
  assert.match(chromeBridgeHtml, /현재 페이지 읽기/);
  assert.match(chromeBridgeHtml, /Skkima로 보내기/);
  assert.match(chromeBridgeSidepanel, /api\/chrome-context/);
  assert.match(chromeBridgeSidepanel, /skkima-chrome-bridge/);
  assert.match(chromeBridgeSidepanel, /truncateUtf8/);
  assert.match(chromeBridgeSidepanel, /TextEncoder/);
  assert.match(chromeBridgeSidepanel, /selectedText/);
  assert.match(chromeBridgeSidepanel, /read-page-context/);
  assert.match(chromeBridgeServiceWorker, /read-page-context/);
  assert.match(chromeBridgeServiceWorker, /result\?\.result/);
  assert.match(chromeBridgeServiceWorker, /chrome\.scripting\.executeScript/);
  assert.match(chromeBridgeHtml, /최근 전달 기록/);
  assert.match(chromeBridgeSidepanel, /TRANSFER_HISTORY_KEY/);
  assert.match(chromeBridgeSidepanel, /TRANSFER_HISTORY_LIMIT/);
  assert.doesNotMatch(chromeBridgeSidepanel, /document\.cookie|chrome\.cookies/);
  assert.match(appJavaScript, /get_chrome_bridge_history/);
  assert.match(appJavaScript, /select-chrome-context/);
  assert.match(appJavaScript, /delete-chrome-context/);
  assert.match(appJavaScript, /clear-chrome-context-history/);
  assert.match(appJavaScript, /delete_chrome_bridge_context_record/);
  assert.match(appJavaScript, /clear_chrome_bridge_context_history/);
  assert.match(appJavaScript, /external-bridge-records/);
  assert.match(appJavaScript, /external-bridge-context/);
  assert.match(tauriMain, /mod chrome_bridge;/);
  assert.match(tauriMain, /chrome_bridge::get_chrome_bridge_snapshot/);
  assert.match(tauriMain, /chrome_bridge::get_chrome_bridge_history/);
  assert.match(tauriMain, /chrome_bridge::delete_chrome_bridge_context_record/);
  assert.match(tauriMain, /chrome_bridge::clear_chrome_bridge_context_history/);
});

test("only the pinned sidebar removes the top breathing room", () => {
  assert.match(
    styles,
    /body\.sidebar-pinned \.sidebar-header\s*\{[^}]*padding-top:\s*8px;/s,
  );
  assert.match(styles, /\.sidebar-header\s*\{[^}]*padding:\s*30px 13px 12px 17px;/s);
});

test("active projects show every non-archived work session", () => {
  assert.match(
    appJavaScript,
    /const visibleSessions = project\.sessions\s*\.filter\(\(session\) => !session\.archived\);/s,
  );
  assert.doesNotMatch(
    appJavaScript,
    /const visibleSessions = project\.sessions[\s\S]*?\.slice\(0,\s*3\)/,
  );
  assert.match(styles, /\.sidebar-scroll\s*\{[^}]*overflow:\s*auto;/s);
});

test("project monitoring is bounded and refreshes project state independently", () => {
  assert.match(appJavaScript, /monitoredProjectIds\(/);
  assert.match(appJavaScript, /DEFAULT_PROJECT_MONITOR_LIMIT/);
  assert.match(appJavaScript, /scheduleProjectMonitoring\(\);/);
  assert.match(appJavaScript, /recordProjectRefresh\(/);
});

test("project and CLI recovery controls are reachable from the desktop UI", () => {
  assert.match(appJavaScript, /contextMenuItem\("앱 목록에서 제거", "remove-project"\)/);
  assert.match(appJavaScript, /data-action="confirm-project-removal"/);
  assert.match(appJavaScript, /data-action="retry-cli-execution"/);
  assert.match(appJavaScript, /selectionGuard\.isCurrent/);
  assert.match(appJavaScript, /currentSurface\(\)\.kind !== "extension-hub"/);
  assert.match(appJavaScript, /invoke\("list_workflow_cli_launches"/);
  assert.match(appJavaScript, /selectReusableExecutionRecord/);
  assert.match(tauriMain, /cli_execution::list_workflow_cli_launches/);
});

test("local environment entry point is connected to the native diagnostic", () => {
  assert.match(html, /id="local-environment-trigger"/);
  assert.match(html, /data-action="show-local-environment"/);
  assert.match(html, /href="\.\/local-environment\.css"/);
  assert.match(appJavaScript, /invoke\("get_local_environment"\)/);
  assert.match(
    appJavaScript,
    /renderLocalEnvironmentMarkup\(\s*environment,\s*escapeHtml,\s*\)/s,
  );
  assert.match(appJavaScript, /refresh-local-environment/);
});

test("environment view communicates its read-only privacy boundary", () => {
  assert.match(
    environmentJavaScript,
    /계정, API 키, 사용자 경로는 수집하지 않습니다/,
  );
  assert.match(
    appJavaScript,
    /classList\.toggle\("dialog-environment", options\.environment === true\)/,
  );
  assert.match(
    environmentStyles,
    /\.dialog\.dialog-environment \.dialog-body\s*\{[^}]*scrollbar-width:\s*none;/s,
  );
  assert.match(
    environmentStyles,
    /\.dialog\.dialog-environment \.dialog-body::\-webkit-scrollbar\s*\{[^}]*display:\s*none;/s,
  );
});

test("environment view distinguishes missing, timed out, and failed tools", () => {
  assert.match(environmentJavaScript, /missing:\s*"찾지 못함"/);
  assert.match(environmentJavaScript, /timeout:\s*"응답 시간 초과"/);
  assert.match(environmentJavaScript, /error:\s*"확인 실패"/);
  assert.match(environmentStyles, /\.environment-tool-state\.timeout/);
  assert.match(environmentStyles, /\.environment-tool-state\.error/);
});

test("workflow projects render an operational summary before raw identifiers", () => {
  for (const id of [
    "workflow-surface",
    "workflow-conversation",
    "workflow-status",
    "workflow-validation",
    "workflow-deliverable-count",
    "workflow-next-action",
    "workflow-file-list",
    "workflow-history-list",
    "workflow-summary-status",
    "workflow-summary-validation",
    "workflow-summary-deliverables",
    "workflow-summary-next-action",
    "inspector-pane",
    "inspector-content",
    "inspector-resize-handle",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(appJavaScript, /invoke\("inspect_workflow_project"/);
  assert.match(appJavaScript, /function showWorkflowDetails\(\)/);
  assert.match(html, /data-action="show-workflow-details"/);
  assert.match(appJavaScript, /function openInspector\(/);
  assert.match(appJavaScript, /function renderInspector\(/);
  assert.match(appJavaScript, /function closeInspector\(/);
  assert.match(appJavaScript, /INSPECTOR_STORAGE_KEY/);
  assert.match(appJavaScript, /실행 요약/);
  assert.match(appJavaScript, /실행 식별 정보/);
  assert.match(appJavaScript, /inspector-detail-list technical/);
  assert.match(styles, /\.workflow-summary-strip\s*\{/);
  assert.match(
    styles,
    /body\.split-view \.workspace-frame\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s*var\(--inspector-width\);/s,
  );
  assert.match(
    styles,
    /\.inspector-content\s*\{[^}]*overflow:\s*auto;[^}]*scrollbar-width:\s*none;/s,
  );
  assert.match(
    styles,
    /\.inspector-content::\-webkit-scrollbar\s*\{[^}]*display:\s*none;/s,
  );
  assert.match(
    styles,
    /@media \(max-width:\s*1149px\)[\s\S]*?\.companion-pane\s*\{[^}]*position:\s*absolute;/s,
  );
  assert.match(html, /data-inspector-kind="summary"/);
  assert.match(html, /data-inspector-kind="deliverables"/);
  for (const view of ["flow", "review", "deliverables", "history"]) {
    assert.match(html, new RegExp(`data-workflow-view="${view}"`));
    assert.match(html, new RegExp(`data-workflow-panel="${view}"`));
  }
});

test("browser opens as a right-side tool without replacing the main work surface", () => {
  assert.match(html, /id="browser-panel-toggle"/);
  assert.match(html, /id="browser-tool-launcher"/);
  assert.match(html, /data-browser-action="open-browser"/);
  assert.match(appJavaScript, /function openBrowserPanel\(\)/);
  assert.match(appJavaScript, /function closeBrowserPanel\(\)/);
  assert.match(appJavaScript, /browserController\.showLauncher/);
  assert.doesNotMatch(appJavaScript, /kind:\s*"browser-workspace"/);
  assert.match(browserControllerJavaScript, /root\.dataset\.mode = "launcher"/);
  assert.match(browserControllerJavaScript, /root\.dataset\.mode = "browser"/);
  assert.match(
    styles,
    /body\.browser-side-open \.workspace-frame\s*\{[^}]*grid-template-columns:\s*minmax\(320px,\s*1fr\)\s*6px\s*var\(--browser-panel-width\);/s,
  );
  assert.match(html, /id="browser-resize-handle"/);
  assert.match(appJavaScript, /function setBrowserPanelWidth\(width\)/);
  assert.match(appJavaScript, /const maxWidth = Math\.max\(320, frameWidth - 326\)/);
  assert.match(appJavaScript, /if \(uiState\.browserPanelOpen\) \{\s*setBrowserPanelWidth/s);
  assert.match(appJavaScript, /browser-resizing/);
  assert.match(html, /data-browser-action="zoom-out"/);
  assert.match(html, /data-browser-action="zoom-reset"/);
  assert.match(html, /data-browser-action="zoom-in"/);
  assert.match(html, /data-browser-action="toggle-viewport"/);
  assert.match(html, /id="browser-focus-toggle"/);
  assert.match(appJavaScript, /function toggleBrowserFocusMode\(\)/);
  assert.match(styles, /body\.browser-side-open\.browser-focus-mode \.workspace-frame/);
  assert.match(styles, /body\.browser-focus-mode \.workbench/);
  assert.match(browserControllerJavaScript, /set_browser_workspace_zoom/);
  assert.match(browserControllerJavaScript, /set_browser_workspace_viewport/);
  assert.match(browserControllerJavaScript, /invoke\("focus_browser_workspace"\)/);
  assert.match(browserControllerJavaScript, /invoke\("keep_browser_workspace_on_top"\)/);
  assert.match(browserControllerJavaScript, /function desktopFitZoom\(panelWidth\)/);
  assert.doesNotMatch(browserShellRust, /"scale": scale/);
  assert.match(browserShellRust, /SetWindowPos/);
  assert.match(browserShellRust, /HWND_TOP/);
  assert.match(browserControllerJavaScript, /DESKTOP_VIEWPORT_WIDTH = 1280/);
  assert.match(styles, /\.browser-viewport-button\[aria-pressed="true"\]/);
  assert.match(browserControllerJavaScript, /function scheduleBoundsSync\(\)/);
  assert.match(styles, /@container browser-panel \(max-width: 520px\)/);
  assert.match(styles, /@container browser-panel \(max-width: 400px\)/);
  assert.doesNotMatch(html, /browser-restore-panel/);
  assert.doesNotMatch(browserControllerJavaScript, /shouldOfferBrowserRestore/);
});

test("browser page inspection is explicit, read-only, and evidence backed", () => {
  assert.match(html, /data-browser-action="inspect-page"/);
  assert.match(html, /class="browser-inspect-button"/);
  assert.doesNotMatch(html, /data-browser-action="approve-mock"/);
  assert.match(browserControllerJavaScript, /invoke\("inspect_browser_workspace"\)/);
  assert.match(browserControllerJavaScript, /saveBrowserReadEvidence/);
  assert.match(browserControllerJavaScript, /await onEvidence\(displayedEvidence\)/);
  assert.match(appJavaScript, /function showBrowserPageInsight\(evidence, options = \{\}\)/);
  assert.match(appJavaScript, /async function showBrowserEvidenceHistory\(\)/);
  assert.match(appJavaScript, /renderBrowserEvidenceHistory\(records\)/);
  assert.match(appJavaScript, /list_browser_web_evidence/);
  assert.match(html, /data-action="show-browser-evidence-history"/);
  assert.match(tauriMain, /browser_evidence::list_browser_web_evidence/);
  assert.match(appJavaScript, /buildBrowserPageInsight\(evidence\)/);
  assert.match(appJavaScript, /browser-insight-observation/);
  assert.match(appJavaScript, /observationCount/);
  assert.match(appJavaScript, /firstCapturedAt/);
  assert.match(appJavaScript, /lastCapturedAt/);
  assert.match(appJavaScript, /persistence\.revision/);
  assert.match(appJavaScript, /페이지 작업 진단/);
  assert.match(appJavaScript, /copy-browser-agent-brief/);
  assert.match(browserControllerJavaScript, /setReadState\(\s*"ready",\s*`최근 진단에서/);
  assert.doesNotMatch(styles, /data-read-state="complete"/);
  assert.doesNotMatch(html, /id="browser-read-evidence"/);
  assert.doesNotMatch(html, /class="browser-mcp-bar"/);
  assert.doesNotMatch(html, /WebView2 · 읽기 전용/);
  assert.doesNotMatch(html, /id="browser-context-label"/);
  assert.doesNotMatch(appJavaScript, /setObscured\(uiState\.sidebar\.mode === "peek"\)/);
});

test("browser clicks use an explicit proposal and approval gate", () => {
  assert.match(appJavaScript, /createBrowserClickProposal/);
  assert.match(appJavaScript, /approveBrowserClick/);
  assert.match(appJavaScript, /data-browser-click-order/);
  assert.match(appJavaScript, /이번 작업만 허용/);
  assert.match(appJavaScript, /현재 세션에서 허용/);
  assert.match(appJavaScript, /browserSessionClickApprovals/);
  assert.match(appJavaScript, /save_browser_action_record/);
  assert.match(browserControllerJavaScript, /invoke\("execute_browser_click"/);
  assert.match(browserControllerJavaScript, /async function inspectPage\(\)/);
  assert.match(browserShellRust, /pub async fn execute_browser_click/);
  assert.match(browserShellRust, /현재 단계에서는 버튼과 링크만 클릭할 수 있습니다/);
  assert.match(tauriMain, /browser_shell::execute_browser_click/);
  assert.match(tauriMain, /browser_evidence::save_browser_action_record/);
  assert.match(styles, /\.browser-click-approval\s*\{/);
  assert.match(styles, /\.browser-insight-click\s*\{/);
});

test("browser read evidence is persisted to the active workflow project", () => {
  assert.match(appJavaScript, /projectRoot: project\?\.path/);
  assert.match(browserControllerJavaScript, /invoke\("save_browser_web_evidence"/);
  assert.match(browserControllerJavaScript, /status: "local_only"/);
  assert.match(tauriMain, /browser_evidence::save_browser_web_evidence/);
});

test("browser mode reserves the work surface for the page", () => {
  assert.match(styles, /\.browser-toolbar\s*\{[^}]*grid-row:\s*1;/s);
  assert.match(styles, /\.browser-context-strip\s*\{[^}]*grid-row:\s*2;/s);
  assert.match(styles, /\.browser-webview-mount\s*\{[^}]*grid-row:\s*3;/s);
  assert.match(
    styles,
    /\.browser-workspace\[data-mode="browser"\]\s*\{[^}]*grid-template-rows:\s*auto\s+auto\s+minmax\(0,\s*1fr\);/s,
  );
  assert.doesNotMatch(styles, /browser-evidence-resize-handle/);
  assert.doesNotMatch(styles, /browser-clear-read-button/);
});

test("shell refresh hides an existing native browser until its panel is reopened", () => {
  assert.match(browserControllerJavaScript, /async function reconcileNativeWorkspace\(\)/);
  assert.match(browserControllerJavaScript, /invoke\("browser_workspace_state"\)/);
  assert.match(
    browserControllerJavaScript,
    /invoke\("set_browser_workspace_visible", \{ visible: false \}\)/,
  );
  assert.match(browserControllerJavaScript, /reconcileNativeWorkspace\(\);/);
});

test("branch runs expose their source run in the detailed inspector", () => {
  assert.match(appJavaScript, /workflowRelationLabel\(run\)/);
  assert.match(appJavaScript, /\["기준 Run ID", run\.parentRunId \?\? "해당 없음"\]/);
});

test("review-pending runs expose evidence and validation-needed details", () => {
  assert.match(html, /id="workflow-evidence"/);
  assert.match(html, /id="workflow-validation-needed"/);
  assert.match(appJavaScript, /workflowEvidenceLabel\(run\)/);
  assert.match(appJavaScript, /run\.validationNeeded/);
});

test("failed and recovered runs expose cause and recovery details", () => {
  assert.match(html, /id="workflow-failure-card"/);
  assert.match(html, /id="workflow-failure-reason"/);
  assert.match(html, /id="workflow-recovery-card"/);
  assert.match(html, /id="workflow-recovery-action"/);
  assert.match(appJavaScript, /run\.failureReason/);
  assert.match(appJavaScript, /run\.recoveryAction/);
});
