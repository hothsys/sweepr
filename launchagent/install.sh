#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT=47836
PLIST_LABEL="com.sweepr.server"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
APP_NAME="Sweepr"
APP_DEST="/Applications/${APP_NAME}.app"
SERVER_SCRIPT="$SCRIPT_DIR/server.py"

# Find python3
PYTHON="$(which python3 2>/dev/null || echo "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3")"
if [ ! -x "$PYTHON" ]; then
    echo "Error: python3 not found"
    exit 1
fi

# Build PATH that includes common tool locations (Homebrew, nvm, etc.)
FULL_PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

echo "Installing Sweepr..."
echo "  Python: $PYTHON"
echo "  Server: $SERVER_SCRIPT"

# --- App Icon (SVG → ICNS) ---
ICONSET_DIR="/tmp/Sweepr.iconset"
ICNS_FILE="$APP_DEST/Contents/Resources/Sweepr.icns"
SVG_FILE="$SCRIPT_DIR/logo.svg"

# --- LaunchAgent ---
echo "Setting up LaunchAgent..."
mkdir -p "$HOME/Library/LaunchAgents"

# Unload existing if present
launchctl unload "$PLIST_DEST" 2>/dev/null || true

sed -e "s|__PYTHON_PATH__|$PYTHON|g" \
    -e "s|__SERVER_PATH__|$SERVER_SCRIPT|g" \
    "$SCRIPT_DIR/launchagent/com.sweepr.server.plist" > "$PLIST_DEST"

launchctl load "$PLIST_DEST"
echo "  LaunchAgent installed and loaded."

# --- .app Bundle ---
echo "Creating Sweepr.app..."
rm -rf "$APP_DEST"
mkdir -p "$APP_DEST/Contents/MacOS"
mkdir -p "$APP_DEST/Contents/Resources"

# Generate icon from SVG using macOS built-in tools
if [ -f "$SVG_FILE" ]; then
    rm -rf "$ICONSET_DIR"
    mkdir -p "$ICONSET_DIR"

    # Use qlmanage to render SVG → PNG at high resolution
    qlmanage -t -s 1024 -o /tmp/ "$SVG_FILE" 2>/dev/null || true
    RENDERED_PNG="/tmp/$(basename "$SVG_FILE").png"

    if [ -f "$RENDERED_PNG" ]; then
        for size in 16 32 64 128 256 512; do
            sips -z $size $size "$RENDERED_PNG" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null 2>&1
            double=$((size * 2))
            sips -z $double $double "$RENDERED_PNG" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null 2>&1
        done
        iconutil -c icns "$ICONSET_DIR" -o "$ICNS_FILE" 2>/dev/null && \
            echo "  Icon generated." || echo "  Icon generation failed, using default."
        rm -rf "$ICONSET_DIR" "$RENDERED_PNG"
    else
        echo "  Could not render SVG, using default icon."
    fi
fi

cat > "$APP_DEST/Contents/Info.plist" << 'INFOPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Sweepr</string>
    <key>CFBundleIdentifier</key>
    <string>com.sweepr.launcher</string>
    <key>CFBundleName</key>
    <string>Sweepr</string>
    <key>CFBundleIconFile</key>
    <string>Sweepr</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
INFOPLIST

cat > "$APP_DEST/Contents/MacOS/Sweepr" << LAUNCHER
#!/bin/bash
export PATH="$FULL_PATH"
PORT=$PORT
URL="http://localhost:\$PORT"
PYTHON="$PYTHON"
SERVER_SCRIPT="$SERVER_SCRIPT"

# Check if server is already running
if curl -s -o /dev/null -w "%{http_code}" "\$URL/api/health" 2>/dev/null | grep -q "200"; then
    open "\$URL"
    exit 0
fi

# Start server in background with full PATH so brew/npm are found
PATH="$FULL_PATH" "\$PYTHON" "\$SERVER_SCRIPT" &

# Wait for server to be ready (up to 5 seconds)
for i in \$(seq 1 50); do
    if curl -s -o /dev/null "\$URL/api/health" 2>/dev/null; then
        open "\$URL"
        exit 0
    fi
    sleep 0.1
done

osascript -e 'display dialog "Sweepr failed to start." buttons {"OK"} default button "OK" with icon stop'
exit 1
LAUNCHER

chmod +x "$APP_DEST/Contents/MacOS/Sweepr"
echo "  Sweepr.app created at $APP_DEST"

# --- Verify ---
echo ""
echo "Waiting for server to start..."
sleep 2
if curl -s -o /dev/null "http://localhost:$PORT/api/health" 2>/dev/null; then
    echo "Server is running at http://localhost:$PORT"
    echo ""
    echo "Installation complete!"
    echo "  - The server will start automatically when you log in."
    echo "  - Click 'Sweepr' in /Applications to open it."
    echo "  - Or visit http://localhost:$PORT in your browser."
    open "http://localhost:$PORT"
else
    echo "Server may still be starting. Try opening http://localhost:$PORT in a moment."
fi
