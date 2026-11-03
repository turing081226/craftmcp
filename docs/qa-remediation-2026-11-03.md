# 추가 QA 수정 — 2026-11-03

[추가 QA](qa-additional-2026-11-03.md)의 새 결함 5건, RCON 다중 응답 누락,
실제 JVM lease 시험의 시간 의존성을 수정했다. 당시 QA 결과는 원문에 보존한다.

## 수정 내용

| 문제 | 변경 |
|---|---|
| Windows 경로 검사 우회 | rooted·drive-relative·절대 경로·상위 경로를 거절한다. resolve한 목적지가 소스 루트 안인지 확인한다. 동일 플러그인의 빌드·배포는 파일 잠금으로 직렬화한다. |
| 정지 실패 전 JAR 교체 | 실행기가 기존 JVM의 종료를 확인한 후에만 대상 JAR를 기록한다. 정지가 확인되지 않으면 대상은 유지하고 실행 여부 불명 상태와 백업 위치를 반환한다. |
| 잘못된 배포 성공 판정 | 정확한 플러그인 이름, 산출물 plugin.yml의 이름·버전, 새 부팅의 enable/disable 로그, 서버 준비 완료, 설치 해시를 대조한다. |
| 새 로그 첫 오류 누락 | 실행기가 부팅 ID별 콘솔 로그를 따로 기록한다. 배포 검증은 새 부팅 로그의 처음부터 읽는다. 이전 파일의 offset을 재사용하지 않는다. |
| 빌드 중 MCP 차단 | 빌드·배포를 취소 시 작업을 버리지 않는 작업 스레드에서 수행한다. 디버거 조회·재개는 이벤트 루프에서 계속 처리한다. |
| RCON 응답 조기 종료 | 첫 응답 후 별도 요청 ID의 완료 마커를 보내고, 그 응답까지 모든 명령 응답 패킷을 수집한다. 마커 누락·오류·기한 초과는 성공으로 반환하지 않는다. 명령 자체를 재전송하지 않는다. |
| 500ms JVM lease 시험 불안정 | 실제 stopped 이벤트로 동기화하고 프레임 조회 예산을 포함해 lease를 3초로 늘렸다. 실제 결과 완료·lease_expired·이전 참조 거절을 함께 검증한다. |

RCON의 완료 마커는 Minecraft/Paper가 알 수 없는 타입 0 요청에 돌려주는
`Unknown request 0` 응답을 이용한다. 로컬 Paper 1.20.1 build 196의 RconClient 바이트코드와
루프백 회귀 시험으로 확인했다. 임의의 짧은 대기 시간으로 응답 완료를 추측하지 않는다.

## 서버 실행과 자동 배포

자동 배포는 `paper_runner`로 실행한 서버를 대상으로 한다. 이전 서버를 정상 종료하고,
자동 재시작하는 기존 배치 창도 닫은 뒤 저장소 루트에서 실행한다.

```powershell
.\.venv\Scripts\python.exe -m mcp_server.paper_runner --server-dir "C:\minecraft\paper" --debug
```

기본 JDWP 포트는 5005다. `--jdwp-port`로 바꿀 수 있다. RCON 설정은 대상 서버의
server.properties를 사용한다. MCP의 MC_SERVER_DIR·MC_RCON_PORT·MC_RCON_PASSWORD를 맞춘다.
DEBUGGER_CONFIG의 JVM 주소와 runtime JAR도 같은 서버로 지정한다.

실행기는 실제 자식 JVM을 회수하고 동일 Java/JDWP 명령으로 재시작한다.
서버의 `.craftmcp/`에 실행 상태, 부팅별 콘솔 로그, 설치 기록과 백업을 둔다.
배포 중에는 실행기의 재시작 잠금을 잡아 종료 확인과 설치 사이에 새 JVM이 시작하지 못하게 한다.
공용 MCP가 보유한 디버거 세션도 정지 요청 전에 detach한다. 재접속 시 새 부팅 ID를 증거에 반영한다.

기존 start-debug.bat은 수동 실행에 계속 사용할 수 있다. 그 방식으로 실행한 서버에
자동 배포를 요청하면 파일 교체 전에 실행기 필요 오류를 반환한다.
start.bat에 루프 문구가 있다는 사실만으로 실제 프로세스의 재시작을 가정하지 않는다.

## 실패·취소와 복구

- 실패 응답의 `phase`, `files_modified`, `stop_requested`, `execution_unknown`, `backup_path`를 확인한다.
- stop 응답을 잃어도 실제 기존 JVM 종료를 관찰하면 설치를 진행할 수 있다. stop 명령을 다시 보내지 않는다.
- Windows에서 기존 파일 교체 권한 경합을 피하기 위해, 종료와 재시작 잠금 안에서 JAR를 기록하고 flush·fsync·해시 검증을 수행한다.
- 설치 전 `.craftmcp/install.json`을 디스크에 기록한다. 기록이 미완료이거나 설치 해시가 다르면 실행기는 새 JVM 시작을 보류한다. 부분 JAR가 자동으로 실행되지 않는다.
- `recovery_required=true`이면 실행기를 종료하고 서버가 꺼졌음을 확인한 뒤 반환된 백업을 복원한다. 백업이 없으면 완성된 JAR를 오프라인 설치한다. install.json의 target·artifact_sha256을 실제 복원 파일과 맞추고 phase를 installed로 기록한 뒤 실행기를 다시 시작한다.
- 취소된 호출의 작업 스레드는 외부 작업을 정리할 때까지 수행한다. 클라이언트 응답이 취소됐다는 이유로 배포 미실행을 가정하지 않는다. 같은 대상의 중복 호출은 잠금으로 거절된다.

이전처럼 파일 교체 뒤 오류가 발생했는데도 변경 여부나 백업 위치가 사라지는 응답을 반환하지 않는다.

## 검증 명령

```powershell
uv sync --locked --python python
.\.venv\Scripts\python.exe eval/qa_additional.py
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p "test_*.py" -v
```

추가 QA 드라이버는 이제 추적되는 빌드·배포·RCON·로그 회귀 시험을 실행한다.
통과는 종료 코드 0, 회귀 실패는 1이다. 결과는 `.cache/qa-additional-regression-results.json`에 기록한다.

실제 Paper 배포 검증은 이미 EULA에 동의한 Paper 서버 폴더를 지정해 별도로 실행한다.
헬퍼·샘플 플러그인 빌드가 필요하다.

```powershell
.\.venv\Scripts\python.exe tests/probe_managed_paper.py --server-dir "C:\minecraft\paper"
```

별도 `.cache/paper-managed/<id>/server`에 바이너리와 라이브러리만 복사해 새 월드와 임시 포트를 사용한다.
실제 공용 stdio MCP의 list_players → JDI attach → deploy_plugin → 새 JVM → JDI 재접속을 확인한다.
원본 서버의 월드·플러그인·실행 프로세스는 변경하지 않는다. 결과는 해당 폴더의 summary.json에 남긴다.

## 최종 검증 결과

Windows, Python 3.13.2, Temurin JDK 17.0.20.1에서 확인했다.

| 검증 | 결과 |
|---|---|
| 경로·빌드·배포·작업 취소·실행기·RCON·로그 회귀 | 28개 통과, skipped=0 |
| Python 디버거·stdio MCP·실제 JDI/JVM 회귀 | 29개 통과, skipped=0 |
| 합계 | 57개 통과 |
| 실제 Paper 1.20.1 build 196 | 공용 MCP 조회·JDI 연결·배포·새 JVM·정확한 플러그인 로드·JDI 재접속 통과, 정상 종료 |
| 의존성·공백 검사 | uv sync --locked --offline 성공, git diff --check 통과 |

최종 실제 Paper 증거:

- `.cache/paper-managed/905713404abd4a308e36332bdbeff011/summary.json`
- status=passed, shutdown=normal, debugger_reattach=true.
- 이전 boot_id: `e0d07a45fe6d4e858847b8f2217aed68`; 새 boot_id: `2bd22a82d3fe45978177a7e8f20b9746`.
- 원본/복사 Paper SHA-256: `67f31f691afd52a0c4ce33827fd635d0d0e86d46dec0ef6f89e1d58cd27832cf`.
- 설치 샘플 SHA-256: `efdd1856a4d1b45bf1b5173aa567a294dc81a7dadb2cbed84c02117156dc92bc`.

회귀 로그는 `.cache/qa-additional-regression-final.log`, `.cache/qa-debugger-final.log`,
실제 Paper 드라이버 로그는 `.cache/qa-managed-paper-final.log`에 보관한다.
초기 검증 실패 로그도 보존했다. Windows 파일 처리와 Paper의 여러 줄 플러그인 목록 형식은
그 실패를 수정한 뒤 최종 실행에서 통과했다.

이 검증은 별도 서버에서의 배포·재시작 결과다. 기존 사용자 실행 인스턴스,
게임 클라이언트 화면·HUD와 실제 IDE 호스트 등록까지 검증했다는 의미는 아니다.
