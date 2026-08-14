# MCP

MCP 연결과 권한 경계를 설명합니다.

- 현재 구현: `apps/browser-workspace-prototype/src-tauri/src/chrome_devtools_mcp.rs`
- 연결 대상: Chrome DevTools MCP
- 현재 허용 범위: 읽기, 페이지 목록, 스냅샷, 근거 저장
- 클릭·입력·로그인·제출 같은 쓰기 동작은 이 경계에서 실행하지 않습니다.

MCP가 추가되면 연결 방법, 허용 도구, 저장되는 근거 형식을 이 폴더에 기록합니다.
