# 16. Hologram body with camera presence and gestures

- Status: accepted
- Date: 2026-10-23

## Context

Step 3 of the Jarvis roadmap is a "body". The owner chose an animated hologram in the style
of a holographic helmet display that shows emotions, looks at him through the webcam,
greets him and reacts to hand gestures. The camera should run whenever the app is open.

## Decision

- The hologram is the MediaPipe canonical face mesh (468 points, Apache 2.0) drawn with
  three.js as glowing points and lines. The jaw follows the loudness of the voice (Web
  Audio analyser on the voice audio), mouth corners lift after a successful action, the
  colour shows the state (listening, thinking, alert, emergency stop), and it blinks.
- Camera, face and hand tracking use `@mediapipe/tasks-vision` (new dependency, agreed
  with the owner). The WASM runtime and the face and gesture models are bundled with the app
  (`scripts/prepare-vision.mjs`), so nothing is loaded at runtime and no image leaves the
  PC. Frames are only analysed in memory. The CSP adds `'wasm-unsafe-eval'` for this.
- The app keeps Tauri's `freezePrototype` hardening. MediaPipe cannot run with frozen
  prototypes, so it runs in a classic Web Worker, and the hologram uses precomputed mesh
  edges instead of importing MediaPipe in the main window.
- Gestures: swipe = next/previous view, open palm (held) = stop speaking, fist = back to
  Jarvis, thumb up = "yes" only for a pending spoken question (for example "Vergiss …?").
  Approvals for actions are never given by gesture.
- Presence: when a face appears after at least 10 minutes away, Jarvis greets at most
  every two hours. A chip in the top bar shows while the camera runs; it can be switched
  off in the settings.
- SENTINEL's own camera use (the app executable) does not raise the "new app uses the
  camera" alert.

## Consequences

- The installer grows by about 26 MB (WASM and models).
- WebView2 asks once for camera permission.
