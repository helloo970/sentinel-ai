# Architecture

```mermaid
flowchart LR
  subgraph Desktop["Windows desktop (no admin rights)"]
    UI["Tauri 2 shell + React HUD"]
    TRAY["Tray: open / E-STOP / quit<br/>Hotkey Ctrl+Alt+End"]
    subgraph Backend["Python backend · 127.0.0.1 only"]
      API["FastAPI<br/>session token · Host/Origin check"]
      CHAT["Chat service<br/>quick commands · tool-use loop"]
      LLM["LLM provider interface<br/>(Claude / offline)"]
      PERM["Permission engine<br/>allowlist · risk · autonomy<br/>E-STOP · rate limit · undo"]
      ACT["Actions<br/>programs · files · network<br/>processes · reminders"]
      MON["Monitor + fixed rules<br/>processes · ports · autostart · Sysmon<br/>folders · file scan · USB · extensions · camera"]
      SCHED["Scheduler<br/>mode timer · weekly cleanup · price alerts"]
      RELAY["Relay<br/>status out · halt in"]
      MEM["Memory<br/>FTS5 + local vectors"]
      AG["Agents<br/>analyst · network · server · planner"]
      VOICE["Voice<br/>clap · wake word · PTT<br/>Whisper · Piper"]
      DB[("SQLite<br/>append-only audit log")]
    end
  end
  SRV["Server agent (Ubuntu)<br/>TLS · token · IP allowlist<br/>status · Docker · Pi-hole · backups"]
  PHONE["Phone (home Wi-Fi)<br/>PIN · read + emergency stop"]
  NTFY["ntfy (home network)"]
  CLAUDE["Claude API"]
  WEB["Gmail (read-only) · shops · weather · news"]

  UI -- "Bearer token" --> API
  TRAY -- "POST /control/halt" --> API
  API --> CHAT --> LLM <--> CLAUDE
  CHAT -- "proposes" --> PERM
  MON -- "fixed rules only" --> PERM
  AG -- "proposals → approvals" --> PERM
  UI -- "approve / reject / undo" --> PERM
  PERM -- "executes" --> ACT
  PERM --> DB
  MON -- "untrusted data" --> AG
  SCHED -- "fixed rules" --> PERM
  ACT --> SRV
  RELAY -- "POST /v1/relay" --> SRV
  PHONE -- "second HTTPS port" --> SRV
  RELAY -- "halt only" --> PERM
  ACT -- "untrusted data" --> WEB
  API --> NTFY
  VOICE --> CHAT
```

## Trust boundaries

| Source | May trigger autonomous actions? | Reaches the model as |
| --- | --- | --- |
| User in the UI (buttons, approvals) | yes (risk-dependent) | – |
| User in chat / voice | via Jarvis, subject to autonomy level | trusted message |
| Offline quick commands (fixed patterns on the user's own message) | yes, like a UI button | – (no model involved) |
| Phone view (PIN, via server agent) | only the emergency stop, never releasing it | – |
| Fixed detection rules (`monitor/rules.py`) | yes, at autopilot/guardian level | – |
| Agents | never – proposals become approvals | – |
| Logs, events, files, process names, network data, web/news, mails, shop pages | never | `<untrusted_data>` block, tools disabled |

## Permission engine

Every action is an `ActionSpec` in an allowlist (`sentinel/actions`). A request goes
through: lookup → strict parameter validation (pydantic, unknown fields rejected) →
emergency stop → action-specific checks (paths inside managed folders, RFC 1918
addresses, protected processes) → decision → execute / create approval / deny.
Everything is written to an append-only, hash-chained audit log.

| Risk | Examples | Level 0 | Level 1 | Level 2 | Level 3 |
| --- | --- | --- | --- | --- | --- |
| read | system info, processes, alerts | auto | auto | auto | auto |
| low | launch program, reminder, volume, modes, shopping list, price alert | confirm | auto | auto | auto |
| reversible | close program, quarantine, organize, port check, mark device | confirm | confirm | auto + undo | auto + undo |
| high | kill process | confirm | confirm | confirm | auto for critical fixed rules |
| final | recycle bin, open order page (PIN) | confirm | confirm | confirm | confirm |

The undo window is 60 s by default; an action can define its own (weekly cleanup:
7 days). More than 10 autonomous actions per hour switch the level to 1 and every further
autonomous action needs confirmation until the hour has passed.

## Chat and untrusted data

1. The model gets the tool catalogue (generated from the allowlist) and the user's
   memories as *notes, not instructions*.
2. Tool calls go to the engine with source `assistant`.
3. If a tool returns untrusted data, the result is wrapped in `<untrusted_data>` and
   the rest of the turn runs with `tool_choice: none`. Any tool call the model still
   emits is denied.
4. Assistant messages from such turns are flagged; while flagged messages are in the
   context window, actions always need confirmation.
5. Agents and the news briefing call the model **without tools**.
6. Before the model, `chat/quick.py` matches a few fixed English commands (modes,
   shopping list) and the emergency stop. They run through the engine like a button
   press and work without an API key.

## Processes

- The Tauri shell generates a random port and a 256-bit token per start and passes
  both to the backend via environment variables. The frontend obtains them only via
  the `backend_config` command. The backend exits when the shell process is gone.
- Background tasks: monitor loop (default every 30 s), reminder loop (5 s), scheduler
  (5 s: mode timers, weekly cleanup, one price check per minute), relay (5 s, only
  with a configured server agent), server poll (5 min), optional agent schedule,
  voice threads.

## Phone view

The PC never listens on the network (ADR 0008). The relay posts a small, redacted
status to the server agent and receives pending commands in the response; the only
accepted command is `halt`. The phone talks to a separate agent port that is limited
to the home subnet and protected by a PIN.
