# SENTINEL AI

A Jarvis-style desktop assistant for Windows with a built-in **defensive** security
monitor for your own PC, home network and Linux home server.

The language model only proposes. An independent permission engine decides, logs every
action and can be stopped at any time – from the app, the tray, a hotkey, by voice or
from your phone.

> **Status:** v0.1.0 – milestones M1–M12 done, plus security, home-network and everyday
> features on top. Built and tested on Windows 11 with an Ubuntu home server.
> 367 backend, 32 server-agent and 33 frontend tests.

## Features

### Jarvis

- Streaming chat with Claude tool use, memory of earlier conversations
- The app and Jarvis (text and voice) are English; the HUD uses a calm dark theme with a blue accent (`#3b82f6`)
- One screen, no menu: Jarvis opens everything by voice in full screen – "Briefing", "What's going on in the world?" (3D globe), "Show me the markets" (crypto, indices, stocks, currencies), "Show me the numbers" (key figures), "Show me your brain"; websites open in an app window
- Speech recognition with ElevenLabs Scribe (fallback: local faster-whisper)
- Push-to-talk, wake word "Hey Jarvis", **triple clap** opens the app
- Conversation mode: keeps listening for 30 s after an answer until you say "thanks"
- Morning briefing on the first clap of the day: weather, unread Gmail, security
  status, then a **world-news tour on a map** (country by country)
- Evening daily report: alerts, actions, new devices, Pi-hole, AI costs
- **Offline quick commands** without any language model, e.g. "Gaming mode",
  "Focus mode 45 minutes", "Mode off", "Add milk to the list",
  "What's on the list?" and the emergency stop "SENTINEL, stop"

### Body (hologram)

- A holographic face made of light (MediaPipe face mesh, three.js): the jaw moves with the voice, colour and mood follow Jarvis' state, it blinks and looks at you through the webcam
- Greets you when you sit down; gestures: swipe = next view, open palm = stop, fist = back to Jarvis, thumb up = "yes"
- Camera, face and gesture recognition run locally (MediaPipe models bundled), no image leaves the PC; can be switched off

### Brain (3D knowledge graph)

- Jarvis' brain in the Jarvis view, built like a body: BRAIN (knowledge areas), VOICE, HAND (skills) and a ring of SOURCES that feed it; glowing in 3D, rotate and zoom with the mouse
- Personal profile from a local JSON file (`sentinel brain profile <file>`, example in `docs/brain-profile.example.json`) – never in the repo; style wishes always go to Jarvis
- Learns from your own messages, from SENTINEL itself (skills, programs, devices, server) and from selected folders (`.md`/`.txt`), in idle time with a small daily request budget
- Thoughts light up live while Jarvis answers; "What do you know about …?" flies the camera to the thought
- "Remember that …", "Forget …" (asks before deleting), "Show me my brain"
- Knows everything in SENTINEL (memory, reminders, shopping list, alerts, devices, price alerts, routines, reports); Jarvis can look up, add and (with confirmation) delete through the permission engine
- Never stores secrets, grades, addresses or phone numbers; every thought shows its source
- Encrypted backup to your own server (`sentinel brain password`, `sentinel brain restore`)

### Hermes Agent

- Hermes (Nous Research) can use SENTINEL through MCP (`sentinel mcp`, setup in [docs/hermes.md](docs/hermes.md))
- Separate agent token that only opens `/agent/…`; reads run, low-risk actions run on their own in Autopilot, everything else needs your approval; the emergency stop blocks Hermes too

### PC security

| Check | Details |
| --- | --- |
| Processes, ports, autostart | baseline on the first run, then alerts for new entries |
| Sysmon and event log | fixed detection rules (e.g. Office spawning a shell, LOLBins) |
| Folder watch | risky new files, mass changes (ransomware pattern) |
| File scan | new downloads checked against the MalwareBazaar SHA-256 list and Windows Defender – locally, nothing is uploaded; malicious files go to quarantine |
| USB | new devices, extra warning for new devices that act as a keyboard |
| Browser extensions | Chrome, Edge, Brave, Firefox: new extensions with broad permissions |
| Camera and microphone | which apps use them, warning for a new app on the camera |
| Certificates | expiry of your own TLS services |

Response commands (plugins) react to alerts: `kill_process` (PIN), `quarantine_file`
(undo), `rescue_files` (copy only), `raise_alarm`. Your own plugins are loaded only
after you approved the exact file version with your PIN.

### Home network and server

- Device inventory of your own RFC 1918 subnet with vendor lookup (offline), alerts
  for unknown devices ("I know it" / "Unknown" buttons), port check of own devices
- Read-only **server agent** for Ubuntu: status, services, failed SSH logins with an
  attack map (offline GeoIP), updates, Docker containers, Pi-hole statistics
- **Backup watcher:** warns when a backup folder on the server is missing, empty or
  older than a day
- **Phone view** through the server agent: status, alerts and the emergency stop on
  your phone in the home Wi-Fi – the PC itself opens no port
- Push notifications through a self-hosted ntfy

### Automation and everyday life

- Allowlisted programs, routines, reminders and timers, volume/media/lock screen
- **Gaming and focus modes:** start and close programs, quiet notifications, optional
  timer; ending the mode restores everything
- Weekly cleanup of the downloads folder, undo possible for a week
- Weather, read-only Gmail (sender and subject only), **parcel tracking** from
  shipping mails, **shopping list**, **price alerts** for allowlisted shops
- Order suggestions: Jarvis researches products in allowlisted shops; after your
  approval with PIN it only opens the product page – SENTINEL never pays

### Agents and memory

- Security analyst, network watch, server admin and planner (they only propose)
- Memories come only from your own messages, never secrets; FTS5 + local vectors;
  everything can be viewed, edited and deleted

## Security model

- **The AI only proposes.** Every action is an allowlisted `ActionSpec` executed by
  the permission engine: strict parameters, risk class, autonomy level, rate limit,
  emergency stop and audit log. Unknown actions are denied.
- **Forbidden:** free shell commands, `shell=True`, `eval`/`exec`, privilege
  escalation, deleting without the recycle bin.
- **Final actions always need confirmation** (recycle bin, opening an order page).
  Firewall and system settings are not implemented because they need admin rights.
- **Untrusted data never controls anything.** Logs, events, files, mails, web and
  network data reach the model only as `<untrusted_data>` and switch tools off for
  that turn. A conversation that contained such data needs confirmation for actions.
- **Local only.** Backend on `127.0.0.1` with a random token per start, Host and
  Origin checks, network features limited to RFC 1918.
- **Secrets** never go into code, frontend, logs, memory or the repository. API key
  in `.env`, Gmail app password in Windows Credential Manager.
- **Emergency stop** blocks all actions and agents until you release it on the PC:
  UI button, tray, `Ctrl+Alt+End`, "SENTINEL, Stopp" or the phone view.

Details: [docs/architecture.md](docs/architecture.md) and the decision records in
[docs/adr/](docs/adr/).

### Autonomy levels

| Level | Behaviour |
| --- | --- |
| 0 Observer | Read and report only; every action needs confirmation |
| 1 Assistant | Low risk automatically, everything else with confirmation |
| 2 Autopilot (default) | Everything reversible automatically, notification with undo |
| 3 Guardian | Additionally a protective reaction when a fixed rule reports *critical* |

More than 10 autonomous actions per hour switch back to level 1 with a warning.

## Tech stack

| Area | Technology |
| --- | --- |
| Desktop | Tauri 2, React 19, TypeScript, Vite, i18next (English only), three.js + 3d-force-graph |
| Backend | Python 3.12, FastAPI, SQLite (FTS5), psutil, watchdog; PyInstaller sidecar |
| AI | Claude API behind a provider interface, daily and monthly cost limit |
| Voice | faster-whisper (CPU, int8), openWakeWord, Piper |
| Server agent | Python standard library only, hardened systemd units |
| Quality | pytest, Vitest, ruff, ESLint, Prettier, clippy, pre-commit with gitleaks, GitHub Actions |

## Getting started (Windows, PowerShell)

Requirements: Git, Python 3.12, Node.js LTS, Rust (MSVC toolchain), WebView2.

```powershell
# Backend
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
cd ..

# App
cd app
npm install
npm run check
npm test
cd ..

# Configuration (all options are documented in .env.example)
Copy-Item .env.example .env

# Run in development (Tauri starts Vite and the backend)
.\scripts\dev.ps1
```

The app also runs without an API key: monitoring, automation, quick commands, voice
commands and the emergency stop work offline. Chat, agents and product research need
`ANTHROPIC_API_KEY`.

Optional, run them yourself:

```powershell
.\scripts\setup-sysmon.ps1 -User $env:USERNAME   # once, as administrator
.\scripts\download-voice-models.ps1              # voice packages and models
.\scripts\download-network-data.ps1              # offline GeoIP and vendor lists
.\scripts\build-sidecar.ps1 -Bundle              # sidecar + NSIS installer
```

Gmail (read-only, app password goes to Windows Credential Manager):

```powershell
sentinel gmail login
```

### Your own configuration

Everything lives in `%LOCALAPPDATA%\SentinelAI\config\`:

| File | Content |
| --- | --- |
| `programs.json` | allowlisted programs: name → absolute path and fixed arguments |
| `routines.json` | fixed sequences of allowed actions |
| `modes.json` | modes such as `gaming` and `fokus` (programs to start/close, quiet, minutes) |
| `response_plugins.json` | order and disabled response commands |

### Adding your own response command

1. Copy `backend\src\sentinel\response\_template.py` to
   `%LOCALAPPDATA%\SentinelAI\response_plugins\my_command.py`.
2. Adjust `name`, `description`, `risk_level`, `requires_pin`, `can_run()` and `run()`
   (one class per file; `medium` needs `undo()`, `critical` needs a PIN).
3. In the app: Settings → Response commands → set a PIN → Reload → approve the plugin
   with your PIN. Any change to the file blocks it again.
4. Test at autonomy level 0: Security Center → open an alert → "Response".

```powershell
sentinel plugins list     # all commands with their state
sentinel plugins reload   # reload (running backend) or check the files
```

## Server agent

The agent runs on Ubuntu as its own unprivileged user, speaks HTTPS with a
self-signed certificate, needs a token and only answers the PC's IP. Installation and
updates are a single script that you run yourself on the server:

```bash
sudo SENTINEL_BACKUP_PATHS="/srv/backup" ./install.sh <SERVER_IP> <PC_IP> ssh docker
sudo PYTHONPATH=/opt/sentinel-agent/src python3 -m sentinel_agent --set-mobile-pin
sudo systemctl restart sentinel-agent
```

See [server-agent/README.md](server-agent/README.md) for endpoints, hardening,
Docker, Pi-hole, backups and the phone view.

## Repository layout

```
app/           Tauri shell (src-tauri) and React HUD (src)
backend/       FastAPI backend (sentinel package) and tests
server-agent/  agent for an Ubuntu home server, deploy scripts and tests
docs/          architecture and decision records (ADRs)
scripts/       setup and build helpers that you run yourself
```

## Roadmap

| Milestone | Scope | State |
| --- | --- | --- |
| M0–M3 | Tooling, core, app shell, chat, memory, programs, notifications | done |
| M4 | Windows security: Sysmon, ports, autostart, folder watch, alerts | done |
| M5 | Automation: routines, files, system control, reminders | done |
| M6–M7 | Network inventory, server agent, ntfy | done |
| M8–M9 | Voice, semantic memory | done |
| M10 | Agents | done |
| M11 | Protective actions with undo | done (firewall pending: needs admin) |
| M12 | Plugins, packaging, README | done |
| Next | Signed installer, license, screenshots with sample data, public release | open |

## License

Not decided yet.
