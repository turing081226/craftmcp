# craftmcp
마인크래프트 개발을 위한 MCP

히히
---

## 각 파일의 역할

| 파일 및 디렉터리                             | 역할                | 비고                |
| ------------------------------------- | ----------------- | ----------------- |
| `README.md`                           | 프로젝트 소개 및 실행 방법   | 기존 파일 유지          |
| `.gitignore`                          | Git에서 제외할 파일 지정   | 서버 데이터 및 비밀 정보 제외 |
| `.env.example`                        | 환경 변수 예시          | 실제 비밀번호는 기록하지 않음  |
| `docs/decisions.md`                   | 기술적 의사결정 기록       | Java, Paper 버전 등  |
| `docs/paper-server-setup.md`          | 서버 설치 및 실행 안내     | 팀원 공통 매뉴얼         |
| `mc_server/start.sh`                  | Linux/macOS 서버 실행 | 실행 권한 필요          |
| `mc_server/start.bat`                 | Windows 서버 실행     | Windows 팀원용       |
| `mc_server/server.properties.example` | 공통 서버 설정          | 실제 설정의 원본         |
| `mcp_server/`                         | Python MCP 서버     | 추후 코드 및 의존성 파일 추가 |
| `plugin_template/`                    | Paper 플러그인        | 추후 Gradle 프로젝트 구성 |
| `eval/`                               | 테스트 시나리오 및 평가 코드  | 실험 결과 평가          |
| `data/`                               | 실험 데이터            | 대용량 데이터는 별도 관리 고려 |
