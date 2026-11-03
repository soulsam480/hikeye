# mu-dekhuchi Python + Local VLM Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild mu-dekhuchi in Python with feature parity and add local, motion-gated Liquid AI vision events.

**Architecture:** FastAPI serves the existing UI and a camera hub. One active camera runs a threaded RTSP client + AES decryptor + ffmpeg pipeline. An event engine samples frames, gates on motion, calls a single local VLM worker, and stores structured events in SQLite with snapshots.

**Tech Stack:** Python 3.13, uv, FastAPI, uvicorn, httpx (DigestAuth), cryptography (AES-128-ECB), Pillow, numpy, sqlite3, ffmpeg, OpenAI-compatible vision endpoint.

**Spec:** `docs/superpowers/specs/2026-11-02-mu-dekhuchi-python-vlm-design.md`

## Global Constraints

- Python ≥ 3.11; dependency management with `uv`; no system Python installs.
- Same HTTP contract as the Node app: `/api/status`, `/api/cameras`, `/api/snapshot.jpg`, `/stream.mjpeg`, `/api/recordings*`, `/healthz`.
- One active camera and one VLM call at a time; bounded queues.
- Decrypt only H.264. H.265 must fail with a clear error, never garbage video.
- Read the verification code from `GET /ISAPI/System/Network/EZVIZ`; never log or return it.
- `.env` is gitignored; no secrets in source or tests.
- Unit tests run without a DVR or model.
- Every task ends with `uv run pytest` green and a commit.

## Review Focus

- RTSP session expiry / reconnect while the UI holds an MJPEG connection.
- VLM latency starving the event queue; frames must not pile up.
- Event spam from motion (lighting, IR) — cooldown and dedupe must hold.
- Switching cameras while recording — recording must stop cleanly.
- Secret leakage into logs, status JSON, or error strings.
- Model OOM on 16 GB — single worker, 1.6B default.
- H.265 streams — explicit unsupported error, not a black feed.

---

### Task 1: Scaffold, config, and test harness

**Files:**
- Create: `python/pyproject.toml`
- Create: `python/mu_dekhuchi/__init__.py`
- Create: `python/mu_dekhuchi/config.py`
- Create: `python/tests/test_config.py`
- Create: `python/.env.example`

**Interfaces:**
- Produces: `Settings.load(cwd: Path = Path(".")) -> Settings`
- Produces: `CameraConfig(id: str, channel: int, name: str, stream_id: str, stream_url: str, snapshot_url: str)`
- Produces: fields `stream_mode`, `stream_kind`, `verification_code`, `cameras`, `port`, `host`, `dashboard_user`, `dashboard_password`, `recordings_dir`, `data_dir`, `vision_backend`, `vision_model`, `vision_interval`, `vision_cooldown`.

- [ ] **Step 1: Write failing config tests**
  - `parse CAMERAS=1:Front,2:Back` → two cameras, ids `1`,`2`.
  - `STREAM_STREAM=main` → stream ids `101`,`201`; `sub` → `102`,`202`.
  - `STREAM_MODE=decrypt` and `STREAM_SECRET_KEY` parsed; missing key allowed.
  - `STREAM_MODEl` invalid falls back to `rtsp`.

- [ ] **Step 2: Run `uv run pytest tests/test_config.py -v` — expect import errors.**

- [ ] **Step 3: Implement `Settings.load` and `CameraConfig`; add `pyproject.toml` with deps: fastapi, uvicorn[standard], httpx, cryptography, pillow, numpy, python-dotenv; dev: pytest, pytest-asyncio.**

- [ ] **Step 4: Run tests — expect PASS.**

- [ ] **Step 5: Commit `feat(py): scaffold config and tests`.**

---

### Task 2: Hikvision AES decryptor

**Files:**
- Create: `python/mu_dekhuchi/crypto.py`
- Create: `python/tests/test_crypto.py`

**Interfaces:**
- Produces: `HikDecryptor(verification_code: str, header_size: int = 1)`
- Produces: `.process(nal: bytes, flagged: bool) -> bytes`
- Produces: `.decrypted: int`, `.saw_flag: bool`

- [ ] **Step 1: Write failing tests**
  - Build `[0x65] + AES-128-ECB(original NAL incl. header)[:n] + rest`; `process(..., True)` returns the original NAL.
  - Wrong code returns the input unchanged and `decrypted == 0`.
  - Unflagged NAL after a flag is returned unchanged.
  - Empty code disables decryption.

- [ ] **Step 2: Run `uv run pytest tests/test_crypto.py -v` — expect failure.**

- [ ] **Step 3: Implement with `cryptography.hazmat.primitives.ciphers` AES-128-ECB, `set_auto_padding(False)`, first `min(floor(len/16)*16, 4096)` bytes, header-size-aware sanity check.**

- [ ] **Step 4: Run tests — PASS.**

- [ ] **Step 5: Commit `feat(py): port AES-128 NAL decryptor`.**

---

### Task 3: RTSP client (Digest, RTP, NAL reassembly)

**Files:**
- Create: `python/mu_dekhuchi/rtsp.py`
- Create: `python/tests/test_rtsp.py`

**Interfaces:**
- Produces: `RtspClient(url: str, username: str, password: str, timeout: float = 15.0)`
- Produces: `.start(on_sdp: Callable[[SdpInfo], None], on_nal: Callable[[bytes, int, bool], None])`
- Produces: `.stop()`
- Produces: `SdpInfo(codec: Literal["h264","hevc"], control: str, sps: bytes|None, pps: bytes|None)`
- Raises: `RtspAuthError` on 401; `RtspError` on setup/play failure.

- [ ] **Step 1: Write failing tests**
  - `parse_sdp` extracts `H264`, control `*`, SPS/PPS from `sprop-parameter-sets`.
  - `parse_rtp` handles single NAL, STAP-A, FU-A; extension `0x4000` sets flagged.
  - Digest header matches RFC 2617 vector.

- [ ] **Step 2: Run tests — expect failure.**

- [ ] **Step 3: Implement blocking socket client: OPTIONS, DESCRIBE, SETUP interleaved 0-1, PLAY, GET_PARAMETER keepalive; parse `$` frames; RTP header/extension; FU-A buffer; digest auth on 401.**

- [ ] **Step 4: Run tests — PASS.**

- [ ] **Step 5: Commit `feat(py): RTSP client with RTP/NAL handling`.**

---

### Task 4: ISAPI helpers

**Files:**
- Create: `python/mu_dekhuchi/isapi.py`
- Create: `python/tests/test_isapi.py`

**Interfaces:**
- Produces: `read_verification_code(base_url: str, user: str, password: str) -> str | None`
- Produces: `get_channel(base_url, user, password, stream_id) -> dict`
- Produces: `set_channel_codec(base_url, user, password, stream_id, codec="H.264") -> bool`
- Produces: `list_channels(...)` optional.

- [ ] **Step 1: Write failing tests using `httpx.MockTransport`**
  - Parses `<verificationCode>ABC123</verificationCode>`.
  - Returns `None` on 404/parse failure.
  - `set_channel_codec` PUTs XML with `H.264` and returns status 200.
  - Never includes the password in the URL.

- [ ] **Step 2: Run tests — fail.**

- [ ] **Step 3: Implement with `httpx.Client(auth=httpx.DigestAuth(...))`, XML regex for the code, `put` for codec.**

- [ ] **Step 4: Run tests — PASS.**

- [ ] **Step 5: Commit `feat(py): ISAPI code reader and codec setter`.**

---

### Task 5: Camera worker (RTSP → decrypt → ffmpeg → JPEG)

**Files:**
- Create: `python/mu_dekhuchi/camera.py`
- Create: `python/tests/test_camera.py` (unit parts only)
- Create: `python/scripts/live_smoke.py` (manual)

**Interfaces:**
- Produces: `CameraWorker(camera: CameraConfig, verification_code: str, ffmpeg_path: str, fps: int, quality: int)`
- Produces: `.start()`, `.stop()`, `.latest() -> tuple[bytes, int]`, `.snapshot(timeout=8.0) -> bytes`, `.status() -> dict`
- Produces: `.on_nal: Callable[[bytes], None] | None` for recorders.
- Status keys: `state`, `live`, `width`, `height`, `fps`, `bitrate_kbps`, `frames`, `decrypted_nals`, `flagged_nals`, `error`.

- [ ] **Step 1: Write failing tests for the JPEG splitter and status math (no socket).**

- [ ] **Step 2: Run tests — fail.**

- [ ] **Step 3: Implement threads: RTSP thread writes Annex-B to ffmpeg stdin; ffmpeg stdout reader splits JPEG SOI/EOI, updates latest/seq, dimensions via Pillow; reconnect with 3 s backoff; 401 → 5 min backoff; unsupported HEVC → `state=error`, clear message.**

- [ ] **Step 4: Run unit tests — PASS.**

- [ ] **Step 5: Run `uv run python scripts/live_smoke.py --camera 1` — expect `live 352x288 ~15fps decrypted>0`.**

- [ ] **Step 6: Commit `feat(py): camera worker with decrypting RTSP pipeline`.**

---

### Task 6: NAL recorder

**Files:**
- Create: `python/mu_dekhuchi/recorder.py`
- Create: `python/tests/test_recorder.py`

**Interfaces:**
- Produces: `NalRecorder(dir: Path, ffmpeg_path: str)`
- Produces: `.start() -> dict`, `.push(nal: bytes) -> None`, `.stop() -> dict`, `.list() -> list[dict]`, `.resolve(name: str) -> Path | None`, `.remove(name) -> None`, `.status() -> dict`

- [ ] **Step 1: Write failing tests for filename validation, list sorting, and reject path traversal.**
- [ ] **Step 2: Run tests — fail.**
- [ ] **Step 3: Implement `ffmpeg -f h264 -i pipe:0 -c copy -movflags +faststart -f mp4 <dir>/rec-<ts>.mp4`; push NALs; stop closes stdin and waits.**
- [ ] **Step 4: Run tests — PASS.**
- [ ] **Step 5: Commit `feat(py): NAL recorder`.**

---

### Task 7: Camera hub, FastAPI API, auth, static parity

**Files:**
- Create: `python/mu_dekhuchi/hub.py`
- Create: `python/mu_dekhuchi/server.py`
- Create: `python/mu_dekhuchi/auth.py`
- Create: `python/mu_dekhuchi/static/` (copy `public/`, rename only if needed)
- Create: `python/tests/test_api.py`
- Create: `python/mu_dekhuchi/cli.py`

**Interfaces:**
- Produces: `CameraHub(settings)`: `.list()`, `.active_id`, `.activate(id)`, `.camera`, `.recorder`, `.status_payload()`, `.shutdown()`.
- Produces: FastAPI app factory `create_app(settings) -> FastAPI`.
- Routes exactly match the Node app, plus `/api/events` later.

- [ ] **Step 1: Write failing API tests with a fake camera/recorder**
  - `/healthz` 200.
  - `/api/status` shape includes `activeId`, `cameras`, `camera`, `recorder`.
  - Basic auth enforced when `DASHBOARD_PASSWORD` set.
  - `/api/cameras/1/activate` changes active id.
- [ ] **Step 2: Run tests — fail.**
- [ ] **Step 3: Implement hub (lazy workers/recorders, stop on switch), MJPEG StreamingResponse (latest frame, drop repeats), snapshot, recordings, SSE-less polling, static files, Basic auth dependency.**
- [ ] **Step 4: Run tests — PASS.**
- [ ] **Step 5: Run `uv run mu-dekhuchi serve` and open `:8080`; verify camera 1 live + switch.**
- [ ] **Step 6: Commit `feat(py): hub, API, auth, UI parity`.**

---

### Task 8: Motion detector and event store

**Files:**
- Create: `python/mu_dekhuchi/events.py`
- Create: `python/tests/test_events.py`

**Interfaces:**
- Produces: `EventStore(db_path: Path, events_dir: Path)`
  - `.add(camera_id, result, jpeg) -> dict`
  - `.list(camera_id: str|None, limit: int) -> list[dict]`
  - `.snapshot_path(event_id) -> Path | None`
  - `.prune(days: int) -> int`
- Produces: `MotionDetector(width=64, height=48, threshold=0.01)`
  - `.update(jpeg: bytes) -> bool`

- [ ] **Step 1: Write failing tests**
  - SQLite insert/list ordering, dedupe within cooldown, snapshot file written.
  - Motion detector: identical frames → False; changed frame → True; first frame → False.
- [ ] **Step 2: Run tests — fail.**
- [ ] **Step 3: Implement sqlite schema from spec, ISO timestamps, dedupe/cooldown, grayscale diff via Pillow/numpy.**
- [ ] **Step 4: Run tests — PASS.**
- [ ] **Step 5: Commit `feat(py): motion detector and event store`.**

---

### Task 9: Vision backends and event engine

**Files:**
- Create: `python/mu_dekhuchi/vision/__init__.py`
- Create: `python/mu_dekhuchi/vision/base.py`
- Create: `python/mu_dekhuchi/vision/mock.py`
- Create: `python/mu_dekhuchi/vision/liquid.py`
- Create: `python/mu_dekhuchi/vision/prompts.py`
- Create: `python/mu_dekhuchi/engine.py`
- Create: `python/tests/test_vision.py`

**Interfaces:**
- Produces: `VisionResult(event_type, people_count, vehicles_count, description, confidence)`
- Produces: `VisionBackend.analyze(jpeg: bytes, prompt: str) -> VisionResult`
- Produces: `MockVision` (deterministic) and `LiquidVision(model_id, max_tokens)`
- Produces: `EventEngine(settings, hub, store, backend)`: `.start()`, `.stop()`, `.status()`

- [ ] **Step 1: Write failing tests**
  - Mock backend returns a valid `VisionResult`.
  - JSON parser accepts fenced JSON and rejects invalid output.
  - Engine with a fake camera/motion feed creates exactly one event within cooldown.
- [ ] **Step 2: Run tests — fail.**
- [ ] **Step 3: Implement prompt + schema validation; single worker queue; motion gate; cooldown; webhook optional; liquid backend lazy import with clear status error if missing.**
- [ ] **Step 4: Run tests — PASS.**
- [ ] **Step 5: Commit `feat(py): local vision engine`.**

---

### Task 10: Events UI

**Files:**
- Modify: `python/mu_dekhuchi/static/index.html`
- Modify: `python/mu_dekhuchi/static/styles.css`
- Modify: `python/mu_dekhuchi/static/app.js`
- Create: `python/tests/test_ui_contract.py`

**Interfaces:**
- Consumes: `/api/events?camera=&limit=`, `/api/events/{id}/snapshot`, `/api/status.vision`.
- Produces: event cards with time, camera, type, counts, confidence, description, snapshot thumbnail; filters by active camera; empty state.

- [ ] **Step 1: Write a failing contract test that asserts the static HTML contains the events container and app.js fetches `/api/events`.**
- [ ] **Step 2: Run test — fail.**
- [ ] **Step 3: Add the panel + renderer; poll every 5 s; use the existing visual language.**
- [ ] **Step 4: Run test — PASS.**
- [ ] **Step 5: With `VISION_BACKEND=mock`, trigger motion and verify an event card + snapshot appear.**
- [ ] **Step 6: Commit `feat(py): events panel`.**

---

### Task 11: OpenAI-compatible vision endpoint

**Files:**
- Create: `python/mu_dekhuchi/vision/openai.py`
- Create: `python/tests/test_vision.py`
- Modify: `python/mu_dekhuchi/config.py` (`VISION_BACKEND`, `VISION_BASE_URL`, `VISION_API_KEY`)
- Modify: `python/README.md`

**Interfaces:**
- Produces: `OpenAIVision(base_url, model, api_key="", timeout=60.0, max_tokens=160, transport=None)`.
- Produces: `.analyze(jpeg, prompt) -> VisionResult`; `.status() -> dict`.
- `create_backend(settings)` returns `MockVision` for `mock`, `OpenAIVision` for `openai`.

- [ ] **Step 1: Write failing tests with `httpx.MockTransport` for request path/body/auth and response parsing.**
- [ ] **Step 2: Run tests — fail.**
- [ ] **Step 3: Implement the backend; send the frame as a data URL in `image_url`; parse `choices[0].message.content`.**
- [ ] **Step 4: Run tests — PASS.**
- [ ] **Step 5: Integration: stub endpoint returns a vehicle event; `VISION_BACKEND=openai` stores it and `/api/status` shows it.**
- [ ] **Step 6: Commit `feat(py): OpenAI-compatible vision backend`.**

---

### Task 12: Parity, docs, cutover

**Files:**
- Modify: `python/README.md`
- Create: `python/docs/PARITY.md`
- Modify: root `README.md` (link to Python app)
- Optional: root `Makefile`/`mise.toml` tasks.

- [ ] **Step 1: Run the acceptance checklist from the spec against the DVR: 7 cameras, snapshot, recording, events, restart, auth.**
- [ ] **Step 2: Record results in `python/docs/PARITY.md` (pass/fail + evidence).**
- [ ] **Step 3: Document the vision endpoint, backends, event tuning, and the H.264-only rule.**
- [ ] **Step 4: Run `uv run pytest` and a 10-minute soak (switch cameras, record, events).**
- [ ] **Step 5: Commit `docs(py): parity report and cutover notes`.**

---

## Execution Handoff

The user requested a one-shot rewrite. Execute tasks 1–10 natively in this
session, then attempt 11 (model) and 12 (parity). Keep the Node app untouched
until the Python acceptance checklist passes.
