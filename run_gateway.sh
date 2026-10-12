#!/bin/bash
# FreeRoam Polaris Gateway - Unified Launcher
# Runs Chat Gateway (5000), Burst Receiver (5001), and Sandbox Gateway (7007)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${FREEROAM_DATA_DIR:-/data/freeroam}"

# Create data directories
mkdir -p "$DATA_DIR/bursts"
mkdir -p "$DATA_DIR/logs"

# ADB bridge to the operator's phones (Polaris phone-control tools use /usr/bin/adb).
# adb connect is idempotent; per-phone watchdogs auto-reconnect after WiFi flaps.
# NOTE: the container's adb is Debian v28 - fixed-port 5555 (tcpip) only, no mDNS/TLS.
# After a phone reboot 5555 dies on this ROM: re-arm from the Mainframe with
# rearm_phones.sh (beside this script) - it uses ~/platform-tools/adb v37 mDNS over
# the persistent TLS pairing (Rich guid adb-FY24031100E6-71NED1, Matt guid
# adb-FY24031100BE-usU8h8) and can re-arm tcpip 5555 over the wireless transport.
# Rich FY24031100E6 = .42 | Matt FY24031100BE (RedMagic 9) = .98
if command -v adb >/dev/null 2>&1; then
    RICH_ADB_HOST="${PHONE_ADB_HOST:-192.168.50.42:5555}"
    MATT_ADB_HOST="${MATT_PHONE_ADB_HOST:-192.168.50.98:5555}"
    for PHONE_HOST in "$RICH_ADB_HOST" "$MATT_ADB_HOST"; do
        adb connect "$PHONE_HOST" >/dev/null 2>&1
        ( while sleep 30; do
              adb -s "$PHONE_HOST" get-state >/dev/null 2>&1 || adb connect "$PHONE_HOST" >/dev/null 2>&1
            done ) &
    done
    echo "📱 ADB watchdogs armed: Rich $RICH_ADB_HOST | Matt $MATT_ADB_HOST"
else
    echo "📱 ADB not installed - phone-control tools disabled"
fi

# Production WSGI stack: gunicorn+gevent for the chat gateway (werkzeug fallback
# if the gevent stack is ever missing). No hot-reload in production.

# Unbuffered stdout so [Tool Execution]/[DEBUG] log lines reach docker logs immediately
export PYTHONUNBUFFERED=1

echo "🚀 FreeRoam Polaris Gateway Starting..."
echo "📂 Data Directory: $DATA_DIR"
echo "🏭 Production WSGI: gunicorn+gevent (werkzeug fallback if missing)"
echo ""

# Start Burst Receiver on port 5001
echo "📡 Starting Burst Receiver on port 5001..."
cd "$SCRIPT_DIR"
BURST_STORAGE_DIR="$DATA_DIR/bursts" TELEMETRY_LOG="$DATA_DIR/telemetry.log" \
    python3 burst_receiver.py &
BURST_PID=$!
echo "   Burst Receiver PID: $BURST_PID"

# Start Chat Gateway on port 5000 (production: gunicorn + gevent, 1 worker)
echo "💬 Starting Polaris Gateway (Stark LLM - Text-Only Mainframe (voice lives on X17 LGI)) on port 5000..."
cd "$SCRIPT_DIR"
if python3 -c "import gunicorn, gevent, geventwebsocket" >/dev/null 2>&1; then
    # NOTE: Flask-SocketIO requires a message queue (e.g. Redis) for >1 worker - keep -w 1
    gunicorn -k gunicorn_gevent_ws.GeventWebSocketWorker -w 1 \
        --bind 0.0.0.0:5000 \
        --access-logfile - \
        --timeout "${GUNICORN_TIMEOUT:-120}" \
        --graceful-timeout 30 --keep-alive 5 \
        --log-level info \
        polaris_continuous_learning_gateway:app &
else
    echo "   [WARN] gunicorn/gevent not installed - falling back to werkzeug dev server"
    python3 polaris_continuous_learning_gateway.py &
fi
GATEWAY_PID=$!
echo "   Polaris Gateway PID: $GATEWAY_PID"

# Start Sandbox Gateway on port 7007
echo "🔮 Starting Sandbox Gateway (X17 LGI SSH) on port 7007..."
cd "$SCRIPT_DIR"
python3 sandbox_gateway.py &
SANDBOX_PID=$!
echo "   Sandbox Gateway PID: $SANDBOX_PID"

echo ""
echo "=== FreeRoam Polaris Gateway Online ==="
echo "Chat Gateway:     http://0.0.0.0:5000/api/chat"
echo "Burst Receiver:   http://0.0.0.0:5001/api/burst"
echo "Telemetry:        http://0.0.0.0:5001/api/telemetry"
echo "Sandbox Gateway:  http://0.0.0.0:7007/"
echo "JARVIS Toolkit:   http://0.0.0.0:5000/api/jarvis/"
echo ""
echo "Press Ctrl+C to stop all services..."

# Wait for all processes - container stays alive as long as any service is running
wait $BURST_PID $GATEWAY_PID $SANDBOX_PID