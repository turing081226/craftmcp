# A 담당 구현: Java·JDI 디버거 헬퍼

수정일: 2026-10-13

## 1. 제공하는 구현

Java 17 헬퍼가 게임 밖에서 실행되어 JDWP로 대상 JVM에 연결한다. [통신 계약 v1](debugger-protocol.md)의 8개 디버거 요청과 프로세스 관리용 `shutdown`을 지원한다. RCON, Python MCP, Paper 샘플 플러그인은 포함하지 않는다.

| A 작업 | 구현 내용 |
|---|---|
| A1 | Gradle Wrapper, UTF-8 JSON Lines, 준비 이벤트, 메시지·버전·입력 검증 |
| A2 | JDI SocketAttach, 세션당 JVM 하나, detach와 재접속 |
| A3 | 클래스명·줄 중단점, 미로딩 pending, 클래스 준비 이벤트, 실제 위치 확인 |
| A4 | 스택·인자·지역 변수·객체 필드·배열, 페이지 조회, 문자열 잘림, 참조 수명 |
| A5 | 중단 기한과 자동 재개, 추가 스레드 중단 정리, EOF·shutdown·VM 종료 처리 |

구현 경로는 `debugger_helper/`, 단독 Java 샘플은 `tests/fixtures/java_debuggee/`다. Java 헬퍼가 세션·중단·프레임·객체 ID의 최종 관리 주체이며 Python에서 이 ID를 다시 발급하지 않는다.

## 2. 빌드와 자동 검증

저장소 루트에서 PowerShell로 실행한다. 현재 터미널에서 Java 17을 사용해야 한다. 기존 JAVA_HOME이 다른 버전이나 없는 경로를 가리키면 이 터미널에만 올바른 경로를 설정한다.

```powershell
java -version
$env:JAVA_HOME = Split-Path (Split-Path (Get-Command java).Source -Parent) -Parent
$env:GRADLE_USER_HOME = Join-Path $PWD 'debugger_helper/.gradle-user-home'
.\debugger_helper\gradlew.bat -p debugger_helper --no-daemon test
```

검증에 사용한 Wrapper 직접 호출 방식도 동일한 Gradle 작업을 실행한다:

```powershell
java -cp debugger_helper/gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain -p debugger_helper --no-daemon test
```

Linux/macOS에서는 JDK 17을 선택한 뒤 `sh debugger_helper/gradlew -p debugger_helper --no-daemon test`를 실행한다. 이번 실행 검증은 Windows에서 수행했으며 다른 OS는 아직 검증하지 않았다.

Gradle 8.14.3과 Gson 2.13.2, JUnit 5.11.4를 고정했다. 처음 빌드할 때 네트워크로 배포 및 Maven 의존성을 내려받는다. 이후 캐시가 준비되면 `--offline`을 사용할 수 있다. Wrapper의 배포 ZIP SHA-256은 properties에 고정되어 있다. IDE와 Java 언어 서버는 필요하지 않다.

빌드 결과:

| 경로 | 내용 |
|---|---|
| `debugger_helper/build/libs/craftmcp-debugger-helper.jar` | Gson을 포함한 실행 JAR |
| `debugger_helper/build/fixtures/debuggee/` | 줄·지역 변수 디버그 정보를 포함한 단독 샘플 |
| `debugger_helper/build/fixtures/no-vars/` | 지역 변수 정보 누락 시험용 샘플 |
| `debugger_helper/build/reports/tests/test/index.html` | 자동 시험 HTML 보고서 |
| `debugger_helper/build/test-results/test/` | 기계 판독 가능한 시험 결과 |

빌드 캐시와 산출물은 `debugger_helper/.gitignore`로 제외한다. 공용 `.gitignore`와 B 담당 경로는 수정하지 않았다.

## 3. Windows에서 수동으로 단독 JVM 조사하기

두 개의 터미널을 저장소 루트에서 연다. 자동 시험은 포트 충돌을 피하려고 임시 포트를 사용하지만, 아래 수동 예시는 고정된 5005를 사용한다.

첫 터미널에서 샘플을 시작한다:

```powershell
java '-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:5005' -cp debugger_helper/build/fixtures/debuggee io.craftmcp.fixture.Debuggee
```

JDWP의 Listening 메시지와 샘플의 `READY`를 확인한다. 아직 `run`을 입력하지 않는다. Calculator 클래스가 첫 명령에서 로딩되므로 pending 중단점을 시험할 수 있다.

둘째 터미널에서 헬퍼를 시작한다:

```powershell
java --add-modules jdk.jdi -jar debugger_helper/build/libs/craftmcp-debugger-helper.jar
```

`ready` JSON 이벤트를 확인하고 다음 JSON 한 줄을 stdin에 입력한다:

```json
{"protocol_version":1,"type":"request","id":"req-1","method":"attach_debugger","params":{"host":"127.0.0.1","port":5005,"connect_timeout_ms":5000,"stop_timeout_ms":30000}}
```

응답의 session_id를 복사한다. 아래 `<session_id>`를 실제 값으로 바꿔 중단점을 등록한다:

```json
{"protocol_version":1,"type":"request","id":"req-2","method":"set_breakpoint","params":{"session_id":"<session_id>","class_name":"io.craftmcp.fixture.Calculator","line":16}}
```

현재 소스 `Calculator.java`의 **16번 줄**은 `BREAKPOINT_AFTER_CALC` 표식이 있는 출력문이다. 이 위치에서 `damage = base * multiplier` 대입과 테스트 변수 초기화가 끝나 있다. 소스를 수정하면 실제 줄 번호를 다시 확인한다. 자동 시험은 표식으로 줄을 찾아 소스 변경에 대응한다.

첫 터미널에 `run`을 입력한다. 헬퍼의 `stopped` 이벤트에서 stop_id와 thread_id를 복사한다. 중단 기한은 예시에서 30초이므로 조회 전에 자동 재개되면 `run`으로 다시 재현한다. 조회 요청은 기한을 연장하지 않는다.

```json
{"protocol_version":1,"type":"request","id":"req-3","method":"get_stack_trace","params":{"session_id":"<session_id>","stop_id":"<stop_id>","thread_id":"<thread_id>","offset":0,"limit":10}}
```

첫 프레임의 frame_id를 사용해 지역 변수를 읽는다:

```json
{"protocol_version":1,"type":"request","id":"req-4","method":"get_variables","params":{"session_id":"<session_id>","stop_id":"<stop_id>","frame_id":"<frame_id>","offset":0,"limit":100}}
```

기대값은 `base=4`, `multiplier=3`, `damage=12`다. this와 target은 같은 객체 참조다. values는 `{4,3,12}` 배열이며 large는 긴 한글 문자열이 잘려 표시된다. this의 object_ref로 get_variables를 다시 호출하면 label·samples·self·wide 필드를 읽을 수 있다. self는 기존 this와 같은 object_ref를 반환하므로 순환 객체를 자동으로 무한 확장하지 않는다.

```json
{"protocol_version":1,"type":"request","id":"req-5","method":"resume_execution","params":{"session_id":"<session_id>","stop_id":"<stop_id>"}}
```

첫 터미널에서 `RESULT damage=12 ...`가 출력되는지 확인한다. 이전 stop_id/frame_id/object_ref로 조회하면 stale_reference 오류가 나야 한다. 추가 계산은 `run`, 샘플 정상 종료는 `quit`이다.

헬퍼를 정상 종료하려면 다음을 입력한다:

```json
{"protocol_version":1,"type":"request","id":"req-6","method":"shutdown","params":{}}
```

## 4. B에게 전달할 실행·연동 정보

- 실행 파일: `debugger_helper/build/libs/craftmcp-debugger-helper.jar`의 절대 경로.
- 실행 인자: `java`, `--add-modules`, `jdk.jdi`, `-jar`, JAR 경로를 각각 별도 인자로 전달한다.
- 입력·출력: UTF-8 JSON Lines. stdout은 JSON 메시지만, stderr는 진단 로그만 사용한다.
- 먼저 ready를 기다리고 protocol_version=1을 확인한다. 이벤트와 응답을 구분하고 id로 응답을 연결한다.
- 8개 메서드는 계약의 이름을 그대로 사용한다. runtime_id/project_id는 Python에서 검증한 뒤 host/port로 변환한다.
- 중단 기한은 attach의 stop_timeout_ms로 설정한다. expiry 후 resumed 이벤트가 발생하며 프레임·객체 참조는 무효화된다.
- 메타데이터 없는 프레임은 debug_info_missing이다. details의 argument_values와 this_value는 가능한 대체 조회 자료다. 대체 인자는 argument_next_offset으로 페이지를 표시한다.
- 없는 실행 줄은 rejected이며 다른 줄로 이동시키지 않는다. 클래스 미로딩은 pending, 복수 로더의 모호성은 ambiguous_class 또는 rejected로 알린다.
- 잘못된 JSON·UTF-8·프로토콜 envelope는 stderr 진단과 종료 코드 2로 연결을 닫는다. 정상 envelope의 잘못된 params나 메서드는 오류 응답을 보내고 계속 접수한다.
- detach 이후 동일 JVM이 포트를 다시 열었는지 확인한다. 테스트처럼 address=0이면 재접속 포트가 달라질 수 있다. 헬퍼는 자동 재접속하지 않는다.
- EOF는 중단 정리·detach·종료를 수행한다. Python은 stdout을 계속 소비하고 프로세스 종료 시 진행 중 요청을 실패 처리한다.

API·메시지 형식은 [통신 계약](debugger-protocol.md)을 따른다. 이 구현을 위해 공유 계약이나 B 담당 파일을 변경하지 않았다.

## 5. 검증 범위와 남은 공동 작업

2026-10-13 Windows 11 / Temurin 17.0.20.1에서 `--no-daemon --offline test`를 실행해 **16개 시험을 모두 통과**했다. 이 중 13개는 실제 JVM 프로세스를 사용하는 통합 시험, 3개는 전송·입력 검증 시험이다. 최종 결과는 BUILD SUCCESSFUL이며 실제 Paper·MCP 통합 결과와는 구분한다.

자동 시험은 모의 JVM이 아닌 **별도 Java 프로세스의 실제 JDWP/JDI 연결**을 사용한다. 다음을 검증한다:

- pending 중단점 적용과 계산 이후 `damage=12` 확인.
- 스택·인자·지역 변수·객체·배열 조회, 같은 중단에서 참조 안정성.
- 한글·개행 문자열, 4096문자 표시 제한, 큰 UTF-8 응답의 페이지 크기 제한.
- 재개·detach 이후 참조 거절과 새 세션 재접속.
- 중단 기한 만료, EOF·shutdown·detach로 멈춘 실행 복구.
- 추가 스레드 중단 정리와 원래 중단 유지.
- 같은 위치의 복수 중단점이 하나의 중단으로 처리되고 한 번만 재개됨.
- 지역 변수 정보 누락, 없는 실행 줄, 중복 클래스 로더, 잘못된 입력.
- 중단 중 잘못된 JSON으로 헬퍼가 종료될 때도 대상 JVM의 실행 복구.
- JVM 정상 종료 감지와 헬퍼 강제 종료 후 대상 JVM의 실행 복구.

실제 Paper와 MCP 호스트에서의 시험은 B의 후속 통합 작업이다. 단독 샘플 통과를 Minecraft 게임 결과 검증으로 확대하지 않는다. Linux/macOS, 원격 JVM, 여러 로더의 복잡한 플러그인 구성도 별도 검증이 필요하다.

헬퍼는 메서드 호출·표현식 평가·변수 변경·step·hot swap을 제공하지 않는다. JDI 문자열 값과 필드만 읽으며 대상 객체의 getter나 toString을 실행하지 않는다. 객체가 다른 스레드에서 변경되면 전체 JVM의 동일 시점 스냅샷으로 해석할 수 없다.
