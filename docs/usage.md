# CraftMCP 사용법

CraftMCP는 AI(Claude Code 등 MCP 호스트)가 Minecraft 플러그인 개발을 도와주도록 만든 MCP 서버다.
AI가 Paper 테스트 서버를 켜고 끄고, 플러그인을 빌드·배포하고, 서버 로그와 디버거를 보고,
**게임 화면을 캡처하고 직접 플레이하며** 기능을 확인할 수 있다.

처음이라면 [1. 설치](#1-설치)부터, 이미 연결했다면 [3. 이렇게 요청하면 된다](#3-이렇게-요청하면-된다)를 본다.

## 1. 설치

### 1-1. 필요한 프로그램

| 프로그램 | 용도 |
|---|---|
| Windows 10/11 | 화면 캡처·게임 조작은 Windows 전용 (나머지 도구는 다른 OS에서도 동작) |
| Python 3.11~3.13, [uv](https://docs.astral.sh/uv/) | MCP 서버 실행 |
| JDK 17 (`winget install EclipseAdoptium.Temurin.17.JDK`) | Paper 서버, 플러그인 빌드, 디버거 |
| Git | 저장소 내려받기 |
| Claude Code 등 MCP 호스트 | AI가 도구를 호출 |
| Minecraft Java 1.20.1 | 캡처·게임 조작을 쓸 때만 |

### 1-2. 내려받기와 의존성 설치

```powershell
git clone https://github.com/turing081226/craftmcp.git
cd craftmcp
uv sync --locked --python python
```

### 1-3. Paper 테스트 서버

[Paper 서버 준비](paper-server-setup.md)를 따라 `mc_server/paper.jar`, `server.properties`, EULA를 준비한다.

### 1-4. 설정 파일 2개

두 파일 모두 PC마다 내용이 달라 커밋하지 않는다(`.gitignore` 처리됨).

**`mcp_server/.env`**: `mcp_server/.env.example`을 복사한 뒤 채운다.

```ini
MC_SERVER_DIR=C:\각자\경로\craftmcp\mc_server
MC_RCON_PASSWORD=server.properties의 rcon.password와 같은 값
# AI가 게임 조작(명령어 입력·이동·클릭)까지 하게 하려면
CRAFTMCP_GAME_INPUT=1
```

**`.mcp.json`**: 저장소 루트의 `.mcp.example.json`을 복사하고 `C:/path/to/craftmcp`를 각자 clone한 절대 경로로 바꾼다.

### 1-5. 연결 확인

1. 저장소 폴더에서 Claude Code를 연다. 프로젝트 MCP 서버(`craftmcp`)를 허용할지 물으면 허용한다.
2. `/mcp`에서 `craftmcp`가 connected이고 도구가 **29개**인지 확인한다.
3. 첫 기동은 10~30초 걸릴 수 있다. `.claude/settings.json`에서 연결 제한을 90초로 늘려 두었다.
   그래도 실패하면 백신 실시간 검사 예외에 `.venv` 폴더를 추가하고 VS Code/Claude Code를 재시작한다.

## 2. 도구 한눈에 보기

AI가 알아서 고르므로 이름을 외울 필요는 없다. 무엇이 가능한지만 알면 된다.

| 분류 | 도구 | 하는 일 |
|---|---|---|
| 서버 | `list_runtimes`, `get_runtime_status` | 연결된 테스트 서버와 상태 조회 |
| | `start_server`, `stop_server` | 관리 서버 시작·정상 종료 (job으로 진행) |
| | `list_players`, `get_server_log` | 접속자, 서버 로그(`logs/latest.log`) 읽기 |
| 명령 job | `execute_command`, `get_job`, `cancel_job` | 허용된 RCON 명령(`list_players`, `say`)을 job으로 실행·조회·취소 |
| 플러그인 | `build_plugin` | Java 소스를 받아 Gradle로 빌드, `build_id` 반환 |
| | `deploy_plugin`, `rollback_plugin` | 빌드를 서버에 배포(재시작·활성화 확인) / 이전 JAR로 되돌리기 |
| | `get_plugin_status` | 설치된 JAR와 현재 부팅에서 실제로 켜졌는지 조회 |
| 디버거 | `attach_debugger`, `set_breakpoint`, `get_stack_trace`, `get_variables`, `resume_execution` 등 8개 | JDWP로 서버 JVM에 붙어 중단점·변수 확인 ([디버거 안내](debugger-python.md)) |
| 화면 | `list_game_clients` | 실행 중인 Minecraft 창 찾기 (`client_id`) |
| | `capture_game_view` | 게임 창을 자동으로 앞으로 가져와 촬영, 이미지를 AI에게 전달 |
| | `get_game_capture`, `record_visual_assertion`, `get_visual_run_evidence` | 이전 캡처 다시 보기, 시각 판정 기록, 실행별 증거 목록 |
| 게임 조작 | `focus_game_window` | 게임 창 불러오기 |
| | `run_game_chat_command` | 플레이어로서 채팅/명령 입력 (예: `/heal 4`) |
| | `send_game_input` | 키(이동·점프 등), 시점 회전, 좌·우클릭, 휠, 글자 입력 |

> RCON은 서버 콘솔 권한으로 실행되므로 `/heal`처럼 **플레이어만 쓸 수 있는 명령**은 실행할 수 없다.
> 이런 명령은 `run_game_chat_command`로 게임 안에서 입력한다.

## 3. 이렇게 요청하면 된다

AI에게 평소 말로 요청한다. 아래는 실제로 확인한 흐름이다.

### 플레이어 명령 기능 확인 (예: SimpleKit의 `/heal`)

> "체력 깎인 상태에서 캡처하고, `/heal 4` 입력한 다음 다시 캡처해서 체력이 4 올랐는지 확인해줘"

AI는 `list_game_clients` → `capture_game_view`(전) → `run_game_chat_command("/heal 4")` →
`capture_game_view`(후) → `get_server_log`로 명령 실행 기록 확인 → `record_visual_assertion`으로 판정을 남긴다.
정확히 비교하려면 미리 게임에서 `/gamerule naturalRegeneration false`로 자연 회복을 끈다.

### 새 플러그인 만들고 바로 시험 (예: BlazeFireball)

> "BlazeFireball 플러그인 빌드해서 서버에 배포하고, 블레이즈 막대 받아서 우클릭으로 쏴본 다음 화면 확인해줘"

`build_plugin` → `deploy_plugin`(→ `get_job`으로 완료 확인) → `get_plugin_status` →
`run_game_chat_command("/give @s blaze_rod")` → `send_game_input`(우클릭) → `capture_game_view`.
플러그인 자체 설명은 [BlazeFireball README](../plugin_template/blaze_fireball/README.md)를 본다.

### 움직이며 확인

> "앞으로 2초 걸어가서 오른쪽으로 돌고 앞의 블록을 우클릭해줘"

`send_game_input`의 `actions` 예:

```json
[
  {"type": "key", "keys": ["w"], "hold_ms": 2000},
  {"type": "look", "dx": 600},
  {"type": "click", "button": "right"}
]
```

| 동작 | 형식 |
|---|---|
| 키 누르기/누르고 있기 | `{"type":"key","keys":["w","ctrl"],"hold_ms":1000}` (w/a/s/d, space, shift, ctrl, e, q, 1~9, escape, enter, f1~f12 등) |
| 클릭 | `{"type":"click","button":"left"\|"right"\|"middle","hold_ms":50,"count":1}` (블록 부수기는 left + hold_ms 1000~3000) |
| 시점 회전 | `{"type":"look","dx":600,"dy":-100}` (+dx 오른쪽, +dy 아래. 회전량은 마우스 감도에 따라 다름) |
| 휠 | `{"type":"scroll","amount":-1}` (핫바 칸 이동) |
| 글자 입력 | `{"type":"type_text","text":"안녕"}` (이미 열린 채팅·표지판 등에) |
| 대기 | `{"type":"wait","ms":500}` |

### 서버 로그·디버깅

> "서버 로그 마지막 50줄 보여줘" / "플러그인이 켜졌는지 확인해줘" /
> "DamageCalculator 23번째 줄에 중단점 걸고 값 확인해줘"

## 4. 게임 화면·조작을 쓸 때 알아둘 것

- Minecraft는 MCP와 **같은 PC**에서 실행하고 테스트 서버에 접속해 둔다. 창 모드/테두리 없는 창 모드를 권장한다.
- AI가 창을 불러오면 Minecraft가 포커스를 잃었을 때 열어 둔 **게임 메뉴(일시정지 화면)** 가 보일 수 있다.
  AI가 캡처로 확인하고 `escape`를 보내 닫는다. 게임에서 **F3+P**를 누르면 이 메뉴가 아예 열리지 않는다.
- 게임 조작 중 **다른 창을 클릭하면 즉시 멈추고** 눌린 키를 모두 뗀다(`focus_lost`). 멈추고 싶을 때 쓰면 된다.
- 게임 조작은 로그인한 플레이어 권한으로 실행된다. 공용·실서버 계정에서는 `CRAFTMCP_GAME_INPUT`을 켜지 않는다.
- 호출 한 번에 동작 50개, 누르기·대기 합계 30초까지다. 길게 하려면 여러 번 나눠 요청한다.
- 입력이 "전달됨"은 기능 성공의 증거가 아니다. 결과는 항상 캡처·서버 로그로 확인한다.

## 5. 문제 해결

| 증상 / 오류 코드 | 원인과 조치 |
|---|---|
| `/mcp`에 craftmcp가 failed / 연결 타임아웃 | `.mcp.json` 경로 확인, `uv sync` 재실행, 백신 예외에 `.venv` 추가, 호스트 재시작 |
| `MC_RCON_PASSWORD 환경변수가 설정되지 않았습니다` | `mcp_server/.env` 작성 후 MCP 서버 재시작 |
| RCON 연결 실패 | 서버 실행 여부, `server.properties`의 `enable-rcon=true`, 포트·비밀번호가 `.env`와 같은지 확인 |
| `list_game_clients` 결과가 비어 있음 | Minecraft Java가 실행 중인지, 최소화 상태여도 표시됨. 다른 런처 창만 있으면 후보가 없음 |
| `client_occluded` | 메시지에 나온 창이 게임을 가림. 그 창을 옮기거나 닫기 |
| `overlay_windows_present` 경고 | Medal·Discord 등 투명 오버레이. 촬영은 됨. 오버레이 그림이 찍혔는지만 확인 |
| `focus_failed` | Windows가 창 전환을 거부. 게임 창을 한 번 직접 클릭 후 재시도 |
| `input_disabled` | `.env`에 `CRAFTMCP_GAME_INPUT=1` 추가 후 MCP 서버 재시작 |
| `input_blocked` | 게임이 관리자 권한으로 실행 중. 일반 권한으로 다시 실행 |
| 명령이 채팅에 안 들어감 | 메뉴·인벤토리가 열려 있었음. 캡처로 확인 후 `escape`로 닫고 재시도 |
| 첫 `build_plugin`이 시간 초과 | 의존성 내려받기 때문. `offline=false`로 캐시 준비 또는 `timeout_seconds` 늘리기 |

## 더 자세한 문서

- [화면 캡처·게임 입력 상세](client-capture.md)
- [플러그인 빌드·배포](plugin-build-deploy.md), [도구 입력·결과 계약](tool-contracts.md)
- [JVM 디버거](debugger-python.md), [테스트 방법](testing.md)
