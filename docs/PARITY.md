# Parity report

**Date:** 2026-11-02
**App:** repo root (FastAPI + Jinja + Alpine + Tailwind).

## Verified

| Area | Evidence |
| --- | --- |
| Config | `config.yaml` is the single source; `.env` has 3 secrets only |
| DVR login + verification code | `uv run netra check` → code read from DVR |
| Live decryption | Camera 1: live, 352×288, ~15 fps, `decryptedNals == flaggedNals` |
| All 7 cameras | Cameras 2–7 switch and decrypt with no error |
| Snapshot | `/api/snapshot.jpg` → HTTP 200, valid JPEG |
| MJPEG | 44 JPEG frames in 3 s |
| Recording | playable MP4, 352×288, H.264, `-c copy` |
| Events | SQLite rows + snapshots; dedupe/cooldown and confidence gate |
| Vision | OpenAI-compatible endpoint, reasoning disabled, `min_confidence=0.75` |
| UI | Jinja + Alpine + Tailwind console, 7 cameras, live status, 0 console errors |
| Tests | `uv run pytest` — 83 passed |
| Lint | `ruff` clean; `ty` 0 diagnostics |

## Known limits

- **H.264 only** for encrypted streams. Set the DVR stream to H.264.
- Sub streams are capped at 352×288 by this DVR; `capture.stream: main` plus
  H.264 gives 704×576 or 960×1080.
- Real labels require the OpenAI-compatible endpoint; `vision.backend: mock`
  works without one.
- Only the active camera is analyzed; switching resets the motion detector.
- No automatic pruning yet (`EventStore.prune` exists, not scheduled).
- Binds to `127.0.0.1`; set `server.host: 0.0.0.0` and
  `server.dashboard_password` before LAN exposure.
