@echo off
setlocal
cd /d "%~dp0"
if not exist "paper.jar" (
  echo Missing paper.jar. See docs/debugger-python.md.
  exit /b 1
)
if not exist "server.properties" (
  echo Create server.properties and set enable-rcon=false for this manual debugger test.
  exit /b 1
)
findstr /r /i /c:"^enable-rcon=false$" "server.properties" >nul
if errorlevel 1 (
  echo Set enable-rcon=false before starting the independent debugger test.
  exit /b 1
)
set "JDWP_SUSPEND=n"
if /i "%~1"=="--suspend" set "JDWP_SUSPEND=y"
java -Xms1G -Xmx2G -agentlib:jdwp=transport=dt_socket,server=y,suspend=%JDWP_SUSPEND%,address=127.0.0.1:5005 -jar paper.jar --nogui
exit /b %errorlevel%
