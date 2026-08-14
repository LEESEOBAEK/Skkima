# Python engine map

Python 실행 엔진의 실제 구성과 책임을 표시하는 계층입니다.

| 경로 | 역할 | 구분 |
|---|---|---|
| `engine/python/workflow/` | CLI 진입점·실행 라우팅·검증 명령 | 실행 경로 |
| `engine/python/shared/` | 거버넌스·Run identity·artifact·continuation | 공유 모듈 |
| `engine/python/layers/` | 입력 구조화·라우팅·상황 문맥·보고서 계층 | 01~07 계층 |

각 모듈은 `engine/python`을 import root로 사용하며, Run 경로(`ProjectRoot/outputs/workflows`)
와는 분리되어 있습니다.
