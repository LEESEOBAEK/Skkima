# 릴리스와 공개 범위

## 버전 기준

데스크톱 앱의 현재 포트폴리오 기준은 0.1.5다. 앱의 package, Tauri 설정과 Rust 패키지 버전은 함께 갱신하며, Python Workflow Engine 릴리스와는 별도로 관리한다.

## 로컬 검증 명령

~~~powershell
cd apps\browser-workspace-prototype
npm ci
npm test
npm run build
~~~

빌드가 성공하면 Windows NSIS 설치 프로그램이 다음 경로에 생성된다.

~~~text
apps\browser-workspace-prototype\src-tauri\target\release\bundle\nsis
~~~

## 공개할 자료

- README와 구조 문서
- 대표 화면 캡처
- 검증된 테스트 수와 빌드 결과
- 제한사항과 후속 계획
- 공개 가능한 릴리스 설치 파일과 SHA-256

## 공개하지 않을 자료

- 실제 프로젝트 폴더와 실행 기록
- 개인 경로·계정·API 키·브라우저 로그인 상태
- node_modules, Rust target과 임시 빌드 파일
- 검증되지 않은 성능 수치
- 내부 운영 저장소와 미완성 실험 자료

포트폴리오 저장소는 제품의 모든 내부 코드를 복제하는 곳이 아니라, 문제 정의부터 설계·구현·검증까지의 판단을 읽을 수 있게 만드는 공개 스냅샷이다.
