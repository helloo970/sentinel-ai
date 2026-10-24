# SENTINEL AI – Project status

**Status:** v0.1.0, M1–M12 + ideas 1–17, 19, 22–25, 27–30 · Branch `feat/m1-m12-turbo`

## Done
- Backend: permission engine (autonomy 0–3, undo, 10/h limit, emergency stop), audit hash chain
- Chat with Claude tool use, taint lock, memory; offline quick commands without AI
- Security: processes, ports, autostart, Sysmon, folders, file check (hashes + Defender),
  USB, browser extensions, camera/microphone, response plugins with PIN
- Home network/server: inventory, agent (TLS/token), Docker, Pi-hole, backups, phone view
- Everyday life: weather, Gmail, parcels, shopping list, price alerts, gaming/focus mode, cleanup
- Brain: 3D knowledge graph in the Jarvis area, learning in idle time, encrypted server backup
- Tests: 519 backend, 47 server agent, 36 frontend; lint green

## Next
- Real use with an API key; tighten rules after false alarms
- Signed installer, license, screenshots with sample data for the public repo

## Open items (do yourself)
- Update the server agent (brain backup), set `sentinel brain password`
- GitHub remote + `gh auth login`, then PR

## Key decisions
- ADR 0003–0012: engine, untrusted data, local API, plugins, phone, AI providers, brain
