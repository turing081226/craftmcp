# CraftMCP 테스트 방법

작성일: 2026-10-27. 원격 main의 RCON 코드와 debugger 브랜치를 병합한 구성을 기준으로 한다.
모든 명령은 저장소 루트의 PowerShell에서 실행한다.

## 1. 준비

- Python 3.11~3.13, uv.
- 실제 JVM 시험과 Paper 실행에는 JDK 17.
- 최초 의존성 설치·Gradle 실행에는 네트워크가 필요하다.

```powershell
python --version
java -version
uv sync --locked --python python
```

루트 pyproject.toml·uv.lock에 디버거와 RCON 의존성을 함께 관리한다.
기존 RCON 전용 mcp_server/requirements.txt도 유지하지만 전체 개발 환경은 위 uv 명령으로 준비한다.

## 2. 서버 없이 실행하는 Python·MCP 시험

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p test_debugger.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p test_stdio.py -v
```

현재 기준 총 22개다. 정상 응답, 이벤트와 응답의 순서 변경, timeout·늦은 응답, UTF-8, 페이지·참조,
자동 재개, EOF·프로세스 종료, 설정·경로 검증과 MCP 도구 8개 호출을 확인한다.
백엔드는 시험 전용 모의 헬퍼이며 실제 JVM이나 Paper 성공으로 해석하지 않는다.

RCON 모듈 설치와 도구 등록도 서버 없이 확인할 수 있다.

```powershell
.\.venv\Scripts\python.exe -c "import asyncio; from mcp_server.server import mcp; print([tool.name for tool in asyncio.run(mcp.list_tools())])"
```

기대 결과는 list_players가 포함된 목록이다. 이 명령은 RCON 명령을 전송하지 않는다.

QA에서 발견한 RCON 장애·로그 이어 읽기 문제의 회귀 시험은 다음과 같다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_server_qa.py -v
```

임시 파일과 임시 루프백 TCP 포트만 사용한다. 인증·명령 무응답, EOF, 인증 거절,
총 통신 기한과 소켓 정리, 실제 공용 stdio MCP의 장애 후 복구·동시 호출,
로그 교체·재작성·잘림과 부분 UTF-8 기록을 검사한다. 실제 Paper 서버는 필요하지 않다.

추가 QA의 경로·빌드·배포·동시 호출·재시작 시험까지 함께 실행하려면 다음을 사용한다.

```powershell
.\.venv\Scripts\python.exe eval/qa_additional.py
```

현재 28개 회귀 시험이며 통과 시 종료 코드 0이다. 임시 실행기와 자체 프로세스의 실제 종료·재시작도 포함한다.

## 3. 실제 Java 헬퍼·JVM·MCP 통합 시험

먼저 Java 헬퍼와 디버그 정보 유무가 다른 샘플 두 개를 빌드한다.

```powershell
java -cp debugger_helper/gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --gradle-user-home .cache/gradle -p debugger_helper --no-daemon jar compileDebuggee compileDebuggeeWithoutVars
```

직접 실제 통합 시험을 실행한다.

```powershell
.\.venv\Scripts\python.exe tests/debugger_python/test_java_helper.py -v
```

현재 기준 7개 시험이다. 별도 Java 프로세스와 임시 JDWP 포트를 자동으로 생성한다.
개발자가 실행 중인 Minecraft 서버와 RCON은 필요하지 않다.

- 실제 stdio MCP 서버→Java 헬퍼→JDI→JVM에서 8개 도구 흐름.
- 계산 결과 damage=12, 인자·지역 변수·객체·배열·한글과 큰 UTF-8 페이지.
- 중단 기한 만료와 재개 후 stale_reference.
- 지역 변수 정보가 없는 경우 debug_info_missing의 대체 인자 페이지·this 객체.
- stdin EOF, 정상 shutdown, 헬퍼 강제 종료 뒤 JVM 정상 실행 복구.
- 대상 JVM 종료 감지와 헬퍼 생존.

Java 산출물이 없으면 이 직접 실행 명령은 실패한다. 아래 전체 discovery에서는 7개가 skipped로 표시될 수 있으므로 결과의 skipped 수를 반드시 확인한다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p "test_*.py" -v
```

현재 디버거 전체 기대 결과는 **29개 통과, skipped=0**이다. 별도의 추가 QA 회귀 시험 28개도 함께 실행한다.
테스트 수는 향후 코드 변경 시 늘어날 수 있다.
실행 증거는 data/debugger/<run_id>/evidence.jsonl과 validation-summary.json에 생성한다.

## 4. Java 헬퍼 자체 시험

A가 작성한 전송·실제 JDI 시험을 별도로 실행하려면 다음을 사용한다.

```powershell
java -cp debugger_helper/gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --gradle-user-home .cache/gradle -p debugger_helper --no-daemon test
```

시험 보고서는 debugger_helper/build/reports/tests/test/index.html에 생성된다.
상세 범위와 샘플 수동 입력은 [Java 헬퍼 안내](debugger-java.md)를 따른다.
이 결과는 Python·MCP 디버거 통합 시험 29개와 별도로 센다.

## 5. 실제 Paper에서 RCON 없이 전체 흐름 검증

샘플 플러그인을 빌드한다.

```powershell
.\plugin_template\debugger_sample\gradlew.bat --gradle-user-home .cache/gradle -p plugin_template/debugger_sample build
```

이 절의 Paper 기준은 기존 프로젝트 결정인 Paper 1.20.1 build 196, JDK 17이다.
기존에 EULA를 수락한 Paper 서버 폴더를 지정한다. 예제 경로는 실제 폴더로 바꾼다.

```powershell
.\.venv\Scripts\python.exe tests/debugger_python/probe_paper.py --server-dir "C:\minecraft\paper"
```

드라이버는 지정한 paper.jar·기존 EULA·캐시·라이브러리를 .cache/paper-debugger/<run_id>로 복사한다.
새 테스트 월드, 샘플 플러그인, RCON=false, 임시 게임/JDWP 포트로 검증 서버를 실행한다.
원본 서버의 월드·플러그인·설정을 수정하거나 실행 중인 원본 프로세스를 종료하지 않는다.

검증 순서:

1. 중단점 없이 craftdebug calculate baseline-001 7 3을 실행해 damage=10을 확인한다.
2. 실제 stdio MCP 클라이언트가 JVM에 연결하고 DamageCalculator의 7번 줄에 중단점을 설정한다.
3. 콘솔 명령을 실행하고 base=7, bonus=3, damage=10, result 객체, inputs 배열을 조회한다.
4. 재개 후 콘솔 명령의 정상 완료를 확인한다.
5. 이전 프레임·객체 참조가 거절되는지 확인하고 중단점을 제거·detach한다.
6. 중단점 없는 regression 명령을 재실행하고 검증 서버를 정상 종료한다.

합격 조건은 probe의 종료 코드 0, paper-validation-summary.json의 status=passed와 shutdown=normal이다.
원본/검증 Paper JAR 해시, 빌드/배포 플러그인 해시, 관찰 버전과 명령 결과를 함께 확인한다.
이 시험은 지정한 바이너리의 별도 검증 서버 결과다. 기존 실행 인스턴스나 실제 플레이어 화면 검증은 아니다.

## 6. 기존 서버에 수동으로 디버거 연결

게임 포트 25565와 디버거 포트 5005는 서로 다르다. JVM 시작 시 -jar 앞에 다음 옵션이 필요하다.

```text
-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:5005
```

저장소 mc_server/start-debug.bat은 **그 스크립트가 있는 폴더**의 paper.jar와 server.properties를 사용한다.
RCON은 server.properties 설정을 그대로 사용하며 JDWP와 함께 켤 수 있다. 서버 파일과 EULA를 준비하고 기존 동일 포트 서버를 정상 종료한 뒤 사용한다.

Windows 배치 파일은 오류와 서버 프로세스 종료 후 pause로 창을 유지한다.
파일 이름은 start-debug.bat이다. 다른 서버 폴더로 복사한 경우 수정한 최신 파일로 교체한다.
서버를 실행하지 않고 필수 파일과 Java를 확인하려면 서버 폴더에서 다음을 실행한다.

```powershell
.\start-debug.bat --check --no-pause
```

자동화에서는 --no-pause로 대기를 끌 수 있다. --suspend는 JVM 시작부터 디버거 연결을 기다린다.

샘플 JAR를 대상 서버 plugins/에 설치하고 서버를 시작한 다음 로컬 설정을 만든다.

```powershell
Copy-Item debugger.example.json debugger.local.json
```

debugger.local.json의 runtime JAR, JDWP 주소, 프로젝트·배포 JAR 경로를 실제 환경에 맞게 수정한다.
그다음 수동 콘솔 입력을 기다리는 드라이버를 실행한다.

```powershell
.\.venv\Scripts\python.exe tests/debugger_python/probe_real.py --config debugger.local.json
```

안내가 나오면 대상 서버 콘솔에 입력한다.

```text
craftdebug calculate probe-001 7 3
```

스택·변수·필드·배열 조회, 재개, 오래된 참조 거절, 중단점 삭제와 detach를 확인한다.
수동 드라이버의 probe-summary.json에서 콘솔 완료는 별도 확인 항목이다.
서버 콘솔의 CRAFTDEBUG status=ok run_id=probe-001 damage=10과 서버 계속 실행을 직접 대조한다.

## 7. RCON 시험

이 절은 RCON이 켜진 시험 서버에서 진행한다. start-debug.bat도 enable-rcon=true 설정을 허용하므로 JDWP와 RCON을 함께 사용할 수 있다. 자동 Paper 검증 드라이버는 독립 시험을 위해 RCON=false를 사용한다.
현재 RCON 코드는 localhost:25575로 접속한다. 실제 server.properties에서 enable-rcon=true와 비어 있지 않은 비밀번호를 설정해야 한다.
다른 로컬 포트는 MC_RCON_PORT로 지정한다. MC_RCON_TIMEOUT은 접속·인증·명령 응답 전체의
기한이며 기본 5초, 최대 300초다. 기한 초과나 EOF는 MCP 오류로 반환하고 소켓을 닫는다.
명령 전송 후 오류가 나면 실행 여부는 불명일 수 있으며 자동 재전송하지 않는다.

MCP 서버의 비밀번호는 로컬 .env 또는 환경변수로 제공한다.

```powershell
Copy-Item mcp_server/.env.example mcp_server/.env
```

mcp_server/.env의 MC_RCON_PASSWORD를 실제 값으로 수정한다. 값을 Git에 추가하지 않는다.
직접 시험 스크립트는 .env를 자동 로딩하지 않으므로 환경변수를 별도로 설정한다.

```powershell
$env:MC_RCON_PASSWORD = Read-Host '시험 서버 RCON 비밀번호'
.\.venv\Scripts\python.exe -m mcp_server.rcon_test
```

기대 결과는 실제 서버의 list 명령 응답이다. 인증 오류·연결 실패와 정상 응답을 구분한다.
list 성공만으로 플러그인 계산·게임 상태 검증까지 통과했다고 보지 않는다.

공용 MCP 진입점은 다음과 같다. RCON·로그·빌드·배포·디버거 도구를 함께 등록한다.

```powershell
.\.venv\Scripts\python.exe -m mcp_server.server
```

디버거 설정은 DEBUGGER_CONFIG로 지정한다. 생략하면 저장소의 debugger.example.json을 사용한다.
디버거만 필요한 경우의 별도 진입점도 유지한다.

```powershell
.\.venv\Scripts\python.exe -m mcp_server.debugger_app --config debugger.local.json
```

공용 앱의 list_players는 소켓 작업을 별도 스레드에서 실행하므로 RCON 응답을 기다리는 동안
디버거 도구를 호출할 수 있다. 실제 Paper에서 RCON 샘플 명령 → 중단 → 재개 → 최종 RCON 응답의
전체 흐름은 별도 통합 시험이 필요하다.

### 로그 이어 읽기

처음에는 get_server_log(max_lines=100)을 호출하고, 이후에는 응답의 next_cursor 문자열을
get_server_log(cursor=이전_next_cursor, max_lines=100)에 그대로 전달한다.
기존 next_offset도 반환하며 cursor와 일치하는 start_offset을 함께 전달할 수 있다.
양수 start_offset만 전달하면 ok=false, reset_required=true, next_offset=0으로 거절한다.
파일 식별 정보가 없는 숫자 offset만으로는 로그 교체를 판별할 수 없기 때문이다.

파일 교체·잘림·이미 읽은 내용의 변경을 감지하면 reset=true와 reset_reason을 반환하고
새 로그의 처음부터 읽는다. 소비한 바이트 전체의 SHA-256을 다시 계산하므로
검증 비용은 이전 offset까지의 파일 크기에 비례한다. 파일이 변경되는 순간의 원자적 스냅샷은 보장하지 않는다.
개행이 없는 마지막 줄은 반환하거나 offset을 전진시키지 않고, 개행이 추가된 다음 호출에서 반환한다.
따라서 나누어 기록된 UTF-8 문자가 이어 읽기에서 손실되지 않는다.

### 자동 배포와 재시작 시험

자동 배포 대상은 실행기로 시작한다. 기존 서버와 자동 재시작 배치 창을 먼저 정상 종료한 뒤 실행한다.

```powershell
.\.venv\Scripts\python.exe -m mcp_server.paper_runner --server-dir "C:\minecraft\paper" --debug
```

실행기는 서버 종료와 재시작을 관찰하고 설치 중 재시작을 보류한다. 기존 start-debug.bat은
수동 디버깅에 사용할 수 있으며, 그 서버에 자동 배포를 요청하면 실행기 필요 오류로 거절한다.
MCP 설정의 서버 폴더·RCON 포트와 비밀번호·디버거 주소를 실행 중인 동일 서버로 맞춘다.

실제 Paper를 별도 임시 서버에 복사해 공용 MCP의 조회·디버거 연결·배포·새 JVM·재접속을 검증하려면 다음을 사용한다.

```powershell
.\.venv\Scripts\python.exe tests/probe_managed_paper.py --server-dir "C:\minecraft\paper"
```

기존 EULA 동의가 필요하다. 원본 서버를 변경하지 않고 `.cache/paper-managed/<id>`에 증거를 남긴다.
실패 상태와 설치 복구 방법은 [추가 QA 수정 안내](qa-remediation-2026-11-03.md)를 따른다.

## 8. Git에 포함할 파일과 로컬에 둘 파일

커밋 대상: 소스·테스트·문서, pyproject.toml, uv.lock, requirements.txt, .env.example, debugger.example.json, Gradle Wrapper.

로컬 전용: .env와 변형 파일, debugger.local*.json, data/debugger/의 증거, .cache/의 서버·캐시,
가상환경·egg-info·빌드 산출물·Gradle 캐시, 개인 경로가 들어간 docs/debugger-python-validation.json.
실제 파일은 보존하며 .gitignore로 제외한다. 이미 추적되던 로컬 검증 JSON은 Git 추적에서만 제거했다.

```powershell
git check-ignore -v debugger.local.json data/debugger/example/evidence.jsonl mcp_server/.env .cache/paper-debugger/example/paper.jar docs/debugger-python-validation.json
git status --short
```

.env.example와 debugger.example.json은 공유 예제이므로 추적한다. uv.lock과 Gradle Wrapper도 재현에 필요해 추적한다.
증거 파일이 다른 개발자에게 없을 때는 문서의 절차로 새로 생성한다.

## 9. 실패 시 확인

| 증상 | 확인할 내용 |
|---|---|
| Python 모듈을 찾을 수 없음 | 저장소 루트인지, uv sync --locked를 실행했는지, .venv Python을 사용하는지 |
| 실제 JDI 시험이 skipped | Java 헬퍼 JAR와 debuggee/no-vars 두 빌드 산출물이 모두 있는지 |
| ready_timeout 또는 backend_unavailable | 헬퍼 JAR·JDK 17·jdk.jdi, evidence.jsonl의 stderr·종료 코드 |
| attach_failed | 게임 포트 대신 JDWP 포트를 지정했는지, JVM 시작 옵션이 있는지 |
| debug_info_missing | 디버그 정보 포함 빌드인지; details의 대체 인자·this 조회 사용 |
| stale_reference | 이미 재개·lease 만료된 stop인지; 새 중단에서 참조를 다시 조회 |
| Paper 시작 실패 | Paper/JDK 버전, EULA, 파일 접근 권한, paper-console.jsonl |
| Gradle/uv 다운로드 실패 | 네트워크 접근·프록시와 캐시; 캐시가 완전할 때만 offline 옵션 사용 |
| RCON 실패 | RCON 활성화·25575 리스닝·MC_RCON_PASSWORD와 서버 설정 일치 여부 |

실제 사용자 IDE/에이전트 호스트에서의 도구 발견·호출은 자동 MCP 클라이언트 시험과 별도 확인한다.
