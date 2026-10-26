# 단독 Java 디버깅 샘플

Java 17에서 실행하는 반복 입력 샘플이다. Minecraft·RCON 없이 Java 헬퍼를 실제 JVM에 연결해 시험한다.

- `Debuggee`: stdin 명령을 기다린다. 첫 `run`까지 Calculator 클래스를 로딩하지 않는다.
- `Calculator`: `4 * 3 = 12`를 계산하고 객체·배열·순환 참조·긴 한글 문자열을 제공한다.
- `Calculator.java:16`: `BREAKPOINT_AFTER_CALC` 표식이 있는 실행 줄. 계산 결과를 읽는 중단점이다.
- `run`: 계산 한 번, `parallel`: 두 스레드 계산, `duplicate`: 별도 클래스 로더로 동명 클래스 로딩, `quit`: 정상 종료.

저장소 루트에서 `debugger_helper/gradlew.bat -p debugger_helper compileDebuggee`로 빌드한다. 클래스는 `debugger_helper/build/fixtures/debuggee/`에 생성된다. 전체 자동 시험은 `test` task다.

`compileDebuggeeWithoutVars`는 줄 번호만 있고 지역 변수 정보가 없는 변형을 만들어 debug_info_missing 응답을 확인한다. 실 JVM을 띄우는 Java 시험 드라이버는 `debugger_helper/src/test/java/io/craftmcp/debugger/JdiIntegrationTest.java`에 있다.

수동 실행과 요청 예시는 [Java 헬퍼 안내](../../../docs/debugger-java.md)를 따른다. 샘플 소스를 변경하면 위 줄 번호와 안내를 함께 갱신한다.
