# Skills

프로젝트가 Skill을 발견·검토·등록하는 경계를 설명합니다.

- Skill 원본은 외부 GitHub 저장소 또는 사용자가 지정한 로컬 파일에서 가져옵니다.
- 등록 전 `SKILL.md`의 이름·설명·해시를 확인합니다.
- 원본을 덮어쓰지 않고 프로젝트에서 승인한 스냅샷만 저장합니다.
- 실제 레지스트리 구현은
  `apps/browser-workspace-prototype/src-tauri/src/skill_library.rs`와
  `plugin_library.rs`에 있습니다.

이 폴더는 프로젝트가 직접 소유한 Skill 계약을 추가할 때 사용하는 명시적 위치입니다.
