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

IS_WSL=0
if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then IS_WSL=1; fi

echo "serving $PWA_DIR"
echo
echo "  this machine : http://localhost:${PORT}/"
if [[ -n "$LAN_IP" ]]; then
  echo "  phone / LAN  : http://${LAN_IP}:${PORT}/"
else
  echo "  phone / LAN  : (no network address found -- connect to Wi-Fi first)"
fi

if [[ "$IS_WSL" == "1" ]]; then
  echo
  echo "WSL detected: the address above is WSL's NAT address and a phone cannot"
  echo "reach it. Two ways to get the page onto the phone:"
  echo
  echo "  A. keep serving from WSL, forward the port on Windows (admin PowerShell):"
  echo "       netsh interface portproxy add v4tov4 listenport=${PORT} listenaddress=0.0.0.0 \\"
  echo "         connectport=${PORT} connectaddress=${LAN_IP}"
  echo "       netsh advfirewall firewall add rule name=\"FYP PWA ${PORT}\" dir=in action=allow \\"
  echo "         protocol=TCP localport=${PORT}"
  echo "     then open http://<Windows-LAN-IP>:${PORT}/ on the phone (ipconfig shows it)."
  echo "     Note: WSL's address changes after a restart; delete the rule with"
  echo "       netsh interface portproxy delete v4tov4 listenport=${PORT} listenaddress=0.0.0.0"
  echo
  echo "  B. copy pwa/ to Windows and serve it there (no admin, survives restarts):"
  echo "       cp -r '$PWA_DIR' /mnt/c/Users/<you>/Desktop/pwa"
  echo "       # then in Windows: cd %USERPROFILE%\\Desktop\\pwa && python -m http.server ${PORT}"
  echo "     and open http://<Windows-LAN-IP>:${PORT}/ on the phone."
  echo
  echo "  The Windows browser itself can use http://localhost:${PORT}/ right now"
  echo "  (WSL forwards localhost ports to Windows automatically)."
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
