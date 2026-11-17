# C 브랜치와 최신 main 통합

작성일: 2026-11-17 (KST)

## 통합 대상

- 개발 브랜치: `C`, 통합 전 커밋 `584f8ce` (`스크린샷`)
- 원격 기준: fetch로 확인한 `origin/main`의 `1d8967b`
- 방식: C 브랜치에서 origin/main을 merge해 양쪽 커밋 이력 보존

main의 A/B/D 통합(runtime·수명 관리·공통 job·플러그인 배포·디버거 연결)을 가져오고,
C의 게임 화면 캡처·시각 판정·이미지 증거 도구를 유지했다.

## 충돌 해결과 호환성 변경

충돌은 `mcp_server/server.py`의 import 한 곳이었다. main의 runtime/plugin 도구 등록 import와
C의 client_capture 서비스/도구 import를 모두 유지했다. 초기화와 lifespan은 main의 관리 서버·job·
디버거 정리 순서를 유지하고, 캡처 서비스 등록을 추가했다. 캡처는 Windows 백엔드를 필요할 때만
생성하므로 앱 시작 시 촬영하거나 게임 클라이언트를 실행하지 않는다.

공용 도구는 main의 21개와 캡처 도구 5개를 합쳐 **26개**다. 중복 이름 없이 다음 범위를 함께 제공한다.

- runtime 조회 및 관리 서버 시작·종료
- 명령 실행, job 조회·취소
- build_id 기반 빌드·배포·플러그인 상태 조회·롤백
- 디버거 8개 도구와 서버 로그
- 게임 창 조회·촬영·기존 캡처 조회·시각 판정 기록·시각 증거 조회

`tests/integration/test_abcd.py`와 `probe_abcd.py`의 21개 고정 검사를 26개로 갱신하고 캡처 도구
존재 검사도 추가했다. 캡처의 실제 stdio 시험에는 A/B/C/D 도구가 함께 존재하는지도 추가로 검사한다.
`tests/client_capture/__init__.py`를 추가해 캡처 시험이 루트 unittest discovery에 포함되도록 했다.

## 기능 경계

main의 build/deploy는 공통 job을 반환하는 통합 경로를 사용한다. 이전 C의 독립 서비스/fake 시험을
공용 앱 통합 완료의 근거로 바꿔 쓰지 않는다. 자세한 A/B/C/D 통합 이력은
[main의 통합 기록](integration-2026-11-17.md)을 참고한다.

캡처에 전달하는 runtime_id/build_id/deployment_id는 계속 호출자가 제공하는 참조다.
이번 merge만으로 실제 게임 접속 서버와 설치 빌드의 일치 여부를 자동 검증하지는 않는다.
시각 판정과 서버 상태 판정도 기존처럼 분리한다. 사용법은 [캡처 문서](client-capture.md)를 따른다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p test_debugger.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/debugger_python -p test_stdio.py -v
```

이번 통합 검증에서는 원본 Minecraft 서버를 시작·중지·배포하지 않는다. 기존 테스트의 임시 서버
프로세스와 모의 RCON, 메모리 비트맵을 사용한다. 실제 Gradle/Paper/게임 렌더링 및 실제 사용자
MCP 호스트 재연결 시험은 별도 범위다. main에 기록된 과거 실제 호스트 검증과 이번 실행을 구분한다.

로컬 전체 회귀 로그: `.cache/c-main-merge-tests.log`.

전체 Python 회귀는 133개 실행 중 **131개 통과, 2개 skipped**였다(115.395초).
건너뛴 항목은 심볼릭 링크 생성 권한 시험과 opt-in 실제 Gradle 빌드다.
스크린샷 및 실제 공용 stdio 등록(26개), A/B/C/D 배포·롤백·수명 관리, 기존 RCON/로그 회귀를 포함한다.

별도 디버거 Python 회귀는 21개 통과(65.865초), 디버거 전용 stdio는 1개 통과(16.457초)였다.
합계 **153개 통과, 2개 skipped**다. 디버거 로그는 `.cache/c-main-debugger-tests.log`와
`.cache/c-main-debugger-stdio-tests.log`에 보존했다.
충돌 표식 부재, Python 문법 검사, staged diff 공백 검사 및 merge 후 양쪽 부모 이력 포함 여부도 확인한다.
