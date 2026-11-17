import asyncio
import os
import json
import re
import shutil
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path, PureWindowsPath

import anyio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from fastmcp import FastMCP
from mcp_server.adapters.rcon import execute_rcon
from mcp_server.services.debugger import DebuggerConfig, DebuggerService
from mcp_server.services.deployment import deploy
from mcp_server.services.file_gate import FileGate
from mcp_server.services.jobs import JobError, JobManager, default_commands
from mcp_server.services.server_log import read_server_log
from mcp_server.tools.debugger import register_debugger_tools

load_dotenv(Path(__file__).with_name(".env"))

def _create_debugger_service() -> DebuggerService:
    config_name = os.environ.get("DEBUGGER_CONFIG", "debugger.example.json")
    config_path = Path(config_name)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = DebuggerConfig.load(config_path, workspace=PROJECT_ROOT)
    return DebuggerService(config)


debugger_service = _create_debugger_service()


@asynccontextmanager
async def _lifespan(server):
    try:
        yield {"debugger": debugger_service}
    finally:
        try:
            await debugger_service.close()
        finally:
            await job_manager.close()


mcp = FastMCP(
    "minecraft-dev",
    lifespan=_lifespan,
    strict_input_validation=True,
)
register_debugger_tools(mcp, debugger_service)
MAX_LOG_LINES = 1000
PLUGIN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
JAVA_CLASS_PATTERN = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")
FORBIDDEN_SOURCE_PATTERNS = (
    "Runtime.getRuntime().exec",
    "ProcessBuilder",
    "java.net.",
    "java.nio.file.Files.delete",
    "java.nio.file.Files.move",
    "java.nio.file.Files.copy",
)
DEPLOY_TIMEOUT_SECONDS = 120


def _result(
    *,
    ok: bool,
    stage: str,
    errors: list[str] | None = None,
    raw_output: str = "",
    **extra: object,
) -> str:
    return json.dumps(
        {
            "ok": ok,
            "stage": stage,
            "errors": errors or [],
            "raw_output": raw_output,
            **extra,
        },
        ensure_ascii=False,
    )


def _plugin_error(message: str) -> str:
    return _result(ok=False, stage="build_plugin", errors=[message])


def _find_gradle() -> str | None:
    candidates: list[Path] = []
    gradle_home = os.environ.get("GRADLE_HOME")
    if gradle_home:
        candidates.append(Path(gradle_home) / "bin" / "gradle.bat")
        candidates.append(Path(gradle_home) / "bin" / "gradle")

    path_command = shutil.which("gradle") or shutil.which("gradle.bat")
    if path_command:
        candidates.append(Path(path_command))

    candidates.extend(Path("C:/Gradle").glob("gradle-*/bin/gradle.bat"))
    candidates.extend(Path("C:/Gradle").glob("gradle-*/bin/gradle"))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _rcon_command(command: str) -> str:
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        raise RuntimeError("MC_RCON_PASSWORD 환경변수가 설정되지 않았습니다.")
    return execute_rcon(
        command, password,
        port=int(os.environ.get("MC_RCON_PORT", "25575")),
        timeout=float(os.environ.get("MC_RCON_TIMEOUT", "5")),
    )


async def _execute_job_command(command: str, timeout: float) -> str:
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        raise RuntimeError("MC_RCON_PASSWORD 환경변수가 설정되지 않았습니다.")
    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        execute_rcon,
        command,
        password,
        port=int(os.environ.get("MC_RCON_PORT", "25575")),
        timeout=timeout,
        cancel_event=cancelled,
    ))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        try:
            await asyncio.shield(worker)
        except Exception:
            pass  # The socket worker has closed; the job owns the cancellation result.
        raise


job_manager = JobManager(_execute_job_command, default_commands())


def _find_built_jar(plugin_name: str) -> Path | None:
    project_root = Path(__file__).resolve().parents[1]
    jars = list(
        (project_root / "data" / "build_workspace" / plugin_name / "build" / "libs").glob(
            "*.jar"
        )
    )
    return jars[0] if len(jars) == 1 else None


@mcp.tool()
async def list_players() -> str:
    """테스트 Minecraft 서버의 접속자 목록을 조회합니다."""
    return await asyncio.to_thread(_rcon_command, "list")


@mcp.tool()
async def execute_command(
    command_id: str,
    arguments: dict[str, object] | None = None,
    runtime_id: str = "default",
    request_id: str | None = None,
    run_id: str | None = None,
    timeout_seconds: float = 5,
) -> str:
    """허용된 명령을 비동기 job으로 시작하고 job 상태를 반환합니다."""
    try:
        # Until A's registry is connected this app controls exactly one configured RCON target.
        selected_runtime = os.environ.get("MC_RUNTIME_ID", "default")
        if runtime_id != selected_runtime:
            raise JobError(f"Unknown runtime_id: {runtime_id}; configured target is {selected_runtime}.")
        job = await job_manager.submit(
            command_id=command_id,
            arguments=arguments,
            runtime_id=runtime_id,
            request_id=request_id,
            run_id=run_id,
            timeout=timeout_seconds,
        )
        return _result(ok=True, stage="execute_command", **job.as_dict())
    except JobError as error:
        return _result(ok=False, stage="execute_command", errors=[str(error)])


@mcp.tool()
async def get_job(job_id: str, page: int = 0, page_size: int = 4000) -> str:
    """명령 job을 조회합니다. 조회는 명령을 재전송하지 않습니다."""
    if page < 0 or not 1 <= page_size <= 10000:
        return _result(ok=False, stage="get_job", errors=["page/page_size가 올바르지 않습니다."])
    try:
        job = await job_manager.get(job_id)
        return _result(ok=True, stage="get_job", **job.as_dict(page=page, page_size=page_size))
    except JobError as error:
        return _result(ok=False, stage="get_job", errors=[str(error)])


@mcp.tool()
async def cancel_job(job_id: str) -> str:
    """전송 전 job을 취소하거나 실행 중인 job을 unknown으로 표시합니다."""
    try:
        job = await job_manager.cancel(job_id)
        return _result(ok=True, stage="cancel_job", **job.as_dict())
    except JobError as error:
        return _result(ok=False, stage="cancel_job", errors=[str(error)])


@mcp.tool()
def get_server_log(start_offset: int = 0, max_lines: int = 100, cursor: str | None = None) -> str:
    """완성된 로그 줄을 읽습니다. 이어 읽을 때 이전 next_cursor를 cursor에 전달하세요.

    파일 교체·내용 변경은 reset=true와 함께 처음부터 반환합니다.
    """
    server_dir = os.environ.get("MC_SERVER_DIR")
    if not server_dir:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=["MC_SERVER_DIR 환경변수가 설정되지 않았습니다."],
        )
    if start_offset < 0:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=["start_offset은 0 이상이어야 합니다."],
        )
    if not 1 <= max_lines <= MAX_LOG_LINES:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=[f"max_lines는 1부터 {MAX_LOG_LINES} 사이여야 합니다."],
        )

    log_path = Path(server_dir) / "logs" / "latest.log"
    try:
        return _result(
            ok=True,
            stage="get_server_log",
            **read_server_log(log_path, start_offset=start_offset, max_lines=max_lines, cursor=cursor),
        )
    except ValueError as error:
        return _result(
            ok=False, stage="get_server_log", errors=[str(error)],
            reset_required=True, next_offset=0,
        )
    except FileNotFoundError:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=[f"로그 파일을 찾을 수 없습니다: {log_path}"],
        )
    except OSError as error:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=[f"로그 파일을 읽을 수 없습니다: {error}"],
        )


def _build_plugin(
    plugin_name: str,
    main_class: str,
    source_files: dict[str, str],
) -> str:
    """검사한 Java 소스를 임시 Gradle 프로젝트에 배치하고 플러그인을 빌드합니다."""
    if not PLUGIN_NAME_PATTERN.fullmatch(plugin_name):
        return _plugin_error(
            "plugin_name은 영문자, 숫자, 밑줄, 하이픈만 사용해 1~64자로 입력해야 합니다."
        )
    if not JAVA_CLASS_PATTERN.fullmatch(main_class):
        return _plugin_error("main_class가 올바른 Java 클래스 경로가 아닙니다.")
    if not source_files:
        return _plugin_error("source_files는 비어 있을 수 없습니다.")

    for relative_path, source in source_files.items():
        path = Path(relative_path)
        if (
            path.anchor or PureWindowsPath(relative_path).anchor
            or ".." in path.parts
            or ".." in PureWindowsPath(relative_path).parts
            or ":" in relative_path
            or path.suffix != ".java"
            or not source.strip()
        ):
            return _plugin_error(f"허용되지 않은 Java 소스 경로 또는 내용입니다: {relative_path}")
        if any(pattern in source for pattern in FORBIDDEN_SOURCE_PATTERNS):
            return _plugin_error(f"위험한 API가 포함되어 있습니다: {relative_path}")

    project_root = Path(__file__).resolve().parents[1]
    template_dir = project_root / "plugin_template"
    build_root = project_root / "data" / "build_workspace" / plugin_name
    try:
        workspace = (project_root / "data/build_workspace").resolve()
        if (not workspace.is_relative_to(project_root.resolve())
                or build_root.is_symlink() or not build_root.resolve().is_relative_to(workspace)):
            return _plugin_error("빌드 경로가 작업 공간 밖을 가리킵니다.")
        if not (template_dir / "build.gradle").is_file():
            return _plugin_error("plugin_template/build.gradle이 없습니다.")
        if not (template_dir / "settings.gradle").is_file():
            return _plugin_error("plugin_template/settings.gradle이 없습니다.")
        if not (template_dir / "src" / "main" / "resources" / "plugin.yml").is_file():
            return _plugin_error("plugin_template/src/main/resources/plugin.yml이 없습니다.")

        if build_root.exists():
            shutil.rmtree(build_root)
        shutil.copytree(template_dir, build_root)
        java_root = (build_root / "src" / "main" / "java").resolve()
        if not java_root.is_relative_to(build_root.resolve()):
            return _plugin_error("Java 소스 경로가 빌드 폴더 밖을 가리킵니다.")
        for relative_path, source in source_files.items():
            destination = (java_root / relative_path).resolve()
            if not destination.is_relative_to(java_root):
                return _plugin_error("Java 소스 경로가 빌드 폴더 밖을 가리킵니다.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source, encoding="utf-8")

        plugin_yml = build_root / "src" / "main" / "resources" / "plugin.yml"
        plugin_yml.write_text(
            "\n".join(
                [
                    f"name: {plugin_name}",
                    "version: 0.1.0",
                    "main: " + main_class,
                    "api-version: '1.20'",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        return _plugin_error(f"빌드 작업 공간을 준비할 수 없습니다: {error}")

    gradle = build_root / "gradlew.bat"
    if not gradle.is_file():
        gradle_command = _find_gradle()
        if not gradle_command:
            return _plugin_error(
                "Gradle을 찾을 수 없습니다. Gradle 설치 후 다시 시도하거나 "
                "plugin_template에 gradlew.bat을 추가하세요."
            )
        command = [gradle_command, "build", "--no-daemon", "--offline"]
    else:
        command = [str(gradle), "build", "--no-daemon", "--offline"]

    try:
        completed = subprocess.run(
            command,
            cwd=build_root,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _result(
            ok=False,
            stage="build_plugin",
            errors=["Gradle 빌드가 180초 제한을 초과했습니다."],
        )
    except OSError as error:
        return _plugin_error(f"Gradle을 실행할 수 없습니다: {error}")

    output = (completed.stdout + "\n" + completed.stderr).strip()
    jar_files = list((build_root / "build" / "libs").glob("*.jar"))
    if completed.returncode != 0:
        return _result(
            ok=False,
            stage="build_plugin",
            errors=["Gradle 빌드가 실패했습니다."],
            raw_output=output,
        )
    if len(jar_files) != 1:
        return _result(
            ok=False,
            stage="build_plugin",
            errors=["빌드 결과 JAR을 하나로 특정할 수 없습니다."],
            raw_output=output,
        )
    return _result(
        ok=True,
        stage="build_plugin",
        raw_output=output,
        jar_path=str(jar_files[0]),
    )


def _build_locked(plugin_name: str, main_class: str, source_files: dict[str, str]) -> str:
    if not PLUGIN_NAME_PATTERN.fullmatch(plugin_name):
        return _plugin_error("Invalid plugin name.")
    project_root = Path(__file__).resolve().parents[1]
    try:
        with FileGate(project_root / ".cache/build-locks" / f"{plugin_name}.lock"):
            return _build_plugin(plugin_name, main_class, source_files)
    except OSError as error:
        return _plugin_error(str(error))


@mcp.tool()
async def build_plugin(plugin_name: str, main_class: str, source_files: dict[str, str]) -> str:
    """Build validated Java sources without blocking debugger calls."""
    return await anyio.to_thread.run_sync(_build_locked, plugin_name, main_class, source_files)


def _deploy_plugin(plugin_name: str, prepare_stop=lambda: None) -> str:
    if not PLUGIN_NAME_PATTERN.fullmatch(plugin_name):
        return _result(ok=False, stage="deploy_plugin", errors=["Invalid plugin name."],
                       phase="preflight", files_modified=False, backup_path=None, execution_unknown=False)
    server_value = os.environ.get("MC_SERVER_DIR")
    if not server_value:
        return _result(ok=False, stage="deploy_plugin", errors=["MC_SERVER_DIR is required."],
                       phase="preflight", files_modified=False, backup_path=None, execution_unknown=False)
    project_root = Path(__file__).resolve().parents[1]
    try:
        properties = {}
        for line in (Path(server_value) / "server.properties").read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith(("#", "!")):
                continue
            key, separator, value = line.partition("=")
            if separator:
                properties[key.strip()] = value.strip()
        if (properties.get("enable-rcon", "false").lower() != "true"
                or int(properties.get("rcon.port", "25575")) != int(os.environ.get("MC_RCON_PORT", "25575"))):
            raise ValueError("The MCP RCON port must match this managed server's enabled RCON configuration.")
        with FileGate(project_root / ".cache/build-locks" / f"{plugin_name}.lock"):
            artifact = _find_built_jar(plugin_name)
            if artifact is None:
                raise ValueError("Build exactly one plugin JAR before deployment.")
            result = deploy(plugin_name, artifact, Path(server_value), rcon=_rcon_command,
                            prepare_stop=prepare_stop, timeout=DEPLOY_TIMEOUT_SECONDS)
        return _result(stage="deploy_plugin", **result)
    except (OSError, ValueError, RuntimeError) as error:
        return _result(ok=False, stage="deploy_plugin", errors=[str(error)], phase="preflight",
                       files_modified=False, backup_path=None, execution_unknown=False)


@mcp.tool()
async def deploy_plugin(plugin_name: str) -> str:
    """Deploy to paper_runner: stop, observe exit, install, restart, verify exact plugin."""
    loop = asyncio.get_running_loop()

    def prepare_stop():
        pending = asyncio.run_coroutine_threadsafe(debugger_service.detach_all(), loop)
        try:
            pending.result(timeout=30)
        except TimeoutError:
            pending.cancel()
            raise RuntimeError("Could not detach debugger sessions before restart.")

    return await anyio.to_thread.run_sync(partial(_deploy_plugin, plugin_name, prepare_stop))


if __name__ == "__main__":
    mcp.run(transport="stdio")
