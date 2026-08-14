# Skkima Portfolio Preview v0.1.0

This preview release pairs the public source snapshot with a Windows installer.

## Build identity

- Source branch: `portfolio/source-preview`
- Desktop application version: `0.1.8`
- Target: Windows x64
- Build command: `npm run build:portfolio`
- Installer: `쓰끼마 Portfolio_0.1.8_x64-setup.exe`
- Installer size: `3,387,469` bytes
- SHA-256: `CD3A2E713697F4FBAB30356EA59DCC12D37C8AD1E5B7BA3B6C2487A56D8FDB74`

## Verification

- Desktop JavaScript tests: 143/143 passed
- Python workflow smoke tests: 4/4 passed
- Python engine compilation: passed
- Tauri portfolio build: passed
- Clean-machine installation test: pending

## Scope

This is a Windows-only portfolio preview. It contains no real user Run data,
credentials, or private research outputs. Codex, Claude Code, and Antigravity
are optional integrations and are not required to inspect the source or open
the application.

The installer should be distributed as a GitHub Release Asset rather than
committed to the source tree.
