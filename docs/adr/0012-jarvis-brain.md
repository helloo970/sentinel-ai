# 12. Jarvis' brain: a 3D knowledge graph

- Status: accepted
- Date: 2026-10-22

## Context

The owner wants a visible "brain" for Jarvis: a 3D knowledge graph you can rotate, similar to
the one in Moritz Maaker's Jarvis video. It should know about him, his projects, his
devices and Jarvis' own abilities. It should learn from conversations, from his folders
(Documents, Desktop, the SENTINEL project) and from SENTINEL itself. Thoughts should light
up live while Jarvis answers. Voice commands: "What do you know about …?", "Remember that …",
"Forget …" and "Show me my brain". The existing memory store stays separate.

## Decision

- New module `sentinel.brain` with its own tables: categories, nodes (label, category,
  summary, origin, source), edges, an FTS5 index and a learning queue. Every node stores
  its origin (`user`, `chat`, `seed`, `system`, `ai`, `file`) and its source.
- The taboo filter `brain/privacy.py` rejects secrets, school grades and reports,
  street addresses, phone numbers and e-mail addresses before anything is stored.
  Folder excerpts are also scrubbed before they are queued.
- Learning runs only when the app is idle (no chat for `SENTINEL_BRAIN_IDLE_MIN` minutes,
  voice idle, no emergency stop), with at most `SENTINEL_BRAIN_AI_DAILY` model requests
  per day (default 15), because free models have a small quota. Input always goes to the
  model as `<untrusted_data>`, with no tools. The output is JSON facts only, and each fact
  passes the taboo filter again.
- Folder ingestion only reads `.md` and `.txt` files. It never opens files whose names
  suggest credentials, grades or contracts, skips large files and build folders, and
  goes at most three levels deep.
- Knowledge with origin `file` is passed to the chat model only inside `<untrusted_data>`
  and marks the turn as tainted, so actions need confirmation. Brain knowledge never
  grants permissions.
- "Vergiss …" highlights the node in red and deletes it only after "Ja" in the same
  channel within 60 s. Deleting from the UI needs a second click. Both are audit-logged.
  Brain commands act on the user's own data, like the memory API, so they do not go
  through the permission engine.
- Layout like Maaker's: three fixed parts (Brain, Voice, Hand) with flowing "tunnels"
  between them, knowledge areas around the brain, and sources on a ring. SENTINEL fills
  Hand (actions grouped by module), Voice, Sources and a daily logbook by itself.
- Personal data (family, exam dates, preferences) lives in a local profile JSON that
  the owner imports with `sentinel brain profile <file>`. Only this direct command may also
  create reminders. Nodes in the category `stil` from the owner himself are always added to
  Jarvis' context as style wishes.
- The 3D view uses `three` and `3d-force-graph` (new frontend dependencies, agreed with
  the owner), with a bloom pass for the glow and HTML labels so text stays sharp.
- Backup: the graph is encrypted on the PC with AES-256-GCM, using a key derived with
  scrypt from a password the user chooses (`sentinel brain password`). Only the derived key
  and salt are kept, in Windows Credential Manager. The server agent stores the opaque
  bytes under `/var/lib/sentinel-agent/vault` (systemd `StateDirectory`) and keeps the
  last five versions. `sentinel brain restore` restores the graph with the password.

## Consequences

- CLAUDE.md rule 10 ("memories only from my own messages") stays true for the memory
  store. The brain is a separate area that may also learn from folders; that knowledge
  is always marked with its source and treated as untrusted.
- The server agent now has one writable directory; the rest of the hardening is unchanged.
- Learning is slow on purpose. A large folder takes days to sort in with the free quota.
