# 공통 도구 계약 v1 — A의 첫 검토 초안

작성: 2026-11-10 (KST). [분업안](team-work-split-2026-11-03.md) §3에 따른 **검토용 초안**이다. 팀 합의를 완료했다고 간주하지 않는다. 구현은 `mcp_server/models/contracts.py`이며 서비스 구현을 import하지 않는다. 기존 debugger JSON Lines 프로토콜과 기존 도구 응답 형식은 유지한다.

## runtime

`RuntimeTarget`은 하나의 서버를 가리키는 불변 등록 정보다. 같은 ID나 같은 서버 폴더의 다른 ID를 중복 등록하면 거절한다. `resolve`는 RCON/JDWP에 접속하지 않는다.

| 필드 | 의미 |
|---|---|
| runtime_id / project_id | 서버의 정식 ID / 연결 프로젝트 ID |
| project_dir / server_dir / server_jar | 정규화한 절대 경로. JAR는 server_dir 바로 아래 파일 |
| rcon | `{host, port, password_env}` 또는 null. 환경변수 이름만 저장·반환하며 값은 읽지 않음 |
| jdwp | `{host, port}` 또는 null. 설정된 주소이며 연결 성공을 뜻하지 않음 |
| managed | 관리 대상으로 명시적으로 등록했는지. 기존 debugger 설정 변환의 기본값은 false. 로컬 설정의 managed_ids로만 명시적으로 허용 |

부팅 ID는 등록 정보에 넣지 않는다. 실제 runner 또는 직접 소유한 자식 프로세스 관찰에서 확인할 수 있을 때만 상태 결과에 반환한다. 관찰하지 못하면 null이다. PID만으로 제어 권한을 부여하지 않는다.

내부 API:

```python
from mcp_server.models.contracts import RconEndpoint
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService
from mcp_server.tools.runtime import register_runtime_tools

registry = RuntimeRegistry.from_debugger_config(
    debugger_config,
    rcon_by_runtime={
        "paper-local": RconEndpoint("127.0.0.1", 25575, "MC_RCON_PASSWORD"),
    },
)
runtime = RuntimeService(registry)
target = runtime.resolve("paper-local")  # RuntimeTarget; 조회 실패는 ContractError
listing = runtime.list_runtimes()        # 공통 결과 envelope
status = await runtime.status("paper-local")
register_runtime_tools(mcp, runtime)     # D가 기존 앱에 한 번 연결
```

이 예시는 기존 `debugger.example.json`의 ID를 사용한다. RCON 주소·환경변수 참조는 사용자가 같은 서버로 확인한 로컬 설정에서 전달한다. 누락된 RCON 연결을 전역 MC_* 변수로 추측하지 않는다. 등록되지 않은 ID에 대한 바인딩도 거절한다. 기존 debugger 설정의 boot_id/server_version은 현재 관찰값으로 복사하지 않는다.

## 공통 결과와 오류

신규 A 도구의 응답에는 아래 필드가 항상 있다. `ok=true`는 조회를 처리했다는 뜻이며 서버나 게임의 정상 작동을 보증하지 않는다. 조회 자체가 실패하면 result=null, error가 객체다.

```json
{
  "schema_version": 1,
  "ok": false,
  "result": null,
  "error": {
    "code": "runtime_not_found",
    "message": "The runtime is not registered."
  },
  "observed_at": "2026-11-10T00:00:00Z",
  "warnings": [],
  "evidence_ids": []
}
```

시각은 UTC RFC3339 문자열이다. `evidence_ids=[]`는 증거 저장소에 연결하지 않았다는 뜻이다. 기존 서비스의 evidence/run 체계를 새로 만들지 않는다. `invalid_argument`는 내부 API의 빈 ID 등에 사용한다. MCP 입력 타입·빈 문자열은 MCP schema에서도 검증한다. 알려지지 않은 ID와 백엔드 예외의 원문은 응답에 반사하지 않는다.

## 상태 관찰

`get_runtime_status(runtime_id)`는 result 안에 runtime_id, managed, boot_id, observations를 반환한다. observations에는 process, rcon, jdwp, suspension, game_ready가 항상 존재한다. 각 항목은 독립적인 `Observation`이다.

```json
{
  "status": "unknown",
  "value": null,
  "source": "runner_unavailable",
  "observed_at": "2026-11-10T00:00:00Z",
  "details": {}
}
```

- `observed`: 해당 출처가 실제 보고한 값. `paper_runner`의 running은 runner가 자식 프로세스를 실행했다고 보고한 것이며 게임 준비 완료가 아니다.
- `unknown`: 관찰 실패·기한 초과·대상 불일치 등으로 현재 값을 알 수 없음. 서버가 정지했다는 의미가 아니다.
- `unsupported`: 이 관찰 채널의 제공자가 아직 연결되지 않음. value는 null이다.

조회 전용 서비스의 기본 제공자는 process뿐이다. 기존 runner의 잠금·heartbeat·서버 폴더 검증을 통과하고 JAR 명령·설정된 JDWP 주소까지 일치할 때 상태와 실행 부팅 ID를 노출한다. 원격 JDWP 대상에 로컬 파일 관찰을 붙이지 않는다. 과거 상태 파일만 남은 경우 unknown이다. 명령 원문은 반환하지 않는다.

수명 관리 서비스가 연결되면 직접 생성한 프로세스 핸들을 우선 관찰하고 game_ready 제공자도 추가한다. 현재 부팅 로그의 준비 완료 표식으로만 game_ready=true를 반환한다. 실행 확인만 설정한 LaunchSpec(ready_pattern=None)은 게임 준비 완료를 주장하지 않는다. 조회 결과의 boot_id는 실행 중일 때만 현재 부팅 ID이고, stop의 반환값에 있는 boot_id는 종료를 확인한 부팅의 ID다.

B/D는 `async def observer(target: RuntimeTarget) -> Observation` 함수를 rcon, jdwp, suspension 등에 주입한다. 관찰 함수는 해당 target의 값만 반환하고 민감 정보를 details에 넣지 않는다. 디버거 상태는 D의 기존 세션·관찰 캐시에서 읽는다. JDWP 단순 TCP 접속은 디버거 연결에 영향을 줄 수 있어 기본 probe로 사용하지 않는다.

```python
runtime = RuntimeService(
    registry,
    observers={"suspension": observe_debugger_state},
    observation_timeout=1,
)
```

채널은 동시에 조회하며 각각 기본 1초, 최대 설정 5초의 기한을 적용한다. 비동기 관찰 함수는 취소에 협조해야 한다. 로컬 runner 파일 읽기는 스레드에서 실행하며 기존 read_state의 최대 약 3초 재시도를 따르므로, 호출 기한이 먼저 끝나도 해당 파일 읽기가 짧게 계속될 수 있다. 요청 취소는 비동기 관찰에도 전파한다. 변경 작업의 queue/잠금을 상태 조회에 사용하지 않는다.

RCON 미연결 상태와 게임 준비 완료는 다른 관찰이다. 향후 RCON probe를 연결할 때도 중단 중에는 probe를 생략하거나 짧게 제한한다. 포트 열림, 인증 성공, 플러그인 활성화, game_ready를 서로 추론하지 않는다.

## 내부 수명 관리와 배타 작업

`RuntimeLifecycle`은 명시적으로 managed인 대상과 로컬 LaunchSpec이 함께 있어야 제어한다. 기존 수동 서버와 별도 paper_runner의 프로세스를 채택하지 않는다. 서비스가 생성한 subprocess 핸들을 보관하고, 해당 핸들의 종료로 성공을 확인한다. 이전 state.json이 실행 중인 상태로 남아 있으면 소유권 불명으로 시작을 거절한다.

```python
from mcp_server.services.runtime_lifecycle import RuntimeLifecycle, paper_launch

# 관리 전환은 확인한 로컬 설정에서만 명시한다. 서버를 자동으로 시작하지 않는다.
registry = RuntimeRegistry.from_debugger_config(debugger_config, managed_ids={"paper-local"})
target = registry.resolve("paper-local")
lifecycle = RuntimeLifecycle(
    registry,
    {target.runtime_id: paper_launch(target, java=debugger_config.java_executable)},
    release_runtime=release_runtime,  # async callback, 아래 계약 참조
)
runtime = RuntimeService(registry, lifecycle=lifecycle)

started = await runtime.start(target.runtime_id, timeout_seconds=120)
stopped = await runtime.stop(target.runtime_id, timeout_seconds=30)
assert stopped["exit_confirmed"]
```

`start`는 등록된 JAR와 JDWP 주소에 맞는 명령만 허용한다. Windows에서는 자식 창을 숨긴다. 정상 종료는 직접 소유한 프로세스의 로컬 stdin으로 `stop`을 한 번만 전달한다. RCON 비밀번호나 전역 포트를 사용하지 않는다. 이 방식으로 기존 샘플의 ConsoleCommandSender 제한도 유지한다. 자식은 자동 재시작하지 않으며, 재시작은 다음 start 호출로만 수행한다.

기본 start 기한은 120초, stop은 30초이고 최대 300초다. 준비 완료 기한이 지나도 프로세스가 남아 있을 수 있다. stop 기한 만료·EOF는 종료 성공을 뜻하지 않으며, stop 재전송이나 강제 종료를 하지 않는다. 아직 실행 중인 핸들은 보존해 상태 조회와 후속 정리가 가능하다. OS의 프로세스 생성 도중 취소되면 핸들 확보를 마친 후 취소를 전파하므로 자식을 추적하지 못한 채 잃어버리지 않는다.

`release_runtime(runtime_id) -> None`은 D가 **해당 runtime에만** 속한 세션의 중단을 해제하고 detach한다. 실패하면 예외를 발생시켜야 한다. JDWP 대상은 이 callback이 없으면 시작도 거절한다. callback 실패·기한 만료 시 stop을 전송하지 않는다. A의 실제 Paper probe는 기존 DebuggerService.call을 사용해 알려진 runtime/session 매핑 하나를 해제한다. 공용 앱 전체의 매핑은 D가 연결한다.

C의 stop→교체→start는 하나의 배타 작업 토큰을 재사용한다.

```python
async with runtime.exclusive(runtime_id) as operation:
    stopped = await runtime.stop(runtime_id, operation=operation)
    assert stopped["exit_confirmed"]
    # 여기서 C가 검증된 JAR를 교체하고 설치 journal을 완료한다.
    started = await runtime.start(runtime_id, operation=operation)
```

토큰은 해당 서비스·runtime·asyncio task에서 문맥이 열린 동안에만 유효하다. 토큰 없이 같은 잠금을 재획득하면 operation_busy로 즉시 거절한다. 기존 `.craftmcp/operation.lock`과 `deploy.lock`을 사용해 C의 배타 작업과 조정하며, 상태 조회는 이 잠금에 묶지 않는다. 기존 deployment.py는 자동 재시작 paper_runner를 전제로 하므로 새 관리 대상에는 위의 명시적 stop/start 흐름을 사용해야 한다.

수명 오류에는 runtime_unmanaged, runtime_not_owned, ownership_unknown, invalid_launch, operation_busy, invalid_operation, installation_incomplete, start_failed, start_timeout, stop_timeout, debugger_release_unavailable, debugger_release_failed, release_timeout, service_closed가 있다. 내부 API는 ContractError를 발생시킨다. `start_timeout`과 `stop_timeout`은 실행 여부·완료 불명이며 자동 재실행 조건이 아니다.

## B의 JobManager와 공용 앱 연결 지점

main의 `0e9f89f` JobManager는 `submit(command_id=..., arguments=...)`와 실행 콜백을 받는다. 일반 operation 콜백 API는 아직 없으므로 A는 기존 CommandSpec/Job 모델과 단일 manager를 재사용하는 router를 제공한다. B 소유 파일은 수정하지 않았다.

```python
from mcp_server.services.jobs import JobManager, default_commands
from mcp_server.services.runtime_jobs import RuntimeJobRouter, RuntimeJobs

command_specs = default_commands()
router = RuntimeJobRouter(runtime, execute_command)  # 기존 B의 async 실행 함수
job_manager = JobManager(router.execute, {**command_specs, **router.commands()})
runtime_jobs = RuntimeJobs(runtime, job_manager)
register_runtime_tools(mcp, runtime, runtime_jobs)  # 조회 2개 + start_server/stop_server
```

`start_server`/`stop_server` 입력은 runtime_id, 필수 request_id, 선택 run_id와 timeout_seconds다. 응답 result는 B의 job snapshot이며 즉시 반환한다. 같은 manager의 get_job/cancel_job을 사용한다. 최종 raw_output은 runtime_id, phase, boot_id, pid, game_ready와 stop의 exit_confirmed를 담은 JSON이다. 시작·종료를 수행하는 내부 서비스는 새 job을 등록하지 않는다. 중복 요청과 queue는 B의 manager가 처리한다.

router의 제어 메시지는 RCON에 전달하지 않는다. **일반 execute_command 도구는 기존 command_specs의 ID만 허용하고**, start_server/stop_server는 A 도구로만 제출한다. 제어 메시지에 넣는 runtime_id와 manager.submit의 runtime_id는 RuntimeJobs가 동일하게 고정한다. 이 경계를 우회해 두 ID가 다른 job을 직접 만들지 않는다.

B의 현재 manager는 lifecycle 완료 JSON을 일반 텍스트로 보존한다. 기존 sent/execution_status 필드는 lifecycle job에서 RCON 송신 상태를 뜻하지 않는다. 성공은 raw_output의 준비/종료 관찰과 함께 해석한다. 실행 기한 초과는 unknown으로 반환한다. queue 대기 시간의 총 기한·실행 중 cancel의 적극 정리·영속 journal은 B의 현재 구현 범위를 따르며 이 어댑터가 추가하지 않는다.

D의 lifespan 종료 순서는 다음과 같다. close 결과가 false면 아직 종료가 확인되지 않은 runtime을 사용자에게 보고한다.

```python
await job_manager.close()          # 실행 중 작업 취소·queue 정리
cleanup = await runtime.close()    # debugger가 살아 있는 동안 중단 해제·정상 종료 확인
await debugger_service.close()
```

기존 공용 server.py의 변경은 D가 취합한다. 이번 A 변경은 독립 등록 함수와 실제 메모리 MCP 시험까지 제공하며, 공용 앱을 자동으로 관리 서버에 연결하지 않는다.

## 이후 계약 확장

B 소유의 job, C 소유의 build/deployment, D 소유의 run/evidence는 해당 담당자의 예제·시험과 함께 취합한다. 이 초안에서 별도 모델을 미리 만들지 않는다. A의 독립 검증과 공용 연결의 완료 범위는 [작업 기록](runtime-work-plan-2026-11-10.md)에서 구분한다.
