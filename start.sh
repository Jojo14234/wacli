
#!/bin/sh
set -eu

export WACLI_STORE_DIR=/data/store
export MCP_SERVER_URL=http://127.0.0.1:8765/mcp

: "${CONTROL_PLANE_API_KEY:?Missing API key}"
: "${CONTROL_PLANE_TUNNEL_ID:?Missing tunnel ID}"

SYNC_PID=""
MCP_PID=""
TUNNEL_PID=""

cleanup() {
  trap - EXIT INT TERM
  for pid in "$SYNC_PID" "$MCP_PID" "$TUNNEL_PID"; do
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$SYNC_PID" "$MCP_PID" "$TUNNEL_PID"; do
    if [ -n "$pid" ]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

echo "[1/3] Starting WhatsApp synchronization"
/usr/local/bin/wacli --store /data/store \
  sync --follow --presence-mode quiet --max-reconnect 0 &
SYNC_PID=$!

echo "[2/3] Starting WhatsApp MCP server"
/opt/wa-mcp/bin/python3 /opt/whatsapp_mcp.py &
MCP_PID=$!

echo "Waiting for MCP server..."
/opt/wa-mcp/bin/python3 - <<'PY'
import socket
import time
import sys

for _ in range(30):
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=1):
            print("MCP server ready")
            sys.exit(0)
    except OSError:
        time.sleep(1)

sys.exit("MCP server failed to start")
PY

echo "[3/3] Starting OpenAI tunnel"
/usr/local/bin/tunnel-client run &
TUNNEL_PID=$!

echo "All three processes launched"

while true; do
  for pid in "$SYNC_PID" "$MCP_PID" "$TUNNEL_PID"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A component stopped unexpectedly: PID $pid" >&2
      exit 1
    fi
  done
  sleep 3
done
