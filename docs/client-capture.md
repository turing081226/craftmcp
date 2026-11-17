# Minecraft 화면 캡처와 시각 검증 MCP

작성일: 2026-11-17 (KST)

최신 main 통합 후 공용 앱은 runtime/job/플러그인/디버거 도구와 캡처 도구를 합쳐 26개를 제공한다.
통합 범위와 재검증은 [C/main 통합 기록](c-main-integration-2026-11-17.md)을 참고한다.

실행 중인 Minecraft Java 창의 화면을 촬영하고, MCP의 PNG 이미지 응답으로 AI에게 전달한다.
AI 또는 사용자가 확인한 시각 판정을 스크린샷과 연결해 기록할 수 있다. 캡처 성공과 서버 기능 성공은
별도로 다루며, 이 도구 안에서 외부 비전 모델을 호출하거나 게임 입력을 자동 실행하지 않는다.

## 준비

1. Minecraft Java 클라이언트와 MCP 서버를 같은 Windows 10/11 데스크톱 세션에서 실행한다.
2. 테스트 서버에 접속하고 확인할 메뉴·HUD 등 시험 화면을 준비한다.
3. 공용 MCP 서버를 재시작하고 호스트에서 연결을 새로 고쳐 추가 도구를 발견한다.

```powershell
.\.venv\Scripts\python.exe -m mcp_server.server
```

추가 Python 패키지는 필요하지 않다. Windows GDI/User32를 ctypes로 호출하고 표준 라이브러리로 PNG를
인코딩한다. 다른 운영체제에서는 캡처 기능만 `unsupported_platform` 오류를 반환한다.
서버 PC에 게임 클라이언트가 없는 경우 Paper/RCON만으로는 플레이어 화면을 촬영할 수 없다.

## 추가된 도구

| 도구 | 입력 | 반환 |
|---|---|---|
| `list_game_clients` | 없음 | client_id, 창 제목, PID, 실행 파일 이름, 창 클래스 |
| `capture_game_view` | client_id, run_id, label; 선택 runtime_id/build_id/deployment_id/delay_seconds | PNG 이미지 콘텐츠와 구조화된 증거 메타데이터 |
| `get_game_capture` | run_id, capture_id | 해시를 재검증한 기존 PNG 및 메타데이터 |
| `record_visual_assertion` | run_id, capture_id, expected, observed, verdict | 호출자가 판단한 passed/failed/unknown 시각 판정 기록 |
| `get_visual_run_evidence` | run_id; 선택 offset=0, limit=20 | 시간순 촬영·시각 판정 메타데이터 페이지, next_offset |

`list_game_clients`는 `java.exe`/`javaw.exe`의 GLFW30/LWJGL 창을 후보로 제공한다.
다른 Java OpenGL 프로그램도 후보가 될 수 있으므로 제목을 확인해 원하는 Minecraft 창을 선택한다.
목록 조회는 촬영하지 않는다. client_id는 창 핸들·PID·프로세스 시작 시각에 연결되며, 게임을 다시
실행했다면 목록을 다시 조회해야 한다. 등록 목록은 MCP 서버 프로세스가 유지하는 동안 유효하다.

## 사용 흐름

### 1. 창 선택

AI에게 “게임 클라이언트 목록을 확인해줘”라고 요청한다. `list_game_clients()`의 결과에서 사용할
client_id를 선택한다. 후보가 여러 개면 창 제목과 PID를 확인한다.

### 2. 실행 전 화면 촬영

다음과 같은 인수로 촬영을 요청한다. client_id는 실제 목록 결과로 바꾼다.

```json
{
  "client_id": "목록에서_받은_ID",
  "run_id": "shop-test-001",
  "label": "before-purchase",
  "delay_seconds": 5
}
```

요청한 뒤 5초 안에 Minecraft 창으로 전환한다. `delay_seconds`는 0~10의 정수이며, 전환을 위한
대기 시간이다. 게임 상태가 준비됐는지를 확인하는 자동 동기화 장치는 아니다.
촬영 시 게임 창이 전경에 있고 전체 클라이언트 영역이 화면 안에 보여야 한다.
도구가 창을 자동으로 앞으로 가져오거나 마우스·키보드를 조작하지는 않는다.

이미지는 화면 테두리를 제외한 클라이언트 영역이며, 비율을 유지해 최대 1920×1080으로 축소한다.
원래 영역 좌표는 source_rect, 결과 크기는 width/height로 기록한다.

### 3. 기능 실행 후 같은 run으로 촬영

사용자가 실제 구매 버튼을 누르는 등 플러그인 기능을 실행하고 같은 run_id로 다시 요청한다.
label은 `after-purchase`처럼 구분한다. 이전 이미지가 필요하면 `get_game_capture`로 다시 불러온다.
게임 해상도, GUI 크기, 리소스팩, 카메라 위치를 맞추면 비교가 쉬워진다.

### 4. 이미지 판정 및 서버 상태 대조

AI가 반환된 이미지를 보고 다음처럼 시각 판정을 기록할 수 있다.

```json
{
  "run_id": "shop-test-001",
  "capture_id": "촬영_결과의_capture_id",
  "expected": "구매 완료 문구와 구매한 아이템이 표시됨",
  "observed": "완료 문구가 보이며 해당 아이템이 인벤토리에 표시됨",
  "verdict": "passed"
}
```

이 값은 `assessment_source=caller_visual_review`인 호출자의 판단이다. `record_visual_assertion`이
이미지를 자체 분석해서 결론을 내리는 것은 아니다. 판단이 불분명하면 `unknown`으로 기록한다.
금액 차감·권한·DB 저장 등 화면만으로 확정할 수 없는 항목은 정확한 서버 상태 조회와 별도로 대조한다.
모든 응답의 `functional_result`는 `unknown`이며, 시각 판정 passed를 게임 로직 성공으로 승격하지 않는다.

현재 시험용 플러그인을 선택해 실행하는 `run_visual_scenario`, 자동 클릭·입력, OCR 및 기준 이미지
자동 비교는 포함하지 않는다. 먼저 이 수동 실행 → 촬영 → AI 검토 흐름을 사용할 수 있다.

## 증거와 상관 ID

저장 위치는 `.cache/client-captures/<run_id>/`다. 기존 `.gitignore`의 `.cache` 규칙으로 로컬에 보관된다.

- `<capture_id>.png`: 촬영 이미지
- `<capture_id>.json`: 이미지 SHA-256, 촬영 시각, 창 정보, 크기·좌표, label, 상관 ID
- 별도 evidence_id의 JSON: 해당 capture_id와 이미지 해시에 연결된 시각 판정

`runtime_id`, `build_id`, `deployment_id`는 선택 입력이다. 현재는 호출자가 제공한 참조를 저장하며
실제 접속 서버/설치 빌드와 일치한다고 검증하지 않는다(`correlation_verified=false`).
D의 공통 run/evidence 서비스에는 이후 이 ID와 이미지 해시를 연결하면 된다.
이 도구의 `get_visual_run_evidence`는 시각 증거 전용 조회이며 공통 `get_run_evidence`를 대체하지 않는다.

ID는 1~64자의 ASCII 영문·숫자·밑줄·하이픈을 사용하고 첫 글자는 영문 또는 숫자여야 한다.
Windows 예약 이름은 거절한다. 파일 저장 경로는 호출자가 지정할 수 없다.
run당 최대 1000개 기록, PNG 최대 8 MiB, 조회 페이지 최대 100개를 적용한다.
백엔드가 생성한 실제 촬영과 테스트용 결과는 `simulated`로 구분한다.

## 실패와 한계

| 코드/경고 | 의미와 조치 |
|---|---|
| `unknown_client` / `stale_client` | 게임 창 목록을 다시 조회하고 선택 |
| `client_not_foreground` | Minecraft를 전경으로 전환; 필요하면 촬영 지연 사용 |
| `client_not_visible` | 최소화/숨김 상태를 해제 |
| `client_occluded` | 게임 위에 겹친 다른 창을 치운 뒤 재시도 |
| `client_offscreen` | 창 전체가 모니터 영역 안에 오도록 이동 |
| `client_changed` | 촬영 중 창 위치가 바뀜; 해당 프레임은 저장하지 않음 |
| `capture_busy` | 진행 중인 촬영이 끝난 뒤 요청 |
| `possible_blank_frame` | 단색 프레임; 게임 렌더링이 정상 수집됐는지 이미지를 보고 확인 |
| `evidence_modified` | 저장한 PNG가 변경됨; 새 캡처로 증거 생성 |

프로세스 신원·전경·가림·영역을 촬영 전후 검사한다. 데스크톱 화면을 복사하는 방식이라 촬영 순간의
아주 짧은 오버레이 변경까지 완전히 막을 수는 없다. GPU/독점 전체 화면/원격·잠금 세션에서는 검은
프레임이나 캡처 실패가 발생할 수 있다. 먼저 창 모드 또는 테두리 없는 창 모드에서 확인한다.
단색 경고만으로 모든 렌더링 실패를 탐지할 수 없으므로 반환된 이미지를 실제로 확인해야 한다.

## 구현 및 검증

| 파일 | 역할 |
|---|---|
| [adapters/client_capture.py](../mcp_server/adapters/client_capture.py) | Java 창 탐색, 신원·영역 확인, Windows GDI 촬영, PNG 인코딩 |
| [services/client_capture.py](../mcp_server/services/client_capture.py) | 클라이언트 식별자, 증거 저장·해시 검증·판정·페이지 조회 |
| [tools/client_capture.py](../mcp_server/tools/client_capture.py) | 이미지/텍스트/structured content를 반환하는 도구 5개 |
| [server.py](../mcp_server/server.py) | 공용 앱에 추가 도구 등록; 기존 빌드·배포·로그·디버거 유지 |
| [tests/client_capture/](../tests/client_capture/) | 테스트용 이미지, 실제 GDI 메모리 비트맵, 실제 stdio 검증 |

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/client_capture -v
```

테스트용 화면의 PNG/색상, MCP 이미지 블록과 structured content, 증거 재조회·변조 감지,
경로 이탈 거절, 시각/서버 판정 분리, 동시 도구 호출 및 Windows 창 상태를 검사한다.
네이티브 GDI 색상 시험은 메모리 비트맵을 사용하며 실제 데스크톱 픽셀을 읽지 않는다.
심볼릭 링크 생성 권한이 없으면 해당 경로 시험은 skipped로 표시된다.
개발 환경의 실제 창 탐색에서는 게임 후보가 없었다. 실제 Minecraft 렌더링·게임 UI 기능은 미검증이며
클라이언트를 실행한 뒤 위 사용 흐름으로 확인해야 한다.

### 이번 실행 결과

| 검사 | 결과 |
|---|---|
| 캡처 기능 시험 | 14개 실행: 13개 통과, 심볼릭 링크 권한 시험 1개 skipped |
| 실제 공용 stdio 시험 | 1개 통과: 새 도구와 기존 도구의 중복 없는 등록, 창 탐색, 미등록 ID 오류 응답 |
| 기존 로그·RCON 시험 | 10개 통과 |
| 기존 공용 stdio 회귀 | 초기 연결 10초 응답 제한으로 실패; 단독 재실행도 실패 |
| 변경 전 코드 비교 | HEAD의 server.py를 별도 프로세스에서 실행한 같은 시험도 초기 연결 실패 재현 |
| Python 문법/공백 | compileall 및 git diff --check 통과 |
| 실제 Minecraft 촬영 | 실행 중인 게임 후보가 없어 미실행 |

신규 시험은 합계 15개 중 14개 통과, 1개 skipped다. 실제 공용 앱 시험은 요청 timeout 30초,
init_timeout 60초를 사용했다. 기존 stdio 회귀의 실패는 새 기능 통과 결과에 합산하지 않았다.
제한 환경에서 멈춘 최초 시험 프로세스는 종료했고, 일반 권한의 최종 시험 결과를 위에 기록했다.

Windows API 참고: [GetClientRect](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclientrect),
[GetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow),
[StretchBlt](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-stretchblt),
[GetDIBits](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getdibits).
