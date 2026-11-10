# D RCON·디버거 인수 검증 기록

실행일: 2026-11-10 (KST). 작업 사본 기준 `212f707` + 이 변경.
Python 3.12.15, Microsoft OpenJDK 17.0.18, Paper 1.20.1 build 196.
공유 계약/실행 명령은 [D → B 계약 v1](d-rcon-debugger-contract.md)을 따른다.

## 판정

RCON fixture·parser·중단점/기대값·run 증거 연결 및 실행 가능한 통합 driver를 제공했다.
실제 Paper/RCON/JDI/stdio MCP에서 핵심 흐름과 복구를 확인했다.
job은 D의 **시험용 in-memory seam**을 사용했다. B의 실제 queue/journal/취소/중복 처리나
공용 앱 연결까지 완료한 것으로 판정하지 않는다. B와 최종 tool envelope를 맞추는 절차가 남는다.

| 확인한 항목 | 결과/범위 |
|---|---|
| 샘플 Gradle build | 통과. Java 17, source/lines/vars 유지 |
| 샘플 JUnit | 1개 통과, skipped=0. 실제 Paper API 타입의 합성 RCON 허용, console/player/generic 거절, 정상·오류 출력 |
| D Python 시험 | 9개 통과. 엄격한 파서, fake driver, 실패 시 detach, ID 매핑, 실제 loopback timeout/EOF와 중복 요청 시 전송 1회 |
| 기존 디버거 Python/MCP/JVM 회귀 | 28개 통과, skipped=0. 실제 Java 헬퍼/JDI 시험 7개 포함 |
| 실제 Paper sender | 기존 콘솔 명령 성공, 기존 명령 RCON 거절, 새 명령 로컬 콘솔 거절, 새 명령 RCON 성공 |
| 실제 RCON 오류 | int32 합 overflow를 INTEGER_OVERFLOW terminal failure로 확인 |
| 실제 핵심 통합 | job pending → 실제 중단점 → 지역 변수·result·inputs → 재개 → 같은 job의 최종 damage=10 → remove/detach → 새 job 정상 재실행 |
| 참조 무효화 | 재개 뒤 이전 frame/object는 stale_reference; detach 후 새 attach에서 이전 session은 session_not_found |
| 실제 Paper lease | held stop 자동 해제 후 RCON 완료, 이전 stop 조회 거절 |
| 실제 Paper detach | 중단 상태에서 detach 후 원래 RCON 명령 완료 |
| 실제 MCP client 종료 | stop을 유지한 채 client를 닫고 독립 RCON 관찰자가 최종 성공 응답을 받음 |
| 실제 Paper 중단 중 강제 종료 | 이 driver의 격리 자식만 종료. helper disconnected, active_stop=null, 이전 stack 조회 vm_disconnected, 미완료 job unknown |
| 기본 흐름 + Paper 정상 종료 | 별도 run `rcon-31805477c6ca468faa1a1a5d6c6151ec`: status=passed, shutdown=normal. 최종 driver로 result의 base/bonus/runId도 검증 |

복구 포함 성공 실행:

```powershell
.\.venv\Scripts\python.exe tests/integration/probe_rcon_paper.py --server-dir mc_server --java 'C:\Users\OWNER1\.jdks\ms-17.0.18\bin\java.exe' --recoveries --terminate-target
```

run: `rcon-2f7251a2645e4122904e86d92def3b63`, 전체 status=`passed`,
shutdown=`expected_target_termination`.
로컬 원문: `.cache/rcon-debugger/<run_id>/flow-evidence.jsonl`,
`validation-summary.json`, `paper-console.jsonl`.
최초 debugger evidence run: `13c4251d0ae24da58dee3fb491c2c942`.
새 MCP 앱으로 수행한 target 종료 시험의 debugger run은 같은 통합 원문의 tool 응답에서 연결한다.

관찰 Paper JAR SHA-256:
`234a9b32098100c6fc116664d64e36ccdb58b5b649af0f80bcccb08b0255eaea`.
샘플 JAR SHA-256:
`5e0f6ddcf90138155a5be89ed4728fc7178b48870ce38ce8da50929833483a70`.
복사본/설치본의 해시 일치도 확인했다. JAR 해시만으로 JVM의 로드된 소스 일치를 단정하지 않는다.

## unknown으로 남긴 범위

- B의 실제 execute_command/get_job 및 공용 앱 lifespan, journal 복구·취소·총 queue 기한.
- A의 registry/관리 서버 종료 서비스와 C의 자동 build/deploy/rollback 연결.
- 실제 사용자 IDE/에이전트 MCP 호스트의 도구 발견·호출. 이번 것은 실제 stdio MCP client다.
- 실제 플레이어 접속·GUI·게임 상태. 플레이어 sender 거절은 합성 Paper API 타입으로만 시험했다.
- 네트워크 단절·기한 만료 후 실제 게임 변경 여부. timeout/EOF는 unknown으로 보존한다.
- 지연된 큰 다중 패킷 응답의 완전성. 이 fixture는 짧은 결과를 사용하는 범위다.

## 검증 중 수정한 사항

- RCON listener 로그가 Done보다 먼저 나올 수 있어 이미 관찰한 로그도 확인하도록 수정했다.
- JDWP address=0은 detach 후 새 포트를 선택했다. 시작 시 빈 포트를 선택하고 JVM에 명시해
  같은 runtime 주소로 재연결하도록 수정했다.
- Windows에서 0.2초의 loopback 기한은 인증 전에도 만료됐다. 2초 기한으로 명령 수신 후 timeout/EOF를
  확인하고 관찰 명령 개수가 정확히 1인지 시험했다.
- 합성 sender 시험을 표준 JUnit으로 등록해 Gradle build의 check/test 경로에서 실행한다.
- 회귀 job에는 이전 stop/session을 활성 ID로 붙이지 않고 최초 debug 실행의 binding을 별도로 보존한다.

초기 실패 실행은 성공 증거로 사용하지 않는다. `.cache`/`data/debugger`의 원문·서버 복사본·비밀번호와
JAR는 로컬 전용이다. 기존 사용자 서버를 종료하거나 기존 월드/플러그인을 변경하지 않았다.
