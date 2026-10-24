# 15. Hermes as an external agent via MCP

- Status: accepted
- Date: 2026-10-22

## Context

The owner wants to use the Hermes Agent (Nous Research) to run SENTINEL autonomously, for
example through Telegram. Hermes can use tools from MCP servers. SENTINEL's local API
only listens on 127.0.0.1 and uses a per-start session token.

## Decision

- `sentinel mcp` is a small stdio MCP server (JSON-RPC, no extra dependency). Hermes
  starts it as a subprocess on the same PC. It reads the backend port from
  `<data>/agent.json` (no secrets) and calls `/agent/tools` and `/agent/call`.
- `sentinel hermes setup` creates a separate agent token in Windows Credential Manager.
  The backend accepts it only for `/agent/…` paths, so it can never approve actions,
  change settings or release the emergency stop.
- New permission source `EXTERNAL`: read actions run, low-risk actions run on their own
  in Autopilot (and above), everything else becomes an approval in the app. Final
  actions always need confirmation. The emergency stop blocks Hermes completely,
  including reads. Every call is audit-logged as `agent:hermes`.
- Untrusted tool output is passed to Hermes inside `<untrusted_data>`. Thoughts that
  Hermes adds to the brain are stored with origin `ai`, not as the user's own statements.
- Hermes runs on the PC, because SENTINEL is only reachable there and the backend must
  not listen on the network.

## Consequences

- Hermes' own built-in tools (terminal, files) are outside SENTINEL's control. The setup
  guide tells the user to switch them off or run them in Docker.
- The agent token only takes effect after restarting SENTINEL.
