# 18. ONVIF cameras: live video and motion alerts

- Status: accepted
- Date: 2026-10-25

## Context

The user has a network video recorder on the home network and wants to see the cameras
in SENTINEL and be told about motion. The recorder speaks ONVIF (WS-Discovery, device,
media and event services); its RTSP port is closed.

## Decision

- A small ONVIF client without new dependencies (httpx + ElementTree): unicast
  WS-Discovery for one address, WS-Security password digest, `GetProfiles`,
  `GetSnapshotUri` and a PullPoint subscription for motion events.
- Live video: ffmpeg (installed by the user with `winget install Gyan.FFmpeg`, found on
  PATH, in the winget folder or via `SENTINEL_FFMPEG_PATH`) reads the recorder's RTSP
  stream and outputs MJPEG; the backend streams it to the app, which shows the newest
  frame. Grid = sub stream at 8 fps / 640 px, enlarged = main stream at 15 fps / 1280 px.
  ffmpeg gets a fixed argument list (no shell), at most 10 streams at once, and is killed
  when the viewer leaves. The RTSP URL carries the login, so ffmpeg output is discarded.
- Without ffmpeg the app falls back to JPEG still images (`GetSnapshotUri`).
- Recorders often move RTSP away from 554 and leave ports out of their own URLs: the
  RTSP port is found by an `OPTIONS` probe on the same host, missing ports fall back to
  the device port. Snapshots send Basic auth right away (some recorders never answer an
  unauthenticated request), Digest as fallback; home network only.
- Nothing is written to disk (`Cache-Control: no-store`).
- Address, username and password are stored together in the Windows Credential Manager
  via `sentinel camera login` (hidden input). Only private IPv4 addresses are allowed, and
  every URL the device returns (services, snapshots, subscriptions) must stay on the same
  host, so a device cannot redirect SENTINEL elsewhere.
- Camera answers are untrusted: size-limited, DTD/entities rejected. Motion only creates a
  notification (toast + phone push, 2 min cooldown per camera), never an action.
- Jarvis opens the view by voice ("show me the camera"); motion alerts can be switched
  off in the view.

## Consequences

ffmpeg is an external tool the user installs (not bundled, so the installer and the
license stay unchanged). MJPEG costs more bandwidth than H.264, which is fine on the
local machine.
