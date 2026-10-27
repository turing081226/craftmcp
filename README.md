# craftmcp
마인크래프트 개발을 위한 MCP

히히히
---

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
| `eval/`                               | 테스트 시나리오 및 평가 코드  | 실험 결과 평가          |
| `data/`                               | 실험 데이터            | 대용량 데이터는 별도 관리 고려 |

## MCP 서버와 JVM 디버거 실행

공용 MCP 서버는 RCON·플러그인 도구와 JVM 디버거 도구를 함께 등록합니다. 먼저
`mcp_server/.env`에 `MC_SERVER_DIR`과 `MC_RCON_PASSWORD`를 설정한 뒤 저장소 루트에서
다음처럼 실행합니다.

```powershell
.\mcp_server\.venv\Scripts\python.exe -m mcp_server.server
```

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
