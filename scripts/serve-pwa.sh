#!/usr/bin/env bash
# Serve the PWA demo over plain HTTP and print the address to open.
#
# Why plain HTTP is enough: the page does not register a Service Worker, so it
# needs neither HTTPS nor an install prompt. (Service Workers only run on HTTPS
# or localhost -- see docs/tasks/pwa-demo-hosting-options.md. If you ever want
# "add to home screen" on a phone, you need form B or C there instead.)
#
# Usage:
#   scripts/serve-pwa.sh              # port 8080
#   scripts/serve-pwa.sh 9000         # another port
#
# Then, on the phone: connect to the same Wi-Fi/hotspot as this machine and open
# the printed http://<ip>:<port>/ address.

set -euo pipefail

PORT="${1:-8080}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PWA_DIR="$ROOT/pwa"

if [[ ! -f "$PWA_DIR/index.html" ]]; then
  echo "not found: $PWA_DIR/index.html" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to serve static files" >&2
  exit 1
fi

# First non-loopback IPv4 address; empty if the machine is offline.
LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | grep -v '^127\.' | head -1 || true)"

echo "serving $PWA_DIR"
echo
echo "  this machine : http://localhost:${PORT}/"
if [[ -n "$LAN_IP" ]]; then
  echo "  phone / LAN  : http://${LAN_IP}:${PORT}/"
else
  echo "  phone / LAN  : (no network address found -- connect to Wi-Fi first)"
fi
echo
echo "checklist on the page:"
echo "  1. status line says the model loaded and shows 20,193 parameters"
echo "  2. press 跑一致性自检  -> both rows 通过, end-to-end diff < 1e-3"
echo "  3. press 测 20 次时延  -> median/P95 printed (W2 gate: 1 s / 2 s)"
echo "  4. press 复制结果      -> paste the JSON into the report or send it back"
echo
echo "Ctrl-C stops the server."
echo

cd "$PWA_DIR"
exec python3 -m http.server "$PORT" --bind 0.0.0.0
