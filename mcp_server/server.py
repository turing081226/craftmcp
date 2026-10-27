import os
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from fastmcp import FastMCP
from mcrcon import MCRcon
from mcp_server.services.debugger import DebuggerConfig, DebuggerService
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
        await debugger_service.close()


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
DEPLOY_LOCK = threading.Lock()


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
    with MCRcon("127.0.0.1", password, port=25575) as rcon:
        return rcon.command(command)


def _find_built_jar(plugin_name: str) -> Path | None:
    project_root = Path(__file__).resolve().parents[1]
    jars = list(
        (project_root / "data" / "build_workspace" / plugin_name / "build" / "libs").glob(
            "*.jar"
        )
    )
    return jars[0] if len(jars) == 1 else None


@mcp.tool()
def list_players() -> str:
    """테스트 Minecraft 서버의 접속자 목록을 조회합니다."""
    return _rcon_command("list")


@mcp.tool()
def get_server_log(start_offset: int = 0, max_lines: int = 100) -> str:
    """서버 로그에서 start_offset 이후의 새 로그를 최대 max_lines줄 반환합니다."""
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
        file_size = log_path.stat().st_size
        if start_offset > file_size:
            return _result(
                ok=False,
                stage="get_server_log",
                errors=["start_offset이 현재 로그 파일 크기보다 큽니다."],
                next_offset=file_size,
            )

        with log_path.open("rb") as log_file:
            log_file.seek(start_offset)
            chunks: list[bytes] = []
            next_offset = start_offset
            for _ in range(max_lines):
                line = log_file.readline()
                if not line:
                    break
                chunks.append(line)
                next_offset = log_file.tell()

        return _result(
            ok=True,
            stage="get_server_log",
            raw_output=b"".join(chunks).decode("utf-8", errors="replace"),
            next_offset=next_offset,
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


@mcp.tool()
def build_plugin(
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
            path.is_absolute()
            or ".." in path.parts
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
        if not (template_dir / "build.gradle").is_file():
            return _plugin_error("plugin_template/build.gradle이 없습니다.")
        if not (template_dir / "settings.gradle").is_file():
            return _plugin_error("plugin_template/settings.gradle이 없습니다.")
        if not (template_dir / "src" / "main" / "resources" / "plugin.yml").is_file():
            return _plugin_error("plugin_template/src/main/resources/plugin.yml이 없습니다.")

        if build_root.exists():
            shutil.rmtree(build_root)
        shutil.copytree(template_dir, build_root)
        java_root = build_root / "src" / "main" / "java"
        for relative_path, source in source_files.items():
            destination = java_root / relative_path
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


@mcp.tool()
def deploy_plugin(plugin_name: str) -> str:
    """빌드된 플러그인을 백업하고 교체한 뒤 서버 재시작과 로드를 확인합니다."""
    if not PLUGIN_NAME_PATTERN.fullmatch(plugin_name):
        return _result(
            ok=False,
            stage="deploy_plugin",
            errors=["plugin_name은 영문자, 숫자, 밑줄, 하이픈만 사용해야 합니다."],
        )
    server_dir_value = os.environ.get("MC_SERVER_DIR")
    if not server_dir_value:
        return _result(
            ok=False,
            stage="deploy_plugin",
            errors=["MC_SERVER_DIR 환경변수가 설정되지 않았습니다."],
        )
    server_dir = Path(server_dir_value)
    if not server_dir.is_dir():
        return _result(
            ok=False,
            stage="deploy_plugin",
            errors=[f"서버 폴더를 찾을 수 없습니다: {server_dir}"],
        )

    jar_path = _find_built_jar(plugin_name)
    if jar_path is None:
        return _result(
            ok=False,
            stage="deploy_plugin",
            errors=["플러그인 빌드 결과 JAR을 하나로 찾을 수 없습니다."],
        )

    start_script = server_dir / "start.bat"
    try:
        start_contents = start_script.read_text(encoding="utf-8")
    except OSError as error:
        return _result(
            ok=False,
            stage="deploy_plugin",
            errors=[f"start.bat을 읽을 수 없습니다: {error}"],
        )
    if ":loop" not in start_contents or "goto loop" not in start_contents.lower():
        return _result(
            ok=False,
            stage="deploy_plugin",
            errors=["start.bat에 서버 재시작 루프가 없어 배포를 중단했습니다."],
        )

    plugins_dir = server_dir / "plugins"
    target = plugins_dir / f"{plugin_name}.jar"
    backup_dir = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "plugin_backups"
        / plugin_name
        / time.strftime("%Y%m%d-%H%M%S")
    )
    with DEPLOY_LOCK:
        try:
            plugins_dir.mkdir(parents=True, exist_ok=True)
            backup_path: str | None = None
            if target.exists():
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_file = backup_dir / target.name
                shutil.copy2(target, backup_file)
                backup_path = str(backup_file)

            temporary_target = plugins_dir / f".{plugin_name}.jar.tmp"
            shutil.copy2(jar_path, temporary_target)
            temporary_target.replace(target)
            log_path = server_dir / "logs" / "latest.log"
            log_offset = log_path.stat().st_size if log_path.is_file() else 0

            _rcon_command("stop")
            deadline = time.monotonic() + DEPLOY_TIMEOUT_SECONDS
            server_ready = False
            while time.monotonic() < deadline:
                time.sleep(2)
                try:
                    output = _rcon_command("plugins")
                    if plugin_name.lower() in output.lower():
                        server_ready = True
                        break
                except (OSError, TimeoutError):
                    continue

            if not server_ready:
                return _result(
                    ok=False,
                    stage="deploy_plugin",
                    errors=["서버 재시작 후 플러그인 로드 확인 시간 초과"],
                    next_log_offset=log_offset,
                    backup_path=backup_path,
                )

            current_log_size = log_path.stat().st_size if log_path.is_file() else log_offset
            with log_path.open("rb") as log_file:
                log_file.seek(min(log_offset, current_log_size))
                log_delta = log_file.read().decode("utf-8", errors="replace")
            errors = [
                line
                for line in log_delta.splitlines()
                if "exception" in line.lower() or "error" in line.lower()
            ]
            return _result(
                ok=not errors,
                stage="deploy_plugin",
                errors=errors,
                raw_output=log_delta,
                next_log_offset=current_log_size,
                backup_path=backup_path,
            )
        except (OSError, RuntimeError) as error:
            return _result(
                ok=False,
                stage="deploy_plugin",
                errors=[f"플러그인 배포에 실패했습니다: {error}"],
            )


if __name__ == "__main__":
    mcp.run(transport="stdio")