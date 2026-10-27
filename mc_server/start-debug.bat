@echo off
setlocal
set "CRAFTMCP_SERVER_DIR=%~dp0"
set "CRAFTMCP_EXIT_CODE=0"
set "CRAFTMCP_NO_PAUSE=0"
set "CRAFTMCP_CHECK_ONLY=0"
set "CRAFTMCP_SUSPEND=n"

:parse_args
if "%~1"=="" goto validate
if /i "%~1"=="--suspend" (
  set "CRAFTMCP_SUSPEND=y"
  shift
  goto parse_args
)
if /i "%~1"=="--no-pause" (
  set "CRAFTMCP_NO_PAUSE=1"
  shift
  goto parse_args
)
if /i "%~1"=="--check" (
  set "CRAFTMCP_CHECK_ONLY=1"
  shift
  goto parse_args
)
echo [ERROR] Unknown argument: %~1
echo Usage: start-debug.bat [--suspend] [--check] [--no-pause]
set "CRAFTMCP_EXIT_CODE=2"
goto finish

:validate
cd /d "%CRAFTMCP_SERVER_DIR%"
if errorlevel 1 (
  echo [ERROR] Cannot open the server folder: %CRAFTMCP_SERVER_DIR%
  set "CRAFTMCP_EXIT_CODE=1"
  goto finish
)
echo Server folder: "%CD%"
if not exist "paper.jar" (
  echo [ERROR] Missing paper.jar. Put this script in the folder containing paper.jar.
  set "CRAFTMCP_EXIT_CODE=1"
  goto finish
)
if not exist "server.properties" (
  echo [ERROR] Missing server.properties in the server folder.
  set "CRAFTMCP_EXIT_CODE=1"
  goto finish
)
"%SystemRoot%\System32\where.exe" java >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Java was not found on PATH. Install or select JDK 17.
  set "CRAFTMCP_EXIT_CODE=1"
  goto finish
)
if "%CRAFTMCP_CHECK_ONLY%"=="1" (
  echo Required files and Java found. Paper was not started.
  goto finish
)
echo Starting Paper with JDWP at 127.0.0.1:5005.
echo RCON follows server.properties and can be enabled together with JDWP.
java -Xms1G -Xmx2G -agentlib:jdwp=transport=dt_socket,server=y,suspend=%CRAFTMCP_SUSPEND%,address=127.0.0.1:5005 -jar paper.jar --nogui
set "CRAFTMCP_EXIT_CODE=%ERRORLEVEL%"
if not "%CRAFTMCP_EXIT_CODE%"=="0" (
  echo [ERROR] Paper exited with code %CRAFTMCP_EXIT_CODE%. Read the Java error above.
) else (
  echo Paper process exited.
)

:finish
if "%CRAFTMCP_NO_PAUSE%"=="0" pause
exit /b %CRAFTMCP_EXIT_CODE%
