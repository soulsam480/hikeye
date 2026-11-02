from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from .config import Settings
from .engine import EventEngine
from .events import EventStore
from .hub import CameraHub


def render_dashboard(
    settings: Settings,
    hub: CameraHub,
    store: EventStore,
    engine: EventEngine,
) -> str:
    status = hub.status_payload()
    camera = status["camera"]
    recorder = status["recorder"]
    cameras = status["cameras"]
    vision = engine.status()
    active_id = str(hub.active_id or "")
    events = store.list(active_id or None, 12)
    recordings = hub.active_recorder.list() if hub.active_recorder else []

    state = "live" if camera.get("live") else str(camera.get("state", "idle"))
    recording = bool(recorder.get("recording"))
    resolution = f"{camera['width']} × {camera['height']}" if camera.get("width") else "—"
    record_action = "stop" if recording else "start"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0a0f16">
<title>{escape(str(camera.get("name", "Camera")))} · Netra</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/styles.css">
</head>
<body>
<div class="ambient"></div>
<header class="topbar">
  <div class="brand">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5c-5 0-8.5 4.4-9.7 6.2a1.4 1.4 0 0 0 0 1.6C3.5 14.6 7 19 12 19s8.5-4.4 9.7-6.2a1.4 1.4 0 0 0 0-1.6C20.5 9.4 17 5 12 5Z"/><circle cx="12" cy="12" r="3.2"/></svg>
    <span class="brand-name">NETRA</span><span class="brand-tag">cctv</span>
  </div>
  <div class="topbar-right">
    <span class="clock">{datetime.now().strftime("%H:%M:%S")}</span>
    <span class="pill" data-state="{escape(state)}"><span class="dot"></span>{escape(state)}</span>
  </div>
</header>
<main>
  <nav class="camera-bar">{_camera_links(cameras, active_id)}</nav>
  <section class="card viewer">
    <div class="video-wrap">
      <img id="live" src="/stream.mjpeg?camera={escape(active_id)}" alt="Live camera feed">
      <div class="video-top">
        <span class="badge badge-live"><span class="dot"></span> LIVE</span>
        <span class="badge">{escape(resolution)}</span>
      </div>
      <div class="video-controls">
        <a class="btn btn-ghost" href="/api/snapshot.jpg?camera={escape(active_id)}" download>Snapshot</a>
        <form method="post" action="/ui/record/{record_action}?camera={escape(active_id)}">
          <button class="btn btn-rec" data-active="{"true" if recording else "false"}" type="submit">
            <span class="rec-dot"></span>{"Stop recording" if recording else "Record"}
          </button>
        </form>
        <a class="btn btn-ghost" href="/?camera={escape(active_id)}">Refresh</a>
      </div>
    </div>
    <div class="viewer-foot">
      <div class="foot-item"><span class="label">Camera</span><strong>{escape(str(camera.get("name", "—")))}</strong></div>
      <div class="foot-item"><span class="label">Uptime</span><strong>{_duration(camera.get("uptimeSeconds", 0))}</strong></div>
      <div class="foot-item"><span class="label">Viewers</span><strong>{camera.get("viewers", 0)}</strong></div>
      <div class="foot-item"><span class="label">Bitrate</span><strong>{camera.get("bitrateKbps", 0)} kbps</strong></div>
    </div>
  </section>
  <aside class="side">
    <section class="card">
      <div class="card-head"><h2>Stream health</h2><span class="chip">{escape(str(camera.get("transport", "—")))}</span></div>
      <dl class="stats">
        <div><dt>Status</dt><dd>{escape(state)}</dd></div>
        <div><dt>Resolution</dt><dd>{escape(resolution)}</dd></div>
        <div><dt>Frame rate</dt><dd>{camera.get("fps", 0)} fps</dd></div>
        <div><dt>Decryption</dt><dd>{_decryption(camera)}</dd></div>
        <div><dt>Reconnects</dt><dd>{camera.get("restarts", 0)}</dd></div>
        <div><dt>Frames seen</dt><dd>{camera.get("frames", 0)}</dd></div>
      </dl>
      {_error_box(camera)}
    </section>
    <section class="card">
      <div class="card-head"><h2>Recording</h2><span class="chip">{"recording" if recording else "idle"}</span></div>
      <div class="rec-meta">
        <div><span class="label">Current file</span><strong>{escape(str(recorder.get("name") or "—"))}</strong></div>
        <div><span class="label">Elapsed</span><strong>{_duration(recorder.get("durationSeconds", 0))}</strong></div>
        <div><span class="label">Size</span><strong>{_bytes(recorder.get("bytes", 0))}</strong></div>
      </div>
      <form method="post" action="/ui/record/{record_action}?camera={escape(active_id)}">
        <button class="btn btn-rec btn-block" data-active="{"true" if recording else "false"}" type="submit">
          <span class="rec-dot"></span>{"Stop recording" if recording else "Start recording"}
        </button>
      </form>
      <p class="hint">Copies the decrypted H.264 stream to disk without re-encoding.</p>
    </section>
  </aside>
  <section class="card recordings">
    <div class="card-head"><h2>Recordings</h2><a class="btn btn-ghost btn-sm" href="/?camera={escape(active_id)}">Refresh</a></div>
    {_recordings(recordings, active_id)}
  </section>
  <section class="card events">
    <div class="card-head">
      <h2>Vision events</h2>
      <div class="head-actions">
        <span class="chip" data-state="{"error" if vision.get("lastError") else "live"}">{escape(str(vision.get("name", "—")))} · {escape(str(vision.get("model", "—")))}</span>
        <a class="btn btn-ghost btn-sm" href="/?camera={escape(active_id)}">Refresh</a>
      </div>
    </div>
    {_events(events)}
  </section>
</main>
</body>
</html>"""


def _camera_links(cameras: list[dict[str, Any]], active_id: str) -> str:
    return "".join(
        f'<a class="cam-chip" data-active="{str(camera["id"] == active_id).lower()}" '
        f'href="/?camera={escape(str(camera["id"]))}">'
        f'<span class="cam-num">{int(camera.get("channel", 0)):02d}</span>'
        f"{escape(str(camera.get('name', '')))}</a>"
        for camera in cameras
    )


def _decryption(camera: dict[str, Any]) -> str:
    decryption = camera.get("decryption") or {}
    return f"{decryption.get('decryptedNals', 0)} / {decryption.get('flaggedNals', 0)} NALs"


def _error_box(camera: dict[str, Any]) -> str:
    error = camera.get("error")
    if not error:
        return ""
    return f'<div class="error-box"><strong>Camera error</strong><p>{escape(str(error))}</p></div>'


def _recordings(items: list[dict[str, Any]], active_id: str) -> str:
    if not items:
        return '<div class="empty">No recordings yet. Hit record to capture the stream.</div>'
    rows = []
    for item in items:
        name = escape(str(item["name"]))
        rows.append(
            '<div class="rec-row">'
            f'<div class="name">{name}</div>'
            f'<div class="muted size">{_bytes(item.get("bytes", 0))}</div>'
            f'<div class="muted date">{_short_time(item.get("modified_at"))}</div>'
            '<div class="row-actions">'
            f'<a class="icon-btn" title="Play" href="/api/recordings/{name}?camera={escape(active_id)}">▶</a>'
            f'<a class="icon-btn" title="Download" href="/api/recordings/{name}?camera={escape(active_id)}" download>↓</a>'
            f'<form method="post" action="/ui/recordings/{name}/delete?camera={escape(active_id)}">'
            '<button class="icon-btn danger" title="Delete" type="submit">✕</button></form>'
            "</div></div>"
        )
    return "".join(rows)


def _events(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<div class="empty">No events yet. Motion will trigger the vision model.</div>'
    cards = []
    for item in items:
        event_id = int(item["id"])
        kind = escape(str(item.get("event_type", "motion")))
        cards.append(
            '<article class="event-card">'
            f'<img class="event-thumb" loading="lazy" alt="" src="/api/events/{event_id}/snapshot">'
            '<div class="event-body">'
            f'<div class="event-title"><span class="tag">{kind}</span> '
            f"{item.get('people_count', 0)} people · {item.get('vehicles_count', 0)} vehicles</div>"
            f'<div class="event-meta">{_short_time(item.get("created_at"))} · '
            f"{round(float(item.get('confidence', 0)) * 100)}%</div>"
            f'<div class="event-desc">{escape(str(item.get("description") or "No description"))}</div>'
            "</div></article>"
        )
    return f'<div class="event-list">{"".join(cards)}</div>'


def _bytes(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" or amount >= 10 else f"{amount:.1f} {unit}"
        amount /= 1024
    return "0 B"


def _duration(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def _short_time(value: Any) -> str:
    if not value:
        return "—"
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).astimezone().strftime("%b %d, %H:%M")
    except ValueError:
        return str(value)
