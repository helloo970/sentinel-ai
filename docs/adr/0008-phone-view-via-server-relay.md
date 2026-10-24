# 8. Phone view through the server agent, never a port on the PC

- Status: accepted
- Date: 2026-10-21

## Context

The owner wants to see the status, alerts and the emergency stop on his phone at home.
The backend binds to 127.0.0.1 only (ADR 0005). Opening it to the Wi-Fi would expose
the permission engine and the action API to every device on the network.

## Decision

- The PC never listens on the network. A `RelayService` posts a small status every
  5 seconds to the existing server agent (`POST /v1/relay`, same token, TLS and IP
  allowlist as the read endpoints). The response contains pending commands.
- The status has a fixed shape (protection state, autonomy, alert counts, the five
  newest alert titles redacted and cut to 160 characters, active mode, quiet flag).
  The agent sanitizes it again and keeps it in memory only.
- The phone uses a second HTTPS port of the agent (default 8845), restricted to an
  RFC 1918 subnet of /16 or smaller and opened only for that subnet in ufw.
- Login with a PIN (scrypt hash file, set by the owner with `--set-mobile-pin`),
  5 failures per IP lock that IP for 5 minutes, 20 failures per hour lock the login.
  Session cookie `HttpOnly; Secure; SameSite=Strict`, POST needs a custom header,
  strict CSP with no inline script, every text rendered with `textContent`.
- The only command is `halt`. The PC accepts nothing else and logs unknown commands
  as denied. Releasing the emergency stop is only possible on the PC.

## Consequences

- No new attack surface on the PC. The worst case for a compromised server agent is
  a false emergency stop, which is the safe direction.
- Up to about 5 seconds delay until a stop from the phone takes effect.
- The phone shows a browser warning once because the agent uses a self-signed
  certificate. The certificate can be installed on the phone to remove it.
- The agent is no longer strictly read-only: it now holds the relay status in memory.
