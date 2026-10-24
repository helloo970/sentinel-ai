# 9. Full phone control with commands signed end to end

- Status: accepted (extends ADR 0008)
- Date: 2026-10-21

## Context

The owner wants to control everything from his iPhone, including releasing the emergency
stop. With ADR 0008 the server agent only relays a `halt`. If the agent simply relayed
arbitrary commands, anyone who compromises the home server would control the PC.

## Decision

- **Pairing on the PC** with the owner PIN. The PC creates a 160-bit secret, shows it
  once as a code and stores only two derived keys in Windows Credential Manager:
  `K_pair = HMAC(secret, "pair")` and `K_pin = HMAC(secret, "pin:" + owner PIN)`.
  The phone stores the secret in its local storage.
- **Every phone command is signed** on the phone with `K_pair` over
  `nonce.timestamp.payload`. Releasing the emergency stop, changing autonomy and
  approving PIN actions additionally need `K_pin`, so the phone asks for the owner PIN
  each time. Neither the PIN nor the secret ever passes the server.
- The **agent checks only the shape** of the envelope (hex lengths, payload size,
  queue limit) and forwards it. It cannot create valid signatures.
- The **PC verifies** signature, a timestamp window of ±5 minutes and a one-time nonce,
  locks PIN signatures for 5 minutes after 5 failures and runs the command like a
  click in the app: through the permission engine with source `user` and actor
  `phone`. Allowed commands: halt, resume, autonomy, approve, reject, undo, alert ack,
  any allowlisted action, and a chat message to Jarvis.
- The PC sends control data (approval previews, undo entries, modes, routines,
  program names) only while a phone is paired. Parameters and paths are never sent.
- The phone page uses its own SHA-256/HMAC in JavaScript because `crypto.subtle` is
  not available on pages with a self-signed certificate. A test compares it with
  Python.

## Consequences

- A compromised server can still trigger a false emergency stop and see the status,
  but cannot run actions, release the stop or learn the PIN.
- A stolen, unlocked phone with an active phone-view session can run actions that need
  no PIN. Unpairing on the PC (or pairing again) invalidates it at once.
- On iOS the home-screen app has its own storage, so the code is typed into the app.
