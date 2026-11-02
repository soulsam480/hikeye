# Netra

Local-first CCTV dashboard for Hikvision DVRs/NVRs, with **encrypted RTSP
decryption** and **vision-language events** from an OpenAI-compatible endpoint.
No cloud, no hosted inference, no accounts.

## Features

- **7-camera selector** — one live stream at a time, all channels configured
- **Decrypts Hikvision stream encryption** in real time (AES-128, H.264)
- **Reads the verification code from the DVR** with an admin account
- **Live MJPEG**, snapshots, per-camera MP4 recording, browser playback
- **Vision events** — motion-gated frames sent to a local VLM endpoint; people,
  vehicles, animals, descriptions, confidence, snapshots
- **SQLite event store** with cooldowns/dedupe and retention
- FastAPI + uvicorn, typed Python 3.13, `uv`, `ruff`, pytest

## Requirements

- Python 3.13 (mise pins it in the repo)
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg` on `PATH`
- A Hikvision / HiLook / EZVIZ DVR or NVR with an **admin** account
- An OpenAI-compatible vision endpoint (vLLM, llama.cpp server, LM Studio, …)
  for real events; `mock` works without one

## Install

```bash
uv sync --extra dev
```

The app reads the repository-root `.env` by default. Copy the example if needed:

```bash
cp .env.example .env
```

## Configure

```dotenv
STREAM_IP=192.168.0.100
STREAM_USER=admin
STREAM_PASS=your-password

CAMERAS=1:Front Door,2:Backyard,3:Garage,4:Driveway,5:Side,6:Porch,7:Office

STREAM_MODE=decrypt
STREAM_STREAM=sub
```

Then verify the DVR, credentials, verification code and first camera:

```bash
uv run netra check
```

Run the dashboard:

```bash
uv run netra serve
# http://localhost:8080
```

Or from the repo root:

```bash
mise run start     # Python app
mise run check
mise run test
mise run lint
```

## Capture modes

| `STREAM_MODE` | Use when | How |
| --- | --- | --- |
| `decrypt` | Hik-Connect encryption is on | RTSP + AES-128 decryption |
| `rtsp` | Encryption is off | Plain RTSP |
| `snapshot` | Last resort | ISAPI JPEG polling |

Encrypted NALs are flagged by RTP header extension `0x4000` and laid out as
`[NAL header] + AES-128-ECB(first 4 KB) + plaintext rest`. The key is the device
verification code, zero-padded to 16 bytes. The decryption port is based on
[homebridge-hikvision-dvr](https://github.com/matthewdeaves/homebridge-hikvision-dvr)
(MIT).

**H.264 only.** Hikvision H.265 uses a different layout. Set each sub stream to
H.264 (DVR UI or `uv run netra codecs --set-h264`).

## Vision

The model is not loaded by this process. It runs wherever you like and exposes
an OpenAI-compatible API. The app sends a single frame as a base64 `image_url`
data URL and expects a JSON object in return.

```dotenv
VISION_BACKEND=openai
VISION_BASE_URL=http://127.0.0.1:8000/v1
VISION_MODEL=lfm2.5-vl-3b
# VISION_API_KEY=
VISION_INTERVAL=2
VISION_COOLDOWN=20
```

Any endpoint that supports `POST /v1/chat/completions` with image content works:
vLLM, llama.cpp's server, LM Studio, or a gateway in front of Liquid AI's latest
`LFM2.5-VL-3B`.

Without an endpoint, keep `VISION_BACKEND=mock` (the default) to exercise the
pipeline; it returns a deterministic `person` event.

How events are produced:

1. Sample the active camera every `VISION_INTERVAL` seconds.
2. Cheap motion gate (64×48 grayscale diff) — no motion, no model call.
3. One VLM call at a time; results are parsed and validated.
4. `none` is discarded; repeats within `VISION_COOLDOWN` update `last_seen`.
5. Snapshot + row stored; the UI polls `/api/events`.

Events live in SQLite (`data/events.db`), snapshots in `data/events/<camera>/`.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/status` | Camera, recorder, vision state |
| `GET` | `/api/cameras` | Camera list |
| `POST` | `/api/cameras/:id/activate` | Switch camera |
| `GET` | `/api/snapshot.jpg?camera=1` | Single JPEG |
| `GET` | `/stream.mjpeg?camera=1` | Live MJPEG |
| `GET` | `/api/recordings?camera=1` | List recordings |
| `POST` | `/api/recordings/start?camera=1` | Start recording |
| `POST` | `/api/recordings/stop?camera=1` | Stop recording |
| `GET` | `/api/recordings/:name?camera=1` | Play/download |
| `DELETE` | `/api/recordings/:name?camera=1` | Delete |
| `GET` | `/api/events?camera=1&limit=24` | Vision events |
| `GET` | `/api/events/:id/snapshot` | Event JPEG |
| `GET` | `/healthz` | Health check |

## Tests and lint

```bash
uv run pytest
uv run ruff check netra tests
uv run ruff format --check netra tests
```

Unit tests need no DVR and no model. The live smoke test is `netra check`.

## Layout

```
netra/
  config.py       .env + camera definitions
  rtsp.py         RTSP digest, RTP, NAL reassembly
  crypto.py       AES-128 NAL decryptor
  camera.py       RTSP -> decrypt -> ffmpeg -> JPEG frames
  recorder.py     decrypted H.264 -> MP4 (-c copy)
  hub.py          camera selector + recorders
  isapi.py        verification code, channel codec
  events.py       SQLite event store + motion detector
  engine.py       motion gate -> VLM -> event
  vision/         mock + OpenAI-compatible backends, prompt/JSON parser
  server.py       FastAPI routes, MJPEG, auth, static UI
  cli.py          serve / check / codecs
  static/         stylesheet and icon
```

## Privacy

Everything runs on your machine except the vision request, which goes to the
`VISION_BASE_URL` you configure. Snapshots and events stay local. Bind to
`127.0.0.1` by default; set `HOST=0.0.0.0` and `DASHBOARD_PASSWORD` only on a
trusted LAN. Do not expose this to the internet without TLS and a proxy.
