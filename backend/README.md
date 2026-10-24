# sentinel-backend

Local FastAPI backend for SENTINEL AI. It binds to `127.0.0.1` only, requires a
per-start bearer token and is bundled as a Tauri sidecar in release builds.

## Packages

| Package | Responsibility |
| --- | --- |
| `permissions` | Allowlist registry, `decide()` matrix, engine (approvals, undo, rate limit, emergency stop) |
| `actions` | All allowlisted actions (read, automation, security) |
| `api` | Guard middleware (token, Host, Origin), routes, SSE streams |
| `chat`, `llm` | Tool-use loop, provider interface (Claude, offline, scripted), cost limits, prompts |
| `memory` | SQLite FTS5 + vectors, secret filter, extraction from user messages |
| `monitor` | Processes, ports, autostart, Sysmon, folder watch, fixed rules, alerts |
| `network` | ARP inventory, ping sweep, port check (RFC 1918 only) |
| `automation` | Program allowlist, routines, reminders, files, system control |
| `server_agent`, `notify`, `news`, `voice`, `agents`, `plugins` | see names |

## Development (Windows PowerShell)

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
python -m sentinel   # standalone, writes port+token to %LOCALAPPDATA%\SentinelAI\session.json
```

Optional extras: `.[voice]`, `.[embeddings]`, `.[package]` (PyInstaller).
