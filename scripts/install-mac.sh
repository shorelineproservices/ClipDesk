#!/usr/bin/env bash
# Run ON the Mac after scripts/push-to-mac.ps1 copied the project to ~/ClipDesk.
# Installs two user services (no sudo): clipdesk.service (HQ on :8810, all interfaces: ufw already limits the box to
# the LAN and the tailnet) and clipdesk-worker.service (the Mac's employees). Both restart on failure and at boot
# (linger is on for this user). Idempotent: run again after every push.
#   bash ~/ClipDesk/scripts/install-mac.sh           install / update and (re)start
#   bash ~/ClipDesk/scripts/install-mac.sh status
#   bash ~/ClipDesk/scripts/install-mac.sh stop      stop both and disable autostart
#   bash ~/ClipDesk/scripts/install-mac.sh logs
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNITDIR="$HOME/.config/systemd/user"
PY="$(command -v python3)"

case "${1:-install}" in
status) systemctl --user --no-pager status clipdesk.service clipdesk-worker.service | grep -E "service|Active" ; curl -s -o /dev/null -w 'HQ: HTTP %{http_code}\n' http://127.0.0.1:8810/api/health ;;
stop) systemctl --user disable --now clipdesk-worker.service clipdesk.service; echo "stopped and disabled" ;;
logs) journalctl --user -u clipdesk.service -u clipdesk-worker.service -n 60 --no-pager ;;
install)
    $PY -c "import yaml, requests" 2>/dev/null || $PY -m pip install --user pyyaml requests
    mkdir -p "$UNITDIR" "$HERE/data"
    cat > "$UNITDIR/clipdesk.service" <<UNIT
[Unit]
Description=ClipDesk HQ (office page, API, scheduler)
After=network-online.target

[Service]
WorkingDirectory=$HERE
Environment=OFFICE_MACHINE=mac PYTHONUNBUFFERED=1
ExecStart=$PY -m office serve --host 0.0.0.0 --port 8810
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT
    cat > "$UNITDIR/clipdesk-worker.service" <<UNIT
[Unit]
Description=ClipDesk worker (the Mac's employees)
After=clipdesk.service ollama.service
Wants=clipdesk.service

[Service]
WorkingDirectory=$HERE
Environment=OFFICE_MACHINE=mac OFFICE_URL=http://127.0.0.1:8810 PYTHONUNBUFFERED=1
ExecStart=$PY -m office work --machine mac
Restart=always
RestartSec=10
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable clipdesk.service clipdesk-worker.service >/dev/null
    systemctl --user restart clipdesk.service
    sleep 2
    systemctl --user restart clipdesk-worker.service
    sleep 1
    bash "$0" status
    echo "Phone / PC: http://$(tailscale ip -4 2>/dev/null || hostname -I | awk '{print $1}'):8810"
    ;;
*) echo "usage: install-mac.sh [install|status|stop|logs]"; exit 1 ;;
esac
