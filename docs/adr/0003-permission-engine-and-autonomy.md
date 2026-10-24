# 3. Permission engine with risk levels and autonomy levels

- Status: accepted
- Date: 2026-10-20

## Context

SENTINEL can act on the PC (start programs, move files, kill processes). The language
model must never be the component that decides whether an action is allowed, and the
user wants a configurable degree of autonomy.

## Decision

- A single `PermissionEngine.request()` is the only path to execute anything. Actions
  are registered `ActionSpec`s (deny by default) with strict pydantic parameters.
- Five risk classes (read, low, reversible, high, final) and four autonomy levels
  (observer, assistant, autopilot, guardian). The decision is a pure function
  (`decide()`), tested as a matrix.
- Final actions always need confirmation. Fixed rules may never trigger them.
- Agents only create approvals. Approvals are only reachable from the UI API; no tool
  can approve.
- Reversible actions return undo data; undo is possible for 60 s.
- Rate limit: more than 10 autonomous actions per hour drop the level to 1.
- Emergency stop is persisted in SQLite, blocks all non-read actions, approvals and
  agents until the user releases it. Undo stays possible.

## Consequences

- New capabilities are added as `ActionSpec` plus tests for the denied cases.
- Routines and plugins cannot bypass the engine: each step is requested individually
  and high/final actions are not allowed in routines.
- Firewall changes need admin rights and are therefore not implemented yet.
