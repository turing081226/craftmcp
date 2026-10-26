# Java 헬퍼 ↔ Python 통신 계약 v1

작성일: 2026-10-13  
상태: 구현 전 기준 계약. 자체 프로토콜이며 DAP·JDWP의 메시지 형식이 아니다.

담당 배정은 [2인 분업안](debugger-work-split.md), 기능 범위는 [디버거 계획](debugger-plan.md)을 따른다. Java 헬퍼가 JVM에 JDI/JDWP로 연결하며, 이 계약은 Python과 헬퍼 사이에서만 사용한다.

## 1. 프로세스와 메시지

- Python이 `java --add-modules jdk.jdi -jar <헬퍼 JAR 절대 경로>`를 인자 배열로 실행한다.
- stdin/stdout은 UTF-8 JSON Lines다. 한 줄에 JSON 객체 하나를 출력하고 즉시 flush한다. JSON 문자열 안의 개행은 이스케이프한다.
- 헬퍼 stdout은 메시지 전용, stderr는 로그 전용이다. 여러 스레드의 출력은 직렬화해 한 줄이 섞이지 않게 한다.
- 한 프로세스는 동시에 JVM 세션 하나만 관리한다. detach 후 새 attach는 가능하며 새 session_id를 발급한다.
- 모든 메시지는 `protocol_version: 1`과 `type`을 가진다. 요청·응답에는 문자열 id, 이벤트에는 프로세스 내 단조 증가 정수 seq가 있다.
- ID는 불투명한 문자열이다. Python이 JVM 숫자 ID나 값의 의미를 해석하지 않는다. 이벤트 seq는 헬퍼 재시작 시 초기화된다.
- 응답과 이벤트가 섞여 올 수 있다. Python은 요청 ID로 응답을 연결하며 요청 제출 순서대로 응답한다고 가정하지 않는다.
- 정의되지 않은 method와 params 필드는 거절한다. 응답·이벤트의 부가 필드는 수신자가 무시할 수 있다. 버전 불일치는 연결 실패다.

시작 직후 헬퍼가 보내는 첫 메시지:

```json
{"protocol_version":1,"type":"event","seq":1,"event":"ready","session_id":null,"observed_at":"2026-10-13T00:00:00Z","data":{"helper_version":"0.1.0"}}
```

Python은 ready 확인 후 요청을 보낸다. 준비 대기와 요청 timeout은 설정으로 관리한다. 메시지 한 줄은 최대 1 MiB다. 이를 넘는 입력·출력은 정상 응답으로 해석하지 않는다. 조회 한도로 큰 출력 생성을 방지한다.

요청과 성공 응답:

```json
{"protocol_version":1,"type":"request","id":"req-1","method":"attach_debugger","params":{"host":"127.0.0.1","port":5005,"connect_timeout_ms":5000,"stop_timeout_ms":10000}}
{"protocol_version":1,"type":"response","id":"req-1","ok":true,"result":{"session_id":"session-1","connection_state":"connected","capabilities":{"stack_trace":true,"variables":true,"object_fields":true,"method_return_events":false}}}
```

오류 응답:

```json
{"protocol_version":1,"type":"response","id":"req-2","ok":false,"error":{"code":"stale_reference","message":"Reference belongs to a resumed stop.","details":{"session_id":"session-1","stop_id":"stop-1"}}}
```

성공 시 result, 실패 시 error만 포함한다. details는 기계 처리용 객체, message는 설명 문자열이다. 비밀 정보는 반환하지 않는다. 이벤트는 요청 응답을 대체하지 않는다.

## 2. 상태와 참조의 소유권

Java는 세션, JDI 연결 상태, 활성 중단, 프레임·객체 참조와 이벤트 순번을 관리한다. Python은 런타임·프로젝트 설정, 요청 ID와 증거 파일을 관리한다.

session_id와 stop_id는 재사용하지 않는다. frame_id/object_ref는 해당 session_id/stop_id에서만 유효하다. 재개·detach·VM 연결 단절 이후 이전 참조 요청은 거절한다. Python 캐시만으로 유효성을 판단하지 않고 Java에서도 검증한다.

get_debug_state는 Java의 현재 상태를 반환한다. Python은 이벤트와 응답의 도착 순서에서 상태를 추측해 덮어쓰지 않는다.

## 3. 메서드 입력과 결과

표의 입력은 명시한 선택 필드 이외 모두 필수다. ID 필드는 모두 문자열이다. JSON 정수·불리언·문자열을 구분한다. port 범위는 1~65535다. timeout 값은 예시이며 watchdog 설정과 함께 실제 환경에서 검증한다.

| method | params | result |
|---|---|---|
| `attach_debugger` | host:문자열, port:정수, connect_timeout_ms:양의 정수, stop_timeout_ms:양의 정수 | session_id, connection_state, capabilities |
| `set_breakpoint` | session_id, class_name:완전한 클래스명, line:양의 정수; 선택 source_path:문자열, class_loader_id:문자열 | breakpoint_id, status, locations, reason |
| `remove_breakpoint` | session_id, breakpoint_id | breakpoint_id, removed:true |
| `get_debug_state` | session_id | connection_state, active_stop, breakpoints, last_event_seq |
| `get_stack_trace` | session_id, stop_id, thread_id, offset:0 이상 정수, limit:양의 정수 | frames, next_offset, truncated |
| `get_variables` | session_id, stop_id, offset, limit; frame_id 또는 object_ref 중 정확히 하나 | variables, next_offset, truncated, warnings |
| `resume_execution` | session_id, stop_id | resumed_stop_id |
| `detach_debugger` | session_id | detached:true, released_stop_ids |
| `shutdown` | 빈 객체 | shutdown:true; 응답 후 프로세스 종료 |

MCP의 runtime_id/project_id는 Python에서 검증·변환하므로 헬퍼 attach 입력에 넣지 않는다. source_path는 표시 정보이며 Java가 지정 파일을 실행하거나 표현식을 평가하지 않는다. 여러 클래스 로더에서 같은 클래스명이 발견되면 ambiguous_class와 로더 ID 목록을 반환한다.

미로딩 클래스는 pending으로 보관한다. 로딩 시 실제 위치를 적용하고 breakpoint_changed로 알린다. 여러 로더가 이미 발견되면 로더 선택 없이 적용하지 않는다. 먼저 적용한 뒤 추가 로더가 발견되면, 로더를 명시하지 않은 중단점은 기존 적용을 해제하고 rejected와 모호성 사유를 알린다.

breakpoint status는 pending/verified/rejected다. locations는 `{class_name, method_name, source_name, line, class_loader_id}` 배열이며 적용 전에는 빈 배열이다. reason은 설명 문자열 또는 null이다. breakpoints는 위 정보에 breakpoint_id를 포함한 객체 배열이다. 없는 실행 줄은 rejected로 반환하고 임의로 다른 줄을 고르지 않는다. 없는 breakpoint 삭제는 breakpoint_not_found다.

connection_state는 connected/disconnected/terminated이며 스레드 중단 상태와 분리한다. active_stop은 null 또는 다음 객체다:

```json
{"stop_id":"stop-1","thread_id":"thread-1","reason":"breakpoint","breakpoint_id":"bp-1","suspend_policy":"event_thread","observed_at":"2026-10-13T00:00:01Z","expires_at":"2026-10-13T00:00:11Z"}
```

frames 항목은 `{frame_id, index, class_name, method_name, source_name, line}`이다. 정보가 없는 source_name/line은 null이다. index는 스택 선두를 0으로 한다. frame_id는 같은 stop 안에서 안정적으로 유지한다.

frame_id 조회는 가능한 인자·지역 변수와 this 참조를 반환한다. object_ref 조회는 메서드를 호출하지 않고 인스턴스 필드 또는 배열 요소를 반환한다. 변수 항목:

```json
{"name":"damage","kind":"primitive","type_name":"int","display_value":"10","object_ref":null,"has_children":false,"truncated":false}
```

kind는 primitive/string/null/object/array다. display_value는 표시 문자열이며 Python에서 자동으로 숫자형으로 변환하지 않는다. 자식 조회가 가능한 값에는 object_ref를 발급하고 그 외에는 null이다. 배열 요소 이름은 [0] 등으로 표기한다. static 필드 조회는 MVP에서 제외한다. 변수 메타데이터가 없으면 debug_info_missing과 대체 조회 정보가 있는 details를 반환한다.

페이지 끝의 next_offset은 null이다. stack/variables limit 상한은 각각 100, display_value는 4096문자다. limit 상한 초과는 invalid_params다. 페이지가 남거나 표시 값을 잘랐으면 truncated를 true로 한다. warnings는 설명 문자열 배열이다. 재귀 확장은 자동 실행하지 않으며 자식 참조를 별도로 요청한다.

조회 응답은 UTF-8 직렬화 후 512 KiB 이내로 유지한다. 한글 등 다중 바이트 문자열 때문에 항목·문자 수 상한만으로 메시지 크기를 보장하지 않는다. 필요하면 페이지를 일찍 끝내고 next_offset과 truncated를 반환한다. 개별 값도 적절히 줄이되 JSON 자체를 바이트 중간에서 자르지 않는다.

## 4. 이벤트

공통 필드는 ready 예시와 같다. ready 이외의 session_id는 관련 세션 ID다. observed_at/expires_at은 UTC ISO 8601 문자열이다. 실제 기한 판정은 Java의 단조 시계를 사용한다.

| event | data | 의미 |
|---|---|---|
| `ready` | helper_version | 입력 접수 시작 |
| `breakpoint_changed` | breakpoint_id, status, locations, reason | 중단점 적용 상태 변경 |
| `stopped` | active_stop 객체의 필드 | 새 중단, 참조 세대 시작 |
| `resumed` | stop_id, reason | reason=user/lease_expired/detach/shutdown/extra_stop_cleanup |
| `disconnected` | reason | reason=detach/vm_terminated/connection_lost/shutdown |

stopped의 data에 active_stop 객체를 중첩하지 않고 stop_id 등 필드를 직접 넣는다. 중단 기한 만료도 resumed를 발행한다. 추가 중단을 받으면 별도 stop_id를 부여하고 stopped 기록 뒤 해당 EventSet을 재개하여 extra_stop_cleanup으로 기록한다. get_debug_state의 active_stop은 조사 중인 기존 중단을 유지한다. 추가 중단의 참조는 즉시 무효화한다.

EventSet은 한 번만 재개하며 다른 주체가 만든 중단을 무조건 해제하지 않는다. 같은 EventSet에 여러 이벤트가 있으면 하나의 중단으로 처리한다. 중단 상태를 바꾸는 작업과 기한 만료 처리는 Java 내부에서 직렬화한다.

상태 변경 이벤트와 요청 응답 중 무엇이 먼저 오든 Python이 처리해야 한다. last_event_seq는 상태 스냅샷 시점까지 발행한 이벤트의 최대 seq다. 이벤트는 증거로 저장하고, 늦게 온 응답을 근거로 이전 중단 참조를 재유효화하지 않는다.

## 5. 오류와 종료

기본 오류 코드는 다음과 같다:

`invalid_params`, `unsupported_method`, `already_attached`, `attach_failed`, `session_not_found`, `breakpoint_not_found`, `breakpoint_unresolved`, `ambiguous_class`, `debug_info_missing`, `not_suspended`, `stale_reference`, `object_collected`, `vm_disconnected`, `resume_failed`, `internal_error`.

기한 만료·재개한 stop_id 또는 그 참조는 stale_reference다. 중단 없이 조회한 경우는 not_suspended다. 올바른 세션이 없으면 session_not_found, 연결이 끊긴 기존 세션이면 vm_disconnected를 우선한다. Java 예외 스택을 일반 결과에 무제한 포함하지 않는다.

stdin EOF와 shutdown은 보유 중단 정리, JDI detach, 프로세스 종료를 수행한다. shutdown 성공은 정리 후 반환하며 정리 실패를 성공으로 보고하지 않는다. Java 프로세스 강제 종료 시 JVM 동작은 실제 시험으로 확인한다.

Python은 EOF·이상 종료 시 미완료 요청을 backend_unavailable로 실패시킨다. 요청 timeout은 Python의 request_timeout이며 처리 미실행을 의미하지 않는다. attach/resume 등을 자동 재전송하지 않고, 가능하면 상태를 조회한다.

timeout 이후 늦게 도착한 응답은 요청 ID로 식별해 기록하고, 이미 실패로 반환한 요청을 다시 성공으로 전달하지 않는다. 그 사이 발생한 이벤트는 계속 수신·저장한다.

## 6. 필수 계약 시험

양쪽 담당자는 다음을 확인한다:

- ready와 protocol_version, UTF-8 문자열, JSON 문자열 안의 개행.
- 응답 대기 중 stopped 이벤트가 오는 경우.
- attach → 중단점 → stopped → stack → variables → resume → detach.
- 클래스 미로딩 pending → verified, 없는 실행 줄의 rejected.
- 변수 페이지·값 잘림, 같은 stop 안에서 참조의 안정성.
- 재개·자동 재개 후 이전 참조 거절, VM 연결 단절과 미완료 요청 실패.
- stdout 로그 혼입·잘못된 JSON·버전 불일치의 명확한 거절.
- EOF/shutdown에서 중단 정리. 모의 시험과 실제 JDI 시험은 구분한다.
