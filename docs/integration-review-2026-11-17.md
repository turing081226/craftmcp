# A·B·C·D 통합 사전 점검 — 2026-11-17

이 문서는 통합 전 진단 이력이다. 아래 연결 문제의 수정과 최종 검증은
[공용 MCP 통합 결과](integration-2026-11-17.md)에 기록했다.

## 판정

통합 작업을 시작할 수 있다. A/C의 독립 구현과 기존 B/D 회귀는 통과했지만,
현재 브랜치를 병합하는 것만으로 공용 MCP 앱의 전체 기능이 연결되지는 않는다.
아래 API 차이와 등록·종료 경로를 구현한 뒤 공동 인수 시험이 필요하다.

## GitHub 확인 범위

원격 fetch 및 GitHub connector로 브랜치, PR, 비교 결과와 커밋별 status/PR workflow를 확인했다.

| 영역 | 확인한 원격 ref | 커밋 | 상태 |
|---|---|---|---|
| main | `main` | `887318cdc8df315c33bdf9abc731c4d660c23048` | B 구현과 D 통합이 PR #1로 병합됨 |
| A | `codex/runtime-lifecycle` | `782d0864e5a12c7db81ca1220b7a6d3adfb9e77d` | main 기준 ahead 3 / behind 0; 공용 앱 등록은 인계 범위 |
| B | main의 `0e9f89f` 및 D 통합 보강 | main에 포함 | 기존 JobManager와 execute/get/cancel 도구 사용 |
| C | `C` | `7f79bfd86bfe4df9787936c12744e32915a9e2d7` | main 기준 ahead 1 / behind 4; 독립 PluginService와 등록 함수 제공 |
| D | `codex/d-debugger-b-integration` | `6c9ce97ec13b49e0ddfc7dce8200fb09fc96106a` | [PR #1](https://github.com/turing081226/craftmcp/pull/1) merged |
| 과거 debugger | `debugger` | main에 포함 | ahead 0 / behind 22; 별도 신규 통합 내용 없음 |

저장소 PR 검색에서는 병합된 #1만 반환되었고 A/C PR은 확인되지 않았다.
조회한 main/A/C 커밋의 combined status 목록과 PR-triggered workflow 목록은 비어 있었다.
이를 CI 통과로 해석하지 않는다. 로컬 검증 결과는 아래와 구분한다.

## 이번에 직접 수행한 검증

원래 작업 폴더의 D 검증 문서, 로컬 테스트 수정 및 untracked 파일을 보존했다.
별도 `.cache/integration-review-20261117` worktree를 A HEAD에서 만들고,
C를 `--no-commit --no-ff`로 시험 병합했다. 텍스트 충돌은 없었다.
worktree는 detached HEAD의 검토용 병합 상태이며 main 반영·원격 푸시는 하지 않았다.

| 검증 | 결과 | 범위/근거 |
|---|---|---|
| 루트 unittest discovery | 90/90 통과 | A 40개와 기존 B/QA·D 50개; `.cache/review-root-tests.log` |
| C PluginTests | 15/15 통과 | 실제 파일·fake runtime·인프로세스 MCP; `.cache/review-c-tests.log` |
| C→실제 A 조회 연결 | TypeError 재현 | RuntimeTarget을 dict처럼 읽음; status coroutine 미await 경고도 발생 |
| C→실제 B 제출 signature | TypeError 재현 | positional spec/operation이 B의 keyword-only command_id API와 불일치 |
| 공용 앱 도구 목록 | 15개 확인 | A/C를 병합해도 등록 함수가 호출되지 않아 신규 도구 6개 미노출 |
| 병합 diff 공백 검사 | 통과 | `git diff --check --cached` |

로그 경로는 위 worktree 기준이다. API 재현 스크립트는
`.cache/review_boundaries.py`, 결과는 `.cache/review-boundaries.json`이다.
제한 환경에서는 비동기 시험이 멈춰 중단했고, 일반 권한으로 재실행한 결과만 통과로 기록했다.
C의 실제 Gradle RealBuildTests, A/C 실제 Paper probe 및 별도 debugger discovery는 이번 점검에서
재실행하지 않았다. 각 브랜치 인계 문서의 과거 실행 기록과 이번 직접 검증을 구분한다.

## 통합 전 해결할 경계

### 1. C의 runtime API를 A의 실제 API에 연결

- C `services/plugins.py:34`는 동기 dict/status/context manager와 `timeout`을 가정한다.
- A는 RuntimeTarget, async status/start/stop, async exclusive, `timeout_seconds`를 제공한다.
- C `get_plugin_status`를 실제 A에 연결하면 `RuntimeTarget object is not subscriptable`이 발생한다.
- 배포 전체를 동일 asyncio task의 `async with runtime.exclusive(...) as operation` 안에 유지하고,
  내부 stop/start에 같은 operation을 전달한다. 파일·해시·Gradle 작업만 필요한 범위에서 thread로 보낸다.
- 종료 성공은 A의 stop 반환값 `exit_confirmed`, pid=null, 종료한 boot_id로 확인한다.
  live status가 stopped일 때 boot_id=null인 것을 C의 이전 부팅 불일치로 처리하지 않는다.
- 정확한 현재 boot의 plugin/version/enabled 진단 제공자를 연결한다. C probe의 시험 전용 adapter는
  실제 A와의 연결 증거로 사용하지 않는다.

### 2. C의 작업을 단일 B JobManager에 제출

- C `tools/plugins.py:27`은 `submit(spec, operation)`을 요구한다.
- B `services/jobs.py:106`은 `submit(*, command_id, arguments, ...)`만 받는다.
- 일반 async operation 제출 경계를 B에 추가하거나 같은 manager를 사용하는 명시적 adapter로 연결한다.
  별도 queue/Job 저장소를 만들지 않는다.
- 배포 job 안에서 start/stop job을 새로 제출하지 않는다. 내부 runtime 서비스를 직접 호출한다.
- queue 대기를 포함한 총 기한, request_id/run_id 중복 처리, 실행 전 취소와 실행 여부 unknown,
  실패 배포 결과의 status 해석 및 thread 정리를 함께 시험한다.

### 3. D가 공용 등록·대상·종료 경로 연결

- 현행 `server.py`는 debugger와 기존 build/deploy 도구를 등록한다.
  `register_runtime_tools`와 `register_plugin_tools`는 호출하지 않는다.
- 신규 `list_runtimes`, `get_runtime_status`, `start_server`, `stop_server`,
  `get_plugin_status`, `rollback_plugin` 여섯 개가 공용 앱에 없다.
- 기존 build/deploy와 C의 도구를 중복 등록하지 않는다. deploy의 새 build_id 기반 인자와 반환 형식을
  호스트 예제·driver에 반영한다.
- RuntimeJobRouter를 같은 manager에 연결하되 일반 execute_command의 allowlist로 lifecycle 우회를 막는다.
- runtime별 debugger session 해제와 suspension 관찰을 연결한다. 조회를 위해 새 JDWP 접속을 만들지 않는다.
- 관리 경로에서는 jobs 정리 → runtime.close → debugger.close 순서로 release callback을 살려 둔다.
  기존 unmanaged 경로의 debugger/jobs 종료 시험도 유지한다.
- 현재 사용자 서버는 수동 시작한 외부 프로세스다. 관리 대상으로 자동 채택하거나 종료하지 않는다.
  lifecycle 인수 시험은 명시적으로 등록한 격리 관리 서버로 수행한다.

### 4. C 시험을 루트 discovery에 포함

C의 `tests/plugins/`에는 `__init__.py`가 없어 현행 루트 discovery 90개에 포함되지 않는다.
패키지 marker를 추가하거나 CI/표준 시험 명령에 C discovery를 명시한다.
실제 Gradle 시험은 opt-in 범위를 유지한다.

## 권장 통합 순서와 완료 조건

1. origin/main에서 통합 브랜치를 만들고 A/C 변경을 병합한다. D의 미커밋 검증 기록도 보존한다.
2. A 조회 도구·runtime ID 검증·runtime별 debugger release/관찰을 연결한다.
3. 단일 B manager에 A lifecycle router를 연결하고 격리 관리 서버 start/stop 및 shutdown을 확인한다.
4. C의 async 배포와 B operation 경계를 연결하고 기존 build/deploy 등록을 교체한다.
5. 같은 manager에서 모든 job 조회, 중단 중 상태 조회, 정상 배포, enable 실패, rollback,
   취소/기한 만료 후 unknown, 중단된 session 무효화 및 종료 정리를 공동 인수 시험한다.
6. 공용 stdio와 실제 Codex 호스트를 구분해 검증하고, 검토된 변경으로 PR을 준비한다.

각 담당의 독립 구현 완료와 통합 제품 완료를 구분한다. 전체 v1 완료 판정은 위 공동 인수 시험 이후다.
