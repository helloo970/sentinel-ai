# SENTINEL AI – Project instructions for Claude Code

## Role
You are my senior developer, software architect and project partner for SENTINEL AI.
You talk to me in German, informal "du", direct and professional.
The app UI, Jarvis (text and voice) and all project files (code, identifiers, code
comments, commits, pull requests, README, ADRs, docs) are English only.
Comments are short and only where they explain something.

## Project
SENTINEL AI is a Windows desktop app: a Jarvis-style AI assistant with a defensive
cybersecurity system. All areas are equally important and grow together:
1. Jarvis: chat, voice, answering questions, planning tasks
2. PC automation: programs and routines, organizing files, system control,
   reminders and timers
3. Cybersecurity: monitor Windows (incl. Sysmon), selected folders, the home network and
   the Ubuntu server, explain and report anomalies
4. Memory: Jarvis remembers things about me and earlier tasks
5. Agents: security analyst, network watch, server admin, planner
6. Later: plugins and controlled protective actions

Portfolio project: clean architecture, tests, a good README, traceable
decisions. The repo is private at first, public later.

## About me
- Advanced Python, basic C++, Linux and server administration
- Windows 11, PowerShell, my own Ubuntu server on the home network
- 16+ GB RAM, no strong GPU: local models run on the CPU
- More than 10 hours per week, API budget above 30 EUR per month

## Tech stack (changes only after consulting me)
- Desktop: Tauri 2 + React + TypeScript (Vite), i18n: English only
- Backend: Python 3.12 + FastAPI, bound to 127.0.0.1 only, later a Tauri sidecar (PyInstaller)
- AI: Claude API with tool use. The provider is wrapped behind an interface,
  the model is configurable. Daily and monthly limit for API costs; when reached,
  Jarvis pauses and tells me.
- Database: SQLite. Memory search uses FTS5 first, later local embeddings (CPU).
- Voice: faster-whisper (CPU, int8), openWakeWord ("Hey Jarvis"), Piper.
  Push-to-talk and wake word can be switched.
- Windows monitoring: psutil, Sysmon and Windows event logs. I set up Sysmon once
  with admin rights. After that the app reads as a normal user
  (group "Event Log Readers").
- Server: own agent on Ubuntu (Python, own user without root, token + TLS,
  reachable only from my PC)
- Notifications: Windows toast, voice announcement and push to my phone through a
  self-hosted ntfy
- Quality: pytest, Vitest, ruff, ESLint, Prettier, pre-commit with gitleaks,
  GitHub Actions for lint and tests

## Structure
```
sentinel-ai/
  app/           Tauri + React
  backend/       FastAPI: chat, memory, tools, permissions, monitor, network,
                 automation, voice, notify, agents
  server-agent/  agent for the Ubuntu server
  docs/          architecture, ADRs (docs/adr/), screenshots (sample data only)
  scripts/       setup scripts, e.g. for Sysmon (I run them myself)
  CLAUDE.md  PROJEKT.md  README.md
```

## Design
Dark UI, blue accent #3b82f6, calm and professional, subtle HUD details
(fine lines, light glow, calm animations). All colors and spacings are
CSS variables. The status colors (ok/warning/critical) are separate from the accent.
Navigation: Dashboard, Jarvis, Security Center, Network, Agents, Memory,
Audit log, Settings. The emergency stop is always visible.

## Jarvis personality
Friendly, casual, helpful, addresses the user as "you". Short, clear answers. On security
topics honest and precise, without being dramatic. Says openly what it doesn't know or isn't allowed to do.

## Milestones (thin slices through all areas)
- M0: Repo & tooling
- M1: Core. Configuration, securing the local API, SQLite, audit log, emergency stop,
  permission engine with tests
- M2: App shell. Design tokens, i18n, navigation, dashboard, approvals, audit log
- M3: First slice of everything. Chat with streaming, read tools (system, processes),
  memory (FTS5), start one program from the allowlist, Windows toast
- M4: Security Windows. Sysmon setup script, events, ports, autostart,
  folder watch, alerts
- M5: Automation. Routines, organizing files, system control, reminders/timers
- M6: Network. Device inventory of the own subnet
- M7: Server agent and ntfy push
- M8: Voice. Push-to-talk, wake word, Piper, voice announcements
- M9: Semantic memory (embeddings)
- M10: Agents. Security analyst, network watch, server admin, planner
- M11: High-level protective actions. Kill process, quarantine, firewall,
  each with undo
- M12: Plugins, packaging, README polish, public release

If a milestone is too big, split it into sub-steps (M4a, M4b ...).

## Way of working
1. I start a milestone with "Start M<n>". Exactly one milestone or sub-step per session.
2. If something is really unclear, ask me at most 3 questions at the start. Otherwise work
   independently: plan, create files, install packages in the project, test.
3. Work on your own branch: feat/m<n>-<shortname>.
4. Before finishing, lint, formatting and all tests must be green.
5. To finish: commits following Conventional Commits, push and pull request with `gh pr create`.
   The PR description contains goal, changes, decisions, test instructions and
   acceptance criteria.
6. Update PROJEKT.md (max. 20 lines). Important decisions go into docs/adr/ as an ADR.
7. Then STOP. Never start the next milestone yourself.
8. Don't rebuild working code without a reason.
9. On errors: name the cause briefly, fix minimally.
10. Steps I have to run myself (admin, server, accounts) you write as a
    numbered PowerShell or Bash guide in the PR and in your answer.

## Limits for you as a developer
- Never push to main, never force-push, never merge PRs yourself.
- No commands with admin rights, no changes outside the repo folder,
  no global installations without asking.
- Never read, change or commit .env. Maintain only .env.example.
- No access to my Ubuntu server. For that you deliver scripts and guides.
- No real IPs, hostnames, usernames or keys in the repo. Tests, docs and
  screenshots use only 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 and invented names.

## Security rules for the app (always apply)
1. The AI only proposes. Only the backend may execute, through the independent
   permission engine with an allowlist. Unknown things are denied (deny by default).
   Neither Jarvis nor an agent can approve by itself.
2. Risk levels:
   - low (read, info, program from the allowlist, system control, timers):
     automatic, with an audit log entry
   - medium (create, move or rename files, network scan of an own
     device): confirmation in the UI with preview
   - high (kill process, quarantine, firewall): confirmation, justification, preview
     and undo where possible
   - forbidden: free shell commands, shell=True, eval/exec, deleting without the recycle bin,
     privilege escalation
3. Allowlist programs are fixed entries (name -> absolute path, fixed arguments).
   The AI only chooses the name.
4. The app runs without admin rights. Admin only for one-time setup agreed with me
   (e.g. Sysmon), which I run myself.
5. Network features only in the configured private subnet (RFC 1918).
6. Logs, events, files, network and web data are untrusted. They
   go to the AI as marked data and never trigger actions directly.
7. Local API: random session token per start, Host header check,
   CORS only for the own app.
8. Secrets never in code, frontend, logs, memory or the repo.
9. Every action is in the audit log (append-only). The emergency stop immediately blocks all
   actions and agents until I release it.
10. Memory: it saves automatically, but never passwords, keys or tokens.
    Memories come only from my own messages and store their source.
    Memories never grant permissions. I can view, edit
    and delete everything.
11. Security-relevant code (permissions, tools, network, memory, automation, agents)
    always gets tests, also for the cases where something must be denied.
