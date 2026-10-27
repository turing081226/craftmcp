# Python·MCP 디버거 구현과 검증

수정일: 2026-10-13. 담당: 분업안의 B.

## pull된 A 문서에서 처리한 요구 사항

[Java 헬퍼 안내](debugger-java.md)의 실행·연동 요구와 [통신 계약 v1](debugger-protocol.md)을 확인했다. 공유 계약 필드와 A의 Java 소스는 변경하지 않았다.

| 요구 사항 | B의 처리 |
|---|---|
| 고정 JAR·실행 인자 | `java --add-modules jdk.jdi -jar <절대 헬퍼 JAR>`를 인자 배열로 실행 |
| ready, 버전, UTF-8, 요청 ID | 준비 이벤트 확인, 응답·이벤트 분리, 엄격한 v1 입력·출력 검증 |
| 8개 도구와 런타임 등록 | 등록된 runtime/project를 host/port로 변환; 임의 실행 명령 도구 없음 |
| 중단 lease와 참조 수명 | Java 상태를 조회하며 자동 재개 후 stale_reference를 그대로 전달 |
| debug_info_missing 대체 조회 | `argument_values`, `argument_next_offset`, `this_value` 보존; 실제 no-vars JVM에서 페이지와 객체 조회 검증 |
| pending, rejected, 로더 선택 | 상태와 오류를 전달하고 선택적 class_loader_id를 지원; 실제 미로딩 클래스 적용 검증 |
| EOF·shutdown·VM·헬퍼 종료 | 독립 수신과 미완료 요청 실패; 실제 JVM 정상 재개·연결 종료 검증 |
| 실제 MCP 조사와 증거 | 별도 stdio MCP 서버→A 헬퍼→단독 JVM 전체 흐름과 Paper 검증 드라이버 추가 |

## 구현 파일

| 경로 | 역할 |
|---|---|
| `mcp_server/adapters/debugger/models.py` | 계약 메시지와 method/params 검증, 크기·페이지 제한 |
| `mcp_server/adapters/debugger/client.py` | 헬퍼 프로세스, ready, 동시 요청, 독립 stdout/stderr, timeout·종료 |
| `mcp_server/services/debugger.py` | 런타임·프로젝트 등록, 소스 경로, 증거, 수명 관리 |
| `mcp_server/tools/debugger.py` | 공용 FastMCP 앱에도 등록 가능한 MCP 도구 8개 |
| `mcp_server/debugger_app.py` | 디버거 전용 stdio 진입점과 종료 훅 |
| `tests/debugger_python/test_debugger.py` | 모의 계약·프로세스·서비스 시험 |
| `tests/debugger_python/test_stdio.py` | 모의 백엔드의 실제 stdio MCP 호출 |
| `tests/debugger_python/test_java_helper.py` | 실제 Java 헬퍼·단독 JVM·stdio MCP 통합 시험 7개 |
| `tests/debugger_python/probe_real.py` | 기존 JDWP 서버에서 수동 콘솔 재현을 기다리는 MCP 검증 드라이버 |
| `tests/debugger_python/probe_paper.py` | 제공한 Paper JAR로 임시 서버를 만들고 콘솔·MCP를 함께 검증 |
| `plugin_template/debugger_sample/` | Java 17 Paper 콘솔 재현 플러그인 |
| `mc_server/start-debug.*` | JDWP 개발 실행; RCON 설정 유지 |

RCON 코드는 구현하지 않았다. 공용 앱이 없어서 디버거 전용 진입점을 사용한다. FastMCP 2.14.5와 전이 의존성은 `pyproject.toml`·`uv.lock`에 고정했다.

## 설치와 시험

저장소 루트의 PowerShell에서 실행한다.

```powershell
uv sync --locked --python python
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p "test_*.py" -v
```

Java 산출물이 없으면 실제 JDI 시험 7개는 skipped로 표시한다. 이를 실제 통합 성공으로 해석하지 않는다. A가 제공한 Wrapper로 산출물을 준비한다.

```powershell
java -cp debugger_helper/gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --gradle-user-home .cache/gradle -p debugger_helper --no-daemon jar compileDebuggee compileDebuggeeWithoutVars
.\.venv\Scripts\python.exe tests/debugger_python/test_java_helper.py -v
```

직접 실행하는 실제 시험 드라이버는 Java 산출물이 없으면 실패한다. A 문서 기준 Gradle 8.14.3, JDK 17을 사용한다. A의 Java 시험 16개와 B의 Python 통합 시험은 별도 결과다.

실제 JVM 시험은 A의 컴파일된 샘플을 임시 실행 JAR로 패키징하고 임시 JDWP 포트에서 시작한다. 외부 에이전트가 명령을 지정하는 기능이 아니라 시험 코드가 고정 fixture를 실행하는 것이다.

## 설정과 MCP 실행

```powershell
Copy-Item debugger.example.json debugger.local.json
.\.venv\Scripts\python.exe -m mcp_server.debugger_app --config debugger.local.json
```

- `helper_jar`: A가 빌드한 `debugger_helper/build/libs/craftmcp-debugger-helper.jar`.
- `java_executable`: JDI가 포함된 JDK 17의 java 또는 절대 실행 파일 경로.
- `projects.root`: workspace 안의 실제 소스 루트. 도구의 source_path는 이 루트 안으로 제한한다.
- `projects.artifact_jar`: 빌드 JAR 또는 실제 배포 JAR. 등록된 외부 경로도 읽기·해시 확인에 사용할 수 있다.
- `runtimes`: 프로젝트 ID, JDWP host/port, 실행 JAR. 실행 JAR는 등록된 외부 서버 경로도 허용하며 attach 전에 존재 여부를 확인한다.
- `boot_id`: 개발자가 갱신하는 부팅 식별값. 자동 시험은 직접 시작한 프로세스의 ID를 기록한다. 기존 서버에서 모르면 null이다.
- `source_revision`: 선택 사항. 미설정이면 Git HEAD를 읽으며 dirty 여부는 확인하지 않아 null이다.
- `server_version`: 설정한 설명. 드라이버가 로그에서 확인했다면 관찰 근거도 별도로 기록한다.

설정 상대 경로는 `--workspace` 기준이고 설정 파일 인자는 현재 디렉터리 기준이다. IDE가 다른 디렉터리에서 실행하면 절대 경로를 사용한다. `debugger.local.json`과 수집 데이터는 Git에서 제외한다.

모의 헬퍼는 다음처럼 명시적으로 선택한다. 모든 결과와 증거에 simulated=true를 남긴다.

```powershell
.\.venv\Scripts\python.exe -m mcp_server.debugger_app --config debugger.example.json --mock-helper
```

stdio 서버는 MCP 클라이언트가 실행하고 호출하는 프로세스다. 터미널에서 직접 실행하는 것만으로 도구 호출 시험이 되지는 않는다. 호스트 등록 예제:

```json
{
  "mcpServers": {
    "craftmcp-debugger": {
      "command": "C:/Users/yjkim/Desktop/craftmcp/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_server.debugger_app", "--config", "C:/Users/yjkim/Desktop/craftmcp/debugger.local.json", "--workspace", "C:/Users/yjkim/Desktop/craftmcp"]
    }
  }
}
```

## MCP 도구와 오류 처리

| 도구 | 입력 |
|---|---|
| attach_debugger | runtime_id, project_id |
| set_breakpoint | session_id, class_name, line, 선택 source_path·class_loader_id |
| remove_breakpoint | session_id, breakpoint_id |
| get_debug_state | session_id |
| get_stack_trace | session_id, stop_id, thread_id, offset=0, limit=20 |
| get_variables | session_id, stop_id, frame_id 또는 object_ref 중 하나, offset=0, limit=20 |
| resume_execution | session_id, stop_id |
| detach_debugger | session_id |

성공 결과는 `{ok:true,result:Java결과,evidence:{run_id,path,simulated}}`, 실패는 `{ok:false,error:{code,message,details},evidence:...}`다. MCP 입력 스키마 위반은 프레임워크 오류다. 통신 성공과 도구의 ok=true를 구분한다.

정수와 불리언을 구분하며 페이지 limit 상한은 100이다. 상대 source_path는 등록된 프로젝트 루트 기준이다. 실제 파일이어야 하고 심볼릭 링크를 해석한 경로도 프로젝트 안에 있어야 한다.

지역 변수 정보가 없는 경우 error.code는 debug_info_missing이다. details.argument_values를 읽고 argument_next_offset을 다음 get_variables의 offset으로 보낸다. this_value.object_ref로 객체 필드를 읽을 수 있다. 이 참조도 원래 stop_id에 속하며 재개 후 무효화된다.

Python은 세션·중단·프레임·객체 ID를 발급하지 않는다. get_debug_state는 항상 Java에 요청한다. 이벤트를 근거로 이전 상태를 다시 유효화하거나 에이전트를 자동 실행하지 않는다.

## timeout·종료와 증거

요청 timeout은 실행 여부가 불명확하다는 뜻이다. attach·resume 등을 자동 재전송하지 않는다. 늦은 응답은 late_response로 저장하고 이미 실패한 호출을 다시 성공으로 전달하지 않는다. 늦은 attach 성공은 late_session_bound에 Java 세션 ID를 기록하며 이후 상태 조회·detach가 가능하다.

stdout EOF·프로세스 종료는 미완료 요청을 backend_unavailable로 끝낸다. stdout의 로그 혼입·잘못된 UTF-8·JSON·버전·이벤트 순번은 연결을 거절한다. stderr는 별도 수신하고 UTF-8 문자가 청크 경계에서 나뉘어도 보존한다.

앱 종료 시 shutdown 응답을 기다린 뒤 stdin을 닫는다. 강제 종료가 필요하면 forced_termination과 실패를 기록한다. 실제 JVM 재개를 확인하지 않았는데 성공으로 보고하지 않는다. 증거 저장 실패가 발생해도 프로세스 정리를 진행한다. 비정상 종료한 헬퍼를 자동 재시작하지 않는다.

`data/debugger/<run_id>/evidence.jsonl`에는 UTC 시각, 요청·응답·이벤트, timeout·로그·종료를 기록한다. 세션별 프로젝트·런타임, 설정/관찰 버전, Git HEAD, JAR SHA-256, 중단·스택·변수와 source_path의 SHA-256을 남긴다. source_match는 unverified가 기본값이며 로컬 해시만으로 실행 소스 일치를 단정하지 않는다.

실제 시험은 validation-summary.json을 함께 남긴다. 별도 프로세스의 콘솔 로그는 fixture.jsonl 또는 paper-console.jsonl에 기록해 여러 프로세스가 같은 증거 파일에 동시에 쓰지 않게 한다.

## Paper 샘플과 기존 서버

```powershell
.\plugin_template\debugger_sample\gradlew.bat -p plugin_template/debugger_sample build
```

JAR는 `plugin_template/debugger_sample/build/libs/craftmcp-debugger-sample.jar`다. source/lines/vars 디버그 정보를 포함한다. 샘플의 클래스는 `io.github.craftmcp.debuggersample.DamageCalculator`, 계산 후 중단점은 7번 줄의 `return result;`다.

```text
craftdebug calculate manual-001 7 3
CRAFTDEBUG status=started run_id=manual-001 base=7 bonus=3
CRAFTDEBUG status=ok run_id=manual-001 damage=10
```

로컬 서버 콘솔만 허용하고 RCON·플레이어 입력을 거절한다. damage, result 객체, inputs 배열을 조사한다. 입력·계산값 확인이며 엔티티 체력이나 화면 검증은 아니다.

이미 JDWP가 켜진 서버에 수동으로 연결할 때:

```powershell
.\.venv\Scripts\python.exe tests/debugger_python/probe_real.py --config debugger.local.json
```

중단점 설정 안내 후 서버 콘솔에 `craftdebug calculate probe-001 7 3`을 입력한다. 조회·재개·오래된 참조 거절·중단점 제거·detach를 확인한다. 콘솔의 정상 완료는 수동 드라이버만으로 확인했다고 주장하지 않는다.

제공한 Paper 바이너리로 전체 흐름을 자동 검증할 때:

```powershell
.\.venv\Scripts\python.exe tests/debugger_python/probe_paper.py --server-dir C:/Users/yjkim/Desktop/mc_server
```

드라이버는 기존에 EULA를 수락한 서버의 동일 paper.jar와 캐시·라이브러리를 작업 폴더의 `.cache/paper-debugger/<run_id>`로 복사한다. 월드·사용자 데이터·기존 플러그인은 복사하지 않는다. 새 테스트 월드, RCON=false, localhost의 임시 게임/JDWP 포트, 테스트 플러그인을 사용한다.

중단점 없는 baseline → 실제 stdio MCP 조사 → 재개 후 콘솔 정상 완료 → 중단점 없는 regression → 서버 정상 종료를 검증한다. Paper 버전, 원본·검증 JAR 해시, 빌드·배포 플러그인 해시, 입력·기대값·관찰값과 정상 종료를 paper-validation-summary.json에 남긴다. 기존 서버에서의 검증과 구분한다.

사용자가 알려준 `/C/Users/yjkim/Desktop/mc_server`는 Windows의 `C:/Users/yjkim/Desktop/mc_server`로 해석했다. 25565에서 Paper 1.20.1 build 196이 실행 중이며 실행 인자에 JDWP가 없음을 확인했다. 25565는 게임 포트로 사용되고 있다. 기존 인스턴스에 디버거를 연결하려면 정상 종료 후 다음 JVM 옵션을 넣고 재시작해야 한다.

```text
-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:5005
```

기존 서버용 `debugger.local.json`을 작업 폴더에 준비했다. 해당 서버에는 샘플 플러그인을 배포하고 실제 JAR 해시를 확인해야 한다. start-debug 스크립트는 server.properties의 RCON 설정을 유지하며 JDWP와 함께 켤 수 있다. Windows 배치 파일은 실패·종료 메시지를 읽을 수 있도록 pause하며 자동화에서는 --no-pause를 사용한다. --check --no-pause로 실제 서버를 시작하지 않고 필수 파일과 Java를 확인할 수 있다.

## 공용 앱 연결과 남은 확인

공용 FastMCP 앱에서 register_debugger_tools(existing_mcp, debugger_service)를 호출하고, 공용 lifespan의 finally에서 await debugger_service.close()를 실행한다. 디버거 전용 create_app으로 공용 lifespan을 덮어쓰지 않는다. RCON 명령 응답을 기다리는 중에도 디버거 도구를 호출할 수 있도록 async 흐름을 유지한다.

실제 사용자 IDE/에이전트 호스트에서의 도구 발견·호출은 별도 확인이다. 이번 자동 시험의 MCP 호스트는 FastMCP 클라이언트다. Windows·JDK 17·Python 3.13에서 검증하며 다른 OS와 원격 JVM은 미검증이다.


## 최종 검증 결과 — 2026-10-13

- Python 자동 시험 27개 모두 통과: 모의·입력·서비스·MCP 시험 20개, 실제 Java 헬퍼·JVM 통합 시험 7개. skipped 없음.
- 실제 stdio MCP→JDI→단독 JVM에서 damage=12, 지역 변수·객체·배열·한글·페이지·참조 수명을 확인했다.
- 실제 JVM에서 lease 만료, stdin EOF, 정상 shutdown, 헬퍼 강제 종료 후 실행 복구와 VM 종료 감지를 확인했다.
- no-vars JVM에서 debug_info_missing의 인자 페이지 4/3과 this 객체의 필드 조회를 확인했다.
- 제공한 Paper 1.20.1 build 196의 동일 JAR를 사용한 임시 서버에서 baseline→MCP 조사→damage=10 정상 완료→regression→정상 종료를 확인했다. RCON=false.
- 원본/검증 Paper JAR SHA-256 일치, 빌드/배포 샘플 JAR SHA-256 일치, 계산 이후 7번 줄의 base=7·bonus=3·damage=10을 확인했다.
- Python 문법, Git diff 공백 검사, 한글 UTF-8 문서 무결성 검사 통과. A 담당 추적 파일 변경 없음.

주요 증거:

- [실제 Paper 검증 요약](../data/debugger/57ddd418e44d40d4afd610b23a7f9be2/paper-validation-summary.json)
- [Paper 중단 위치·조회 값](../data/debugger/57ddd418e44d40d4afd610b23a7f9be2/probe-summary.json)
- [실제 단독 JVM의 stdio MCP/JDI 증거](../data/debugger/22b0904a57bb470793025d7bebad0f63/validation-summary.json)

증거 데이터는 로컬 파일이며 Git에서는 제외된다. 재현하려면 위 명령으로 다시 생성한다.

기존 127.0.0.1:25565 서버는 검증 후에도 TCP 접속 가능함을 확인했다. 기존 인스턴스에는 JDWP를 추가하거나 샘플을 배포하지 않았다. 해당 인스턴스에서의 디버깅과 사용자 IDE 호스트 시험은 아직 미검증이다.
