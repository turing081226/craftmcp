# 결정 사항
- 마인크래프트 버전: 1.20.1
- Paper: 1.20.1 빌드 196 (STABLE), 서버용 paper-1.20.1-196.jar
- Java: 17 (Temurin) (`winget install EclipseAdoptium.Temurin.17.JDK`)
- paper-api: 1.20.1-R0.1-SNAPSHOT, plugin.yml api-version '1.20'
- 빌드 도구: Gradle
- MCP 서버 언어: Python

## JVM 디버거 개발 방식 (2026-10-13)

- 백엔드: JDK 17의 JDI를 사용하는 외부 Java 헬퍼. Python과는 UTF-8 JSON Lines로 통신한다.
- 선정 이유: Java 전용 최소 기능에 집중하고, IDE·Java 언어 서버 설치 없이 로컬에서 실행하는 구성을 우선한다.
- 직접 구현할 부분: 이벤트 수신, 중단점 위치 관리, 세션·참조 수명, 재개·종료 처리. 선정은 설계 결정이며 구현·실제 Paper 검증은 아직 완료하지 않았다.
- 개발 범위: RCON 담당 작업과 독립적으로 개발한다. 단독 Java 샘플과 수동 서버 콘솔 명령으로 검증하며, RCON 연동은 후속 공동 통합이다.
- 비교 근거와 상세 계획: [JVM 디버거 개발 계획](debugger-plan.md).
- 디버거 2인 분업: A는 Java·JDI 헬퍼와 단독 Java 샘플, B는 Python·MCP 연동과 모의 헬퍼·Paper 샘플을 맡는다. [담당 범위](debugger-work-split.md)와 [통신 계약 v1](debugger-protocol.md)을 기준으로 병렬 진행한다.
