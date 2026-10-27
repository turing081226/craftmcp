@echo off
cd /d "%~dp0"

:loop
java -Xms1G -Xmx2G -jar paper.jar --nogui
echo Server stopped. Restarting in 5 seconds...
timeout /t 5 /nobreak
goto loop