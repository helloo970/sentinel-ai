# 2. Monorepo layout and quality tooling

- Status: accepted
- Date: 2026-10-19

## Context

The project consists of a desktop app (Tauri + React + TypeScript), a local Python
backend, and a Linux server agent. All parts change together, often in the same
milestone. The repository will become public later, so leaked secrets or personal
data in the Git history must be prevented from the start.

## Decision

- One repository with `app/`, `backend/`, `server-agent/`, `docs/` and `scripts/`.
- Python: ruff for linting and formatting (including flake8-bandit rules), pytest.
- TypeScript: ESLint (typescript-eslint), Prettier, Vitest, `tsc --noEmit`.
- pre-commit runs basic hygiene checks, ruff, the app checks and gitleaks before
  every commit.
- GitHub Actions runs the same checks on every pull request. The backend runs on
  Windows and Linux, because the app targets Windows and the agent targets Linux.
- Work happens on feature branches and lands on `main` through pull requests only.

## Consequences

- A single pull request can change frontend and backend consistently.
- Contributors need Python 3.12 and Node.js LTS locally.
- Secret scanning runs both locally (pre-commit) and in CI, so a bypassed local hook
  is still caught before merge.
