<div align="center">
  <img src="assets/skkima-icon-aurora-transparent.png" width="128" alt="Skkima icon" />
  <h1>쓰끼마 (Skkima)</h1>
  <p><strong>A local research workspace for turning context into traceable work.</strong><br />
  Structure a request, prepare the right workflow, run an AI CLI, and keep the evidence,<br />
  outputs, and recovery history connected.</p>
  <p>
    <img src="https://img.shields.io/badge/version-0.1.7-7564B8" alt="Version 0.1.7" />
    <img src="https://img.shields.io/badge/platform-Windows-4266C6" alt="Windows" />
    <img src="https://img.shields.io/badge/Tauri-2-24C8DB" alt="Tauri 2" />
    <img src="https://img.shields.io/badge/license-MIT-111827" alt="MIT license" />
  </p>
</div>

쓰끼마는 리서치와 복합 작업을 **실행 가능한 구조**로 바꾸는 Windows 데스크톱 워크스페이스입니다. 프로젝트와 작업 세션을 분리하고, 독립 실행·이어가기·분기 Run을 보존하며, 근거와 산출물의 상태를 확인할 수 있게 합니다.

## Why Skkima?

Most AI workflows lose the connection between the original request, the evidence used, the command that ran, and the final deliverable. Skkima keeps that chain visible and recoverable.

```text
user context
    ↓
request structuring → route validation → situation context
    ↓
prepared Workflow Run → user-approved AI CLI execution
    ↓
evidence + deliverable registration → human-readable report
```

## Core capabilities

- **Project-first workspace** — keep editable work in the selected project instead of mixing it with engine files.
- **Run lifecycle** — create independent runs, continue an existing run, or branch without rewriting the parent.
- **Evidence-aware research preflight** — verify local files, URLs, source metadata, and comparison requirements before execution.
- **AI CLI handoff** — prepare a bounded, reviewable operation for Codex, Claude Code, or Antigravity.
- **Approval and recovery boundaries** — separate read, propose, approve, execute, and review states.
- **Traceable outputs** — connect reports and deliverables to their Run ID, Operation ID, source references, and validation state.
- **Local-first privacy** — project paths and run records stay on the user's Windows environment by default.

## Product surface

The current prototype provides:

- project and work-session selection;
- prepared Run inspection before launch;
- CLI selection and execution status;
- Run ID, Operation ID, artifact path, and validation detail views;
- interruption, recovery, and completed-Run refresh;
- browser-side reading and evidence capture boundaries;
- skill and plugin registry foundations.

## Architecture

| Layer | Responsibility |
| --- | --- |
| Tauri + Rust shell | Windows window, local process boundary, filesystem and environment checks |
| Workflow Engine | request structuring, routing, validation, context, reporting, fulfillment checks |
| AI CLI adapter | bounded handoff to the selected CLI after user approval |
| Project workspace | editable project files and final deliverables |
| Run store | manifests, evidence references, artifacts, recovery and continuation state |

## Quick start

```powershell
cd apps\browser-workspace-prototype
npm ci
npm test
npm run dev
```

Build the Windows installer:

```powershell
npm run build
```

The NSIS package is generated under:

```text
apps\browser-workspace-prototype\src-tauri\target\release\bundle\nsis
```

## Verification

The public candidate is based on the `0.1.7` prototype. The verification scope covers JavaScript and Rust tests, research-source preflight, Run/continuation contracts, Tauri release checks, and Windows packaging.

See [verification notes](docs/verification.md) for the current evidence and limitations.

## Documentation

- [Architecture and responsibility boundaries](docs/architecture.md)
- [Product tour](docs/product-tour.md)
- [Research Run reliability](docs/research-run-reliability.md)
- [Skills and plugin governance](docs/skills-and-plugins.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Release and public-scope guide](docs/release-guide.md)

## Scope and status

Skkima is an actively evolving personal Windows prototype. The repository intentionally documents the validated product boundary rather than presenting every planned integration as complete. External services, credentials, automated browsing, and unattended actions remain explicitly bounded and approval-driven.

## License

MIT. See [LICENSE](LICENSE).
