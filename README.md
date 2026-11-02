# Netra

A small, local-first CCTV dashboard for RTSP cameras and Hikvision DVRs. One
Node process, no build step, no cloud, no accounts. Credentials stay on the
server and are never sent to the browser.

## Features

- **Multi-camera selector** — seven cameras, one running at a time
- **Live view** over MJPEG (no plugins, works in every browser)
- **Snapshot** button to download a still
- **One-click recording** to MP4, per camera
- **Playback** in the browser with HTTP range support
- **Health panel**: state, resolution, FPS, bitrate, reconnects
- Automatic reconnect, stall watchdog, and 401 lockout protection
- Optional password protection

## Requirements

- Node.js 20+
- `ffmpeg` on your PATH (`brew install ffmpeg`)

## Setup

```bash
cp .env.example .env
```

Fill in the DVR/camera:

```dotenv
STREAM_IP=192.168.0.100
STREAM_USER=admin
STREAM_PASS=your-password
```

List your cameras (`channel:name`):

```dotenv
CAMERAS=1:Front Door,2:Backyard,3:Garage,4:Driveway,5:Side,6:Porch,7:Office
```

Verify the connection:

```bash
mise run check     # or: npm run check
```

Then start it:

```bash
mise run start     # or: npm start
```

Open <http://localhost:8080>.

> Hikvision/Dahua: the sub stream is `102` (camera 1), `202` (camera 2), etc.
> Main streams are `101`, `201`, …

## Two capture modes

**`STREAM_MODE=rtsp`** (default) — ffmpeg pulls the RTSP stream. Best quality and
frame rate.

**`STREAM_MODE=snapshot`** — Netra polls the Hikvision ISAPI JPEG endpoint:

```
http://<ip>/ISAPI/Streaming/channels/102/picture
```

It is slower (default 2 fps) but works when the DVR's RTSP output is encrypted
or otherwise unreadable. Only the selected camera polls, so seven cameras do
not mean seven streams. Recording still produces a normal H.264 MP4.

### Hikvision "Image and Video Encryption"

If RTSP decodes as **black or corrupt** (`A non-intra slice in an IDR NAL unit`,
`Missing reference picture`) but snapshots work, the DVR has stream encryption
enabled. Hik-Connect decrypts it with Hikvision's proprietary libraries; ffmpeg
and VLC cannot.

Disable it in Hik-Connect:

1. Open the device → **Settings**
2. Turn off **Image and Video Encryption**
3. Enter the security code sent to your phone/email

Or on the DVR's local monitor: **Menu → Configuration → Network → Advanced
Settings → Other → Stream Encryption → disable**.

Then set `STREAM_MODE=rtsp` and restart. RTSP will run at full frame rate.

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
| `STREAM_USER` | — | Username |
| `STREAM_PASS` | — | Password (special characters are fine) |
| `CAMERAS` | — | `channel:name` list, e.g. `1:Front,2:Back` |
| `STREAM_MODE` | `rtsp` | `rtsp` or `snapshot` |
| `STREAM_URL` | — | Full RTSP URL; overrides the derived one (single camera) |
| `STREAM_PATH` | `/Streaming/Channels/102` | RTSP path (single camera) |
| `STREAM_PORT` | `554` | RTSP port |
| `STREAM_TRANSPORT` | `tcp` | `tcp` or `udp` |
| `STREAM_FPS` | `10` | Browser frame rate in RTSP mode |
| `STREAM_WIDTH` | `0` (native) | Max width; never upscales |
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
RTSP camera ──ffmpeg──> JPEG frames ──> multipart/x-mixed-replace ──> browser
                     └─ ffmpeg -c copy ─> recordings/<camera>/*.mp4
```

- One ffmpeg process per camera, only for the selected camera. Frames are fanned
  out to every viewer; slow clients drop frames instead of buffering.
- Snapshot mode polls JPEGs with HTTP Digest auth, then reuses the same fan-out.
- Recordings use fragmented MP4 (RTSP) or a 2 fps H.264 encode (snapshots).
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

**RTSP is black/corrupt but snapshots work** — stream encryption; see above.

**High latency** — use the sub stream (`102`), keep `STREAM_FPS` at 10–15 and
`STREAM_WIDTH` at `0` or `1280`.

**Recordings fill the disk** — Netra does not prune automatically. Point
`RECORDINGS_DIR` at a large disk and delete from the dashboard.

## Development

```bash
mise run test    # unit tests
mise run dev     # restart on file changes
```

## License

MIT
