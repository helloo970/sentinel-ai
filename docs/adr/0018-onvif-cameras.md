# 18. ONVIF cameras: live still images and motion alerts

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
- Live view = JPEG still images fetched through the backend about every 2 s (0.7 s for a
  single enlarged camera). No video stream, no ffmpeg, nothing is written to disk
  (`Cache-Control: no-store`).
- Address, username and password are stored together in the Windows Credential Manager
  via `sentinel camera login` (hidden input). Only private IPv4 addresses are allowed, and
  every URL the device returns (services, snapshots, subscriptions) must stay on the same
  host, so a device cannot redirect SENTINEL elsewhere.
- Camera answers are untrusted: size-limited, DTD/entities rejected. Motion only creates a
  notification (toast + phone push, 2 min cooldown per camera), never an action.
- Jarvis opens the view by voice ("show me the camera"); motion alerts can be switched
  off in the view.

## Consequences

Frame rate is low compared to a video stream, but it works with any ONVIF recorder that
offers snapshots and keeps the stack unchanged. A recorder without snapshot support would
need RTSP and a decoder later.
