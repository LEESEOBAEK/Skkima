# Agent layer

쓰끼마의 에이전트 운영 계약과 외부 자동화 경계를 설명하는 영역입니다.

## 현재 위치

실행 시 읽는 운영 계약은 [agent.md](agent.md)입니다. 기존 Run과 검증기가 이 경로를
기준으로 동작하도록 resolver와 테스트를 함께 갱신했습니다.

| 하위 영역 | 역할 | 현재 구현 위치 |
|---|---|---|
| `skills/` | Skill 원본·스냅샷·등록 경계 | `apps/browser-workspace-prototype/src-tauri/src/skill_library.rs` |
| `mcp/` | MCP 연결·읽기·근거 저장 경계 | `apps/browser-workspace-prototype/src-tauri/src/chrome_devtools_mcp.rs` |
| `agent.md` | 에이전트 실행·승인·검증 규칙 | `agents/agent.md` |

프로젝트가 소유하지 않은 외부 Skill은 저장소에 복사하지 않고, 등록 시 검증된
`SKILL.md` 스냅샷으로 관리합니다.
