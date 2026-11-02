# Netra Python + Local VLM — Design Spec

**Date:** 2026-11-02
**Status:** Approved direction; implementation follows
**Supersedes:** the Node dashboard as the primary app (kept until Python parity is verified)

## Problem

The current Node app works: it decrypts Hikvision encrypted H.264 RTSP, serves
7 cameras, snapshots, and recordings. The next step is to make it a local
vision system: detect people/vehicles/events with an on-device vision-language
model, with no hosted provider and no cloud.

The rewrite must not lose the hard-won parts: RTSP digest auth, RTP parsing,
AES-128-ECB NAL decryption, the camera selector, recording, and the UI.

## Goals

1. Rewrite the backend in Python with feature parity.
2. Call a vision-language model served by an **OpenAI-compatible endpoint**
   (e.g. Liquid AI LFM2.5-VL) — the model runner stays outside this process.
3. Turn motion-gated frames into structured events: people, vehicles, event
   type, description, confidence, snapshot.
4. Keep it usable on a 16 GB M2: one live camera at a time, one VLM worker,
   bounded queues.
5. Keep the existing HTTP/UI contract so the dashboard survives the rewrite.

## Non-goals

- 24/7 recording of all cameras.
- Audio / two-way talk.
- Cloud, Hik-Connect, or hosted inference.
- Training or fine-tuning the VLM.
- H.265 stream decryption (use H.264 sub/main streams).
- Replacing the DVR's own motion detection.

## Current Facts

- DVR: Hikvision `DS-7108HGHI-M1/T`, firmware `V4.83.614`.
- 7 channels; sub streams capped at **352×288** by the DVR.
- Main streams support H.264 at 960×1080, 960×576, 704×576, 640×480.
- Encrypted NALs are marked by RTP extension `0x4000`; layout is
  `[NAL header] + AES-128-ECB(first 4 KB) + plaintext rest`; key is the
  verification code zero-padded to 16 bytes.
- The verification code is readable from `GET /ISAPI/System/Network/EZVIZ`
  with an admin account (9 chars on this device).
- Decryption works for H.264; H.265 uses a different scheme and is unsupported.

## Architecture

```
FastAPI (uvicorn)
├── CameraHub (one active camera, lazy recorders)
│   ├── HikvisionCamera (thread)
│   │   ├── RtspClient  (digest, RTP, NALs, RTP extension)
│   │   ├── HikDecryptor (AES-128-ECB, sanity check)
│   │   └── ffmpeg (H.264 pipe → MJPEG pipe)
│   └── NalRecorder (decrypted H.264 → MP4, -c copy)
├── EventEngine (thread)
│   ├── MotionDetector (Pillow/numpy frame diff)
│   ├── VisionWorker (single queue, one model call at a time)
│   └── EventStore (SQLite + JPEG snapshots)
└── Static UI (same dashboard + Events panel)
```

- **Camera**: RTSP runs in a thread; ffmpeg reads decrypted Annex-B from stdin
  and writes MJPEG to stdout; a reader thread extracts JPEG frames and updates
  `latest_frame` + a sequence number.
- **Browser**: MJPEG generator yields the latest frame when the sequence
  changes. Slow clients skip frames; no unbounded buffering.
- **Events**: the engine samples the active camera every 2 s; a motion gate
  triggers a VLM call; results are deduped by camera + label within a 20 s
  cooldown.
- **Vision**: one worker thread calls an OpenAI-compatible endpoint. The
  default backend is `mock` (deterministic, no endpoint) so the app and tests
  run without a model. `openai` posts a frame to
  `{VISION_BASE_URL}/chat/completions` and parses the reply.

## Model

- The model runs outside this process. Netra only needs an HTTP endpoint that
  implements `POST /v1/chat/completions` with OpenAI-style image content.
- Works with vLLM, llama.cpp server, LM Studio, or a gateway in front of
  Liquid AI's latest `LFM2.5-VL-3B`.
- `VISION_BACKEND=mock` is the default so the app runs without an endpoint.
  `VISION_BACKEND=openai` enables real inference.
- Image is sent as `data:image/jpeg;base64,…` in an `image_url` part.
- Output contract: JSON only
  `{event_type, people_count, vehicles_count, description, confidence}`.
  Invalid JSON is dropped (and surfaced in `/api/status`).
- Inference is motion-gated, not per frame. Target ≤ 1 call / 2 s, usually far
  less. Only one request is in flight at a time.

## Event Semantics

- `event_type`: `person`, `vehicle`, `animal`, `motion`, `none`.
- An event is stored only when `event_type != none`, motion is present, and the
  camera is outside its cooldown for that label.
- `confidence` is the model's own 0–1 estimate; clamp and display, do not treat
  as calibrated.
- Snapshots are stored under `data/events/<camera>/<timestamp>.jpg`; the DB
  stores the relative path.
- Dedupe key: `camera_id + event_type + people_count/vehicles_count` within
  20 s. Repeated identical events update `last_seen` instead of inserting.

## Data Model (SQLite)

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  camera_id TEXT NOT NULL,
  created_at TEXT NOT NULL,      -- ISO-8601 UTC
  last_seen TEXT NOT NULL,
  event_type TEXT NOT NULL,
  people_count INTEGER NOT NULL DEFAULT 0,
  vehicles_count INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  snapshot_path TEXT,
  model TEXT NOT NULL
);
CREATE INDEX events_camera_time ON events(camera_id, created_at DESC);
```

## Configuration

Reuse the existing `.env` names; add:

| Variable | Default | Purpose |
| --- | --- | --- |
| `STREAM_STREAM` | `sub` | `sub` (xx2) or `main` (xx1) |
| `VISION_BACKEND` | `mock` | `mock` or `openai` |
| `VISION_BASE_URL` | `http://127.0.0.1:8000/v1` | OpenAI-compatible endpoint |
| `VISION_API_KEY` | empty | Bearer token, if the endpoint needs one |
| `VISION_MODEL` | `lfm2.5-vl-3b` | Model name the endpoint expects |
| `VISION_INTERVAL` | `2` | Seconds between samples |
| `VISION_COOLDOWN` | `20` | Per-label event cooldown |
| `VISION_MAX_TOKENS` | `160` | Generation cap |
| `EVENTS_DB` | `data/events.db` | SQLite path |
| `EVENTS_DIR` | `data/events` | Snapshots |
| `EVENT_WEBHOOK_URL` | empty | Optional POST per event |

Existing: `STREAM_IP`, `STREAM_USER`, `STREAM_PASS`, `CAMERAS`, `STREAM_MODE`,
`STREAM_SECRET_KEY`, `PORT`, `HOST`, `DASHBOARD_*`, `RECORDINGS_DIR`,
`STREAM_FPS`.

## Performance Budget (M2, 16 GB)

- Camera: ≤ 5% CPU, ~15 fps, ~1 Mbps (H.264 sub stream).
- Decryption: ~1% CPU (AES-NI equivalent; Python cryptography).
- ffmpeg decode + MJPEG encode: ~5–10% CPU.
- VLM latency depends on the endpoint; one request in flight, motion-gated.
- One camera stream and one VLM call at a time.
- Event DB and snapshots bounded by retention (`EVENT_RETENTION_DAYS`, default 30).

## Security / Privacy

- No outbound network at runtime except the DVR and the configured
  `VISION_BASE_URL`.
- Verification code and camera password never logged or sent to the browser.
- Optional Basic auth remains; default bind `127.0.0.1`.
- Snapshots may contain people; local-only by default. Webhook is opt-in.

## Acceptance Criteria

1. `uv run netra serve` serves the dashboard on `:8080`.
2. All 7 cameras switch and decrypt at ~15 fps; health shows
   `decrypted / flagged`.
3. Snapshot returns a valid JPEG; recording produces a playable MP4.
4. `uv run pytest` passes without a model or DVR (unit tests).
5. With `VISION_BACKEND=mock`, a motion event creates a visible event card and
   a snapshot file within 10 s.
6. With `VISION_BACKEND=openai` and a stub/real endpoint, the same flow returns
   model labels; endpoint failures are reported in `/api/status`, not fatal.
7. No secrets in logs; `.env` remains gitignored.

## Migration

Build in `python/`; keep the Node app at the root until the Python app passes
the acceptance checklist. Then the root can switch to a thin launcher that
runs the Python app, and the Node tree can be archived.

## Open Questions

- Which model/endpoint to standardise on (LFM2.5-VL-3B vs 1.6B) — external.
- Whether to analyze all cameras round-robin or only the active camera.
  Default: active camera; a config flag can enable round-robin later.
- Main-stream resolution (704×576 vs 960×1080) — separate from this rewrite;
  `STREAM_STREAM=main` is supported.
