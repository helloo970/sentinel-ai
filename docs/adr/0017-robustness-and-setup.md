# 17. Robustness pass: database safety, visible errors and in-app setup

- Status: accepted
- Date: 2026-10-24

## Context

A review of the running app found silent failures: a corrupted SQLite file after two
SENTINEL processes wrote at once, a server agent that answered 403 after the PC got a
new IP, a malware feed that abuse.ch now only serves with an auth key, and settings
that could only be changed in `.env`.

## Decision

- Single instance: the backend takes an OS file lock (`sentinel.lock`) before opening the
  database. CLI commands that write (gmail, brain, db) refuse to run while it is held.
- Daily online backup of the database with the SQLite backup API after `quick_check`,
  keeping 7 copies; `sentinel db restore` copies the newest one back and keeps the
  damaged files aside.
- `/status` carries `warnings` (server, malware list, voice) with a plain fix; the UI
  shows them as a chip in the top bar and in Settings. A 403 from the server agent backs
  off for 5 minutes instead of retrying constantly.
- The abuse.ch key is entered only via `sentinel abusech login` (hidden input, Windows
  Credential Manager) and never logged.
- Quiet hours (default 23:00-07:00): no voice announcements or greetings, critical alerts
  still speak.
- Settings → Setup edits home city, Google account and quiet hours (stored in the local
  store, validated server-side), lists connections with the CLI command for missing keys,
  and loads the personal profile JSON, which is kept only in the local config folder.
- The brain graph drops links to missing nodes, which crashed the force simulation.

## Consequences

Keys still never pass through the UI. Personal profile data stays outside the repo.
