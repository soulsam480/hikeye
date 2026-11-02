# Python parity report

**Date:** 2026-11-02
**App:** `python/` (FastAPI), Node app retained at the repo root.

## Verified

| Area | Evidence |
| --- | --- |
| DVR login + verification code | `uv run netra check` → `verification code: read from DVR` |
| Live decryption | Camera 1: `live`, 352×288, 14–15 fps, `decryptedNals 4/4` and `33/33` in separate runs |
| All 7 cameras | Cameras 2–7 switched and reported `live`, 15–17 fps, `decrypted == flagged`, no error |
| Snapshot | `GET /api/snapshot.jpg` → HTTP 200, 12,121-byte JPEG, 352×288 |
| MJPEG | `GET /stream.mjpeg` for 3 s → 44 JPEG frames (~15 fps) |
| Recording | 5 s capture → playable MP4, 352×288, 4.27 s, H.264, `-c copy` |
| Event store | `/api/events` returned rows; `/api/events/1/snapshot` HTTP 200 |
| Mock vision | Event created with `person`, confidence 0.5, snapshot on disk |
| OpenAI-compatible vision | Stub endpoint (`/v1/chat/completions`) returned `vehicle`, 0.91; stored and exposed in `/api/status` |
| Auth | `tests/test_server.py` covers 401/200 for every route |
| Unit/integration tests | `uv run pytest` — all pass |
| Lint/format | `uv run ruff check netra tests` + `ruff format --check` — clean |

## Known limits

- **H.264 only** for encrypted streams. H.265 uses a different scheme; switch
  the DVR sub/main stream to H.264.
- Sub streams are capped at 352×288 by this DVR. Use `STREAM_STREAM=main` and
  H.264 main streams for 704×576 / 960×1080.
- Real vision labels require an OpenAI-compatible endpoint at
  `VISION_BASE_URL`; `VISION_BACKEND=mock` is the default.
- The dashboard is bound to `127.0.0.1` unless `HOST` is changed; set
  `DASHBOARD_PASSWORD` before LAN exposure.
