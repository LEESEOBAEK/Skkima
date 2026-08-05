<div align="center">

<img src="assets/skkima-icon.png" alt="쓰끼마 앱 아이콘" width="128">

# Skkima (쓰끼마)

**AI CLI 작업을 프로젝트에서 검증 가능한 결과까지 이어주는 Windows 데스크톱 작업 공간**

Codex · Claude Code · Antigravity의 작업 세션과 결과를 한곳에서 관리합니다.

![Windows](https://img.shields.io/badge/Windows-11-0078D4?logo=windows11&logoColor=white)
![Tauri](https://img.shields.io/badge/Tauri-2-24C8DB?logo=tauri&logoColor=white)
![Rust](https://img.shields.io/badge/Rust-local_backend-000000?logo=rust&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-2EA043)

</div>

---

## 제품 개요

<p align="center">
  <img src="assets/screenshots/workspace-empty.png" alt="쓰끼마 작업 공간" width="960">
</p>

AI가 작업을 완료했다고 보고한 것만으로는 실제 결과를 판단하기 어렵습니다.
쓰끼마는 프로젝트, 작업 세션, 실행 기록과 산출물을 연결하여 **현재 상태와 다음 행동**을 확인할 수 있게 합니다.

```text
프로젝트 → 작업 세션 → AI CLI 실행 → 결과 검토
```

## 프로젝트를 만든 이유

나는 쓰끼마를 완성된 상용 제품으로 공개하기보다, AI CLI 작업을 프로젝트 단위로 관리하고 실행 과정과 산출물을 검증 가능한 형태로 연결하기 위해 설계한 Windows 데스크톱 포트폴리오 프로젝트로 소개하고 싶었습니다. AI의 완료 보고와 실제 검증 결과를 분리하고, 작업 중 발생하는 실행 상태·산출물·근거를 한 흐름으로 확인하는 것이 이 프로젝트의 출발점입니다.

## 포트폴리오 범위

이 저장소는 개인 로컬 환경에서 검증한 기능과 설계 결과를 선별한 포트폴리오 공개판입니다. 작업 공간, 프로젝트와 작업 세션 관리, 실행 정보와 산출물 검토, 플러그인·스킬 관리, 브라우저 근거 수집, CLI 중단·복구 기능을 대표 화면과 문서로 보여줍니다. 전체 운영 데이터나 내부 실험 과정의 복제본이 아니라 문제 정의, 설계, 구현 판단, 검증 결과를 이해하기 위한 공개 스냅샷입니다.

## 핵심 기능

| 영역 | 기능 |
|---|---|
| 작업 관리 | 프로젝트와 작업 세션을 분리하고 새 작업·이어가기·분기를 구분합니다. |
| 실행 연결 | Codex, Claude Code, Antigravity의 설치 상태와 실행 경로를 확인합니다. |
| 결과 검토 | 완료 상태, 검증 결과, 근거, 산출물과 다음 행동을 함께 확인합니다. |
| 확장 관리 | 로컬 파일과 GitHub 저장소의 스킬을 프로젝트별로 관리합니다. |

## 빠른 시작

> 이 저장소는 포트폴리오 공개판이며 완전한 상용 배포 제품을 제공하는 저장소가 아닙니다.

1. 대표 화면과 문서를 먼저 확인합니다.
2. [구조와 책임 경계](docs/architecture.md)에서 시스템 구성을 확인합니다.
3. [검증 원칙](docs/verification.md)에서 실행 결과를 판단하는 기준을 확인합니다.
4. 배포·운영용 구현은 비공개 개발 저장소에서 별도로 관리합니다.

## 현재 범위

- **지원:** Windows 11, 개인 로컬 환경, Codex·Claude Code·Antigravity
- **지원:** 프로젝트별 작업 세션, 실행 기록, 결과 검토, 스킬 관리
- **아직 보장하지 않음:** 다중 사용자 협업, 다른 운영체제, 원격 데이터 동기화

## 더 알아보기

- [문서 안내](docs/README.md)
- [구조와 책임 경계](docs/architecture.md)
- [검증 원칙](docs/verification.md)

새 기능이 검증되면 README를 늘리는 대신 관련 문서를 이 구조에 추가합니다.

## 공개하지 않는 범위

실제 사용자 작업 데이터, 개인 경로, API 키와 로그인 정보, 전체 실행 로그, `node_modules`, Rust `target`, 검증되지 않은 성능 수치와 미완성 실험 코드는 포함하지 않습니다. 공개판에는 검증된 화면, 핵심 설계 문서, 테스트 및 빌드 결과, 제한사항과 향후 계획만 선별합니다.

## 기술 구성

`Tauri 2` · `Rust` · `HTML/CSS/JavaScript` · `Python` · `JSON/JSONL`

## License

MIT License
