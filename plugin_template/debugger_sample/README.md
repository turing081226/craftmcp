# Paper 디버거 재현 샘플

JDK 17, Paper API 1.20.1, Gradle Wrapper 9.4.1. source/lines/vars 디버그 정보를 포함한다.

저장소 루트에서:

```powershell
.\plugin_template\debugger_sample\gradlew.bat -p plugin_template/debugger_sample build
```

JAR: `build/libs/craftmcp-debugger-sample.jar`.
콘솔 명령: `craftdebug calculate manual-001 7 3` → `damage=10`.
중단점: `io.github.craftmcp.debuggersample.DamageCalculator`, 7번 줄의 `return result;`.
인자·damage·result 객체·inputs 배열을 조회한다. RCON과 플레이어 입력은 거절한다.

RCON 통합용 명령은 별도다:

```text
craftdebugrcon calculate rcon-demo-001 7 3
```

`RemoteConsoleCommandSender`만 허용한다. 로컬 콘솔·플레이어·그 외 sender는
`REMOTE_CONSOLE_REQUIRED`로 거절하며 기존 `craftdebug`의 콘솔 전용 동작은 유지한다.
출력은 `CRAFTDEBUG_RCON v=1`으로 시작하고 최종 `status=ok|error ... end=1` 줄로 끝난다.
각 줄에 LF를 붙이며 Paper가 추가하는 빈 줄은 허용한다.

입력·출력 파서·B의 command_id/arguments·중단점·ID 연결·통합 driver는
[D → B 계약](../../docs/d-rcon-debugger-contract.md), 확인한 범위는
[검증 기록](../../docs/d-rcon-debugger-validation.md)을 따른다.
빌드의 JUnit 시험은 Paper API 인터페이스의 합성 sender를 사용한다. 실제 RCON은
`tests/integration/probe_rcon_paper.py`로 별도 검증한다.

빌드·배포·JDWP·MCP·실제 Paper 검증은 [Python 디버거 안내](../../docs/debugger-python.md)를 따른다.
