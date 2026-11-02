from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .engine import EventEngine
from .events import EventStore
from .hub import CameraHub

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_dashboard(hub: CameraHub, store: EventStore, engine: EventEngine) -> str:
    status = hub.status_payload()
    status["vision"] = engine.status()
    active_id = hub.active_id or ""
    recordings = hub.active_recorder.list() if hub.active_recorder else []
    initial = {
        "status": status,
        "events": store.list(active_id or None, 12),
        "recordings": recordings,
    }
    return _TEMPLATES.get_template("dashboard.html").render(initial=initial)
