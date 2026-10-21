#!/bin/bash
set -e

PLIST_LABEL="com.sweepr.server"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
APP_PATH="/Applications/Sweepr.app"
PID_FILE="/tmp/sweepr.pid"

echo "Uninstalling Sweepr..."

# Stop LaunchAgent
if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "  LaunchAgent removed."
fi

# Stop server process
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        echo "  Server process stopped (PID $PID)."
    fi
    rm -f "$PID_FILE"
fi

# Remove .app
if [ -d "$APP_PATH" ]; then
    rm -rf "$APP_PATH"
    echo "  Sweepr.app removed."
fi

echo ""
echo "Uninstall complete. The server files in $(cd "$(dirname "$0")/.." && pwd) were not removed."
echo "Delete that directory manually if you no longer need it."
