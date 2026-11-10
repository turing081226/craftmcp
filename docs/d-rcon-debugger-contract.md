# D → B: RCON·디버거 통합 계약 v1

작성일: 2026-11-10 (KST). 통합 기준 main: `0e9f89f` (`B`).

이 문서는 [4인 분업안](team-work-split-2026-11-03.md)의 D1–D3 및 B4 연결을 위한 인수 자료다.
fixture 명령·출력·파서를 B의 실제 `JobManager`와 공용 MCP 앱에 연결했다.
아래 JSON은 현재 구현의 공개 형식이다. B의 기존 JSON 문자열 반환 형식을 유지하며 driver가
JSON을 디코딩한다. 공용 Runtime/Job 모델을 D가 별도로 만들지 않는다.
전체 A registry나 job journal/재시작 복구가 구현됐다는 뜻은 아니다.

## 1. 실행할 명령과 sender

```text
craftdebugrcon calculate rcon-demo-001 7 3
```

`craftdebugrcon`은 `RemoteConsoleCommandSender`만 허용한다. 플레이어·블록·로컬 콘솔 등
다른 sender는 `REMOTE_CONSOLE_REQUIRED`를 받고 계산하지 않는다. Bukkit 명령 권한은
sender 검사와 별개이며 이 명령은 고정 덧셈만 수행하고 게임 상태를 변경하지 않는다.

기존 `craftdebug calculate <run_id> <base> <bonus>`의 로컬 콘솔 전용 동작과
`CRAFTDEBUG` 출력은 유지한다. 기존 명령에 RCON을 보내면 `LOCAL_CONSOLE_REQUIRED`다.

| 입력 | 타입/제약 |
|---|---|
| `command_id` | 고정 문자열 `debugger.calculate.v1` |
| `arguments` | 정확히 `{"base": 7, "bonus": 3}`; 두 값 모두 signed int32; bool/float/string 불가 |
| `runtime_id` | 현재는 `MC_RUNTIME_ID`와 정확히 일치해야 한다. 아래 예제는 `paper-rcon-fixture` |
| `run_id` | 외부 driver 생성; ASCII `[A-Za-z0-9_-]{1,64}`. 명령 인자로도 전달 |
| `request_id` | 변경 요청 중복 방지 ID. 같은 ID/입력은 같은 job; 다른 입력은 거절 |
| `timeout_seconds` | 첫 통합 기준 30초. queue·접속·인증·중단·조회·재개·응답을 포함한 총 기한 |

명령 원문은 사용자 입력을 직접 실행하지 않고
`mcp_server.services.debugger_fixture.command_text(arguments, run_id)`로 만든다.
`runtime_id`/`request_id`/timeout은 명령 인자가 아니다. `7+3=10`은 v1 인수 시험의 고정값이며
fixture 자체는 int32 입력을 받아 `Math.addExact`로 계산한다.

## 2. B의 MCP 입력 및 job 조회 경계

```json
{
  "command_id": "debugger.calculate.v1",
  "arguments": {"base": 7, "bonus": 3},
  "runtime_id": "paper-rcon-fixture",
  "run_id": "rcon-demo-001",
  "request_id": "rcon-demo-001-debug",
  "timeout_seconds": 30
}
```

`execute_command`는 명령이 끝날 때까지 기다리지 않고 다음 내용을 담은 JSON 문자열을 반환한다.

```json
{"ok": true, "stage": "execute_command", "job_id": "job-example", "status": "queued"}
```

`get_job({"job_id":"job-example"})`은 재전송 없이 상태를 반환한다. driver가 사용하는 최소 모양:

```json
{
  "ok": true,
  "stage": "get_job",
  "job_id": "job-example",
  "runtime_id": "paper-rcon-fixture",
  "run_id": "rcon-demo-001",
  "request_id": "rcon-demo-001-debug",
  "command_id": "debugger.calculate.v1",
  "status": "running",
  "sent": "unknown",
  "execution_status": "unknown",
  "timeout": 30,
  "raw_output": "",
  "parsed_output": null,
  "errors": []
}
```

중단 중에는 `queued`/`running`; 정상 완료는 `succeeded`, 명령 오류는 `failed`, 판정 불가는
`unknown`, 전송 전 취소는 `cancelled`다. 현재 전송 상태는 `sent`와 동일 값의 `execution_status`로
반환하며 `not_sent`/`unknown`/`confirmed`를 사용한다. 어댑터 호출 중에는 정확한 전송 시점을
알 수 없어 unknown이다. `confirmed`도 게임 assertion 성공과는 다르다.
`deadline_at`은 created_at+timeout인 Unix 시각이다. queue에서 기한이 만료되면 failed/not_sent,
실행 중 기한 만료나 EOF는 unknown이며 자동 재전송하지 않는다.
페이지는 `get_job(job_id, page=0, page_size=4000)`으로 조회한다.
`output_has_more=true`면 다음 페이지를 읽는다. 이 fixture의 고정 응답은 한 페이지 안에 들어간다.

A registry 연결 전 공용 앱은 환경변수의 단일 RCON 대상을 사용한다.
`MC_RUNTIME_ID` 기본값은 `default`이고, 다른 runtime_id는 전송 전에 거절한다.
동일 Paper의 DEBUGGER_CONFIG runtime ID·RCON 포트·MC_SERVER_DIR를 함께 설정해야 한다.

## 3. 완료 표식 및 결과 parser

정상 응답의 **원문**은 아래 두 레코드다. 각 레코드는 LF로 끝나며 CRLF도 받는다.
RCON이 여러 `sendMessage`를 붙여 반환해도 구분되도록 플러그인이 LF를 직접 붙인다.

```text
CRAFTDEBUG_RCON v=1 status=started run_id=rcon-demo-001 base=7 bonus=3
CRAFTDEBUG_RCON v=1 status=ok run_id=rcon-demo-001 base=7 bonus=3 damage=10 end=1
```

완료 표식은 **상관 ID가 일치하는 `v=1 status=ok|error ... end=1`의 완전한 한 줄**이다.
`started`, 소켓 응답 수신, 부분 문자열 `damage=10`만으로 완료 처리하지 않는다.
Paper가 자체 개행도 붙이면 레코드 사이에 빈 줄이 생길 수 있으며 parser는 빈 줄을 무시한다.

```python
from mcp_server.services.debugger_fixture import COMMAND_ID, command_text, parse_output

text = command_text({"base": 7, "bonus": 3}, "rcon-demo-001")
parsed = parse_output(raw_output, run_id="rcon-demo-001", arguments={"base": 7, "bonus": 3})
```

정상 parser 결과 예시(`raw_output`은 실제 원문을 그대로 보존):

```json
{
  "schema_version": 1,
  "command_id": "debugger.calculate.v1",
  "run_id": "rcon-demo-001",
  "status": "succeeded",
  "command_success": true,
  "completion": "terminal",
  "started": true,
  "result": {"run_id": "rcon-demo-001", "base": 7, "bonus": 3, "damage": 10},
  "error": null,
  "raw_output": "두 레코드의 실제 원문과 마지막 LF"
}
```

오류 출력 예시:

```text
CRAFTDEBUG_RCON v=1 status=error run_id=rcon-demo-001 code=INTEGER_OVERFLOW end=1
```

- 오류 코드는 `REMOTE_CONSOLE_REQUIRED`, `INVALID_ARGS`, `INVALID_INTEGER`, `INTEGER_OVERFLOW`다.
- overflow는 `started` 뒤에 error가 온다. sender/인자 오류는 started 없이 error만 온다.
- 잘못된 run_id로 명령을 직접 보내면 error에 run_id가 없다. 이 응답은 정상 요청의 성공/실패로
  연결하지 않고 parser가 `unknown`으로 처리한다. B는 잘못된 입력을 전송 전에 거절한다.
- `status=ok`는 started가 먼저 있고 base/bonus/run_id가 일치하며 damage가 정확한 합일 때만 성공이다.
- 중복/상충 완료, 다른 run, 알 수 없는 필드/버전, 잘린 레코드, 계산값 불일치는 `unknown`,
  `command_success=null`, `completion=invalid`다. 빈 출력/started만 있으면 `completion=missing`이다.
- 알려진 correlated error는 `failed`, `command_success=false`, `completion=terminal`이다.
- parser는 명령이 실제로 몇 번 전송됐는지나 서버 게임 상태를 입증하지 않는다.

## 4. 중단점 및 조회할 값

| 항목 | 고정값 |
|---|---|
| project_id | `debugger-sample` |
| 프로젝트 root | `plugin_template/debugger_sample` |
| 클래스 | `io.github.craftmcp.debuggersample.DamageCalculator` |
| 소스 경로(프로젝트 root 기준) | `src/main/java/io/github/craftmcp/debuggersample/DamageCalculator.java` |
| 줄 | **7**, `return result; // BREAKPOINT...` |
| 메서드 | `calculate` |
| 지역 변수 | `runId=<요청 run_id>`, `base=7`, `bonus=3`, `damage=10`, `result=<object_ref>` |
| result 필드 | `damage=10`, `base=7`, `bonus=3`, `runId=<요청 run_id>`, `inputs=<object_ref>` |
| inputs 요소 | `[0]=7`, `[1]=3` |

소스 줄을 바꿀 때 `debugger_fixture.BREAKPOINT_LINE`, 이 문서, 시험을 함께 갱신한다.
빌드는 JDK 17 및 `-g:source,lines,vars`를 유지한다. driver는 frame 목록 첫 항목을 가정하지 않고
클래스+메서드+줄로 선택하며 필요하면 stack 페이지를 이어 읽는다.
thread_id/stop_id/frame_id/object_ref는 해당 응답에서 받은 값을 사용하고 하드코딩하지 않는다.

## 5. 전체 통합 시험 순서

실행 가능한 driver는 `tests/integration/debugger_flow.py`의 `DebuggerFlow`다.
실제 MCP client의 `call_tool` 결과 `.data`를 인자로 받아 다음을 수행한다.

1. 동일 Paper의 runtime/project를 선택하고 `attach_debugger`로 session_id를 받는다.
2. 위 클래스/소스/줄로 `set_breakpoint`; `pending`은 허용하되 `rejected`는 실패한다.
3. `execute_command`로 job_id를 받는다. 이 호출이 RCON 최종 응답까지 블록되면 실패다.
4. job이 대기하는 동안 `get_debug_state`로 active_stop을 찾는다. 예상 breakpoint_id인지 확인한다.
5. `get_job`이 아직 queued/running인지 확인한다. 디버거 조회가 명령 queue/잠금에 갇히면 실패다.
6. stop의 thread_id로 `get_stack_trace`; 위 frame에서 `get_variables`; result/inputs도 조회한다.
7. `resume_execution`; `get_job`을 조회해 같은 job의 성공과 최종 RCON 원문을 확인한다.
8. 이전 frame_id와 result object_ref 조회가 `stale_reference`인지 확인한다.
9. `remove_breakpoint` 후 `detach_debugger`; 새 request_id로 동일 입력을 다시 실행해 정상 완료한다.
10. 실패 경로에서도 detach를 시도하고 정리 실패는 증거에 남긴다. 명령을 자동 재시도하지 않는다.

lease는 **10초**, 명령 총 기한은 **30초**, 격리 Paper watchdog은 **60초**다.
폴링은 lease를 연장하지 않는다. 기본 RCON 5초를 그대로 사용하면 정상 디버깅도 timeout이 될 수
있으므로 B는 이 command의 30초 기한을 어댑터까지 전달해야 한다.

## 6. run/job/debugger/evidence 연결

- 외부 `run_id`를 명령 인자, execute request, job 결과, 통합 증거의 run_id에 보존한다.
- 같은 run의 디버깅·정상 재실행·복구 시험은 별도 request_id/job_id로 구분한다.
- 같은 request_id의 조회/재요청은 같은 job으로 연결하고 실행하지 않는다. 다른 입력은 거절한다.
  이 중복 방지는 같은 MCP 프로세스 안에서만 유지된다. journal이 없어 프로세스 재시작 뒤에는
  이전 job을 복구할 수 없으므로, 응답을 잃은 변경 명령을 새 프로세스에 자동 재제출하지 않는다.
- debugger 도구의 `evidence.run_id`는 기존 서비스 수명의 별도 ID다. 외부 run_id로 덮어쓰지 않는다.
  통합 증거의 `debugger_run_id`와 `debugger_evidence_path`로 매핑한다.
- `id_binding` 레코드는 runtime_id/project_id/run_id/request_id/job_id/session_id/stop_id 및
  debugger_run_id를 연결한다. 아직 관찰하지 않은 ID는 null이다.
- 모든 MCP 입력/응답은 `flow-evidence.jsonl`에 단조 증가 sequence와 UTC observed_at으로 기록한다.
  RCON 원문·파싱 결과는 job 결과와 함께 보존한다. 비밀번호는 기록하지 않는다.
- 부팅 ID/버전/JAR 해시 관찰은 Paper 시작 기록과 별도로 연결한다. build_id/deployment_id는 C가
  제공하지 않았으므로 unknown이다. 설정 경로/해시가 같다는 이유로 로드된 소스 일치를 단정하지 않는다.
- 모의 driver, 시험용 job, 실제 RCON/JDI, 실제 사용자 호스트, 실제 플레이어 검증을 구분한다.
  미검증 항목은 validation-summary의 verification에서 `unknown`이다.

## 7. 성공·실패·timeout·EOF와 복구

| 관찰 | 판정과 다음 동작 |
|---|---|
| 일치하는 최종 ok + 기대 변수 + 정상 재실행 | 해당 통합 범위 passed |
| 일치하는 최종 error | 명령 failed. 예: int32 합 overflow |
| terminal 누락/충돌·잘린 응답 | unknown. 자동 재전송하지 않음 |
| 총 기한 만료 | 전송 전 failed/not_sent, 실행 중 unknown. 디버거 stop을 해제하고 원문/오류 보존 |
| EOF/전송 오류 | 실행 여부 unknown. 소켓 정리; 같은 변경 명령 자동 재전송 금지 |
| 조회 assertion 실패 | 시험 failed; 명령 성공 여부와 별개. detach하고 실패 증거 보존 |
| 중단 중 detach | held stop을 해제하고 기존 RCON 응답이 완료되는지 확인 |
| lease 만료 | 자동 재개, 이전 stop/frame/object의 stale_reference, RCON 완료를 확인 |
| detach 후 새 attach | 새 session_id. 이전 session은 session_not_found로 거절 |
| MCP client 종료 | lifespan에서 debugger를 먼저 닫고 blocked RCON 작업을 정리 |
| 관리 서버 stop/deploy | A/C가 종료 전에 debugger 해제. 실제 A/C 연결 전에는 unknown |
| JVM 강제 종료 | vm_disconnected/terminated; job은 확정 응답이 없으면 unknown. 재시작 후 새 attach |

## 8. 실행 명령과 B 연결 방법

저장소 루트 PowerShell, JDK 17 및 루트 .venv 기준:

```powershell
.\plugin_template\debugger_sample\gradlew.bat --gradle-user-home .cache/gradle -p plugin_template/debugger_sample build
.\.venv\Scripts\python.exe -m unittest discover -s tests/integration -p 'test_*.py' -v
.\.venv\Scripts\python.exe tests/integration/probe_rcon_paper.py --server-dir mc_server --recoveries
```

`--java <JDK17의 java.exe>`로 실행 Java를 명시할 수 있다. helper JAR도 먼저 빌드해야 한다.
기존 Paper/eula/cache를 `.cache/rcon-debugger/<run_id>`로 복사하고 새 월드·임시 포트에서 실행한다.
기존 서버 프로세스·월드·플러그인은 수정하지 않는다. 원본 EULA 수락이 없으면 실행을 거절한다.
JDWP는 시험마다 빈 포트를 선택한 뒤 JVM에 그 번호를 명시해 detach 후에도 같은 주소로 재연결한다.
`address=127.0.0.1:0`은 detach 때 새 포트를 선택할 수 있어 이 재연결 시험에는 사용하지 않는다.
기본 실행은 **공용 `mcp_server.server`의 실제 B job 구현**을 사용한다.
초기 D 독립 검증에 사용했던 시험용 job 앱은 제거했다. journal/프로세스 재시작 복구는 별도 후속 범위다.

`--terminate-target`을 추가하면 마지막에 이 driver가 만든 격리 자식 Paper만 중단 상태에서
강제 종료해 `vm_disconnected` 및 RCON job unknown을 확인한다. 이 실행의 정상 종료 표식은
`shutdown=expected_target_termination`이다. 옵션이 없으면 Paper의 정상 stop을 확인한다.

기존 호출과의 호환성을 위해 다음 명시 플래그도 계속 받는다.

```powershell
.\.venv\Scripts\python.exe tests/integration/probe_rcon_paper.py --server-dir mc_server --production-jobs --recoveries
```

플래그 유무에 관계없이 실제 공용 앱을 실행하며 필요한 도구가 없으면 실패한다.
A의 registry가 제공되면 현재의 단일 MC_RUNTIME_ID 설정을 A 계약에 맞춰 연결한다.
C 자동 배포 완료를 기다릴 필요는 없다.

검증 결과와 미검증 범위는 [D 인수 검증 기록](d-rcon-debugger-validation.md)을 따른다.
