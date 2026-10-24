# 7. Response commands as code plugins with per-version approval

- Status: accepted
- Date: 2026-10-20

## Context

The owner wants to add his own protective commands later without touching the core.
ADR 0006 allowed only data plugins. Code plugins execute with the user's rights.

## Decision

- `ResponseCommand` base class with a fixed interface (`name`, `description`,
  `risk_level`, `requires_pin`, `requires_confirm`, `can_run`, `run`, optional `undo`).
- Built-in plugins ship in the package. User plugins live in
  `<data_dir>/response_plugins/` and are loaded only after the user approved the exact
  SHA-256 of the file with the owner PIN. The approved bytes are loaded directly
  (no re-read), so a file swapped after the check is not executed.
- Every plugin becomes an `ActionSpec` (`response_<name>`, not visible to the model).
  Risk mapping: critical → final, high/confirm/PIN → high, medium → reversible,
  low → low. PIN actions never run automatically, also not at guardian level.
- Owner PIN: 6–12 digits, stored as scrypt hash, 5 failures lock it for 5 minutes.
- Plugins receive only an alert id and a minimal context; all event data is untrusted.
  Exceptions and timeouts are caught and audited.

## Consequences

- Python cannot kill a hanging thread: a timeout discards the result but the thread
  may keep running. Approval of reviewed code is the real safeguard.
- Firewall and network isolation are not shipped (admin rights).
