# 14. Voice-only shell with full-screen intel views

- Status: accepted
- Date: 2026-10-22

## Context

The owner wants Jarvis to work like the "Jarvis is my secret service" video by Moritz Maaker:
one screen, no side navigation, and Jarvis opens everything by voice: a 3D globe with
the world situation, markets, key figures and the brain.

## Decision

- The app shell has no navigation sidebar. The start screen is Jarvis (core, recent
  conversation, voice control). All areas open full screen through `open_view` (voice
  quick commands or Jarvis tools); Esc, "back" or the logo return to Jarvis. Ctrl+K
  opens a small fallback list. The emergency stop stays visible in the top bar.
- New views: `brain` (full-screen 3D brain), `markets` and `kpis`. "Briefing" runs the
  world tour, then the markets, then the key figures, with narration, and returns home.
- World situation: a three.js globe (country borders from world-atlas, bloom, arcs from
  home to each tour country) replaces the flat map.
- Markets without API keys: CoinGecko (crypto), Yahoo Finance chart API (indices and
  stocks), Frankfurter/ECB (currencies). Only numbers are taken from the responses; names
  come from a fixed list. Data is cached for 10 minutes and the Jarvis tool marks it as
  untrusted.
- Websites open in a separate Tauri webview window (`open_web` event) that has no
  capabilities and therefore no access to SENTINEL. Without the app, the default browser
  is used (`SENTINEL_WEB_IN_APP=false` forces the browser).

## Consequences

- Settings and security views are reached by voice or Ctrl+K instead of a menu.
- Yahoo's chart API is unofficial and may change; markets degrade gracefully per group.
