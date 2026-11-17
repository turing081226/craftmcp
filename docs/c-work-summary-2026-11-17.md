# C 역할 작업 정리 — 플러그인 빌드·배포·롤백

문서 작성일: 2026-11-17 (KST)  
대상 작업: 2026-11-10 대화에서 수행한 C 역할 구현 및 추가 테스트  
담당 범위: [팀 분업안의 C 역할](team-work-split-2026-11-03.md#6-c--플러그인-빌드배포)

## 1. 작업 결과

플러그인의 소스를 빌드하고, 특정 빌드의 JAR를 서버에 배포하며, 실행 상태를 확인하고 이전 JAR로
롤백하는 독립 서비스와 MCP 도구 등록 함수를 구현했다. 빌드·배포 결과를 ID와 해시로 추적하고,
서버 종료 확인 전에 JAR를 교체하지 않도록 했다.

실제 Gradle 빌드와 격리 Paper 시험을 수행했다. 추가 테스트에서 발견한 Windows 파일 교체 오류도
수정했다. A·B의 실제 서비스 및 D의 공용 앱 연결은 남아 있으므로 전체 v1 통합 완료로 보지는 않는다.

## 2. 구현한 기능

| 영역 | 구현 내용 |
|---|---|
| 빌드 입력 | Java 소스, plugin manifest의 commands/permissions, 설정 파일과 한글 텍스트 리소스 지원 |
| 빌드 환경 | Gradle Wrapper 8.14.3 및 배포 ZIP 체크섬 고정, Java 17, 최초 의존성 준비와 offline 빌드 구분 |
| 템플릿 복사 | 허용한 빌드 파일만 복사해 중첩 샘플·캐시·이전 산출물 혼입 방지 |
| 빌드 추적 | build_id별 소스·로그·JAR 보존, 소스/리소스/manifest 해시, JAR SHA-256, Git 상태와 템플릿 해시 기록 |
| 디버그 정보 | 실제 생성 class를 javap로 검사해 Java 17 bytecode와 줄·지역 변수 테이블 확인 |
| 입력 검증 | 경로 이탈, Windows 예약 이름·스트림, 대소문자 중복 경로, manifest 덮어쓰기 거절 |
| 배포 | 정확한 build_id 조회 및 해시 재검사, 서버별 배타 작업, 디버거 해제 호출 경계, 종료 확인 후 원자적 JAR 교체 |
| 배포 검증 | 새 boot ID에서 정확한 플러그인 이름·버전·enabled 상태와 설치 JAR 해시 확인 |
| 상태 조회 | 디스크 JAR 정보와 현재 부팅의 실행 관찰을 분리하고, 관찰 불가 상태를 unknown/null로 표시 |
| 롤백 | deployment_id에 보존한 이전 JAR의 해시를 검증하고 종료·교체·시작·활성화 재확인 |
| 실패 처리 | 단계, 파일 변경 여부, 실행 불명 여부, 복구 필요 여부 기록; 실패 빌드의 이전 JAR 재사용 방지 |
| 중복 설치 방지 | 다른 이름의 JAR가 같은 플러그인을 선언하면 서버 종료 요청 전에 거절 |

롤백은 JAR 복구만 수행한다. 월드·DB 복원이나 마이그레이션 취소는 포함하지 않는다.
디스크 JAR 해시를 실행 중 bytecode 해시로 단정하지 않으며, 비슷한 플러그인 이름이나 오래된 부팅의
관찰을 배포 성공 근거로 사용하지 않는다.

## 3. 주요 산출물

| 파일 | 역할 |
|---|---|
| [services/plugins.py](../mcp_server/services/plugins.py) | 독립 빌드, 산출물 조회, 배포, 상태 조회, 롤백 서비스 |
| [tools/plugins.py](../mcp_server/tools/plugins.py) | build_plugin/deploy_plugin/get_plugin_status/rollback_plugin 등록 및 job 연결 경계 |
| [build.gradle](../plugin_template/build.gradle) | 일반 플러그인의 Java 17·디버그 정보·JAR 빌드 설정 |
| [Wrapper 설정](../plugin_template/gradle/wrapper/gradle-wrapper.properties) | 고정 Gradle 배포와 SHA-256 검증; Wrapper JAR와 실행 스크립트도 추가 |
| [test_plugins.py](../tests/plugins/test_plugins.py) | 독립 서비스·인프로세스 MCP·실제 Gradle·Windows 파일 잠금 회귀 시험 |
| [probe_paper.py](../tests/plugins/probe_paper.py) | 원본 서버를 복사한 격리 Paper의 배포·실패·롤백 검증 |
| [plugin-build-deploy.md](plugin-build-deploy.md) | 상세 API 계약, A/B/D 연결 방법, 재현 명령과 검증 이력 |

공용 `mcp_server/server.py`, A/B 소유의 공통 모델·설정, D 소유의 `debugger_sample/`은 이 작업에서
수정하지 않았다. 공용 앱은 기존 도구를 유지하며, 새 등록 함수의 통합은 D가 취합할 작업이다.

## 4. 테스트 결과

아래는 구현·추가 테스트 당시의 실행 결과다. 2026-11-17 문서 작성 과정에서 테스트를 다시 실행한
결과가 아니다.

| 시험 | 결과 | 확인한 범위 |
|---|---|---|
| 수정 후 C 전체 시험 | **16개 통과**, skipped=0, 125.020초 | 독립 시험 15개와 실제 Gradle 시험 1개 |
| 실제 Gradle 빌드 | 통과 | 명령·권한 선언, 한글 리소스, 실제 class 디버그 정보, 컴파일 실패 후 이전 산출물 보존 |
| 실제 격리 Paper | 통과, runner 종료 코드 0 | 정상 배포, onEnable 실패 감지, 롤백, 새 JVM 시작 실패, 파일 복구 후 롤백·버전 활성화 확인 |
| 기존 빌드·배포 회귀 | 17개 통과 | 기존 runner와 빌드·배포·비동기 호출 동작 |
| 기존 로그·RCON 회귀 | 10개 통과 및 stdio 1개 재시험 통과 | stdio는 제한 환경의 초기 연결 실패 후 일반 권한에서 통과 |
| 문법·공백 검사 | 통과 | 신규 Python 문법 검사, git diff --check |
| 원본 서버 파일 보존 | 확인 | server.properties/paper.jar/eula.txt의 실행 전후 SHA-256 동일 |

Paper 시험은 원본 `mc_server`의 복사본과 임시 포트로 수행했다. runtime 어댑터는 시험 전용이며
기존 Paper runner를 사용했다. 디버거가 연결되지 않은 시험이므로 디버거 해제 callback은 no-op이다.
추가 Paper 시험은 파일 교체 재시도 수정 전에 시작된 프로세스였고, 수정한 경로는 별도의 실제 Windows
파일 잠금 시험과 수정 후 전체 16개 시험으로 검증했다.

### 발견한 문제와 수정

- **Windows 자식 프로세스 정리:** taskkill 기반 timeout 처리가 제한 실행에서 자식을 종료하지 못했다.
  Windows Job Object로 변경하고 부모·자식 프로세스가 모두 제한 시간에 종료되는지 확인했다.
- **Windows JAR 교체 오류:** 롤백 중 원자적 교체가 WinError 5로 일시 거절됐다. Windows 오류
  5/32/33에 한해 파일 교체를 최대 2초, 배포 전체 기한 이내에서 재시도하도록 수정했다. 실제 reader
  handle로 잠금을 만들어 지속 잠금에서는 기존 JAR 보존·실패, 잠금 해제 후에는 교체 성공을 검증했다.
  서버 시작·종료 및 RCON 명령은 재전송하지 않는다.
- **제한 환경의 캐시 접근:** 실제 빌드가 Gradle 캐시 JAR 접근 거부로 실패한 경우 일반 권한에서
  재실행해 통과했다. 환경 실패와 제품 동작 실패를 구분해 기록했다.

### 로컬 증거

- 최종 성공 빌드: `.cache/plugins/builds/fd0d07e6434e430f9a69e1e2a5533d8a/record.json`
- 추가 Paper 결과: `.cache/plugins/paper/f786ca31111a473dacb77e93fa241147/summary.json`
- 각 build/deployment 디렉터리에 빌드 로그, JAR 해시, 백업 및 단계별 기록 보존

증거와 서버 복사본은 로컬 산출물로 Git에 포함하지 않는다. 다른 환경에서는 상세 계약 문서의 명령으로
새 증거를 생성해야 한다.

## 5. 남은 작업과 진행 순서

| 순서 | 담당 연결 | 다음 작업 | 완료 확인 기준 |
|---|---|---|---|
| 1 | A — runtime | resolve/status/exclusive/start/stop 어댑터 연결 | 같은 서버를 참조하고 배포 중 자동 재시작 차단, 종료 확인, JDWP 유지 |
| 2 | B — job | 실제 submit/get/cancel 및 요청 중복 방지 연결 | 즉시 job 반환, get_job 결과 조회, 단일 job 내 수명 호출, 중복·취소·기한 검증 |
| 3 | D — 공용 앱 | 기존 build/deploy 등록 교체, 새 도구 등록 함수 연결 | 도구 중복·누락 없이 공용 MCP에서 새 API 사용 |
| 4 | D — 진단·디버거·증거 | 정확한 플러그인 관찰, 디버거 해제와 이전 참조 무효화, run/evidence 연결 | 재시작 후 이전 세션·참조 거절, 같은 run에 빌드·배포·실행 증거 연결 |
| 5 | 공동 통합 시험 | 실제 MCP 호스트에서 빌드 → job 조회 → 배포 → 상태 확인 → 롤백 | 시험 전용 fake 없이 전체 흐름과 종료 정리 통과 |
| 후속 | C — 의존성 고정 | Paper API와 전이 의존성의 lock·검증 파일 준비 | 최초 온라인 준비 이후 의존성 바이트를 검증할 수 있는 재현 경로 확보 |

현재 일반 템플릿의 Paper API는 기존 기준인 `1.20.1-R0.1-SNAPSHOT`이다. Wrapper 배포는 체크섬으로
고정했지만 의존성 전체 바이트를 고정하지는 못했다. Gradle 검증 XML 자동 생성 시 SNAPSHOT 중복
component 오류가 발생해 해당 파일을 템플릿에 포함하지 않았다.

실제 사용자 MCP 호스트·게임 클라이언트, B의 실제 queue/취소/중복 처리, D의 실제 디버그 세션 무효화와
공통 evidence 연결은 이 작업에서 검증하지 않았다. 다음 우선 작업은 A/B 인터페이스를 확정하고 C의
어댑터를 연결한 뒤 D의 공용 앱에서 통합 흐름을 검증하는 것이다.
