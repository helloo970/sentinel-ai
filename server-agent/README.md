# sentinel-server-agent

Status agent for a Linux home server. It runs as its own unprivileged
system user, speaks HTTPS with a self-signed certificate, requires a bearer token and
only answers requests from one allowlisted private IP (the SENTINEL PC).

It uses the Python standard library only.

## Endpoints

| Path | Content |
| --- | --- |
| `/health` | liveness (still IP-restricted) |
| `/v1/status` | OS, kernel, uptime, load, memory, disk, reboot required |
| `/v1/services` | `systemctl is-active` for the configured services |
| `/v1/auth-failures` | failed SSH logins in the last hour, top sources |
| `/v1/updates` | pending and security updates |
| `/v1/docker` | containers from a root-written snapshot (the agent has no Docker access) |
| `/v1/pihole` | Pi-hole v6 statistics via the local API and an app password |
| `/v1/backups` | age, file count and size of the configured backup folders |
| `POST /v1/relay` | the PC reports its status for the phone view and receives pending commands from the phone |

## Security

- Runs as `sentinel-agent` (no login, no home), member of `adm` only to read
  `auth.log`. Refuses to start as root.
- systemd hardening: `NoNewPrivileges`, empty capability set, `ProtectSystem=strict`,
  syscall filter, memory limit.
- Bind address and allowed client must be RFC 1918 addresses. The firewall rule
  created by the installer allows only the PC.
- Token comparison in constant time, lockout after 10 failed attempts per minute.
- No endpoint changes anything on the server. The only write is the in-memory
  relay status from the PC.

## Docker and Pi-hole

- A root timer (`sentinel-agent-docker.timer`) writes `docker ps` as JSON to
  `/run/sentinel-agent/docker.json` every minute. The agent only reads this file, so
  it never gets access to the Docker socket (which would be root-equivalent).
- Pi-hole is queried only on `127.0.0.1` with an app password stored in
  `/etc/sentinel-agent/pihole-password` (0640 root:sentinel-agent). Set
  `SENTINEL_PIHOLE_URL` for the installer if Pi-hole does not listen on
  `https://127.0.0.1:8443`.

## Backup watcher

A root timer (`sentinel-agent-backups.timer`, hourly) walks the folders from
`SENTINEL_BACKUP_PATHS` and writes only timestamps, file counts and sizes (no file
names) to `/run/sentinel-agent/backups.json`. The agent never needs read access to the
backups. SENTINEL on the PC warns when the newest file is older than a day.

## Phone view

The PC never opens a port. It pushes a small status (protection state, autonomy, alert
counts and titles, active mode) to `POST /v1/relay` every few seconds and picks up
pending commands in the same call. The phone talks to a second HTTPS port of the agent
(`SENTINEL_MOBILE_PORT`, default 8845):

- only clients from `SENTINEL_MOBILE_SUBNET` (RFC 1918, /16 or smaller; firewall rule
  as well)
- login with a PIN stored as an scrypt hash (`--set-mobile-pin`); 5 failures per IP
  lock that IP for 5 minutes, 20 failures per hour lock the login completely
- session cookie `HttpOnly; Secure; SameSite=Strict`, POST requires a custom header,
  strict Content-Security-Policy, all texts rendered with `textContent`
- without pairing the phone can read the status and trigger the emergency stop
- a phone paired on the PC (Settings → Phone) controls everything: approvals, modes,
  routines, programs, undo, Jarvis chat, autonomy and releasing the emergency stop.
  Commands are signed on the phone with a key the agent does not know and verified on
  the PC; sensitive ones also need the owner PIN (ADR 0009)
- "Add to Home Screen" on the iPhone gives an app icon (web app manifest)

## Install (on the server, run it yourself)

```bash
scp -r server-agent user@<server>:~/
ssh user@<server>
cd server-agent/deploy
chmod +x install.sh
sudo SENTINEL_BACKUP_PATHS="/srv/backup" ./install.sh <SERVER_IP> <PC_IP> ssh nginx
sudo PYTHONPATH=/opt/sentinel-agent/src python3 -m sentinel_agent --set-mobile-pin
sudo systemctl restart sentinel-agent
```

Running the installer again updates the agent and keeps token and certificate.

Then copy `/etc/sentinel-agent/cert.pem` to the PC and add the three
`SENTINEL_SERVER_AGENT_*` values to `.env` (see the installer output).

## Development

```bash
pip install -e ".[dev]"
pytest
```
