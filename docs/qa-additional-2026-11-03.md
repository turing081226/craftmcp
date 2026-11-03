# 추가 QA — 2026-11-03

후속 구현과 검증 결과는 [추가 QA 수정](qa-remediation-2026-11-03.md)을 따른다.
아래 내용은 수정 전 `5e76d27`의 재현 기록이다.

## 판정과 범위

대상은 `main`의 `5e76d27`이다. 시작 시 작업 트리는 깨끗했다. 원격 fetch는 수행하지 않았다.
추가 시나리오 7개에서 **새 결함 5건과 기존에 한계로 기록한 결함 1건**을 재현했다.
완성된 RCON 응답 직후 EOF 시나리오는 이번 5회 실행에서 재현되지 않았다.

제품 소스는 수정하지 않았다. 재현 드라이버 `eval/qa_additional.py`와 이 보고서만 추가했다.
빌드·배포는 저장소 `.cache` 아래 테스트 전용 파일과 모의 Gradle/RCON 응답을 사용했다.
통신 시험은 임시 루프백 포트만 사용했다. 기존 Paper 서버·월드·플러그인은 변경하지 않았다.
환경은 Windows, Python 3.13.2, Temurin JDK 17.0.20.1이다.

| 기준 시험 | 결과 |
|---|---|
| 기존 RCON·로그·공용 MCP 회귀 | 10개 통과 |
| 기존 디버거 전체 | 28개 중 27개 통과, 1개 오류, skipped=0 |
| 오류 난 실제 JVM lease 시험 단독 재실행 | 1개 통과. 전체 실행의 실패를 지우거나 전체 통과로 판정하지 않았다. |
| 추가 시나리오 드라이버 | 7개 중 새 결함 5건·기존 한계 1건 재현, 정상 응답 후 FIN 결함 미재현 |
| 공백 검사 | `git diff --check` 통과 |

## 발견 사항

### P1-1. Windows 소스 경로 검사 우회로 빌드 폴더 밖 파일 쓰기

- 위치: `mcp_server/server.py:209`, `:234`.
- Windows에서 `\folder\Probe.java`처럼 드라이브 없이 루트부터 시작하는 경로는
  `Path.is_absolute()`가 false다. 현재 검사를 통과하지만 `java_root / relative_path`는
  원래 빌드 루트를 벗어난 드라이브 루트 기준 경로가 된다.
- 재현: QA 전용 임시 폴더 안에서 빌드 폴더의 바깥을 가리키는 rooted 경로를 전달했다.
  `input_is_absolute=false`, `wrote_outside_build_root=true`를 확인했다.
  Gradle 실행 전 이미 파일이 생성됐고, 최종 도구 응답은 Gradle 없음으로 `ok=false`였다.
- 영향: 입력 검사가 허용한 소스 파일이 도구 프로세스의 쓰기 권한 안에서 다른 위치에 기록될 수 있다.
  실제 시험에서는 소유한 임시 폴더 밖에 쓰지 않았다.
- 수정: drive/root/anchor가 있는 입력을 거절하고, 목적지의 resolve 결과가
  resolve한 Java 소스 루트 내부인지 검증한 후에만 폴더·파일을 변경한다.
  Windows rooted 경로·drive-relative 경로·일반 상대 경로를 회귀 시험에 포함한다.

### P1-2. 서버 정지 실패 전 JAR 교체, 실패 응답에서 복구 정보 누락

- 위치: `mcp_server/server.py:377`, `:381`, `:420`.
- 재현: 기존 JAR와 다른 새 산출물을 준비하고 RCON `stop`에서 연결 오류를 발생시켰다.
  `stop` 진입 전에 이미 대상이 새 JAR였으며, `ok=false` 반환 후에도 새 JAR가 남았다.
  백업 파일은 생성됐지만 실패 응답에 `backup_path`가 없었다.
- 영향: 실패 결과만으로 실제 배포 파일 상태를 판단할 수 없다. 아직 실행 중인 JVM의 JAR를
  먼저 교체하려 하므로 플랫폼의 파일 잠금에도 영향을 받는다.
  [프로젝트 계획](project-plan.md)의 디버거 정리 → 서버 종료 확인 → JAR 교체 순서와도 다르다.
- 수정: 접속·산출물·실행 구성을 사전 검증하고 새 산출물을 별도 staging에 준비한다.
  디버거 중단·연결을 정리하고 대상 서버의 종료를 관찰한 뒤 교체·재시작한다.
  실패 응답에는 완료 단계, 파일 변경 여부, 백업 위치와 실행 여부 불명 상태를 포함한다.
  응답 유실을 정지 미실행으로 단정해 재전송하거나 상태 확인 없이 되돌리지 않는다.

### P1-3. 다른 플러그인 이름도 배포 성공으로 인정

- 위치: `mcp_server/server.py:388`.
- 재현: `QAProbe` 배포 후 `plugins` 응답으로 `Plugins (1): QAProbeHelper`만 반환했다.
  도구는 부분 문자열 일치로 `ok=true`를 반환했다.
- 영향: 대상이 로드되지 않아도 성공으로 보고할 수 있다. 현재 검사는 활성 상태나
  실제 배포 산출물·서버 재시작 여부도 확인하지 않는다.
- 수정: 대상 이름을 정확히 식별하고 enabled 상태를 검증한다. 서버 부팅 식별과
  산출물 해시/버전 관찰을 함께 남긴다. 별도 확인 수단이 없으면 성공 대신 확인 불가로 반환한다.
  비슷한 이름만 존재하는 경우와 대상이 disabled인 경우를 시험한다.

### P1-4. 배포 후 로그 검사에서 새 오류 앞부분을 건너뛰고 성공 반환

- 위치: `mcp_server/server.py:379`, `:405`.
- 재현: 이전 로그 `old\n`의 크기 4를 저장한 뒤 재시작을 모의해 로그를
  `ERROR: plugin failed to enable\nDone\n`으로 바꿨다.
  도구가 읽은 내용은 `R: plugin failed to enable\nDone\n`이었고,
  `errors=[]`, `ok=true`를 반환했다.
- 원인: `get_server_log`의 cursor 수정과 별도로, 배포 도구는 여전히
  이전 파일의 숫자 offset을 새 파일에 적용한다. 새 로그가 offset보다 크면 앞부분이 빠진다.
- 수정: 재시작 전후 로그의 파일·부팅 식별을 확인하고 새 부팅 로그는 처음부터 조사한다.
  공통 로그 서비스의 교체 감지 로직을 재사용하되, 재시작 경계의 오류를 놓치지 않도록 한다.
  첫 줄의 오류, 교체 후 더 큰 로그, 같은 파일의 truncate/rewrite를 검증한다.

### P1-5. 빌드가 공용 MCP 이벤트 루프를 차단

- 위치: `mcp_server/server.py:191`, `:267`.
- 재현: 실제 공용 MCP 클라이언트로 `build_plugin`을 호출하고 Gradle 실행만 0.5초의
  모의 작업으로 대체했다. 그 작업이 예약한 이벤트 루프 callback이 **0.503초 뒤** 실행됐다.
- 영향: 빌드 중 다른 도구 요청과 디버거 제어가 지연된다. 실제 빌드는 최대 180초까지
  동기 대기한다. `deploy_plugin`의 sleep·RCON·파일 작업도 같은 동기 도구 형태다.
  이번 측정은 빌드 경로를 대상으로 했으며 배포의 지연 시간을 직접 측정하지는 않았다.
- 수정: 동기 외부 작업을 이벤트 루프 밖에서 실행하고, 빌드·배포를 작업 ID와 상태 조회가
  있는 방식으로 관리한다. 단순 thread offload 후에는 동시 빌드·배포 간 공유 경로·잠금,
  취소 시 프로세스와 스레드 정리도 검증해야 한다.
  빌드·배포 대기 중 실제 디버거 조회·재개 호출이 완료되는지 시험한다.

### P2-1. 지연된 RCON 다중 응답 누락 — 기존 한계 재확인

- 위치: `mcp_server/adapters/rcon.py:74`.
- 재현: 첫 응답 패킷 `first-` 뒤 150ms 후 두 번째 `second`를 전송했다.
  기대 결과 `first-second` 대신 **5회 모두 `first-`만 성공 응답으로 반환**했다.
- 원인: 현재 읽을 데이터가 없으면 명령 응답 전체가 끝났다고 판단한다.
  [직전 수정 기록](qa-followup-2026-11-03.md)에서 수정 범위 밖으로 기록한 한계다.
- 수정: 대상 Minecraft/Paper 구현에서 응답 완료를 판별할 수 있는 프로토콜 전략을
  검증한 뒤 적용한다. 임의의 짧은 sleep으로 완료를 추정하지 않는다.
  완료를 확인하지 못하면 완전한 성공 응답으로 반환하지 않도록 한다.

### 테스트 안정성. 실제 JVM lease 시험이 중단 구간을 놓침

- 위치: `tests/debugger_python/test_java_helper.py:263`, `:147`.
- 전체 실행에서 `test_lease_expiry_releases_actual_jvm`이 active_stop 상태를 기다리다 시간 초과했다.
  실패 실행의 증거 `data/debugger/f3bb174622724dd49c15b8b033abcdb8/evidence.jsonl`에는
  같은 stop_id의 `stopped`와 `resumed(reason=lease_expired)`가 모두 있다.
  중단 기한은 500ms였고, 상태 조회 응답에서 active_stop을 관찰한 횟수는 0이었다.
- 소스를 변경하지 않고 해당 시험만 재실행했을 때는 통과했다. 증거:
  `data/debugger/b4701be23261426db7b9a90b2e809af4/evidence.jsonl`.
- 판정: 이번 결과는 상태 조회가 짧은 중단 구간을 놓친 시간 의존성으로 판단한다.
  단독 통과만으로 안정성이 해결됐다고 보지 않는다. JVM의 자동 재개 기능 실패로도 단정하지 않는다.
- 수정: 중단 이벤트로 동기화하고 프레임 조회를 마칠 충분한 lease를 확보한다.
  자동 재개 검증은 같은 stop_id의 만료 이벤트·실제 결과 완료·참조 만료를 함께 확인한다.

## 실행 증거와 재현

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_server_qa.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p "test_*.py" -v
.\.venv\Scripts\python.exe eval/qa_additional.py
```

추가 드라이버는 결함이 재현되면 `defect-reproduced`와 함께 **종료 코드 1**을 반환한다.
제품 전체 통과를 의미하는 시험이 아니다. 예상치 못한 드라이버 오류는 traceback으로 구분한다.

- 구조화 결과: `.cache/qa-additional-results.json`.
- 추가 시나리오 로그: `.cache/qa-additional-driver.log`.
- 기존 RCON·로그 회귀 로그: `.cache/qa-additional-baseline-server.log`.
- 기존 디버거 회귀 로그: `.cache/qa-additional-baseline-debugger.log`.
- 실패한 lease 시험 단독 재실행 로그: `.cache/qa-additional-lease-rerun.log`.
- 임시 프로젝트는 드라이버 종료 시 정리하며, 결과 로그는 로컬에 보관한다.

완성된 RCON 응답 뒤 정상 FIN을 전송하는 시험에서는 5회 모두 `complete-response`를 받았다.
이 경로에서 오류가 날 수 있다는 추정은 이번 결함 목록에 포함하지 않았다.

실제 Paper에서 배포·재시작·재연결, RCON 명령 → 중단 → 재개 → 최종 응답,
게임 클라이언트 화면·HUD와 사용자 IDE 호스트는 이번 시험 범위에 포함하지 않았다.
