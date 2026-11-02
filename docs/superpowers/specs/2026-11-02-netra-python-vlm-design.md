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
2. Run a local Liquid AI vision-language model (LFM2.5-VL family) on Apple
   Silicon via MLX.
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
- **Vision**: one worker thread owns the model. The default backend is
  `mock` (deterministic, no model) so the app and tests run without weights.
  `liquid` loads MLX (preferred) or Transformers on MPS/CPU.

## Model

- Latest: `LiquidAI/LFM2.5-VL-3B` (released 2026-08-12).
- Practical default for 16 GB: `mlx-community/LFM2.5-VL-1.6B-4bit`
  (mlx-vlm, ~1–2 GB) with `LiquidAI/LFM2.5-VL-3B` selectable.
- Runtime: `mlx-vlm` on Apple Silicon; `transformers` fallback.
- Output contract: JSON only, validated with a pydantic model; invalid JSON is
  retried once with a repair prompt, then dropped.
- Prompt: CCTV analyst; return
  `{event_type, people_count, vehicles_count, description, confidence}`.
- Inference is motion-gated, not per frame. Target ≤ 1 call / 2 s, usually far
  less.

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
| `VISION_BACKEND` | `mock` | `mock` or `liquid` |
| `VISION_MODEL` | `mlx-community/LFM2.5-VL-1.6B-4bit` | MLX/HF model id |
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
- VLM 1.6B 4-bit: ~2 GB RAM, ~0.5–2 s/frame; 3B 4-bit: ~3–4 GB, ~1–4 s/frame.
- One camera stream and one VLM call at a time.
- Event DB and snapshots bounded by retention (`EVENT_RETENTION_DAYS`, default 30).

## Security / Privacy

- No outbound network at runtime except the DVR.
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
6. With `VISION_BACKEND=liquid` and a pulled model, the same flow returns model
   labels; failure to load is reported in `/api/status`, not fatal.
7. No secrets in logs; `.env` remains gitignored.

## Migration

Build in `python/`; keep the Node app at the root until the Python app passes
the acceptance checklist. Then the root can switch to a thin launcher that
runs the Python app, and the Node tree can be archived.

## Open Questions

- Final model size (1.6B vs 3B) after measuring latency on this M2.
- Whether to analyze all cameras round-robin or only the active camera.
  Default: active camera; a config flag can enable round-robin later.
- Main-stream resolution (704×576 vs 960×1080) — separate from this rewrite;
  `STREAM_STREAM=main` is supported.
