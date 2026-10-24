#!/usr/bin/env bash
# Installs the SENTINEL server agent on Ubuntu. Run it yourself with:
#   sudo ./install.sh <SERVER_IP> <PC_IP> [services...]
# Example:
#   sudo ./install.sh 10.0.0.5 10.0.0.20 ssh nginx docker
# Optional:
#   SENTINEL_BACKUP_PATHS="/srv/backup /mnt/usb/borg"  backup folders for the backup watcher
#   SENTINEL_MOBILE_PORT=8845                         phone view (0 = off)
#   SENTINEL_MOBILE_SUBNET=10.0.0.0/24                who may open the phone view
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo." >&2
  exit 1
fi
if [[ $# -lt 2 ]]; then
  echo "Usage: sudo $0 <SERVER_IP> <PC_IP> [services...]" >&2
  exit 1
fi

SERVER_IP="$1"
PC_IP="$2"
shift 2
SERVICES=("${@:-ssh}")
PORT="${SENTINEL_AGENT_PORT:-8844}"
PIHOLE_URL="${SENTINEL_PIHOLE_URL:-https://127.0.0.1:8443}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MOBILE_PORT="${SENTINEL_MOBILE_PORT:-8845}"
MOBILE_SUBNET="${SENTINEL_MOBILE_SUBNET:-${PC_IP%.*}.0/24}"
read -r -a BACKUP_PATHS <<< "${SENTINEL_BACKUP_PATHS:-}"
for path in "${BACKUP_PATHS[@]}"; do
  if [[ ! "$path" =~ ^/[A-Za-z0-9._/-]*$ ]]; then
    echo "Backup path '$path' is invalid (must be absolute, without spaces or special characters)." >&2
    exit 1
  fi
done
if [[ ! "$MOBILE_SUBNET" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/(1[6-9]|2[0-9]|3[0-2])$ ]]; then
  echo "SENTINEL_MOBILE_SUBNET must be a network like 10.0.0.0/24 (/16 to /32)." >&2
  exit 1
fi

# 0. The port must not be in use already (e.g. Pi-hole often uses 8443)
if ss -ltnH "sport = :${PORT}" | grep -q . && ! systemctl is-active --quiet sentinel-agent; then
  echo "Port ${PORT} is in use. Choose another port: sudo SENTINEL_AGENT_PORT=8845 $0 ..." >&2
  exit 1
fi
if [[ "$MOBILE_PORT" != "0" ]] && ss -ltnH "sport = :${MOBILE_PORT}" | grep -q .   && ! systemctl is-active --quiet sentinel-agent; then
  echo "Port ${MOBILE_PORT} is in use. Choose another: sudo SENTINEL_MOBILE_PORT=8846 $0 ..." >&2
  exit 1
fi

# 1. Dedicated system user without login and without home
if ! id sentinel-agent >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin sentinel-agent
fi

# 2. Program files (owned by root, read-only for the agent)
install -d -m 0755 /opt/sentinel-agent
rm -rf /opt/sentinel-agent/src
cp -r "$SRC_DIR/src" /opt/sentinel-agent/src
chown -R root:root /opt/sentinel-agent
chmod -R a+rX,go-w /opt/sentinel-agent

# 3. Configuration, token and self-signed certificate
install -d -m 0750 -o root -g sentinel-agent /etc/sentinel-agent
if [[ ! -f /etc/sentinel-agent/token ]]; then
  openssl rand -hex 32 > /etc/sentinel-agent/token
fi
if [[ ! -f /etc/sentinel-agent/cert.pem ]]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
    -subj "/CN=sentinel-agent" -addext "subjectAltName=IP:${SERVER_IP}" \
    -keyout /etc/sentinel-agent/key.pem -out /etc/sentinel-agent/cert.pem
fi
SERVICES_JSON=$(printf '"%s",' "${SERVICES[@]}")
BACKUPS_JSON=""
if [[ ${#BACKUP_PATHS[@]} -gt 0 ]]; then
  BACKUPS_JSON=$(printf '"%s",' "${BACKUP_PATHS[@]}")
fi
cat > /etc/sentinel-agent/config.json <<EOF
{
  "bind": "${SERVER_IP}",
  "port": ${PORT},
  "cert": "/etc/sentinel-agent/cert.pem",
  "key": "/etc/sentinel-agent/key.pem",
  "token_file": "/etc/sentinel-agent/token",
  "allowed_clients": ["${PC_IP}"],
  "services": [${SERVICES_JSON%,}],
  "auth_log": "/var/log/auth.log",
  "docker_snapshot": "/run/sentinel-agent/docker.json",
  "pihole_url": "${PIHOLE_URL}",
  "pihole_password_file": "/etc/sentinel-agent/pihole-password",
  "backup_paths": [${BACKUPS_JSON%,}],
  "backup_snapshot": "/run/sentinel-agent/backups.json",
  "mobile_port": ${MOBILE_PORT},
  "mobile_clients": ["${MOBILE_SUBNET}"],
  "mobile_pin_file": "/etc/sentinel-agent/mobile-pin",
  "vault_dir": "/var/lib/sentinel-agent/vault"
}
EOF
chown root:sentinel-agent /etc/sentinel-agent/*
chmod 0640 /etc/sentinel-agent/*

# 4a. Runtime folder and Docker snapshot (root timer, the agent only reads the file)
install -m 0644 "$SRC_DIR/deploy/sentinel-agent.tmpfiles" /etc/tmpfiles.d/sentinel-agent.conf
systemd-tmpfiles --create /etc/tmpfiles.d/sentinel-agent.conf
install -d -m 0755 /usr/local/lib/sentinel-agent
install -m 0755 "$SRC_DIR/deploy/docker-snapshot.sh" /usr/local/lib/sentinel-agent/docker-snapshot.sh
install -m 0644 "$SRC_DIR/deploy/sentinel-agent-docker.service" /etc/systemd/system/
install -m 0644 "$SRC_DIR/deploy/sentinel-agent-docker.timer" /etc/systemd/system/
install -m 0644 "$SRC_DIR/deploy/sentinel-agent-backups.service" /etc/systemd/system/
install -m 0644 "$SRC_DIR/deploy/sentinel-agent-backups.timer" /etc/systemd/system/

# 4b. systemd service
install -m 0644 "$SRC_DIR/deploy/sentinel-agent.service" /etc/systemd/system/sentinel-agent.service
systemctl daemon-reload
systemctl enable --now sentinel-agent-docker.timer
systemctl enable --now sentinel-agent-backups.timer
systemctl start sentinel-agent-backups.service || true
systemctl enable sentinel-agent
systemctl restart sentinel-agent

# 5. Firewall: only the PC may reach the port
if command -v ufw >/dev/null 2>&1; then
  ufw allow from "${PC_IP}" to any port "${PORT}" proto tcp comment "SENTINEL agent"
  if [[ "$MOBILE_PORT" != "0" ]]; then
    ufw allow from "${MOBILE_SUBNET}" to any port "${MOBILE_PORT}" proto tcp       comment "SENTINEL phone view"
  fi
fi

echo
echo "Done. Now on the PC:"
echo "  1. Copy /etc/sentinel-agent/cert.pem to %LOCALAPPDATA%\\SentinelAI\\config\\server-cert.pem"
echo "  2. Add to the .env:"
echo "     SENTINEL_SERVER_AGENT_URL=https://${SERVER_IP}:${PORT}"
echo "     SENTINEL_SERVER_AGENT_CA=<path to server-cert.pem>"
echo "     SENTINEL_SERVER_AGENT_TOKEN=<contents of /etc/sentinel-agent/token>"
echo "  Show the token: sudo cat /etc/sentinel-agent/token"
echo
echo "Optional Pi-hole: in Pi-hole under Settings > Web interface/API create an app password"
echo "and store it like this (the password is not displayed):"
echo "  sudo install -m 0640 -o root -g sentinel-agent /dev/null /etc/sentinel-agent/pihole-password"
echo "  sudo nano /etc/sentinel-agent/pihole-password   # paste the app password, save"
echo "  sudo systemctl restart sentinel-agent"
echo
if [[ "$MOBILE_PORT" != "0" ]]; then
  echo "Phone view: set the PIN once (at least 6 characters), then restart:"
  echo "  sudo PYTHONPATH=/opt/sentinel-agent/src python3 -m sentinel_agent --set-mobile-pin"
  echo "  sudo systemctl restart sentinel-agent"
  echo "  Then on the phone (on the Wi-Fi): https://${SERVER_IP}:${MOBILE_PORT}/m"
fi
