# CraftMCP 남은 구현·통합 검증 계획

작성일: 2026-11-03 (KST)  
코드 기준: `main`, `5e76d273aa21ac3f6e0c33be0d34451a4f625718` (`qa 수정`)  
상태: 현재 구현을 확인한 후 작성한 후속 계획. 아래 신규 도구와 변경 계약은 **구현 예정**이다.  
시험용 플러그인: **미정**. 플러그인 종류를 결정하지 않아도 공통 실행·관찰 도구부터 구현한다.

## 1. 목표와 완료 단계

에이전트가 플러그인 기능을 실행하고, 서버 내부를 디버깅하고, 코드를 수정한 뒤 같은 조건에서 결과를 확인할 수 있게 한다. 소스 편집은 Codex의 기존 파일 도구를 사용하고, CraftMCP는 Minecraft 실행·관찰·검증을 담당한다.

두 완료 단계를 구분한다. 플러그인 제작 자동화 전체가 끝나야 v0라고 부르는 것은 아니다.

| 단계 | 목표 | 필수 완료 증거 |
|---|---|---|
| **v0: RCON·디버거 통합 실행** | 실제 MCP 호스트에서 RCON 명령 → 중단점 → 스택·변수 조회 → 재개 → 최종 RCON 응답 | 같은 서버·같은 실행을 연결한 증거, 대기 중 다른 도구 호출 가능, 중단 해제·정리, 중단점 없는 정상 재실행 |
| **v1: 제작·수정·재검증 반복** | 소스 작성/수정 → 빌드 → 배포 → 시험 → 결과 판정 → 필요시 수정 | 소스·빌드·배포 JAR 추적, 실제 상태 전후 비교, 수정 전 실패와 수정 후 통과, 반복 실행에서 상태 오염 없음 |

플레이어 클릭·GUI·렌더링은 별도 검증 범위다. 콘솔에서 같은 서비스 함수를 호출해도 실제 플레이어 입력을 검증한 것으로 기록하지 않는다. 화면·봇·리소스팩 지원은 선택한 플러그인이 요구할 때 확장한다.

## 2. 최신 GitHub 반영과 현재 구현

이번 동기화는 `089c688` → `5e76d27`의 1개 커밋을 `git pull --ff-only origin main`으로 반영했다. 직후 `HEAD...origin/main` 차이는 `0 0`이다. 원격 변경과 겹친 로컬 `tests/debugger_python/test_debugger.py`는 원격의 시험 안정성 수정과 로컬 fixture 값을 함께 보존했다. 개인 `.obsidian/`과 미추적 QA 스크립트는 보존했다.

### 2.1 이미 등록된 MCP 도구: 12개

공용 진입점은 `python -m mcp_server.server`이며, RCON/플러그인 도구 4개와 디버거 도구 8개가 같은 FastMCP 앱에 등록된다. 디버거 전용 `mcp_server.debugger_app`도 존재하지만, 통합 실험은 공용 앱을 사용한다.

| 도구 | 구현 위치 | 현재 제공 범위 | 남은 한계 |
|---|---|---|---|
| `list_players` | `mcp_server/server.py` | RCON `list`, 비동기 스레드 실행 | 원문 문자열 반환, 환경변수에 묶인 단일 RCON 대상 |
| `get_server_log` | `server.py`, `services/server_log.py` | cursor 기반 완성된 로그 줄 조회, 교체·잘림·재작성 감지 | 런타임/실행별 증거 연결 없음, 읽은 prefix 전체 재해시 비용 |
| `build_plugin` | `server.py` | Java 소스 맵 → 템플릿 복사 → Gradle → JAR | 리소스·명령 선언 입력 부족, 환경 Gradle 의존, 산출물 식별·빌드별 보존 부족 |
| `deploy_plugin` | `server.py` | JAR 백업·교체, RCON stop, 재시작 후 plugins 문자열 확인 | 종료 전 JAR 교체, 자동 재시작 배치 의존, 실제 활성화·JAR 일치 검증 부족 |
| `attach_debugger` | `tools/debugger.py` | 등록된 JVM에 연결 | RCON 런타임 등록과 통합 필요 |
| `set_breakpoint` | 동일 | 클래스·줄 중단점, 소스 경로 검증 | 생성/배포한 플러그인의 실제 프로젝트 매핑 필요 |
| `remove_breakpoint` | 동일 | 등록 중단점 제거 | 신규 재구현 불필요 |
| `get_debug_state` | 동일 | 세션·중단·이벤트 상태 조회 | 명령 job/run과 연결 필요 |
| `get_stack_trace` | 동일 | 현재 중단의 호출 스택 페이지 | 신규 재구현 불필요 |
| `get_variables` | 동일 | 지역 변수·필드·배열 조회 | 임의 식 평가·메서드 호출 도구로 확장하지 않음 |
| `resume_execution` | 동일 | 중단 재개, 이전 참조 만료 | 명령 job 최종 응답과 연결 필요 |
| `detach_debugger` | 동일 | 중단 해제와 디버거 분리 | 배포/서버 종료 서비스와 연동 필요 |

### 2.2 이번 pull로 들어온 QA 수정

다음은 더 이상 “미구현 결함”으로 중복 등록하지 않는다. 구현 사실과 검증 범위는 별개로 관리한다.

| 항목 | 반영된 코드 | 현재 확인 근거 |
|---|---|---|
| RCON 무응답·EOF 무한 대기 | `adapters/rcon.py`: 접속·인증·명령의 총 기한, EOF/패킷 검증, 소켓 종료, 자동 재전송 없음 | 코드와 `tests/test_server_qa.py`, 후속 QA 보고서 |
| RCON 대기 중 MCP 응답성 | `list_players`의 `asyncio.to_thread` | 모의 RCON + 실제 공용 stdio 회귀 시험 구현 |
| 로그 교체·잘림·재작성 | 파일 식별자·offset·SHA-256 cursor, reset 표시 | 로그 회귀 시험 구현 |
| 부분 UTF-8 손실 | 개행이 완성된 줄까지만 소비 | 부분 기록·이어 쓰기 회귀 시험 구현 |
| 디버거 시험의 환경·타이밍 의존 | 임시 외부 fixture, 이벤트 대기, attach 전용 짧은 기한 | 수정된 디버거 시험 |
| 헬퍼 종료 경합 | terminate/kill의 `ProcessLookupError` 처리 | 추가 회귀 시험 |

[2026-11-03 QA 후속 보고서](qa-followup-2026-11-03.md)는 RCON/로그/공용 MCP 10개, 디버거/Python/독립 JVM 28개 통과를 기록한다. 이는 해당 보고서의 실행 결과이며, 본 계획 작성만으로 실제 Paper·실제 MCP 호스트·클라이언트 전체 통과를 새로 주장하지 않는다. [이전 QA](qa-main-905820f-2026-10-27.md)는 과거 관찰 기록으로 유지한다.

## 3. 남은 문제와 우선순위

| ID | 우선순위 | 남은 작업 | 완료 기준 |
|---|---|---|---|
| R-01 | P0 | 실제 플러그인 명령을 실행하는 MCP 도구가 없음 | 허용된 명령을 job으로 실행하고 중단 중에도 상태·변수·재개 호출 가능 |
| R-02 | P0 | RCON은 환경변수, 디버거는 JSON registry로 대상 관리가 나뉨 | 같은 `runtime_id`가 서버 폴더·RCON·JDWP·프로젝트를 가리킴 |
| R-03 | P0 | 실제 Paper의 RCON→중단→재개→응답과 실제 호스트 사용 미검증 | 공용 MCP 앱에서 하나의 실행으로 증거 확보 |
| R-04 | P0 | RCON 기본 기한 5초와 디버거 중단 lease 기본 10초가 독립적 | 통합 시험용 총 기한에 중단·관찰·재개 시간을 포함하고 만료 동작 검증 |
| R-05 | P0/P1 | RCON은 현재 즉시 읽을 수 있는 패킷까지만 수집 | v0는 짧고 완료 표식이 있는 응답으로 제한·검증. 지연 다중 패킷 완전성은 P1에서 별도 구현 |
| R-06 | P1 | 빌드가 plugin.yml의 commands/permissions 등을 보존하지 않음 | manifest·resources를 검증해 반영하고 명령 등록 플러그인 빌드 가능 |
| R-07 | P1 | 실행 중 JAR 교체와 배치 자동 재시작 의존 | 종료 확인→교체→관리 시작→새 부팅·플러그인 활성화 확인 |
| R-08 | P1 | 빌드·배포·소스·실행 결과가 일관된 ID로 묶이지 않음 | build/deployment/run/job과 JAR 해시 추적 |
| R-09 | P1 | 게임 상태 조회·초기 상태 복원·assertion 경로 없음 | 선택한 시험이 요구하는 필드만 명시적으로 조회·복원·판정 |
| R-10 | P1 | 재시작 후 세션/작업·실패 배포의 복구 정책 부족 | 이전 세션 무효화, 실행 불명 처리, JAR 롤백과 게임 데이터 복구 분리 |
| R-11 | P1 | 사용 안내 일부가 현재 구현과 불일치 | 루트 `.venv` 실행, 공용 서버, 사용 중 FastMCP의 실제 도구 조회 API를 검증해 안내 |

P0는 v0 완료를 막는 항목이다. P1은 제작·배포 반복을 완료하기 위한 항목이다. P2는 필요가 확인된 뒤 추가한다. 기존 빌드/배포 도구가 있다는 사실만으로 P1을 완료 처리하지 않는다.

## 4. 추가 구현할 MCP 도구 목록

아래 이름과 입력은 제안 계약이다. 아직 호출 가능한 도구가 아니다. 구현 전에 JSON schema와 오류 코드를 고정하되, 단순 내부 함수를 전부 별도 MCP 도구로 노출할 필요는 없다.

### 4.1 P0: v0에 필요한 신규 도구 4개

| 도구 | 입력 초안 | 반환할 정보 | 필요한 이유 |
|---|---|---|---|
| **`list_runtimes`** | 없음 | `runtime_id`, 연결된 `project_id`, 관찰 기능 목록, 설정/관찰 구분 | 에이전트가 사용 가능한 서버·프로젝트를 추측하지 않게 함 |
| **`get_runtime_status`** | `runtime_id` | 프로세스/RCON/JDWP의 개별 상태, 관찰 시간, 디버거 중단, 알 수 있으면 `boot_id` | 연결 가능·실행 중·중단 중을 구분 |
| **`execute_command`** | `runtime_id`, `command_id`, `arguments`, `run_id`, `request_id`, 선택 `timeout_seconds` | 즉시 `job_id`, `queued/running`, 송신 여부 | 플러그인 시험 명령을 시작하고 디버거 조작을 계속 수행 |
| **`get_job`** | `job_id`, 선택 `cursor` | 단계·상태·원문·해석 결과·오류·증거 ID, 출력 pagination | RCON 응답 대기·완료·실행 불명을 별도 호출로 확인 |

`execute_command`는 임의 문자열을 그대로 실행하는 콘솔 도구로 만들지 않는다. 서버 측 manifest에 허용한 `command_id`를 실제 명령 템플릿으로 바꾸고, 인수 타입·길이·대상·namespace를 검사한다. `stop` 같은 관리 동작은 이 경로에 넣지 않고 수명 관리 서비스로 분리한다. 명령 목록 등록은 로컬 설정/파일 편집으로 시작하며 별도 등록 MCP 도구는 만들지 않는다.

#### `execute_command` / `get_job` 필수 동작

- MCP 요청은 RCON 완료까지 붙잡지 않고 job 등록 후 반환한다. RCON worker와 디버거 요청 처리기는 독립적으로 동작한다.
- job registry와 증거 기록을 먼저 남긴 후 송신한다. `request_id` 중복은 같은 runtime/명령/인수이면 기존 job을 반환하고, 내용이 다르면 거절한다.
- 등록된 같은 `runtime_id`의 변경 명령을 제한된 queue로 직렬화한다. queue 크기·동시 worker·출력 크기·보존 기간을 제한한다.
- queue 대기 기한과 실제 통신 총 기한을 구분한다. 전송 전 만료와 명령 송신 후 응답 유실을 구분한다.
- 중단·관찰·재개 시간을 RCON 기한에 포함한다. 예를 들어 통합 fixture에서는 명령 기한 45초, 중단 lease 10초로 시작해 남은 여유 시간을 실제 측정한다. 값을 무조건 크게 늘려 문제를 숨기지 않는다.
- 상태는 `queued`, `running`, `succeeded`, `failed`, `unknown`, `cancelled`를 기본으로 한다. 소켓 응답 수신만으로 게임 성공을 선언하지 않는다.
- `execution_status`는 최소 `not_sent`, `sent`, `confirmed`를 구분한다. 송신 중 실패처럼 전달 여부도 불명확하면 별도 `unknown`을 사용한다.
- `succeeded`는 등록한 명령별 완료 표식/파서로 확인한 명령 수준 성공이다. 실제 체력·인벤토리 등의 게임 결과는 후속 assertion으로 따로 확인한다.
- timeout/EOF/응답 파싱 불가/완료 표식 부재는 확인 가능한 정보에 맞춰 `failed` 또는 `unknown`이다. 게임 변경 명령을 자동 재전송하지 않는다.
- job 조회는 RCON을 다시 보내지 않는다. 중단 상태에서 RCON health check를 기다려야만 `get_job`이나 디버거 도구가 응답하는 구조를 금지한다.
- MCP 종료 시 worker·소켓·디버거를 기한 내 정리한다. 재시작 후 이전 실행 중 job은 증거를 읽고 `unknown`으로 복구하며 자동 재실행하지 않는다. 초기 저장은 작은 journal로 시작할 수 있다.

#### `get_runtime_status`의 판정 범위

- TCP 포트 열림, RCON 인증 성공, 플러그인 활성화, 게임 준비 완료는 서로 다른 필드다.
- JVM이 중단 중이면 기존 디버거 상태를 근거로 표시하고, RCON probe는 생략하거나 짧게 제한해 `unavailable_while_suspended`로 표시한다.
- PID가 없는 외부 서버는 `managed=false`로 표시한다. PID 재사용을 피하도록 관리 프로세스 시작 시각·명령/경로도 함께 검증한다.
- `boot_id`를 설정 파일의 고정 문자열만으로 현재 부팅 ID라고 주장하지 않는다. 실제 관찰할 수 없으면 null/unknown으로 남긴다.
- 런타임 조회 결과에는 비밀번호와 환경변수 원문을 포함하지 않는다.

### 4.2 P1: 제작·배포·상태 검증을 위한 신규 도구 8개

| 도구 | 입력 초안 | 반환/완료 기준 |
|---|---|---|
| **`get_plugin_status`** | `runtime_id`, `plugin_name` | 설치 JAR 경로·SHA-256, 관찰한 플러그인 이름/버전/enabled 상태, 배포 ID·부팅 연결. 관찰 불가능한 필드는 unknown |
| **`start_server`** | `runtime_id`, `request_id` | 관리 서버만 시작, `job_id`; 실제 준비 완료까지 추적. 중복 시작 차단 |
| **`stop_server`** | `runtime_id`, `request_id` | 정상 종료 job; 디버거 중단 정리→stop→동일 프로세스 종료 확인. stop 응답의 EOF만으로 종료 성공을 확정하지 않음 |
| **`rollback_plugin`** | `runtime_id`, `deployment_id`, `request_id` | 해당 배포의 검증된 이전 JAR를 정상 종료 후 복구, 새 부팅/활성화 확인. 월드·DB 복구와 구분 |
| **`get_game_state`** | `runtime_id`, `query_id`, 타입화된 `arguments`, `run_id` | 등록된 진단 명령이 실제 관찰한 체력·인벤토리·플러그인 상태 등. 필드별 출처·시각·지원 여부 |
| **`reset_test_state`** | `runtime_id`, `fixture_id`, `run_id`, `request_id` | 지정 시험의 엔티티·인벤토리·업무 상태 등만 기준 상태로 복원, job과 복원 후 관찰값 |
| **`get_run_evidence`** | `run_id`, 선택 `cursor`, `limit` | 명령·로그·디버거·빌드/배포·상태 assertion의 시간순 요약, 원문 참조, 미검증 항목 |
| **`cancel_job`** | `job_id` | queue 작업은 전송 전 취소, 실행 중 작업은 취소 요청/정리 상태와 실행 여부 반환. 이미 생긴 게임 변경을 되돌렸다고 주장하지 않음 |

구현 순서는 P0 이후 `get_plugin_status`와 관리 수명 서비스 → build/deploy 개선 → 게임 상태/fixture → 증거 요약으로 잡는다. `start_server`/`stop_server`는 먼저 deploy 내부 서비스로 구현하고, 별도 호출이 필요한 시점에 MCP 공개 도구로 등록해도 된다. 공개 도구 수를 늘리는 것 자체가 완료 조건은 아니다.

`get_game_state`는 모든 Minecraft/플러그인 상태를 자동 읽는 도구가 아니다. 기본 명령으로 조회 가능한 정보 → 시험 플러그인의 진단 명령 → 필요한 경우 최소 브리지 순서로 구현한다. 임의 코드 실행·Java reflection·디버거 메서드 호출을 일반 상태 조회로 사용하지 않는다.

`reset_test_state`는 `reset everything` 명령이나 무조건 월드 폴더를 삭제하는 도구로 만들지 않는다. fixture가 선언한 범위와 복원 결과를 검증한다. 월드 스냅샷 복원이 필요하면 서버 종료가 필요한 별도 fixture 단계로 다룬다.

### 4.3 P2: 선택한 플러그인에 따라 추가할 도구

| 후보 도구 | 도입 조건 | 검증 한계 |
|---|---|---|
| `list_test_scenarios`, `run_test_scenario` | 반복 수동 호출이 안정화되고 여러 시나리오를 묶어야 할 때 | 초기에 모든 도구를 감추는 단일 거대 도구로 만들지 않음 |
| `connect_test_client`, `disconnect_test_client` | 실제 플레이어 접속이 필요한 시험 | 봇 로그인·버전·환경 결정 필요 |
| `perform_client_action` | 우클릭·이동·GUI 클릭의 실제 입력 경로가 필요 | 내부 함수를 직접 호출한 결과와 구분 |
| `get_client_state` | 봇이 관찰한 인벤토리·위치·이벤트가 필요 | 프로토콜 봇은 실제 렌더링 증거를 제공하지 않음 |
| 실제 클라이언트 화면/영상 수집 | HUD·외형·파티클·리소스팩 검증이 필요 | 실게임 클라이언트와 촬영/입력 수단을 먼저 선정 |

게임 브리지, HTTP 서버, 별도 DB 서비스, 클라이언트 모드, 원격 MCP 서비스는 현재 필수 신규 구성 요소로 확정하지 않는다. 어떤 플러그인을 시험할지 정한 뒤 부족한 관찰 경로가 확인될 때 결정한다.

## 5. 기존 도구에 필요한 변경

### 5.1 공통 runtime·결과 계약

- RCON/로그/프로젝트/디버거 설정을 공통 runtime registry로 연결한다. 현재 debugger JSON schema는 허용 필드가 엄격하므로 RCON 필드를 무작정 추가하지 말고 버전이 있는 공통 설정과 호환 전환을 설계한다.
- 기존 `list_players`, `get_server_log`에 `runtime_id`를 추가한다. 기존 단일 서버 호출은 명시한 기본 runtime으로 호환할 수 있다.
- 현재 반환은 원문 문자열, JSON 문자열, dict가 섞여 있다. 후속 도구는 구조화된 결과를 사용하고 기존 호환성 변경은 문서화한다. MCP의 도구 호출 성공과 업무 결과 성공을 구분한다.
- 공통 필드: `schema_version`, `runtime_id`, `run_id`, `job_id`, `status`, `observed_at`, `result`, `error`, `warnings`, `evidence_ids`. 동기 조회에 없는 job/run 값은 null/생략 규칙을 정한다.
- 지원하지 않는 상태는 `unsupported`, 준비 안 된 런타임은 `runtime_unavailable`, 대상 불일치는 `invalid_target`, 전달 후 불명은 `execution_unknown` 등으로 구분한다. 중단으로 일시 관찰 불가인 경우를 평상시 서버 장애와 구분한다.
- 기존 디버거 세션 ID/중단 ID와 evidence의 run_id를 파괴적으로 교체하지 않는다. 공통 run과 기존 debugger evidence run을 명시적으로 매핑한다.

### 5.2 `build_plugin` 개선

1. 입력에 검증된 `plugin_manifest`, `resource_files`, 선택 `project_id`를 추가한다. Java 파일만 받는 현재 계약을 확장해 `plugin.yml`의 commands/permissions와 config 등 필요한 리소스를 보존한다.
2. 신뢰할 수 있는 템플릿의 Gradle Wrapper·의존성을 고정한다. 일반 제작 템플릿에는 현재 Wrapper가 없으며, `debugger_sample`의 Wrapper가 일반 템플릿 루트에 있는 것으로 가정하지 않는다.
3. 최초 의존성 준비 단계와 offline 빌드를 구분한다. 캐시가 없는 환경에 항상 `--offline`만 사용해 실패하는 안내를 피한다.
4. 동일 플러그인의 고정 디렉터리를 매번 삭제하는 대신 `build_id`별 작업 디렉터리를 만들고, 빌드 잠금으로 충돌을 막는다. 템플릿 전체 복사에 중첩 샘플·캐시·이전 빌드가 섞이지 않게 한다.
5. Java 17/Paper 1.20.1 기준의 디버그 정보를 포함한다. 실제 생성 class의 줄·지역 변수 정보를 검사하고 디버거 프로젝트 root/artifact 경로로 연결한다.
6. `build_id`, 소스 내용 해시, 소스 revision/dirty 상태, JAR 경로·SHA-256, 도구 버전, 빌드 출력 참조를 반환한다. 수정된 소스를 HEAD만으로 식별하지 않는다.
7. 오래 걸리는 빌드는 공통 job 실행기로 옮기고 `get_job`으로 조회한다. 취소/기한 만료 시 Gradle 자식 프로세스 정리를 확인한다. 실패 빌드의 이전 JAR를 성공 산출물로 재사용하지 않는다.
8. 경로는 정규화 후 작업 루트 포함 여부를 검사한다. 문자열 금지 API 목록은 샌드박스가 아니므로 생성 플러그인은 격리된 시험 서버에서 실행한다.

### 5.3 `deploy_plugin` 개선

제안 입력: `runtime_id`, `build_id`, `request_id`. 출력: `job_id`와 `deployment_id`. 파일명만으로 최신 산출물을 추측하지 않는다.

```text
runtime/build 검증 → 서버별 잠금 → 기존 JAR·부팅 정보 기록
→ 디버거 재개·detach → 서버 정상 종료 요청 → 실제 프로세스 종료 확인
→ 이전 JAR 백업 → 검증한 새 JAR 설치·해시 확인
→ JDWP 옵션을 포함한 관리 시작 → 새 부팅 확인
→ 해당 플러그인 활성화·버전 관찰 → smoke test → 배포 결과 기록
```

- 현재 `start.bat`의 무한 재시작 루프와 `start-debug.bat`의 단발 JDWP 실행이 분리되어 있다. 자동 배포에서 JDWP가 빠지거나 교체 전에 재시작하지 않도록 프로세스 소유자를 하나로 정한다.
- 자동 재시작 supervisor를 유지한다면 종료·교체 동안 재시작을 멈추는 제어가 필요하다. 권장 첫 구현은 CraftMCP 관리 프로세스가 직접 수명을 소유하는 방식이다.
- 관리하지 않는 외부 서버에는 현재처럼 start.bat 내용만 보고 배포하지 않는다. 자동 수명 제어가 불가능하면 `unmanaged_runtime`으로 거절하고 수동 절차를 제공한다.
- `plugins` 출력의 부분 문자열 포함은 활성화/정확한 버전 증거가 아니다. 시험에 필요한 진단 명령 또는 검증된 조회 경로로 enabled 상태를 확인한다. 디스크 JAR 해시만으로 실행 중 bytecode 해시를 단정하지 않는다.
- 재시작으로 로그가 바뀌므로 기존 숫자 offset을 재사용하지 않고 cursor/reset 정보를 따른다. 배포 단계·부팅·플러그인별 로그를 연결한다.
- 실패 시 교체 전/후와 서버 생존 여부를 기록한다. 롤백 시에도 종료→교체→시작을 지키고, 롤백 실패까지 결과로 반환한다. 월드/DB 마이그레이션은 JAR 복구만으로 취소되지 않는다.
- 서버 재시작 후 이전 디버그 세션·중단·객체 참조를 무효화하고 새 세션으로 연결한다.

### 5.4 RCON·로그·디버거 후속 보완

- RCON: 지연 다중 응답 수집 완료 정책을 실제 Paper에서 검증한다. 패킷 도착 간 임의 sleep만으로 완전성을 보장하지 않고, 대상 서버에서 지원되는 요청/응답 완료 판별 방식을 시험한다. 판별 불가 시 `response_complete=unknown`으로 남긴다.
- v0: 짧은 등록 fixture 명령과 명시적 완료 표식만 사용한다. 일반 긴 명령 응답 지원을 완료했다고 선언하지 않는다.
- 로그: 일반 `get_server_log`에는 cursor 처리가 들어왔지만 현재 `deploy_plugin`의 직접 offset 읽기는 별도로 교체해야 한다. 대용량 prefix 재해시·긴 한 줄·응답 바이트 한도는 측정 후 제한을 추가한다.
- 디버거: 기존 8개 도구를 유지한다. 첫 통합에서는 step/표현식 평가보다 중단 해제, 오래된 참조 거절, 작업 연결과 호스트에서 실제 동시 호출되는지 검증한다.
- 문서: `README.md`의 `mcp_server/.venv` 실행 예시와 루트 `.venv` 기준을 통일한다. `docs/testing.md`의 `mcp.list_tools()` 직접 호출 예시는 설치한 FastMCP에서 지원되는 API인지 확인 후 수정한다. 공용 앱을 두 별도 서버로 중복 등록하지 않도록 안내한다.

## 6. 권장 코드 작업 단위

아래는 새 구조 제안이며 존재하는 파일을 구현 완료로 오인하지 않는다. 대규모 재구성보다 공통 서비스 추출부터 진행한다.

| 작업 단위 | 기존/제안 경로 | 책임 |
|---|---|---|
| 공용 앱 | 기존 `mcp_server/server.py` | 도구 등록·설정 로드·수명 관리, 개별 구현은 점진 추출 |
| RCON 전송 | 기존 `adapters/rcon.py` | 유한 기한·단일 송신·패킷·소켓 정리 |
| 명령 registry/해석 | 신규 `services/commands.py` | 허용 명령 템플릿, 인수 검증, 명령별 완료 판정 |
| 공통 job | 신규 `services/jobs.py` | queue·취소·기한·journal·동시성·출력 페이지 |
| runtime | 신규 `services/runtime.py` | 공통 대상 등록, 관찰 상태, 관리 프로세스 수명 |
| 빌드·배포 | 신규 `services/plugins.py` | 기존 build/deploy 추출, 산출물·백업·복구 |
| 게임 시험 | 신규 `services/game_state.py` | 진단 query·fixture 복원·상태 assertion |
| 증거 연결 | 신규 `services/evidence.py` | 공통 run 인덱스와 기존 디버거 evidence 매핑 |
| 디버거 | 기존 adapters/services/tools/debugger | 현 구현 유지, runtime/job/evidence 연동만 추가 |

첫 단계부터 별도 웹 서버·브로커·대형 DB를 요구하지 않는다. job journal과 파일 기반 증거로 시작하고 동시성/복구 요구가 커질 때 저장 방식을 변경한다.

## 7. 구현 순서와 단계별 합격 조건

### 단계 A — 기준 고정과 pull 후 회귀 확인

- [x] 최신 origin/main fetch 및 fast-forward, 로컬 변경 보존.
- [x] 최신 QA 수정·기존 도구·실행 스크립트 확인.
- [ ] RCON/로그 회귀 10개와 디버거 28개를 현재 개발 환경에서 재실행하고 skipped 수와 증거를 기록.
- [ ] Java helper/fixture 빌드 산출물, Java 17, Python lock 환경을 확인.
- [ ] 공용 MCP 실행·도구 발견 문서를 실제 환경에 맞게 정리.

합격: 구현 목록과 시험 결과를 구분한 최신 기준표가 있고, 환경 실패/미실행을 제품 통과로 기록하지 않는다. 이전 QA의 28개/10개 통과 기록을 이번 실행 결과로 복사하지 않는다.

### 단계 B — v0 도구와 실제 통합

- [ ] 공통 runtime registry와 `list_runtimes`, `get_runtime_status` 구현.
- [ ] 명령 manifest/파서, job registry/기한/중복 요청 처리 구현.
- [ ] `execute_command`, `get_job` 구현.
- [ ] 응답 지연·EOF·송신 후 유실·중복 요청·종료 정리 회귀 시험.
- [ ] 기존 `debugger_sample`을 **통신 fixture**로만 사용해 실제 Paper 통합. 이것을 최종 제작 플러그인 선정으로 보지 않음.
- [ ] 실제 Codex 등 MCP 호스트에서 같은 흐름 재현.

합격 시나리오:

1. 대상 runtime/project와 배포된 fixture JAR를 확인한다.
2. 공용 MCP 앱에서 attach → breakpoint를 설정한다.
3. `execute_command`가 job을 반환한다. 명령에는 추적용 run 식별자를 포함한다.
4. job 대기 중 `get_debug_state` → `get_stack_trace` → `get_variables`가 응답한다.
5. `resume_execution` 후 `get_job`으로 완료 표식과 계산 결과를 확인한다.
6. 중단점 제거·detach 후 같은 조건에서 정상 속도 재실행한다.
7. lease 만료/호스트 연결 종료/명령 timeout 상황에서도 서버가 중단 상태로 남지 않는지 검증한다. 복구 실패는 사용자 조치가 필요한 실패로 남긴다.

이 단계에서는 자동 빌드/배포가 미완성이어도 수동으로 설치한 고정 fixture로 v0를 입증할 수 있다.

### 단계 C — 플러그인 제작·배포 경로

- [ ] manifest/resources 지원, Wrapper/Java 기준, build_id·해시·디버그 정보 보강.
- [ ] 관리 runtime의 시작·종료·부팅 식별과 플러그인 상태 조회.
- [ ] deploy를 build_id 기반 job으로 전환하고 종료 확인 후 교체.
- [ ] 정상 배포·실패 빌드·로딩 실패·재시작 실패·롤백을 격리 Paper에서 검증.
- [ ] 생성 프로젝트를 디버거 registry와 연결하고 배포 후 소스/JAR 불일치 탐지.

합격: 빌드된 JAR와 배포된 JAR 해시가 연결되고 새 부팅에서 대상 플러그인 enabled 상태를 관찰한다. 실패 빌드의 이전 JAR/다른 이름의 플러그인을 성공으로 오인하지 않는다.

### 단계 D — 선택한 플러그인의 기능 검증

- [ ] 아래 9절 기준으로 플러그인 종류·핵심 기능 1개만 선정.
- [ ] 상태 query/fixture/assertion 정의 후 `get_game_state`, `reset_test_state` 구현.
- [ ] 수정 전 실패 → 디버거 원인 근거 → 수정·재빌드·재배포 → 동일 fixture 통과.
- [ ] `get_run_evidence`로 한 실행을 추적하고 미검증 범위를 표시.
- [ ] 실제 플레이어 입력이 필요한 기능은 수동 실게임 체크 또는 선택한 클라이언트 연동으로 검증.

합격: “예외 없음”에 더해 실제 게임 상태가 기대값과 일치하고 비대상 상태는 바뀌지 않는다. 재실행 결과가 초기 상태 잔존·이전 로그·이전 JAR에 의존하지 않는다.

## 8. 테스트·증거 계획

### 8.1 기존 회귀 명령

저장소 루트 PowerShell에서 잠금 의존성을 준비한 뒤 실행한다. 실제 JVM 시험은 JDK 17과 helper/fixture 산출물이 필요하다. 상세 빌드 명령은 [테스트 안내](testing.md)를 따른다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_server_qa.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p "test_*.py" -v
```

새 통합 시험은 `tests/integration/test_rcon_debugger.py` 등의 전용 driver로 추가한다. 기존 `probe_paper.py`는 RCON=false인 독립 Paper 시험이므로 그 결과만으로 RCON 통합을 통과 처리하지 않는다.

### 8.2 신규 필수 시험

| 영역 | 검증할 내용 |
|---|---|
| 명령 job | 즉시 job 반환, 중단 중 동시 디버거 호출, 완료·timeout·EOF, 실행 불명, 중복 요청에서 변경 재송신 없음 |
| runtime | 잘못된 대상 거절, 서로 다른 RCON/JDWP 대상 혼동 방지, 중단 중 상태 조회, 외부 서버 관리 거절 |
| build | commands/resources 포함, 실제 명령 등록, 컴파일 실패, 산출물 혼동 방지, 줄·지역 변수 디버그 정보 |
| deploy | 종료 전 교체 없음, 중복 배포 잠금, JDWP 유지, 새 부팅 확인, 정확한 플러그인 상태, 실패 복구 |
| 관찰 | 로그 reset·페이지·한도, unsupported 상태, 실행 ID 없는 로그의 인과관계 단정 방지 |
| 게임 결과 | 전후 상태, 정확한 대상, 재시도 중복 효과 없음, fixture 복원, 정상 속도 회귀 |
| 실제 호스트 | 공용 stdio 등록·도구 발견·실행·오류 표시·도구 대기 중 후속 호출·종료 정리 |
| 클라이언트 | 해당 기능에 필요한 우클릭/GUI/화면을 실제 경로로 확인. 봇/수동/실게임 결과를 따로 기록 |

### 8.3 실행별 최소 증거

- 코드 revision과 dirty/source hash, template/빌드 환경, build_id·JAR SHA-256.
- runtime_id, 관찰 가능한 부팅 식별자, deployment_id, 공통 run_id와 debugger evidence run 매핑.
- 명령 템플릿 ID·검증된 인수·job_id, 송신 시각·완료 시각·원문·해석·실행 여부.
- 디버그 session_id·stop_id·breakpoint, 스택·확인한 변수·재개 결과.
- 로그 cursor와 reset 정보, 실제 상태 전후 값, 기대값·판정·unsupported/미검증 이유.
- 수동 클라이언트 체크를 했다면 조작·관찰·시각·화면 근거. 채팅/이름/비밀값은 필요한 범위만 보관.

여러 RCON 조회 결과를 같은 틱의 원자적 스냅샷이라고 하지 않는다. 파일·로그·객체 문자열은 관찰 데이터이며 에이전트 지시로 취급하지 않는다.

## 9. 시험용 플러그인 선정은 보류

이번 문서에서는 특정 플러그인이나 Hypixel 기능 복제를 확정하지 않는다. 먼저 다음 시험 계약을 작성할 수 있는 대상을 고른다.

| 선정 항목 | 결정할 내용 |
|---|---|
| 핵심 기능 | 첫 실험에서 검증할 기능 1개 |
| 실행 경로 | 콘솔 명령으로 재현 / 플레이어 입력 필수 / 둘 다 |
| 초기 조건 | 플레이어·엔티티·아이템·업무 상태·월드 범위 |
| 성공 기준 | 실제 관찰할 전후 값과 바뀌면 안 되는 값 |
| 의도적 결함 | 잘못된 대상·조건·계산·중복 실행 등 재현 가능한 결함 1개 |
| 내부 관찰 | 중단할 소스 위치, 확인할 변수·분기 |
| 외부 관찰 | 진단 명령·로그·플레이어·화면 중 필요한 수단 |
| 반복성 | 초기화 방법, 시간·난수·저장 데이터 통제 |
| 추가 자산 | 맵·리소스팩·DB·봇이 정말 필요한지 |

플러그인과 기본 게임 기능은 양자택일이 아니다. 플러그인이 기본 인벤토리·블록·엔티티·입력·표현을 조합하고 규칙을 추가한다. 리소스팩이 필요하면 자산 제작·배포·실제 클라이언트 표시 검증을 별도 작업으로 추가한다. 대규모 서버 네트워크·경제 백엔드는 첫 시험의 필수 조건이 아니다.

## 10. 우선 만들지 않을 것

- Codex에 이미 있는 범용 파일 읽기/소스 편집/셸을 복제한 도구.
- 임의 문자열 RCON, 임의 Gradle task/셸, 임의 Java 메서드 평가를 일반 도구로 공개하는 기능.
- 필요가 검증되지 않은 범용 Java 게임 브리지·HTTP API·DB 서비스.
- v0에 불필요한 debugger step/변수 수정·클라이언트 모드·화면 자동화.
- 운영 서버 무인 관리, 여러 서버의 대규모 매칭/경제 서비스, 무조건적인 자동 재시도.

## 11. 문서·산출물 정리

- 이 문서는 [초기 프로젝트 계획](project-plan.md)의 현재 구현·잔여 작업 후속 문서다. 초기 설계의 과거형 상태를 현재 상태로 오인하지 않는다.
- QA 수정의 근거는 [최신 QA 후속 보고서](qa-followup-2026-11-03.md), 실행 명령은 [테스트 안내](testing.md)를 따른다.
- 구현 PR마다 도구 schema/오류 코드·회귀 시험·실제 실행 증거·미검증 범위를 함께 갱신한다.
- `docs/mcp-setup.md`를 후속 작성해 공용 진입점·cwd·루트 .venv·설정/비밀값 위치·도구 발견·실제 호스트 smoke test를 기록한다. 로컬 Codex 설정과 비밀번호는 저장소에 커밋하지 않는다.
- 일정과 담당자는 팀의 현재 가용 인원을 확인한 뒤 배정한다. 이 문서는 과거 일정/4인 배분을 현재 약속으로 재사용하지 않는다.

## 12. 이번 문서 작성 작업의 검증 기록

최신 커밋의 코드·QA 보고서·시험·템플릿·실행 스크립트를 대조했다. 이번 작업은 pull과 후속 계획 작성이며 신규 도구 구현이나 서버 배포를 수행한 것은 아니다.

| 이번 재실행/확인 | 결과 | 범위 |
|---|---|---|
| `test_server_qa.py` | **10개 통과**, 18.529초 | 임시 파일·모의 루프백 RCON·실제 공용 stdio MCP. 실제 Paper 시험 아님 |
| `test_registered_external_runtime_is_read_only` | **1개 통과**, 0.140초 | 충돌 해결한 임시 외부 fixture 시험. 원격 안정성 변경과 로컬 fixture 값 보존 |
| Git 상태 | `HEAD...origin/main = 0 0`, 미해결 충돌 없음 | pull 완료. 문서와 보존한 로컬 변경은 미커밋 |
| 디버거 전체 28개·Java 자체 시험·실제 Paper·실제 사용자 MCP 호스트·게임 클라이언트 | **이번에는 미실행** | 과거 QA 결과와 이번 검증을 구분 |

재실행은 루트 `.venv`의 Python 3.12.15에서 수행했다. 제한 환경에서 대기한 비동기 시험 시도는 중단하고 일반 실행 환경에서 위 통과를 확인했다. 기존 QA 보고서의 Python 3.13/JDK 17 전체 시험 기록을 이번 환경의 결과로 복사하지 않았다.

충돌 해결한 시험의 재현 명령:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p test_debugger.py -k test_registered_external_runtime_is_read_only -v
```

문서의 상대 링크, 현재 도구 선언 12개의 목록 일치, `git diff --check`, 미해결 Git 충돌과 conflict marker 부재를 확인했다. 서버 시작·중지·JAR 교체·신규 도구 구현·커밋·push는 이번 작업에 포함하지 않았다.
