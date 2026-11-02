# Netra

> **Python rewrite (active app):** `python/` contains the FastAPI backend with
> Hikvision decryption and vision-language events. See
> [python/README.md](python/README.md). The original Node implementation is
> still available via `mise run node:start`.

A local-first CCTV dashboard for Hikvision DVRs/NVRs, including **encrypted
Hik-Connect streams**. One Node process, no build step, no cloud, no accounts.
Credentials stay on the server and are never sent to the browser.

## Features

- **Multi-camera selector** — all channels, one running at a time
- **Decrypts Hikvision "video encryption"** in real time (AES-128, no SDK)
- **Reads the verification code from the DVR** automatically (admin account)
- **Live view** over MJPEG (no plugins, works in every browser)
- **Snapshot** button to download a still
- **One-click recording** to MP4, per camera
- **Playback** in the browser with HTTP range support
- **Health panel**: state, resolution, FPS, bitrate, decrypted NALs, reconnects
- Automatic reconnect, stall watchdog, and 401 lockout protection
- Optional password protection

## Requirements

- Node.js 20+
- `ffmpeg` on your PATH (`brew install ffmpeg`)
- A Hikvision / HiLook / EZVIZ DVR or NVR with an **admin** account

## Setup

```bash
cp .env.example .env
```

Fill in the DVR and cameras:

```dotenv
STREAM_IP=192.168.0.100
STREAM_USER=admin
STREAM_PASS=your-password

CAMERAS=1:Front Door,2:Backyard,3:Garage,4:Driveway,5:Side,6:Porch,7:Office

STREAM_MODE=decrypt
```

Verify the connection:

```bash
mise run check     # or: npm run check
```

Start it:

```bash
mise run start     # or: npm start
```

Open <http://localhost:8080>.

> The verification code is read from the DVR's ISAPI endpoint
> (`/ISAPI/System/Network/EZVIZ`) using the admin account. If that fails, put it
> in `.env` as `STREAM_SECRET_KEY`.

## Capture modes

| `STREAM_MODE` | Use when | How |
| --- | --- | --- |
| `decrypt` | Encryption is on (Hik-Connect / HiLook) | RTSP + AES-128 decryption |
| `rtsp` | Encryption is off | Plain ffmpeg RTSP |
| `snapshot` | Last resort | Polls the ISAPI JPEG endpoint at `SNAPSHOT_FPS` |

### Encrypted streams (the interesting part)

Hikvision marks encrypted NAL units with an RTP header extension (`0x4000`).
Each one is laid out as:

```
[original NAL header] + AES-128-ECB(original NAL, first 4 KB) + plaintext rest
```

The key is the device verification code, zero-padded to 16 bytes. Netra
reassembles the NALs, decrypts the flagged ones, and pipes the result to ffmpeg.
The first decrypted byte is compared with the clear NAL header as a sanity
check, so unencrypted streams are never touched.

This implementation is ported from
[homebridge-hikvision-dvr](https://github.com/matthewdeaves/homebridge-hikvision-dvr)
(MIT). Thanks to that project for documenting the format.

**H.264 only.** Hikvision's H.265 encryption uses a different layout. Set each
camera's **sub stream** to H.264 in the DVR (Configuration → Video → Sub
Stream → Video Encoding → H.264), or via ISAPI. All seven sub streams on the
tested DS-7108HGHI-M1/T were switched this way.

## Viewing from your phone

By default the server only listens on `127.0.0.1`. To open it on your LAN:

```dotenv
HOST=0.0.0.0
DASHBOARD_PASSWORD=something-long
```

Restart and browse to `http://<your-computer-ip>:8080`. The browser asks for
`DASHBOARD_USER` (default `admin`) and that password. Do not expose this to the
internet without a reverse proxy and TLS.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `STREAM_IP` | — | DVR/camera IP (required) |
| `STREAM_USER` | — | Username (admin for automatic code reading) |
| `STREAM_PASS` | — | Password (special characters are fine) |
| `CAMERAS` | — | `channel:name` list, e.g. `1:Front,2:Back` |
| `STREAM_MODE` | `rtsp` | `decrypt`, `rtsp`, or `snapshot` |
| `STREAM_SECRET_KEY` | read from DVR | Verification code fallback |
| `STREAM_URL` | — | Full RTSP URL; overrides the derived one (single camera) |
| `STREAM_PATH` | `/Streaming/Channels/102` | RTSP path (single camera) |
| `STREAM_PORT` | `554` | RTSP port |
| `STREAM_TRANSPORT` | `tcp` | `tcp` or `udp` |
| `STREAM_FPS` | `10` | Browser/recording frame rate |
| `STREAM_QUALITY` | `5` | MJPEG quality (2 = best, 31 = smallest) |
| `SNAPSHOT_FPS` | `2` | Poll rate in snapshot mode |
| `SNAPSHOT_URL` | — | Override the snapshot URL (single camera) |
| `CAMERA_NAME` | `Front Camera` | Name when `CAMERAS` is unset |
| `PORT` | `8080` | Dashboard port |
| `HOST` | `127.0.0.1` | Bind address |
| `DASHBOARD_USER` | `admin` | Basic-auth username |
| `DASHBOARD_PASSWORD` | empty | Basic-auth password; empty disables auth |
| `RECORDINGS_DIR` | `recordings` | Recordings root (one folder per camera) |
| `FFMPEG_PATH` | `ffmpeg` | Path to ffmpeg |

## How it works

```
RTSP/RTP ──reassemble──> NALs ──AES-128 decrypt──> Annex-B ──ffmpeg──> JPEG ──> browser
                                                              └─ ffmpeg ─> recordings/<camera>/*.mp4
```

- One RTSP session per active camera. Frames are fanned out to every viewer;
  slow clients drop frames instead of buffering.
- Recording uses the throttled frame feed, so playback runs at real speed.
- No frames for 20 seconds restarts the capture.
- On `401/403` it retries slowly, then stops after 3 failures to avoid locking
  the camera account.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/status` | Active camera + recorder state |
| `GET` | `/api/cameras` | Camera list |
| `POST` | `/api/cameras/:id/activate` | Switch camera |
| `GET` | `/api/events` | Server-sent status events |
| `GET` | `/api/snapshot.jpg?camera=1` | Single JPEG |
| `GET` | `/stream.mjpeg?camera=1` | Live MJPEG |
| `GET` | `/api/recordings?camera=1` | List recordings |
| `POST` | `/api/recordings/start?camera=1` | Start recording |
| `POST` | `/api/recordings/stop?camera=1` | Stop recording |
| `GET` | `/api/recordings/:name?camera=1` | Play/download (range supported) |
| `DELETE` | `/api/recordings/:name?camera=1` | Delete |
| `GET` | `/healthz` | Health check |

## Troubleshooting

**`401 Unauthorized`** — wrong credentials, RTSP auth disabled, or a temporary
lockout after repeated failures. Confirm the login in the DVR web UI, wait, then
run `mise run check`.

**Encrypted stream but `decryptedNals: 0`** — the verification code is wrong.
Netra reads it from the DVR automatically; make sure the account is admin. The
health panel shows `decrypted / flagged NALs`.

**`PPS id out of range` / HEVC errors** — that stream is H.265. Switch its sub
stream to H.264 (the decryptor supports H.264).

**High latency** — use the sub stream (`102`), keep `STREAM_FPS` at 10–15.

**Recordings fill the disk** — Netra does not prune automatically. Point
`RECORDINGS_DIR` at a large disk and delete from the dashboard.

## Development

```bash
mise run test    # unit tests
mise run dev     # restart on file changes
```

## License

MIT. The RTSP client and decryptor are ported from
[homebridge-hikvision-dvr](https://github.com/matthewdeaves/homebridge-hikvision-dvr)
(MIT).
