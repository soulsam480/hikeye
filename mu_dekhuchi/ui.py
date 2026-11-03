from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .engine import EventEngine
from .events import EventStore
from .hub import CameraHub

STATIC_DIR = Path(__file__).parent / "static"

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def _asset_version() -> str:
    """Cache-bust the built CSS/JS whenever either file changes."""
    latest = 0
    for name in ("styles.css", "app.js"):
        try:
            latest = max(latest, (STATIC_DIR / name).stat().st_mtime_ns)
        except OSError:
            continue
    return str(latest)


def render_dashboard(hub: CameraHub, store: EventStore, engine: EventEngine) -> str:
    status = hub.status_payload()
    status["vision"] = engine.status()
    active_id = hub.active_id or ""
    recordings = hub.active_recorder.list() if hub.active_recorder else []
    initial = {
        "status": status,
        "events": store.list(active_id or None, 18),
        "recordings": recordings,
    }
    return _TEMPLATES.get_template("dashboard.html").render(
        initial=initial,
        asset_version=_asset_version(),
    )
