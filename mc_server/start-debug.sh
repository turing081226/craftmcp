#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -f paper.jar ] || [ ! -f server.properties ]; then
    echo "Create paper.jar and server.properties; see docs/debugger-python.md." >&2
    exit 1
fi
if ! grep -Eq '^enable-rcon=false[[:space:]]*$' server.properties; then
    echo "Set enable-rcon=false for this independent debugger test." >&2
    exit 1
fi
suspend=n
if [ "${1:-}" = "--suspend" ]; then suspend=y; fi
exec java -Xms1G -Xmx2G "-agentlib:jdwp=transport=dt_socket,server=y,suspend=$suspend,address=127.0.0.1:5005" -jar paper.jar --nogui
