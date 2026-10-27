# RCON·JVM 디버거 통합 점검 및 수정 요청

작성일: 2026-10-27  
점검 기준: Git HEAD `6a902ee`의 소스·설정·추적 목록 및 현재 로컬 서버 파일.  
목적: 독립 개발한 RCON MCP와 디버거 MCP를 합치는 과정에서 확인된 문제를 팀에 전달한다.

## 1. 팀 전달 요약

두 구현의 소스와 의존성을 병합한 상태이며, 하나의 MCP 앱에서 RCON과 디버거를 함께 사용하는 통합은 아직 완료되지 않았다.

우선 해결할 항목은 다음과 같다.

1. 공용 FastMCP 구현·진입점·종료 처리를 정한다.
2. RCON에서 실행할 수 있는 공통 샘플 명령과 MCP 실행 도구를 추가한다.
3. 같은 서버에서 RCON과 JDWP를 함께 켜고, 명령 응답 대기 중에도 변수 조회·재개가 가능한지 검증한다.
4. Paper는 개발자가 별도로 다운로드하고 로컬 서버 폴더에서 실행하도록 설치 안내와 경로 설정을 통일한다.
5. 서버 파일이 Git에 잘못 들어간 것으로 단정하지 않는다. 현재 추적 목록에는 서버 JAR·월드·실제 설정이 없다. 다만 저장소 내부의 다른 서버 폴더 이름을 보호하는 규칙은 보강할 수 있다.

이번 점검에서는 서버 실행, 의존성 설치, 통합 시험을 수행하지 않았다. 아래 내용은 코드로 확인한 사실과 향후 검증 항목을 구분한다.

## 2. 구현 현황과 검증 범위

| 영역 | 확인한 현재 상태 | 해석 |
|---|---|---|
| RCON MCP | `mcp_server/server.py`의 `list_players` | 고정된 localhost RCON 서버에 `list` 실행 |
| 로그 MCP | 같은 파일의 `get_server_log` | `MC_SERVER_DIR/logs/latest.log`를 바이트 오프셋으로 읽음 |
| 디버거 MCP | `mcp_server/debugger_app.py` 및 `tools/debugger.py` | attach·중단점·상태·스택·변수·재개·detach 등 8개 도구 |
| 공용 앱 | RCON 앱에서 디버거 도구 등록 및 서비스 생성·종료 코드 없음 | 소스 병합과 앱 통합은 별개 |
| 샘플 플러그인 | `craftdebug calculate <run_id> <base> <bonus>` | 로컬 서버 콘솔만 허용; RCON·플레이어 입력 거절 |
| 기존 시험 | `docs/debugger-python.md`에 Python 시험 27개 및 별도 Paper 시험 통과 기록 | 과거 기록이며 이번 점검에서 재실행하지 않음 |
| Paper 자동 검증 | `probe_paper.py`가 임시 서버를 만들고 RCON=false로 검증 | RCON·디버거 공동 통합 시험이나 실제 게임 화면 시험이 아님 |

주요 근거:

- [RCON·로그 MCP](../mcp_server/server.py)
- [디버거 앱과 종료 처리](../mcp_server/debugger_app.py)
- [디버거 도구 등록 함수](../mcp_server/tools/debugger.py)
- [샘플 명령의 실행자 제한](../plugin_template/debugger_sample/src/main/java/io/github/craftmcp/debuggersample/DebuggerSamplePlugin.java)
- [기존 검증 기록](debugger-python.md)

## 3. 통합 문제와 수정 요청

### P0-1. MCP 앱과 FastMCP 구현이 다름

**확인된 사실**

- RCON 앱은 `from mcp.server.fastmcp import FastMCP`를 사용한다.
- 디버거 앱은 `from fastmcp import FastMCP`를 사용한다.
- 각각 별도 stdio 진입점을 가지며, RCON 앱은 `register_debugger_tools`를 호출하지 않는다.
- 디버거 앱의 lifespan에는 `await service.close()`가 있으나, RCON 앱에는 디버거 수명 관리가 없다.

**영향**

현재 어느 한 진입점을 실행해도 전체 도구가 함께 제공되지 않는다. 단순히 등록 함수만 복사하면 수명 관리와 구현별 API 호환성을 놓칠 수 있다. 서로 다른 FastMCP 사용이 실제 런타임 오류를 일으키는지는 아직 시험하지 않았다.

**수정 요청**

- 공용 앱에서 사용할 FastMCP 구현을 하나로 정한다. 현재 디버거 시험이 대상으로 삼은 `fastmcp==2.14.5`를 우선 검토한다.
- RCON 도구도 등록 함수로 분리해 공용 앱에 등록한다.
- 공용 lifespan에서 디버거 서비스를 생성·공유하고 종료 시 `close()`를 보장한다.
- 독립 진입점을 유지할 경우, 독립 시험용과 공용 실행용을 문서에서 구분한다.

**완료 기준**: 공용 진입점의 MCP 도구 목록에 기존 RCON·로그 2개와 디버거 8개가 함께 보이고, 공용 앱 종료 후 헬퍼가 남지 않는다.

### P0-2. RCON으로 실행할 공통 재현 명령과 도구가 없음

**확인된 사실**

- 현재 RCON MCP의 게임 명령은 `list_players`의 `list`뿐이다.
- 샘플 `craftdebug`는 `ConsoleCommandSender`만 허용한다. 원격 콘솔과 플레이어는 `LOCAL_CONSOLE_REQUIRED`로 거절된다.
- 계획의 `crafttest add 2 3`은 제안이며 실제 명령 구현은 확인되지 않는다.

**영향**

현재 도구만으로는 “RCON으로 샘플 실행 → 중단점 → 변수 조회 → 재개 → RCON 결과 수신”을 재현할 수 없다.

**수정 요청**

- 통합 전용 명령을 추가하거나 기존 명령의 원격 콘솔 허용 여부를 팀에서 결정한다. 기존 독립 시험의 로컬 콘솔 제한은 의도된 동작이므로 별도 검토 없이 제거하지 않는다.
- 공통 명령의 입력, 기대 결과, 중단점 위치, 실행 ID를 고정한다.
- 임의 콘솔 실행보다 먼저 등록된 샘플 명령을 호출하는 MCP 도구를 구현한다.

**완료 기준**: RCON을 통해 공통 명령을 실행하고 예상 계산값과 정상 완료 응답을 받는다.

### P0-3. 독립 시험용 시작 스크립트가 RCON을 금지함

**확인된 사실**

- `mc_server/start-debug.bat`와 `.sh`는 `enable-rcon=false`를 검사하고, 그렇지 않으면 종료한다.
- 일반 `start.bat`와 `.sh`에는 JDWP 옵션이 없다.

**영향**

제공된 시작 스크립트 중 RCON과 JDWP를 동시에 사용하는 통합 실행 경로가 없다.

**수정 요청**

- 독립 디버거 시험 스크립트는 유지한다.
- 별도 통합 시작 스크립트 또는 명시적인 통합 실행 모드를 제공한다.
- 통합 모드는 RCON=true·비어 있지 않은 비밀번호·JDWP `127.0.0.1:5005`를 사용하도록 안내한다.
- 게임 25565, RCON 25575, JDWP 5005를 구분한다.

**완료 기준**: 동일 서버 프로세스에서 게임 접속·RCON 인증·디버거 attach가 모두 성공한다.

### P0-4. RCON 대기 중 디버거 조작의 동시 처리 미검증

**확인된 사실**

- `list_players`는 동기 `MCRcon` 호출이다.
- 공통 명령 실행 작업, 작업 ID 반환, 완료 조회 기능은 아직 없다.
- 통합 앱 자체가 없으므로 RCON 응답 대기 중 디버거 도구 호출 시험도 없다.

**영향 및 확인 필요 사항**

서버 명령이 중단점에 걸리면 RCON 응답이 대기할 수 있다. 동기 함수라는 사실만으로 전체 MCP가 막힌다고 단정할 수는 없지만, 프레임워크의 실행 방식과 별개로 실제 동시 호출 시험이 필요하다.

**수정 요청**

- RCON 작업을 이벤트 루프를 막지 않는 실행 경로로 분리한다.
- 실행 ID 또는 작업 ID로 시작·대기·완료·실패·실행 여부 불명을 구분한다.
- 요청 제한 시간, 디버거 자동 재개 시간(`stop_timeout_ms` 기본 10초), 서버 watchdog 설정의 관계를 확인한다.
- 응답 유실이나 timeout 뒤에 변경 명령을 자동 재전송하지 않는다.

**완료 기준**: RCON 작업이 대기하는 동안 `get_debug_state`, `get_variables`, `resume_execution`이 호출되고, 재개 후 원래 RCON 작업이 정상 완료된다.

### P1-1. 환경 변수·경로·설치 방식이 불일치

**확인된 사실**

- 루트 `.env.example`은 `RCON_PASSWORD`를 사용하지만 코드는 `MC_RCON_PASSWORD`를 읽는다.
- RCON 앱은 `mcp_server/.env`를 명시적으로 로딩한다. 루트 `.env`를 같은 방식으로 로딩하는 코드는 없다.
- `rcon_test.py`는 `.env`를 로딩하지 않고 프로세스 환경 변수만 읽는다.
- RCON 주소·포트는 코드에서 `127.0.0.1:25575`로 고정되어 있다.
- `mcp_server/.env.example`의 `MC_SERVER_DIR`에는 개인 PC 절대 경로가 있다.
- 디버거 기본 런타임 JAR 경로는 `mc_server/paper.jar`이고, 실제 로컬 서버는 저장소 밖 `../server/paper.jar`에 있다.
- 루트 `uv.lock`과 별도 `mcp_server/requirements.txt`가 공존한다. 후자는 디버거용 `fastmcp` 의존성을 포함하지 않는다.

**수정 요청**

- 공통 환경 변수 이름과 설정 파일 위치를 정하고 예제를 통일한다.
- 서버 폴더는 개발자가 지정하도록 하며, 상대 경로의 기준도 명시한다.
- RCON host/port를 설정으로 이동하고, 로그·디버거가 같은 서버를 가리키는지 점검한다.
- 기본 설치는 `uv sync --locked --python python`으로 통일한다. 별도 requirements를 유지하면 RCON 전용임을 명시한다.
- 실제 비밀번호나 개인 경로는 추적 파일에 넣지 않는다.

**완료 기준**: 새 checkout에서 예제 설정을 복사하고 서버 경로·비밀번호를 지정하면 동일한 절차로 실행된다.

### P1-2. 설치 안내와 문서의 진행 상태가 실제 코드와 다름

**확인된 사실**

- `docs/paper-server-setup.md`는 0바이트다.
- 과거 계획에는 아직 구현 전이라는 설명과 미완료 체크리스트가 남아 있다.
- 디버거 문서의 “RCON 코드는 구현하지 않았다”는 설명은 당시 담당 범위의 기록이며 현재 저장소 전체 상태와 혼동될 수 있다.
- `get_server_log`가 추가됐지만 테스트 안내의 도구 설명은 현재 코드와 함께 재확인할 필요가 있다.

**수정 요청**

- 서버 설치 문서에 Paper 별도 다운로드, JDK 17, EULA 직접 동의, 서버 실행, Java Edition 1.20.1 접속 절차를 작성한다.
- 독립 구현 완료·과거 시험 통과·공용 앱 통합·현재 PC 검증을 구분한다.
- 예제의 다른 개발자 절대 경로를 일반적인 경로 또는 설정 변수로 교체한다.

## 4. Paper 별도 설치와 Git 제외 규칙

### 4.1 확인 결과: 현재 서버 파일이 Git에 포함된 것은 아님

- Git 루트는 현재 `craftmcp` 프로젝트 폴더다.
- 발견한 `../server`는 Git 루트 밖이며, 해당 폴더에서 Git 저장소도 확인되지 않았다. 이 저장소의 `.gitignore` 적용 대상이 아니다.
- `git ls-files`에 Paper JAR, 실제 `server.properties`, 월드, EULA, 플레이어 데이터는 없다.
- `git check-ignore`로 `mc_server/paper.jar`, 실제 설정, 월드, plugins가 기존 `mc_server/*` 규칙에 제외됨을 확인했다.
- Gradle Wrapper JAR 두 개는 재현 가능한 빌드를 위한 정상 추적 파일이다. Paper JAR와 구분해야 한다.

따라서 앞선 “Paper를 다시 받을 필요가 없다”는 안내는 이 PC에 이미 설치본이 있다는 설명이다. 프로젝트의 배포·설치 방식은 **각 개발자가 Paper를 별도로 다운로드해 실행하는 방식**으로 문서화하는 것이 맞다.

### 4.2 권장 파일 배치

```text
작업 폴더/
├─ craftmcp/                 # Git 저장소: 코드·Wrapper·설정 예제·실행 스크립트
└─ server/                   # 별도 설치: paper.jar·월드·실제 설정·배포 플러그인
```

- 기준 버전은 프로젝트 결정인 Paper 1.20.1 build 196, JDK 17을 유지한다.
- 다운로드 위치는 Paper 공식 배포 페이지에서 확인하고, 재현에 사용할 JAR의 버전과 SHA-256을 기록한다.
- EULA는 설치자가 읽고 직접 동의한다. 동의된 파일을 저장소에 배포하지 않는다.
- 빌드한 샘플 플러그인은 로컬 서버의 `plugins/`에 복사한다. 빌드 결과 자체는 커밋하지 않는다.
- 현재 시작 스크립트는 스크립트가 있는 폴더의 `paper.jar`를 실행한다. 외부 설치 방식을 기본으로 삼으면 서버 디렉터리 인자를 받도록 수정하거나, 외부 서버 폴더에 스크립트를 복사하는 절차를 명시한다.
- `debugger.local.json`의 런타임 JAR와 `MC_SERVER_DIR`도 같은 외부 서버 폴더를 가리켜야 한다.

### 4.3 `.gitignore` 보강 제안

**확인된 빈틈**: 저장소 내부의 `server/paper.jar`, `server/server.properties`는 현재 규칙으로 제외되지 않는다. 외부 `../server`와 달리, 개발자가 저장소 내부에 `server/`를 생성하면 실수로 추가할 수 있다.

현재 `mc_server/` 예외 규칙은 유지하고, 저장소 루트의 대체 로컬 서버 폴더를 보호하는 다음 규칙을 추가하는 방안을 권장한다.

```gitignore
# Optional local Paper installation inside this repository
/server/
```

이 규칙은 **제안이며 이번 문서 작성에서 실제 `.gitignore`를 변경하지 않았다.** 기본 설치 위치가 외부 폴더로 확정되면 내부 설치를 지원할지 먼저 정한다.

추가 주의 사항:

- `*.jar` 전체를 제외하지 않는다. 두 프로젝트의 `gradle/wrapper/gradle-wrapper.jar`는 계속 추적해야 한다.
- 서버 폴더를 저장소 안 임의의 이름으로 만들면 `/server/` 규칙만으로는 보호되지 않는다. 지원하는 경로를 정하고 그 경로를 제외한다.
- 개인 설정, `.env`, 빌드 결과, `.cache`, `data/debugger`는 기존 제외 규칙을 유지한다.
- 이미 추적 중인 파일은 `.gitignore` 추가만으로 제외되지 않는다. 향후 실제 서버 파일 추적이 확인되면 해당 파일만 `git rm --cached` 대상으로 검토한다. 현재 점검 결과에는 제거할 서버 추적 파일이 없다.
- `git rm -r --cached .` 같은 전체 인덱스 초기화는 필요하지 않다.

점검 명령:

```powershell
git ls-files mc_server server
git ls-files "*.jar"
git check-ignore -v mc_server/paper.jar mc_server/server.properties mc_server/world/level.dat
# /server/ 규칙을 추가한 뒤 확인
git check-ignore -v server/paper.jar server/server.properties
git status --short
```

## 5. 로컬 서버에서 별도로 확인된 사항

- `../server`에 Paper JAR·월드·캐시·라이브러리가 이미 있다.
- 기존 로그는 Paper 1.20.1 build 196과 플레이어 접속을 기록한다. 현재 실행 중임을 뜻하지 않는다.
- 실제 설정은 게임 주소 `127.0.0.1:25565`, RCON=true, RCON 포트 25575다.
- 과거 로그에 비밀번호가 없어 RCON이 비활성화됐다는 메시지가 있다. 지금 실행할 때 실제 설정과 새 시작 로그를 다시 확인해야 한다.
- 기존 `start.bat`에는 JDWP 옵션이 없다.
- 서버 `plugins/`에 디버거 샘플 JAR는 확인되지 않았다.
- Python 3.13.2와 JDK 17은 확인됐고, uv 및 프로젝트 `.venv`는 확인되지 않았다.

이 정보는 현재 PC 관찰 결과다. 팀 공통 경로·설치 완료 상태로 가정하지 않는다.

## 6. 수정 후 통합 합격 기준

1. 새 checkout에서 고정 의존성을 설치하고 헬퍼·샘플 플러그인을 빌드한다.
2. 개발자가 별도로 설치한 Paper 서버 경로를 로컬 설정으로 지정한다.
3. 동일 서버에서 RCON과 JDWP를 켜고 게임 클라이언트로 접속한다.
4. 공용 MCP 진입점에서 RCON·로그·디버거 도구가 함께 발견된다.
5. RCON으로 공통 샘플 명령을 실행하고, 응답 대기 중 계산 이후 중단점에 도달한다.
6. 별도 MCP 호출로 인자·계산값·객체·스택을 조회한 다음 재개한다.
7. 원래 RCON 작업의 정상 완료와 기대 결과를 확인한다.
8. 중단점을 제거하고 같은 명령을 다시 실행해 정상 속도의 결과를 확인한다.
9. 잘못된 인증, 서버 종료, 응답 지연, 헬퍼 종료에서도 실패 상태와 정리 동작을 확인한다.
10. 실제 사용할 IDE/에이전트 호스트에서도 도구 발견·호출을 확인한다.
11. `git status`와 추적 목록에서 서버 파일·비밀번호·월드·빌드 산출물이 포함되지 않았음을 확인한다.

각 시험 기록에는 공용 앱 버전, 서버·플러그인 해시, 실행 ID, 기대값·관찰값, RCON 응답, 디버거 관찰, 재개·종료 결과를 남긴다. 계산값 검증과 실제 게임 상태·화면 검증은 구분한다.
