# Parity report

**Date:** 2026-11-02
**App:** repo root (FastAPI + Jinja + Alpine + Tailwind).

## Verified

| Area | Evidence |
| --- | --- |
| Config | `config.yaml` is the single source; `.env` has 3 secrets only |
| DVR login + verification code | `uv run mu-dekhuchi check` → code read from DVR |
| Live decryption | Camera 1: live, 352×288, ~15 fps, `decryptedNals == flaggedNals` |
| All 7 cameras | Cameras 2–7 switch and decrypt with no error |
| Snapshot | `/api/snapshot.jpg` → HTTP 200, valid JPEG |
| MJPEG | 44 JPEG frames in 3 s |
| Recording | playable MP4, 352×288, H.264, `-c copy` |
| Events | SQLite rows + snapshots; dedupe/cooldown and confidence gate |
| Vision | OpenAI-compatible endpoint, reasoning disabled, `min_confidence=0.75` |
| UI | Jinja + Alpine + Tailwind console, 7 cameras, all-camera wall, live audio, activity signal, all-camera grouped journal, memory search, cache-busted assets, 0 console errors |
| Audio | Sub-stream PCM μ-law 8 kHz → WebSocket 16 kHz s16le → AudioContext running |
| All-camera motion | ISAPI stills every 2 s, VLM-gated to person/animal; clothes/trees/lighting return none |
| Cross-camera stories | `/api/stories` links person/animal events on ≥2 cameras within a 90 s span and 45 s gap; long chains downgrade to "loose link"; strip + evidence modal render with 0 console errors |
| Tests | `uv run pytest` — 96 passed |
| Lint | `ruff` clean; `ty` 0 diagnostics |

## Known limits

- **H.264 only** for encrypted streams. Set the DVR stream to H.264.
- Sub streams are capped at 352×288 by this DVR; `capture.stream: main` plus
  H.264 gives 704×576 or 960×1080.
- Real labels require the OpenAI-compatible endpoint; `vision.backend: mock`
  works without one.
- Only sampled stills are analyzed on inactive cameras (every 2 s), so very
  short crossings can be missed; the active camera keeps the continuous loop.
- Story links use kind + time only. No ReID or LLM narration yet, so the link
  score is correlation confidence, not identity.
- No automatic pruning yet (`EventStore.prune` exists, not scheduled).
- The committed config binds to `0.0.0.0` for LAN access with no dashboard
  password. Keep it on a trusted LAN only.
