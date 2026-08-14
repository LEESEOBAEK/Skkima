import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const packageJson = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);
const tauriConfig = JSON.parse(
  await readFile(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"),
);
const cargoToml = await readFile(
  new URL("../src-tauri/Cargo.toml", import.meta.url),
  "utf8",
);
const nsisHooks = await readFile(
  new URL("../src-tauri/windows/nsis-hooks.nsh", import.meta.url),
  "utf8",
);

test("desktop release versions remain aligned", () => {
  assert.equal(packageJson.version, tauriConfig.version);
  assert.match(
    cargoToml,
    new RegExp(`^version = "${packageJson.version.replaceAll(".", "\\.")}"$`, "m"),
  );
});

test("desktop release builds a current-user NSIS installer", () => {
  assert.equal(tauriConfig.productName, "쓰끼마");
  assert.equal(tauriConfig.mainBinaryName, "Skkima");
  assert.equal(tauriConfig.bundle.active, true);
  assert.deepEqual(tauriConfig.bundle.targets, ["nsis"]);
  assert.equal(tauriConfig.bundle.windows.nsis.installMode, "currentUser");
  assert.equal(
    tauriConfig.bundle.windows.nsis.installerHooks,
    "windows/nsis-hooks.nsh",
  );
  assert.deepEqual(tauriConfig.bundle.windows.nsis.languages, [
    "Korean",
    "English",
  ]);
});

test("desktop uninstall removes shared libraries only when app data deletion is selected", () => {
  assert.match(nsisHooks, /\$DeleteAppDataCheckboxState = 1/);
  assert.match(nsisHooks, /\$UpdateMode <> 1/);
  assert.match(nsisHooks, /\\Skkima\\plugin-library/);
  assert.match(nsisHooks, /\\Skkima\\skill-library/);
  assert.doesNotMatch(nsisHooks, /\.agents|\.claude/);
});
