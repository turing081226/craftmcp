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

빌드·배포·JDWP·MCP·실제 Paper 검증은 [Python 디버거 안내](../../docs/debugger-python.md)를 따른다.
