# C 담당 — 플러그인 빌드·배포 계약

작성: 2026-11-10 (KST). `team-work-split-2026-11-03.md`의 C 영역 구현.
아래 인터페이스는 A/B/D 통합을 위한 **제안 계약 v1**이다. 다른 역할의 공통 모델을 재정의하지 않는다.

## 구현 위치

- `mcp_server/services/plugins.py`: 독립 빌드, build_id 조회, 배포, 상태 조회, 롤백.
- `mcp_server/tools/plugins.py`: 기존 FastMCP 앱에 등록할 도구 4개와 job 연결 경계.
- `plugin_template/`: 일반 제작용 Gradle Wrapper 8.14.3, 배포 ZIP SHA-256 검증, Java 17 및 디버그 정보.
- `tests/plugins/`: 실제 파일/fake 수명 시험, 실제 Gradle 시험, 격리 Paper 드라이버.

공용 `server.py`, 공통 모델/설정, 기존 회귀 시험과 `debugger_sample/`은 수정하지 않았다.
현재 공용 앱의 기존 build/deploy는 그대로다. D가 아래 등록을 연결한 뒤 새 API를 사용한다.

## 빌드 사용 예

```python
from pathlib import Path
from mcp_server.services.plugins import PluginService

plugins = PluginService(Path.cwd())
build = plugins.build(
    "MyPlugin", "local.MyPlugin",
    {"local/MyPlugin.java": java_source},
    plugin_manifest={
        "version": "1.0.0",
        "commands": {"myplugin": {"description": "Run the fixture"}},
        "permissions": {"myplugin.use": {"default": "op"}},
    },
    resource_files={"config.yml": "message: 안녕하세요\n"},
    project_id="my-project",
    offline=False,  # 최초 의존성 준비. 캐시 준비 후 True로 실행.
)
artifact = plugins.get_build(build["build_id"])
```

텍스트 Java 소스와 텍스트 리소스를 지원한다. `plugin.yml`은 manifest에서 생성하며 리소스 입력으로
덮어쓸 수 없다. manifest의 name/main은 함수 입력과 일치해야 한다. commands/permissions는 mapping이다.
입력 경로의 절대 경로, 드라이브 경로, 상위 이동, Windows 예약 이름·스트림, 대소문자 중복을 거절한다.
Wrapper와 빌드 스크립트는 허용 목록으로만 복사하므로 중첩 샘플·기존 빌드·Gradle 캐시를 복사하지 않는다.

산출물은 `.cache/plugins/builds/<build_id>/`에 보존한다.

| 필드 | 의미 |
|---|---|
| `schema_version` | 현재 문자열 `1` |
| `ok`, `stage`, `errors`, `warnings` | 동작 결과 및 한계 |
| `observed_at`, `evidence_ids` | UTC 관찰 시각과 외부 증거 연결용 목록 |
| `build_id`, `project_id` | 빌드 식별자와 외부 프로젝트 참조 |
| `source_sha256` | 소스·리소스·manifest 전체 내용의 정렬된 JSON SHA-256 |
| `source_revision.head`, `dirty` | 작업 사본 Git 상태. 입력 소스 식별자는 별도 source_sha256 |
| `jar_path`, `artifact_sha256` | 보존한 JAR 경로와 실제 바이트 SHA-256 |
| `template_sha256` | 사용한 Wrapper/빌드 스크립트별 해시 |
| `project_root` | 소스와 실제 빌드가 보존된 프로젝트 경로; D의 디버거 프로젝트에 연결 |
| `debug_info` | javap로 검사한 Java 17 class와 줄/지역 변수 테이블 존재 여부 |
| `build_tools` | Wrapper의 Gradle 버전, Java release와 class 검사 도구 |
| `output_path` | `build.log` 원문 참조 |

성공한 빌드만 `get_build()`로 배포할 수 있다. ID별 독립 폴더와 플러그인별 잠금을 사용하며 이전
디렉터리를 지우지 않는다. 빌드 실패에 이전 JAR를 반환하지 않는다. `get_build()`와 배포 직전 양쪽에서
해시를 재검사한다. 파일 이름으로 최신 JAR를 찾지 않는다. 서버 plugins 폴더의 다른 JAR가 같은
플러그인을 선언하면 중복 설치를 만들지 않고 종료 요청 전에 거절한다.

기준 의존성은 기존 프로젝트와 동일한 Paper API `1.20.1-R0.1-SNAPSHOT`이다. Wrapper 배포는
고정·체크섬 검증하지만 이 SNAPSHOT과 전이 의존성 전체의 바이트를 고정한 dependency lock/verification
파일은 아직 없다. 최초 온라인 준비 결과가 달라질 수 있으며 입력 소스 해시만으로 JAR 재현성을 보장하지 않는다.
소스 문자열 API 필터는 보안 경계로 사용하지 않는다. 빌드/실행은 격리된 개발 환경에서 수행한다.

## A의 수명 서비스 연결

`PluginService(..., runtime=runtime, release_runtime=release, observe_plugin=observe)`로 주입한다.
현재 서비스 메서드는 동기식이며 도구 계층이 worker thread에서 실행한다. 실제 A/D 서비스가 async이면
통합 계층에서 해당 실행 모델에 맞게 어댑터를 제공한다.

| 호출 | 필요한 계약 |
|---|---|
| `runtime.resolve(runtime_id)` | `{server_dir: 절대 경로, managed: bool}` |
| `runtime.status(runtime_id)` | `{phase: running/stopped/..., pid: int/None, boot_id: str/None}` |
| `runtime.exclusive(runtime_id)` | 서버별 변경 작업 context manager. 경합 시 busy 예외 |
| `runtime.stop(runtime_id, timeout=seconds)` | 새 job/동일 잠금 재획득 없이 정상 종료 요청·확인 |
| `runtime.start(runtime_id, timeout=seconds)` | 같은 관리 프로필/JDWP로 시작하고 결과 관찰 |
| `release_runtime(runtime_id)` | 이전 디버거 중단 해제·detach·이전 참조 무효화. 실패는 예외 |

`exclusive`를 보유하는 동안 예기치 않은 자동 재시작을 막아야 한다. `stop/start`는 이 잠금을 다시
획득하지 않는다. status/진단/디버거 조회·재개는 변경 잠금으로 막지 않는다. C는 자체 수명 관리자나
runtime registry를 만들지 않는다. 기존 `paper_runner`에 단순 `operation.lock`만 연결해서는 설치 중
자동 재시작을 차단할 수 없으므로 A의 어댑터가 restart gate도 조정해야 한다.

배포 순서는 입력 JAR 동결·해시 확인 → 현재 boot 기록 → 디버거 해제 → stop → 같은 boot의
`stopped`/`pid=None` 확인 → 이전 JAR 백업 → 임시 파일/해시 확인 → 원자적 교체 → start → 새 boot의
정확한 플러그인 활성화·버전 관찰이다. 전체 기한은 기본 120초다.

`.cache/plugins/deployments/<deployment_id>/record.json`에 단계별 기록과 `previous.jar`를 보존한다.
build_id/run_id/deployment_id/이전·새 boot/이전·새 JAR 해시를 연결한다. 프로세스가 중단되면 `installing`
또는 `starting` 단계가 남을 수 있다. 이 기록만으로 자동 복구하거나 변경 명령을 재전송하지 않는다.
A가 잠금을 상실한 프로세스의 비정상 종료를 처리할 때 실제 디스크/프로세스 상태를 재관찰해야 한다.

`rollback(runtime_id, deployment_id)`는 이 배포가 보존한 이전 JAR의 해시·descriptor를 확인하고 동일한
종료·교체·새 부팅 검증 경로를 사용한다. 현재 설치가 해당 배포의 JAR와 달라졌으면 거절한다.
시작 실패로 서버가 이미 `stopped`인 경우에도 롤백할 수 있다. 이전 JAR가 없는 최초 설치는 롤백할 수 없다.
실패 시 `files_modified`, `execution_unknown`, `recovery_required`, `phase`를 함께 확인한다.
월드/DB 복원이나 마이그레이션 취소는 하지 않으며 자동 롤백도 하지 않는다.

## D의 정확한 플러그인 진단 연결

`observe_plugin(runtime_id, plugin_name)`은 아래 dictionary 또는 관찰 불가 시 빈 dictionary를 반환한다.

```json
{
  "plugin_name": "MyPlugin",
  "version": "1.0.0",
  "enabled": true,
  "boot_id": "observed-new-boot-id",
  "raw_output": "optional original diagnostic response"
}
```

실제 진단 명령/fixture처럼 정확한 플러그인을 관찰하는 서비스를 연결한다. 비슷한 이름이 있는
`plugins` 문자열의 부분 일치는 사용하지 않는다. 다른 이름/다른 boot의 관찰은 버리고 enabled/version을
null로 반환한다. `get_plugin_status()`는 설치 JAR의 descriptor/해시와 실행 관찰을 별도로 반환한다.
디스크 JAR 일치를 실행 중 bytecode 일치로 주장하지 않으며 `runtime_bytecode_sha256`는 null이다.
새 boot의 enabled=true·정확한 버전·디스크 해시가 모두 확인되어야 배포 성공이다.

## B의 job과 D의 도구 등록 연결

```python
from mcp_server.services.plugins import PluginService
from mcp_server.tools.plugins import register_plugin_tools

plugins = PluginService(workspace, runtime=runtime_adapter,
                        release_runtime=debugger_adapter.release_runtime,
                        observe_plugin=diagnostics.observe_plugin)
register_plugin_tools(mcp, plugins, jobs_adapter)
```

D는 공용 앱의 기존 build/deploy 도구 등록을 제거한 뒤 이 함수를 한 번 등록한다.
기존 인자 `deploy_plugin(plugin_name)`는 `deploy_plugin(runtime_id, build_id, request_id, run_id=None)`로
바뀐다. `rollback_plugin(runtime_id, deployment_id, request_id, run_id=None)`와
`get_plugin_status(runtime_id, plugin_name)`가 추가된다. build의 기존 3개 필수 인자는 유지된다.

`jobs_adapter.submit(spec, operation)`은 async이며 **즉시 job dictionary를 반환**해야 한다.
operation은 인자 없는 async callable이다. spec에는 kind/runtime_id/request_id/run_id/inputs가 들어간다.
B가 같은 request_id·대상·종류·입력의 중복을 기존 job으로 연결하고 다른 입력의 재사용을 거절한다.
도구 호출 하나당 job 하나이며 내부 start/stop은 job을 추가하지 않는다. 작업의 실제 결과는 get_job으로 조회한다.
이 저장소에 B 서비스가 아직 없어 새 queue/Job 모델을 만들지 않았고, fake adapter로 단일 제출을 시험했다.
jobs 없이 등록하면 독립 build는 직접 실행할 수 있으며 배포/롤백 도구는 명시적 통합 필요 오류를 반환한다.

스레드 작업은 기한까지 정리하도록 기본 cancellation shield를 유지한다. Gradle은 timeout 시 소유한
프로세스 트리를 종료한다. B의 실행 중 취소·프로세스 종료 복구 계약과 연결한 전체 job 수명 검증은 별도다.

## 재현 명령

```powershell
# 실제 파일 + fake lifecycle + 인프로세스 MCP
.\.venv\Scripts\python.exe -m unittest discover -s tests/plugins -p test_plugins.py -k PluginTests -v

# 실제 Gradle/javap: 성공 빌드, 리소스·명령·디버그 정보, 컴파일 실패와 산출물 보존
$env:CRAFTMCP_REAL_BUILD = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests/plugins -p test_plugins.py -k RealBuildTests -v

# 원본을 변경하지 않고 기존 EULA/바이너리/캐시를 복사한 실제 격리 Paper
.\.venv\Scripts\python.exe tests/plugins/probe_paper.py --server-dir mc_server
```

Paper 드라이버의 runtime 어댑터는 시험 전용이며 기존 Paper runner를 사용한다.
정상 배포 → 의도적 onEnable 예외 → 이전 JAR 롤백 → 새 JVM 시작 실패 → 복구·롤백을 검사한다.
시작 실패는 종료가 확인된 격리 서버의 paper.jar를 잠시 다른 이름으로 옮겨 새 JVM이 실제로 비정상
종료하는지 관찰하고, finally에서 파일을 복원하는 방식이다. RCON 진단으로 정확한 버전 활성화를 검사한다.
`--build-ids <v1 ID> <v2 ID> <broken ID>`를 지정하면 명시한 성공 빌드 3개를 해시 검사 후 재사용한다.
결과는 `.cache/plugins/paper/<id>/summary.json`, 부팅별 로그와 deployment 기록에 남는다.
디버거 연결이 없는 시험이므로 release callback은 no-op이다. A의 실제 수명 서비스, B의 실제 queue,
D의 공용 등록·증거 연결·디버거 세션 무효화·실제 MCP 호스트 시험을 대신하지 않는다.

## 이번 작업의 검증 기록

검증 환경: 루트 `.venv` Python 3.13, JDK 17.0.20.1, Wrapper Gradle 8.14.3,
기존 캐시의 Paper 1.20.1 build 196. 원본 `mc_server`는 복사에만 사용했다.

| 시험 | 결과 | 범위 |
|---|---|---|
| C 독립 시험 `-k PluginTests` | 14개 통과 | 실제 파일, fake lifecycle, 인프로세스 MCP, 실제 부모·자식 프로세스 timeout 정리 |
| C 실제 빌드 `RealBuildTests` | 통과 | 명령/permissions/한글 리소스, 실제 class debug table, 컴파일 실패, 이전 JAR 보존 |
| 기존 `test_build_deploy.py` | 17개 통과 | 기존 빌드/배포/실제 runner/비동기 호출 회귀 |
| 기존 `test_server_qa.py` | 로그·RCON 10개 통과, stdio 1개 일반 권한 재시험 통과 | 제한 환경 첫 실행의 stdio 초기 연결 실패와 구분 |
| 격리 Paper 1차 | 통과, runner exit code 0 | 정상 배포, 실제 onEnable 실패, 롤백 후 정확한 버전 RCON 진단 |
| 격리 Paper 2차 | 통과, runner exit code 0 | 정상 배포, 로딩 실패·롤백, 실제 새 JVM 시작 실패, 복구 후 롤백·버전 2.0 활성화 |
| 문법 및 공백 검사 | 통과 | 신규 Python 4개 AST/compileall, `git diff --check` |

실제 빌드 성공 예: build_id `cc1e22e4f8914876a4953733d5b12245`,
`.cache/plugins/builds/cc1e22e4f8914876a4953733d5b12245/record.json`.
1차 Paper 기록: `.cache/plugins/paper/c1f7c0a20c134ee08f432c4ac43db936/summary.json`.
2차 Paper 기록: `.cache/plugins/paper/152556033a424dc4b7df1cae97e525d5/summary.json`.
증거 경로는 로컬 산출물이며 Git에 포함하지 않는다.

제한 실행의 첫 실제 빌드는 Gradle 캐시 JAR AccessDeniedException으로 실패했고 일반 권한 재실행은 통과했다.
첫 timeout 시험은 taskkill 기반 정리가 제한 실행에서 자식을 종료하지 못하는 결함을 드러냈다.
Windows Job Object로 수정한 뒤 부모·자식 모두 제한 시간에 정리되는 시험이 통과했다.
의존성 검증 XML 생성도 시도했으나 Gradle이 SNAPSHOT 중복 component 오류를 반환하여 템플릿에 포함하지 않았다.

C1~C4의 독립 구현과 시험, C6의 정확한 진단 경계 및 C7의 격리 Paper 검증을 수행했다.
C5는 실제 A/B 구현이 없어 adapter
계약과 fake 연결까지만 검증했다. 공용 server.py 등록은 D가 취합한다. 전체 v1 공동 체크리스트는
이 작업만으로 완료 표시하지 않는다. 실제 사용자 MCP 호스트·게임 클라이언트, B의 취소/중복 처리,
재시작 후 D의 실제 디버그 세션 무효화와 공통 run/evidence 연결은 아직 미검증이다.

## 요청 후 재시험과 Windows 파일 교체 수정

사용자의 추가 테스트 요청에 따라 실제 빌드와 격리 Paper 검증을 재실행했다.
첫 전체 시험에서 시작 실패 후 롤백의 JAR 교체가 `WinError 5`로 일시 거절되었다.
단독 및 독립 시험 재실행에서는 통과했으나 원래 오류를 무시하지 않고 파일 교체 경로를 보강했다.

Windows의 접근/공유/잠금 오류(5/32/33)에 한해 원자적 파일 교체를 최대 2초 동안 재시도한다.
배포에서는 남은 전체 기한도 적용하며 서버 변경 잠금을 계속 보유한다. 서버 start/stop/RCON 명령은
재전송하지 않는다. 같은 동작을 결과 JSON의 원자적 갱신에도 적용했다. 잠금이 지속되면 오류를 반환한다.
실제 Windows reader handle로 JAR 이름 변경을 막아, 지속 잠금에서는 기존 JAR 보존 및 실패,
잠금 해제 후에는 교체 성공을 확인하는 시험을 추가했다. 수정 후 독립 시험 15개가 통과했다.

수정 후 실제 Gradle 시험을 포함한 **전체 16개가 통과**했다(125.020초, skipped=0).
빌드 기록: `.cache/plugins/builds/fd0d07e6434e430f9a69e1e2a5533d8a/record.json`.
추가 격리 Paper 시험도 정상 배포, 로딩 실패·롤백, 새 JVM 시작 실패·복구 후 롤백에 통과했고
runner 종료 코드는 0이다. 기록: `.cache/plugins/paper/f786ca31111a473dacb77e93fa241147/summary.json`.
이 Paper 프로세스는 파일 교체 수정 전에 시작된 코드이며, 수정된 재시도 경로는 새 Windows 실제
파일 잠금 시험 및 전체 16개 시험으로 검증했다.
원본 `mc_server/server.properties`, `paper.jar`, `eula.txt`는 실행 전후 SHA-256이 동일했다.
