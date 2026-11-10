# A 담당 작업 계획과 구현·검증 기록

기준: [4인 분업안](team-work-split-2026-11-03.md)의 A 역할과 공통 계약·파일 소유권.  
착수 기준 HEAD: `fb69741` (`qa 실행 및 수정`). 작업 브랜치: `codex/runtime-lifecycle`.  
1차 작업: A1 계약 초안과 A2 독립 P0 구현.  
후속 작업: main의 B 커밋 `0e9f89f`를 fast-forward로 반영하고 **A3~A6의 독립 수명 관리·job 연결·실제 Paper 검증**을 진행했다. 팀 합의와 D의 공용 앱 연결은 별도 완료 조건이다.

## 진행 순서

| 단계 | 결과물·검증 조건 | 현재 상태 |
|---|---|---|
| A1 | runtime·공통 결과 계약, 오류·반환 예시를 B/C/D에게 제공하고 검토 | [계약 초안](tool-contracts.md)과 공통 데이터 모델 작성. 팀 합의 대기 |
| A2 | registry·resolve, `list_runtimes`, `get_runtime_status`, 기존 debugger 설정 변환 | 독립 구현·시험 완료. D의 공용 등록과 실제 관찰 서비스 연결 대기 |
| A3 | 임시 프로세스로 정상 시작·종료, 중복 요청, 기한 만료, 프로세스 오인 방지 | 구현·시험 완료 |
| A4 | JDWP 관리 시작, 동일 프로세스 종료 확인, 외부 서버 변경 거절 | 구현·시험 완료. 격리 Paper에서도 부팅·정상 종료·재시작 확인 |
| A5 | B의 job에 바깥 도구만 등록하고 C에 내부 수명·배타 작업 서비스 제공 | 기존 B JobManager를 수정 없이 연결하는 router·MCP 등록 함수와 시험 완료. D의 공용 앱 연결, C의 배포 적용 대기 |
| A6 | D의 `release_runtime` 연결, 중단 상태 종료, 종료·취소 시 정리 | callback 계약·모의 실패 시험 완료. 실제 DebuggerService로 Paper 중단 해제 후 정상 종료 확인. D의 공용 runtime/session 매핑 적용 대기 |

## 이번 변경의 경계

- A 소유의 `models/contracts.py`, `services/runtime.py`, `services/runtime_lifecycle.py`, `services/runtime_jobs.py`, `tools/runtime.py`, `tests/runtime/`와 기능 문서를 추가한다.
- 기존 debugger 모델은 D의 호환 영역으로 유지하고, A의 `RuntimeTarget`으로 변환하는 어댑터를 제공한다. B/C는 새 Runtime 모델을 따로 정의하지 않고 `runtime.resolve(id)`를 사용한다.
- `mcp_server/server.py`, 기존 배포·디버거 구현, B/C/D 시험 파일은 수정하지 않는다. `register_runtime_tools(mcp, service)`를 D의 연결 지점으로 제공한다.
- 등록 정보 조회는 네트워크에 접속하지 않는다. 상태 조회도 RCON 명령이나 JDWP handshake를 수행하지 않는다. B/D가 제공할 관찰 함수는 비동기·기한 제한 방식으로 주입한다.
- 수명 관리 없이 생성한 서비스는 `capabilities.start/stop=false`다. managed 대상과 LaunchSpec·RuntimeLifecycle을 명시적으로 연결해야 제어한다. 기존 원본 Paper 서버는 조작하지 않았으며, 실제 실행은 `.cache/`의 별도 복사본에서만 수행했다.

## P1 수명 관리 구현 방식

기존 `paper_runner`는 자식 JVM이 종료되면 자동 재시작한다. 기존 동작은 유지하고, 새 RuntimeLifecycle은 같은 `.craftmcp` 잠금·상태 파일 형식을 재사용하면서 직접 소유한 자식을 명시적으로 시작·종료한다. 자동 재시작이 없으므로 정상 종료가 유지된다. 기존 runner의 프로세스를 채택하거나 RCON stop으로 제어하지 않는다.

1. 명시적으로 관리 대상으로 등록하고 소유권을 확보한 프로세스만 제어한다. 기존 수동 서버와 이전 MCP가 남긴 PID를 자동 채택하지 않는다.
2. 기존 `.craftmcp/operation.lock`과 배포 재시작 게이트를 재사용한다. C가 하나의 배타 작업 안에서 stop→교체→start를 호출할 수 있게 토큰 또는 문맥을 전달하며 중첩 잠금을 방지한다.
3. D의 해당 runtime 중단 해제→소유한 stdin에 stop 한 번 전달→같은 부팅·프로세스 종료 관찰 순서로 진행한다. EOF·포트 닫힘·기한 만료만으로 성공을 확정하지 않는다. 자동 재전송과 강제 종료를 하지 않는다.
4. 시작 시 명령·서버 경로·JDWP 주소를 검증하고 프로세스별 식별자를 보관한다. 실행·포트 열림·Paper 준비 완료를 각각 관찰한다.
5. B가 request_id 중복 방지와 job 총 기한을 제공한다. A 내부 수명 서비스는 같은 queue에 추가 job을 넣고 기다리지 않는다.
6. 임시 프로세스로 이미 시작/종료됨, 시작 직후 종료, 정지 무응답, 기한 만료, 배타 작업, 중첩 호출, 취소·서비스 종료를 시험하고 격리 Paper로 확대했다. C의 실제 배포 job 적용과 D의 공용 앱 lifespan은 담당자 통합 시험에서 추가 확인한다.

## B/C/D 전달 사항

| 담당 | 지금 사용할 것 | 다음에 함께 확인할 것 |
|---|---|---|
| B | `resolve(id).rcon`, `.server_dir`, `.project_id`; RuntimeJobRouter가 기존 JobManager를 재사용 | runtime별 RCON 설정, 관찰 캐시, 일반 operation API·queue 총 기한·영속 journal |
| C | `exclusive(id)` 토큰을 stop/start에 전달, exit_confirmed 이후 교체 | 기존 자동 재시작 배포 경로에서 새 수명 서비스로 적용·시험 |
| D | `from_debugger_config`, `register_runtime_tools(mcp, runtime, jobs)`; `jdwp`/`suspension` 관찰 주입 | 단일 JobManager·일반 명령 allowlist, runtime별 해제, jobs→runtime→debugger 순서의 close |

`DebuggerConfig.load` 결과를 변환하므로 기존 runtime/project ID를 유지한다. RCON은 ID별로 명시적으로 연결하며 전역 환경변수를 모든 runtime에 자동 적용하지 않는다. 설정 파일의 고정 boot_id는 실제 관찰값으로 사용하지 않는다.

## 1차 P0 검증 기록

저장소 루트 PowerShell에서 실행한다. Python 의존성은 기존 `uv.lock` 기준으로 준비하며 lock 파일을 갱신하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/runtime -p "test_*.py" -v
.\.venv\Scripts\python.exe eval/qa_additional.py
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p test_debugger.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p test_stdio.py -v
```

서비스 시험은 임시 파일, 주입한 관찰 함수, 테스트가 생성한 짧은 Python 프로세스와 기존 runner를 사용한다. MCP 시험은 실제 FastMCP 메모리 전송으로 도구 발견·호출·입력 검증을 확인한다. 이 결과를 실제 Paper, JDI, 공용 앱 신규 도구 등록, 실제 IDE 호스트 성공으로 해석하지 않는다.

2026-11-10 실행 환경: Windows, Python 3.13.2, 기존 `uv.lock`으로 동기화한 `.venv`, FastMCP 2.14.5. `pyproject.toml`과 `uv.lock`은 변경하지 않았다.

| 시험 | 결과 | 해석 |
|---|---|---|
| `tests/runtime/test_*.py` | **17/17 통과** | 서비스 15개와 실제 FastMCP 메모리 전송·기존 debugger 설정 변환 2개 |
| `eval/qa_additional.py` | **27/28 통과, 오류 1개** | 순차 재실행에서도 같은 공용 stdio 초기화 오류 재현 |
| `test_debugger.py` 전체 | **20/21 통과, 실패 1개** | 동시 실행 시 `test_out_of_order_and_cancellation`의 late_response 확인 실패 |
| 위 debugger 실패 항목 단독 실행 | **1/1 통과** | 시간·실행 부하 의존 가능성이 있으나 원인 확정은 하지 않음 |
| `test_stdio.py` | **1/1 통과** | 기존 debugger 전용 앱·모의 헬퍼의 stdio 8개 도구 흐름 |
| `git diff --check` | 통과 | 기존 추적 파일의 공백 오류 없음 |

### 기존 회귀 시험 인계: 공용 stdio 초기화

`tests/test_server_qa.py`의 `test_common_stdio_app_recovers_and_stays_responsive_during_rcon_wait`는 `Client(..., timeout=10, init_timeout=30)`의 초기 MCP initialize 응답 대기 중 `McpError: Timed out ... Waited 10.0 seconds.`로 실패했다. 실제 RCON 명령 시험에 진입하기 전 오류다. 전체 QA 순차 재실행에서도 재현했으며 단순히 모든 시험 통과로 보고하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_server_qa.py -v
.\.venv\Scripts\python.exe tests/debugger_python/test_debugger.py TransportTests.test_out_of_order_and_cancellation -v
```

공용 앱은 이번 신규 runtime 모듈을 아직 import/등록하지 않는다. 1차 시점에는 해당 앱·시험·의존성 잠금 파일을 변경하지 않았으며 위 오류를 B/D 검토 사항으로 남겼다. 기한 값을 늘려 시험을 통과시키는 변경은 하지 않았다. 아래 후속 검증에서는 main의 B 코드를 반영한 상태로 28/28 통과했으므로 당시 오류의 원인을 확정하거나 코드로 수정했다고 주장하지 않는다.

## 후속 수명 관리·B 연결 검증

main `0e9f89f`의 B 코드를 현재 A 브랜치에 fast-forward로 반영했다. 이 과정에서 기존 `server.properties.example`과 분업 문서 수정의 SHA-256이 동일한 것을 확인했다. B의 JobManager·시험과 D의 공용 server.py에 A 변경을 추가하지 않았다. pyproject.toml·uv.lock도 유지했다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/runtime -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_jobs.py -v
.\.venv\Scripts\python.exe eval/qa_additional.py
.\.venv\Scripts\python.exe tests/runtime/probe_paper_lifecycle.py --server-dir mc_server
```

| 시험 | 결과 | 범위 |
|---|---|---|
| A runtime 전체 | **37/37 통과** | P0 17개 + 수명 14개 + 실제 B JobManager·메모리 MCP 연결 6개 |
| B의 기존 test_jobs.py | **5/5 통과** | B 소유 파일 변경 없이 확인 |
| 기존 QA 회귀 eval/qa_additional.py | **28/28 통과, skipped=0** | 기존 runner·배포·RCON·로그·공용 stdio. 이전 초기화 기한 초과는 이번 순차 실행에서 재현되지 않음 |
| 실제 격리 Paper·JDI probe | **통과, shutdown=normal** | 실제 Paper 중단 해제 후 종료, 새 부팅으로 재시작, 두 번째 정상 종료. 두 JVM 모두 exit_code=0 |

Paper는 실행 로그에서 `git-Paper-196 (MC: 1.20.1)`을 관찰했다. 원본과 복사본 Paper JAR의 SHA-256은 `67f31f691afd52a0c4ce33827fd635d0d0e86d46dec0ef6f89e1d58cd27832cf`로 일치했고 종료 후 원본 JAR도 동일했다. 캐시·라이브러리·버전과 이미 동의한 EULA만 복사하고, 원본 월드·플러그인·서버 설정은 사용하지 않았다. D의 기존 샘플을 로컬 JDK와 캐시 API로 복사본 안에서 컴파일했다. 소스·허용 sender·중단점 줄은 바꾸지 않았다.

실제 시험 순서:

1. B JobManager로 start job 제출 → 현재 부팅 로그의 Paper 준비 완료 확인.
2. 같은 request_id를 재제출해 동일 job_id 반환 확인.
3. 기존 DebuggerService·실제 Java 헬퍼로 같은 JDWP 대상에 연결, DamageCalculator 7번 줄에 중단점 설정.
4. 소유한 서버 콘솔에 샘플 명령 전송 → 실제 event_thread 중단 관찰.
5. stop job → 해당 runtime/session의 detach callback → stop 한 번 전송 → 같은 프로세스의 exit_code=0 확인.
6. 다시 start job → 새로운 boot_id 확인 → 중단 없는 stop job → exit_code=0 및 정상 정리 확인.

로컬 증거는 `.cache/rl-a3f38e30/lifecycle-summary.json`과 최종 시험 월드 설정으로 재실행한 `.cache/rl-6443ad9e/lifecycle-summary.json`, 부팅별 콘솔 로그와 debugger evidence에 남았다. 두 실행 모두 status=passed, shutdown=normal이며 강제 종료를 사용하지 않았다. 이 디렉터리는 Git 제외 대상이며 다른 환경에서는 위 명령으로 새로 생성한다. 기존 자동 재시작 runner, 실제 플레이어·GUI, 공용 MCP 앱 신규 도구 등록, 실제 IDE 호스트의 동작까지 검증한 결과는 아니다.

팀 검토·통합 잔여 작업: D가 문서의 단일 job manager·일반 명령 allowlist·runtime별 중단 해제·종료 순서를 공용 앱에 연결한다. C는 배포에서 토큰 기반 stop→교체→start를 사용한다. 그 후 공용 앱·실제 호스트의 v0/v1 시험을 수행한다. A1의 전원 합의와 이 통합 항목은 독립 시험만으로 완료 처리하지 않는다.
