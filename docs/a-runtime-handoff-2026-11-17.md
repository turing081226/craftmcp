# A runtime 통합·커밋 인계 기록

확인일: 2026-11-17 (KST). [분업안](team-work-split-2026-11-03.md)의 A 영역에 대한 최신 인계 자료다. 이전 독립 구현·실제 Paper 결과는 [11월 10일 작업 기록](runtime-work-plan-2026-11-10.md), API는 [공통 계약](tool-contracts.md)을 따른다.

## GitHub 확인과 병합 범위

`git fetch origin` 후 아래 원격 커밋을 확인했다.

| 대상 | 커밋 | 확인 내용 |
|---|---|---|
| `origin/codex/runtime-lifecycle` | `a143b99` | A 구현은 이미 커밋·푸시되어 있었고 시작 시 작업 트리는 깨끗했음 |
| `origin/main` | `887318c` | [D·B 통합 PR #1](https://github.com/turing081226/craftmcp/pull/1) 병합. 최신 job 총 기한·fixture parser·RCON worker 취소 포함 |
| `origin/C` | `7f79bfd` | 플러그인 빌드·배포 독립 구현. main에는 아직 미반영 |

최신 main을 A 브랜치에 병합했으며 텍스트 충돌은 없었다. D/B 변경을 그대로 보존하고 A의 호환 시험·문서를 추가한다. C는 병합하지 않았다. main과 C의 공통 조상 `fb69741`을 기준으로 `git merge-tree`에서도 충돌 표식은 없었지만, 아래 실행 API 차이는 Git이 해결하지 못하는 통합 사항이다. C의 구버전 스냅샷을 main 파일 위에 복사하거나 브랜치를 강제로 덮어쓰지 않는다.

이번 단계에서는 A를 공용 앱에 자동 등록하거나, 기존 서버를 관리 대상으로 전환하지 않는다. 해당 연결은 D/C/B의 아래 계약을 맞춘 뒤 수행한다. 원본 서버·월드·비밀번호·로컬 설정과 Git 제외 산출물은 커밋하지 않는다.

| 담당 | 다음에 연결할 일 |
|---|---|
| A | 최신 main 호환 시험·인계 문서·충돌 없는 A 브랜치 커밋 준비 |
| B | C의 일반 operation 제출 경계를 기존 단일 JobManager와 합의 |
| C | 동기 runtime 가정을 A의 async·operation 토큰·종료 확인 결과에 맞춤 |
| D | 공용 앱 등록, 단일 RCON 대상 확인, runtime별 중단 해제, 관리 경로의 종료 순서·인수 시험 |

## A가 제공하는 연결 지점

| 구현 | 사용 방식 |
|---|---|
| `RuntimeRegistry.from_debugger_config` | 기존 runtime/project ID 보존, runtime별 명시적 RCON 바인딩, 명시적 managed_ids |
| `RuntimeService.resolve(id)` | `RuntimeTarget` 데이터 객체 반환. `.server_dir`, `.rcon`, `.jdwp`, `.managed` 사용; JSON이 필요하면 `.as_dict()` |
| `await runtime.status(id)` | 공통 envelope의 `result.observations`에서 process/rcon/jdwp/suspension/game_ready를 개별 확인 |
| `await runtime.start/stop(id, timeout_seconds=...)` | 직접 소유한 subprocess만 제어. 내부 서비스는 새 job을 만들지 않음 |
| `async with runtime.exclusive(id) as operation` | stop/start에 같은 operation을 전달. 토큰은 발급 서비스·runtime·동일 asyncio task·열린 문맥에만 유효 |
| `RuntimeJobRouter` / `RuntimeJobs` | B의 실제 CommandSpec·JobManager를 재사용하며 별도 Job 모델·저장소를 만들지 않음 |
| `register_runtime_tools(mcp, runtime, runtime_jobs)` | 조회 2개와 시작·종료 job 도구 2개를 기존 앱에 한 번 등록 |
| `await runtime.close()` | debugger callback이 살아 있는 동안 정상 종료 시도. 실패 결과는 숨기지 않고 보고 |

조회 전용 registry 변환의 기본값은 managed=false다. 로컬 LaunchSpec을 추가하고 managed_ids를 지정해야 제어할 수 있다. 이전 PID·state 파일·별도 paper_runner 프로세스를 자동 채택하지 않는다. JDWP 대상은 runtime별 release callback이 없으면 시작도 거절한다.

## D와 확인할 공용 앱 연결

1. **등록과 단일 job 저장소:** 기존 `job_manager`의 실행 함수를 RuntimeJobRouter로 감싸고, command registry에 lifecycle spec을 함께 넣는다. get_job/cancel_job은 그 manager를 계속 사용한다. 별도의 최종 MCP 앱이나 lifecycle 전용 manager를 추가하지 않는다.
2. **일반 명령의 허용 목록:** `execute_command`는 기존 `default_commands()`의 ID만 허용한다. 시작·종료는 A의 전용 도구로만 제출한다. 일반 명령 인수의 runtime_id와 job envelope의 runtime_id가 달라지는 경계를 만들지 않는다.
3. **같은 서버 확인:** `MC_RUNTIME_ID`, `MC_SERVER_DIR`, RCON 포트와 debugger registry의 ID·JAR/JDWP가 같은 서버를 뜻하는지 검증한다. 현행 `_execute_job_command(command, timeout)`은 전역 RCON 대상 하나를 사용한다. A registry에 여러 ID가 있다는 이유만으로 이 fallback을 다중 서버 실행기로 사용하지 않는다. runtime별 실행 함수가 준비되기 전에는 현재 단일 대상 guard를 유지한다.
4. **중단 해제:** `release_runtime(runtime_id) -> None`은 그 runtime의 세션만 해제한다. 실패하면 예외를 발생시킨다. `invoke()`의 ok=false envelope를 성공으로 무시하지 않는다. A probe의 단일 알려진 session 매핑은 공용 앱 전체의 session registry를 대신하지 않는다.
5. **상태 관찰:** suspension/JDWP 관찰은 D의 세션 정보를 주입한다. 조회를 위해 새 JDWP 연결이나 RCON 명령을 보내지 않는다.

현재 main의 lifespan은 **debugger → jobs** 순서이며 `test_lifespan_closes_debugger_before_jobs_even_if_debugger_cleanup_fails`가 이를 검증한다. A 관리 프로세스를 추가할 때는 debugger를 먼저 닫으면 runtime.close의 중단 해제 callback을 사용할 수 없다. 다음 관리 경로를 D가 명시적으로 연결하고 시험해야 한다.

```python
# 새 요청 수락을 끝낸 뒤, 취소 가능한 RCON worker부터 정리한다.
try:
    await job_manager.close()
finally:
    try:
        cleanup = await runtime.close()  # runtime별 detach callback은 아직 사용 가능
        # cleanup["ok"]가 false면 종료 미확인 runtime을 보고한다.
    finally:
        await debugger_service.close()
```

현행 debugger-only/명령 경로의 종료 회귀를 보존하면서, A 관리 경로의 callback·socket·자식 프로세스 정리를 함께 검증한다. 이번 변경에서 공용 server.py의 종료 순서를 임의로 교체하지 않았다.

## C와 확인할 배포 경계

`origin/C`의 `services/plugins.py`는 동기 Protocol을 사용한다. A 객체를 그대로 전달하거나 dictionary 변환만 추가해서는 연결되지 않는다.

| C의 현재 가정 | A의 실제 API / 필요한 조정 |
|---|---|
| `resolve()`가 dict | RuntimeTarget 속성 사용 또는 명시적 직렬화 |
| 동기 `status()`와 최상위 phase/pid | await 후 envelope·개별 observation 해석 |
| 동기 `with exclusive()` | async context, 발급받은 operation 전달 |
| `stop/start(timeout=...)` | await, `timeout_seconds=...`, 동일 operation |
| 중지 후 status.boot_id가 이전 값 | live status의 stopped boot_id는 null. stop 반환값의 boot_id·pid=null·exit_confirmed로 이전 부팅 종료를 확인 |
| `jobs.submit(spec, operation)` | 현재 B는 command_id/arguments/execute API. C 일반 작업을 단일 manager에 넣을 API를 B와 맞춰야 함 |

배포를 하나의 async 작업으로 묶고, 같은 task에서 아래 순서를 유지하는 것이 A의 기본 계약이다. 파일 복사·해시 등 블로킹 작업만 필요한 범위에서 스레드로 옮긴다.

```python
async with runtime.exclusive(runtime_id) as operation:
    stopped = await runtime.stop(runtime_id, operation=operation)
    assert stopped["exit_confirmed"] and stopped["pid"] is None
    previous_boot_id = stopped["boot_id"]
    # C: 검증한 JAR 교체와 설치 journal 완료
    started = await runtime.start(runtime_id, operation=operation)
    assert started["boot_id"] != previous_boot_id
```

`asyncio.run_coroutine_threadsafe`로 stop/start를 각각 새 task에 보내면 같은 operation의 task 검증에 실패한다. 동기 adapter가 필요하면 C/D와 전체 작업의 실행 경계를 먼저 정해야 한다. 토큰 검증을 완화하거나 별도 수명 관리자를 만들어 우회하지 않는다. 기존 deployment.py의 자동 재시작 runner 경로와 A의 명시적 수명 경로도 섞지 않는다.

## B와 확인할 job 경계

main의 새 JobManager는 queue 시간을 총 기한에 포함하고 실행 함수에 남은 시간만 전달한다. run_id도 중복 요청 fingerprint에 포함한다. A는 새 API에서 다음 항목을 추가로 시험한다.

- D fixture parser와 A lifecycle router가 같은 manager에서 각자의 결과를 보존한다.
- queue에서 기한이 지난 stop은 failed/not_sent이며 실제 stop을 전송하지 않는다. 같은 request_id 재요청도 재전송하지 않는다.
- 실행 중 stop을 manager.close로 취소해도 프로세스 핸들을 잃지 않고, debugger callback을 살려 둔 runtime.close에서 정상 정리한다.

일반 `submit(spec, operation)`은 아직 없다. C 배포를 위해 공통 API를 확장한다면 B가 기존 fixture·queue 기한·중복 요청·취소 시험과 함께 취합한다. 배포 작업 안에서 start/stop job을 추가 제출해 같은 queue를 기다리지 않는다. 영속 journal/프로세스 재시작 후 job 복구는 이번 단계의 검증 범위가 아니다.

## 재현과 인수 조건

```powershell
# tests/runtime/__init__.py를 추가해 A 시험도 루트 discovery에 포함한다.
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p "test_*.py" -v
.\.venv\Scripts\python.exe tests/runtime/probe_paper_lifecycle.py --server-dir mc_server
```

| 이번 실행 | 결과 |
|---|---|
| 공용 루트 discovery | **90/90 통과**. A 40개, 기존 B/QA·D 통합 50개. A 새 회귀 3개 포함 |
| debugger discovery | **29/29 통과, skipped=0**. 실제 Java/JDI 시험 7개와 기존 stdio 포함 |
| `git diff --check` / staged 충돌 검사 | 통과. unresolved 파일 없음 |
| 실제 격리 Paper lifecycle probe | 최종 실행 결과를 아래에 기록 |

실제 probe는 이미 EULA가 수락된 source의 바이너리·캐시를 별도 `.cache/rl-*`에 복사하고, 원본 월드나 플러그인을 변경하지 않는다. 실제 MCP 호스트·플레이어 입력·GUI·C의 전체 배포는 별도 인수 범위다.

최종 공용 앱 인수 조건은 등록 도구의 중복·누락 없음, 한 manager에서 모든 job 조회 가능, 일반 명령의 lifecycle 우회 차단, 같은 서버로 RCON/JDWP/폴더 연결, 중단 중 status 응답, 종료 시 소켓·중단·소유한 자식 정리다. 이 조건은 A 독립 시험만으로 완료 표시하지 않는다.
