# craftmcp
마인크래프트 개발을 위한 MCP

히히히

**처음 사용한다면 [CraftMCP 사용법](docs/usage.md)부터 보세요.** 설치·연결, 도구 목록, 요청 예시,
화면 캡처·게임 조작, 문제 해결을 한 문서에 정리했습니다. 테스트 서버는 [Paper 서버 준비](docs/paper-server-setup.md)를 따릅니다.

---

## 남은 구현·검증 계획

[2026-11-17 A·B·C·D 통합 결과](docs/integration-2026-11-17.md)에 공용 도구 21개,
runtime 설정, build_id 기반 배포·롤백 및 실제 Paper 인수 시험을 정리했습니다.
기존 분업 문서의 연결 예정 상태보다 이 통합 기록을 우선 확인하세요.

최신 main과 C의 화면 캡처 기능을 합친 공용 앱은 **26개 도구**를 제공합니다.
[C/main 통합 기록](docs/c-main-integration-2026-11-17.md)과
[Minecraft 화면 캡처 사용법](docs/client-capture.md)에서 통합 범위와 검증 방법을 확인할 수 있습니다.

[2026-11-03 후속 계획](docs/remaining-plan-2026-11-03.md)에 현재 구현, 추가 MCP 도구의 입력·결과 계약, 빌드·배포 보완, 실제 통합 검증 순서를 정리했습니다. 시험용 플러그인 종류는 미정입니다.

[4인 병렬 개발 분업안](docs/team-work-split-2026-11-03.md)에서 A·B·C·D별 담당 도구·파일, 공통 계약, 독립 개발 방법과 통합 완료 기준을 확인할 수 있습니다.

[D → B RCON·디버거 통합 계약](docs/d-rcon-debugger-contract.md)에 실행할 fixture 명령,
출력 파서, 중단점·기대 변수, ID 연결 및 실제 Paper 검증 절차를 정리했습니다.
[검증 기록](docs/d-rcon-debugger-validation.md)에서 B의 실제 job·공용 MCP 앱을 사용한 Paper 통합 결과를 확인할 수 있습니다.

## 각 파일의 역할

| 파일 및 디렉터리                             | 역할                | 비고                |
| ------------------------------------- | ----------------- | ----------------- |
| `README.md`                           | 프로젝트 소개 및 실행 방법   | 기존 파일 유지          |
| `.gitignore`                          | Git에서 제외할 파일 지정   | 서버 데이터 및 비밀 정보 제외 |
| `.env.example`                        | 환경 변수 예시          | 실제 비밀번호는 기록하지 않음  |
| `docs/decisions.md`                   | 기술적 의사결정 기록       | Java, Paper 버전 등  |
| `docs/paper-server-setup.md`          | 서버 설치 및 실행 안내     | 팀원 공통 매뉴얼         |
| `mc_server/start.sh`                  | Linux/macOS 서버 실행 | 실행 권한 필요          |
| `mc_server/start.bat`                 | Windows 서버 실행     | Windows 팀원용       |
| `mc_server/server.properties.example` | 공통 서버 설정          | 실제 설정의 원본         |
| `mcp_server/`                         | Python MCP 서버     | 디버거 MCP 도구와 프로세스 통신 |
| `plugin_template/`                    | Paper 플러그인        | 디버거 콘솔 재현 샘플 포함 |
| `plugin_template/blaze_fireball/`     | 예제 Paper 플러그인     | 블레이즈 막대기 파이어볼 ([README](plugin_template/blaze_fireball/README.md)) |
| `plugin_template/marvel_heroes/`      | 마블 히어로 플러그인     | 아이언맨(텍스처·리펄서·비행), 스파이더맨(괴력·벽 타기·거미줄), 토르(묠니르 던지기·회수) ([README](plugin_template/marvel_heroes/README.md)) |
| `eval/`                               | 테스트 시나리오 및 평가 코드  | 실험 결과 평가          |
| `data/`                               | 실험 데이터            | 대용량 데이터는 별도 관리 고려 |

## MCP 서버와 JVM 디버거 실행

공용 MCP 서버는 runtime·job·RCON·플러그인·JVM 디버거·화면 캡처 도구를 함께 등록합니다. 먼저
`mcp_server/.env`에 `MC_SERVER_DIR`과 `MC_RCON_PASSWORD`를 설정한 뒤 저장소 루트에서
다음처럼 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m mcp_server.server
```

### Claude Code 등 MCP 호스트에 연결

1. `uv sync --locked --python python`으로 `.venv`를 만든다.
2. `mcp_server/.env.example`을 `mcp_server/.env`로 복사하고 `MC_SERVER_DIR`(각자의 절대 경로)와 `MC_RCON_PASSWORD`를 채운다.
3. `.mcp.example.json`을 `.mcp.json`으로 복사하고 `C:/path/to/craftmcp`를 각자 clone한 경로로 바꾼다.
   `.mcp.json`과 `.env`는 PC마다 다르므로 커밋하지 않는다(`.gitignore` 처리됨).
4. 호스트를 재시작한다. 첫 기동은 라이브러리 로딩으로 10~30초 걸릴 수 있어 `.claude/settings.json`에서
   MCP 연결 제한을 90초(`MCP_TIMEOUT`)로 늘려 두었다.

AI가 게임 창을 불러오고 명령어 입력·이동·클릭까지 하게 하려면 `mcp_server/.env`에
`CRAFTMCP_GAME_INPUT=1`을 추가한다. 자세한 내용과 안전장치는 [캡처·게임 입력 문서](docs/client-capture.md#게임-입력)를 참고한다.

JVM 디버거를 사용하려면 먼저 Java 헬퍼를 빌드합니다.

```powershell
.\debugger_helper\gradlew.bat -p .\debugger_helper --no-daemon jar compileDebuggee compileDebuggeeWithoutVars
```

실제 디버거 설정은 `debugger.example.json`을 복사해 로컬 파일로 만든 뒤,
공용 서버를 실행하는 프로세스에 `DEBUGGER_CONFIG=debugger.local.json` 환경변수를 지정합니다.
`debugger.local.json`과 디버거 증거 파일은 저장소에 커밋하지 않습니다.

## 디버거 Python·MCP 실행

분업안의 B 구현, A의 실제 Java 헬퍼 연결, Paper 검증 절차는
[Python 디버거 안내](docs/debugger-python.md)를 따른다.

```powershell
uv sync --locked --python python
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p "test_*.py" -v
```

모의 시험과 실제 JDI 시험을 구분한다. 실제 JDI 시험에는 A의 Java 빌드 산출물이 필요하며,
Paper 전체 흐름은 `tests/debugger_python/probe_paper.py`로 별도 검증한다.


## 테스트

환경 준비부터 모의 시험, 실제 JVM·Paper, RCON 검증까지는 [테스트 방법](docs/testing.md)을 따른다.
루트의 `uv sync --locked --python python`으로 디버거와 RCON 의존성을 함께 설치한다.
