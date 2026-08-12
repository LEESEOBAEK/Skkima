<div align="center">
  <img src="assets/skkima-icon-aurora-transparent.png" width="128" alt="Skkima icon" />
  <h1>쓰끼마 (Skkima)</h1>
  <p><strong>A local workspace for turning ambiguous requests into traceable, AI-assisted workflows.</strong><br />
  Clarify the request, identify where human review is needed, use AI tools, and keep the context,<br />
  evidence, results, and recovery path connected.</p>
  <p>
    <img src="https://img.shields.io/badge/version-0.1.7-7564B8" alt="Version 0.1.7" />
    <img src="https://img.shields.io/badge/platform-Windows-4266C6" alt="Windows" />
    <img src="https://img.shields.io/badge/Tauri-2-24C8DB" alt="Tauri 2" />
    <img src="https://img.shields.io/badge/license-MIT-111827" alt="MIT license" />
  </p>
</div>

쓰끼마는 AI를 활용해 실제 업무와 리서치를 수행하며 발견한 문제에서 출발한 Windows 데스크톱 워크스페이스입니다. 모호한 요청을 실행 가능한 구조로 바꾸고, 프로젝트와 작업 세션을 분리하며, AI 실행 과정의 맥락·근거·승인·결과·복구 상태를 하나의 작업 흐름으로 연결합니다.

## 왜 Skkima를 만들었나요?

저는 AI를 사용하면서 제가 원하는 결과를 얻기 위해 여러 가지 방법을 직접 시도해왔습니다. 프롬프트를 계속 고쳐보기도 했고, GitHub에 공개된 스킬과 MCP를 찾아 실제 작업에 적용해보기도 했습니다. 같은 요청이라도 어떤 맥락을 제공하는지, 어떤 도구와 연결하는지에 따라 결과가 달라졌기 때문입니다.

하지만 시도를 거듭할수록 프롬프트와 도구만으로는 해결되지 않는 문제가 보이기 시작했습니다. 실제 업무나 특정 도메인의 문제를 해결하려고 하면, 요청의 목적과 범위부터 모호한 경우가 많았습니다. 무엇이 진짜 문제인지, 어떤 정보가 필요한지, 결과를 어떤 기준으로 판단해야 하는지도 먼저 정리해야 했습니다.

이 경험을 통해 저는 더 나은 프롬프트를 만드는 것만큼이나 모호한 문제 상황을 구조화하는 일이 중요하다는 것을 알게 되었습니다. AI가 수행할 작업과 사람이 직접 판단하고 승인해야 할 지점을 나누는 일은 단순한 기능 설계를 넘어, 실제 업무에서 마주하는 워크플로와 밀접하게 연결되어 있었습니다. 결국 AI를 잘 활용하려면 도구를 선택하는 것뿐 아니라, 업무의 흐름과 판단 과정을 함께 설계해야 했습니다.

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
