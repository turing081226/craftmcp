# A·B·C·D 공용 MCP 통합 — 2026-11-17

## 변경 범위

main `887318c`의 B/D, A `782d086`, C `7f79bfd`를
`codex/integrate-abcd-20261117`에서 연결했다. 파일 병합에 더해 다음 실행 경계를 구현했다.

- 공용 앱에 도구 21개를 한 번씩 등록한다. runtime 4개와 C의 build/deploy/status/rollback을 연결한다.
- B의 단일 JobManager가 명령·수명 관리·빌드·배포·롤백의 queue, ID, 중복 요청, 총 기한, 조회와 취소를 맡는다.
- 일반 execute_command는 기존 allowlist만 허용하고, 명시적으로 바인딩한 runtime별 RCON endpoint로 보낸다.
- C ManagedPluginService는 A의 같은 async task와 operation token 안에서 종료 확인 → JAR 교체 → 새 부팅 → 정확한 활성화를 확인한다.
- stop/deploy는 해당 runtime의 debugger만 해제한다. 상태 조회를 위해 JDWP에 새 접속을 만들지 않는다.
- 관리 경로는 jobs → runtime → debugger 순서로 종료한다. 외부 서버만 있는 기존 경로의 debugger → jobs 순서는 유지한다.
- C 시험을 루트 discovery에 포함했다. 기존 Python build/deploy 보조 함수는 MCP 도구로 중복 등록하지 않는다.

## 로컬 설정

Python `.venv`와 JDK 17을 사용한다. 기존 debugger.local.json의 프로젝트/runtime ID와 JAR/JDWP 주소를 유지한다.
빌드의 java/javap도 debugger 설정의 java_executable을 사용한다. 시스템 PATH의 Java 25와 분리할 수 있다.

```powershell
Copy-Item runtime.example.json runtime.local.json
$env:DEBUGGER_CONFIG = 'debugger.local.json'
$env:CRAFTMCP_RUNTIME_CONFIG = 'runtime.local.json'
.\.venv\Scripts\python.exe -m mcp_server.server
```

비밀번호는 password_env가 가리키는 환경변수로만 제공한다. runtime.local*.json은 Git에서 제외한다.
예제는 managed=false다. 수동 시작한 서버는 조회/명령/디버거 대상으로 사용하고 start/stop/deploy/rollback은 거절한다.
기존 PID나 paper_runner 프로세스를 자동 채택하지 않는다. 관리할 전용 서버만 managed=true로 명시한다.
MC_RUNTIME_ID/MC_SERVER_DIR를 제공하면 같은 등록 대상·폴더인지 검사하고, 명시적 RCON 설정과 MC_*가 다르면 거절한다.

## 도구 계약

build_plugin은 기존 세 필수 인자에 manifest/resources/project/offline/request/run/timeout을 추가로 받는다.
즉시 job_id를 반환한다. get_job의 parsed_output에 build_id, JAR 해시, 실제 javap 디버그 정보와 로그 경로를 남긴다.

```json
{"plugin_name":"MyPlugin","main_class":"local.MyPlugin","source_files":{"local/MyPlugin.java":"..."},"plugin_manifest":{"version":"1.0"},"request_id":"build-001","run_id":"run-001","timeout_seconds":180}
```

deploy_plugin은 이름으로 최신 JAR을 추측하지 않고 정확한 성공 build_id를 받는다.

```json
{"runtime_id":"paper-local","build_id":"<build_id>","request_id":"deploy-001","run_id":"run-001","timeout_seconds":120}
```

rollback_plugin은 같은 runtime의 deployment_id를 받는다. 이전 백업과 현재 설치본 해시를 확인한다.
시작 실패 후 정지한 소유 서버도 복구할 수 있다. 월드/DB를 복원하지 않는다.

```json
{"runtime_id":"paper-local","deployment_id":"<deployment_id>","request_id":"rollback-001","run_id":"run-001"}
```

start_server/stop_server도 runtime_id/request_id와 선택적 run_id/timeout_seconds를 받고 같은 get_job으로 조회한다.
동일 입력의 request_id는 같은 job, 다른 입력은 충돌 오류다. 중복 방지는 현재 프로세스 메모리 범위다.
queue 대기도 총 기한에 포함한다. 실행 전 취소는 cancelled/not_sent, 실행 중 취소·기한 만료·시작 실패 등
판정 불명은 unknown으로 남긴다. unknown인 변경 작업을 자동 재전송하지 않는다.
블로킹 빌드는 소유 worker 정리까지 기다린다.

## 상태·파일 교체·복구

설치 JAR·해시와 JVM의 활성화 관찰을 구분한다. runtime_bytecode_sha256는 null이다.
샘플의 RCON 전용 craftmcpstatus는 정확한 plugin name/version/enabled를 반환한다.
문자열은 UTF-8 URL-safe Base64로 인코딩하며, 조회 앞뒤의 A boot_id가 같을 때만 현재 부팅에 연결한다.
다른 이름/boot·불완전 응답·진단 명령 미설치는 unknown이다. 배포 대상에는 진단 샘플이 별도로 있어야 한다.
단순 plugins 부분 문자열이나 디스크 JAR 일치를 실행 성공으로 주장하지 않는다.

Windows에서 종료 후 JAR 교체가 WinError 5로 한 차례 거절되어 기존 2초 재시도를 보강했다.
알려진 파일 공유/잠금 오류만 전체 기한 안에서 최대 10초 재시도한다. 동일 서버 잠금을 유지하고
stop/start/RCON은 재전송하지 않는다. 성공 결과의 file_replace에 시도 횟수와 대기 시간을 기록한다.
정확한 외부 잠금 보유 프로세스는 특정하지 않았으며, 실제 3초 reader handle 회귀로 지연 잠금을 검증했다.
지속 잠금은 실패로 반환하고 기존 JAR와 백업을 보존한다. 이전 bytes가 온전함을 해시로 확인하면
원래 설치 journal을 복원해 명시적 이전 버전 재시작을 허용한다. 확인하지 못한 미완료 journal은 재시작을 차단한다.

## 재현 명령

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p 'test_*.py' -v
$env:CRAFTMCP_REAL_BUILD = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests/plugins -p test_plugins.py -k RealBuildTests -v
Remove-Item Env:CRAFTMCP_REAL_BUILD
.\.venv\Scripts\python.exe tests/integration/probe_abcd.py --server-dir mc_server --java '<JDK17>/bin/java.exe'
.\.venv\Scripts\python.exe tests/integration/probe_rcon_paper.py --server-dir mc_server --java '<JDK17>/bin/java.exe' --production-jobs --recoveries --terminate-target
```

probe_abcd는 EULA를 이미 수락한 원본에서 바이너리·라이브러리만 격리 복사한다.
실제 공용 stdio MCP와 소유 Paper로 JDI·RCON·빌드·배포·실패·롤백·종료를 검증한다.
시작 실패는 종료가 확인된 격리 복사본의 paper.jar에만 주입하고 finally에서 복원한다.
--build-ids 옵션은 이전 실제 빌드 3개의 산출물을 해시 확인 후 재사용하는 재검증 경로다.

## 검증 결과

검증 환경: Python 3.12.15, JDK 17.0.18, Paper 1.20.1 build 196, FastMCP 2.14.5.

| 검증 | 결과 |
|---|---|
| 루트 통합·회귀 | 118개 중 117개 통과, 실제 Gradle opt-in 1개 skipped |
| 실제 Gradle opt-in | 별도 1/1 통과. 설정한 Java 17/java·javap 사용, manifest/resources/debug tables 및 실패 빌드 보존 |
| 별도 debugger discovery | 29/29 통과, skipped=0. 실제 Java/JDI 7개 포함 |
| Java 샘플 build/JUnit | 성공, 기존 sender 계약 JUnit 1/1 통과 |
| 공용 stdio MCP 발견 | 21개 도구, 중복/누락 없음 |
| 실제 Paper/JDI/RCON | 중단·변수/객체/배열·재개·정상 재실행 통과 |
| A/C 정상 관리·배포 | 소유 Paper 시작, 1.0/2.0 배포, 새 boot/version/enabled 확인 |
| 중단 중 C 배포 | D stop 해제, 원래 B job succeeded, 새 boot 정상 배포 |
| onEnable 실패·롤백 | failed 판정, 이전 2.0 백업으로 정상 복구 |
| 실제 시작 실패·롤백 | unknown으로 보존, 바이너리 복원 후 이전 2.0 정상 복구 |
| 지속 Windows reader lock | 교체 failed, files_modified=false, 이전 JAR/journal 보존, 명시적 이전 버전 재시작 성공 |
| 중단 중 공용 MCP 종료 | 소유 Paper 정상 종료: stopped, pid=null, exit_code=0 |
| 기존 RCON 복구 회귀 | lease·detach·MCP 종료·격리 target 종료와 unknown/stale 경계 통과 |
| 원본 서버 파일 | paper.jar/eula.txt/server.properties SHA-256 동일, 원본 서버 종료/배포 없음 |

최종 실제 통합 run: `ab-e2e7999c`, status=passed.
첫 전체 실행 `ab-b4ed916b`에서 세 버전 모두 실제 공용 build_plugin으로 빌드했고,
최종 run은 해당 build_id의 해시 검증 산출물을 재사용했다.
Windows 교체가 실패한 `ab-d06fc627`은 성공 증거로 재사용하지 않았고 위 파일 교체 보강의 재현 기록으로 보존했다.

로컬 증거(저장소 루트 기준):

- `.cache/abcd/.cache/ab-e2e7999c/summary.json`, `flow-evidence.jsonl`, `server/.craftmcp/boots/`
- `.cache/abcd/.cache/rcon-debugger/rcon-e4176ac0f62b4ee29d348f8c28c08cde/validation-summary.json`
- `.cache/abcd/.cache/integration-tests.log`, `debugger-tests.log`, `real-build-final.log`, `sample-build.log`
- 최종 실제 빌드 기록: `.cache/abcd/.cache/plugins/builds/dd098f51a38346ac9dd62b294bcca04c/record.json`

제한 실행에서 비동기 MCP 시험이 멈추는 현상은 일반 권한 재실행과 구분했다.
최종 표는 완료된 일반 권한 실행 결과이며 Authlib deprecation 경고는 시험 실패가 아니다.

## 검증 한계

새 통합의 실제 호스트 경계는 공용 stdio MCP client다. 기존 Codex 연결의 15개 도구 핵심 흐름과
lease/detach 결과는 별도 [D 검증 기록](d-rcon-debugger-validation.md)에 있다.
새 21개 도구의 Codex 연결 재발견은 MCP 재연결 후 별도 확인한다.
실제 플레이어/GUI, 범용 game-state/reset/evidence 도구, 영속 job 복구는 이 통합 범위가 아니다.
