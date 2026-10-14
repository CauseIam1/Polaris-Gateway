"""
polaris-gateway/scene_service.py — Live Eyes plumbing for the Chat Gateway
(v1.7, Oct 13 2026).

Two capabilities, both riding the Burst Receiver's scene custody endpoints:
  * always-know  — get_scene_state(): a thread-safe ~5s-cached fetch of the
    fused X17 scene digest (active window, desktop activity, camera attention
    + occupancy, OCR head, latest vision event); rendered into the system
    prompt every turn via get_scene_prompt_block().
  * look-anytime — request_x17_snapshot(): ONE fresh frame (desktop or
    camera) pulled through the receiver's scene relay, answered by the chat
    gateway's existing vision LLM ladder. Pixels flow only on demand.

Honesty rules: this module NEVER raises — an unreachable receiver degrades to
an honest "unavailable" prompt block / tool error. The identity rides an
X-Service-Token header (POLARIS_SERVICE_TOKEN) so the gateway's machine calls
land in the Operator persona regardless of source address (the LAN-only guard
still applies to every other caller).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests

BURST_RECEIVER_URL = os.environ.get("BURST_RECEIVER_URL", "http://127.0.0.1:5001")
SERVICE_TOKEN = os.environ.get("POLARIS_SERVICE_TOKEN", "")

SCENE_CACHE_TTL_S = 5.0       # prompt reuse window — never refetch per call
SCENE_HTTP_TIMEOUT_S = 3.0
SNAPSHOT_TIMEOUT_S = 14.0     # receiver relays to the X17 and waits up to 10 s
_ABSENT_BLOCK_GRACE_S = 600.0 # nag about an absent feed only this long after last seen

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_ts: float = 0.0
_last_seen_mono: float = 0.0      # last time the scene endpoint answered at all


def _service_headers() -> Dict[str, str]:
    """Header only when a token is configured — otherwise identity falls back
    to the receiver's IP gate (in-container loopback is Operator anyway)."""
    return {"X-Service-Token": SERVICE_TOKEN} if SERVICE_TOKEN else {}


def get_scene_state(force: bool = False) -> Optional[Dict[str, Any]]:
    """~5s-cached fused scene state from the Burst Receiver (None = unavailable)."""
    global _cache, _cache_ts
    now = time.monotonic()
    with _lock:
        if (not force and _cache is not None
                and now - _cache_ts < SCENE_CACHE_TTL_S):
            return _cache
    try:
        resp = requests.get(
            f"{BURST_RECEIVER_URL}/api/vision/scene/state",
            headers=_service_headers(),
            timeout=SCENE_HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("non-dict scene payload")
    except Exception:
        with _lock:
            _cache = None
            _cache_ts = now
        return None
    with _lock:
        _cache = data
        _cache_ts = now
        global _last_seen_mono
        _last_seen_mono = now
    return data


def request_x17_snapshot(
    source: str = "desktop",
    max_width: int = 1280,
    quality: int = 70,
) -> Optional[Dict[str, Any]]:
    """Pull ONE fresh frame from the X17 via the receiver's relay.

    Returns the receiver's JSON envelope on any HTTP answer (including
    {'success': False, ...} failures) or None when the receiver itself is
    unreachable. Never raises.
    """
    try:
        resp = requests.get(
            f"{BURST_RECEIVER_URL}/api/vision/scene/snapshot",
            params={"source": source, "max_width": max_width,
                    "quality": quality},
            headers=_service_headers(),
            timeout=SNAPSHOT_TIMEOUT_S,
        )
        return resp.json()
    except Exception as exc:
        return {"success": False, "source": source,
                "error": f"scene relay unreachable: {exc}"}


def _fmt_attentive(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _fmt_announcement(state: Dict[str, Any]) -> Optional[str]:
    ann = state.get("latest_announcement")
    if not isinstance(ann, dict):
        return None
    message = str(ann.get("message") or "").strip()
    if not message:
        return None
    age = ""
    try:
        ts = datetime.fromisoformat(str(ann.get("timestamp") or ""))
        age = f", {max(0, int((datetime.now() - ts).total_seconds()))}s ago"
    except Exception:
        pass
    return f"{message}{age}"


def get_scene_prompt_block() -> str:
    """Compact [CURRENT SCENE] block for the system prompt.

    Rendered from the ~5s-cached digest — she always-knows without burning a
    vision pass. Absent feed: one honest line for a grace window after the
    last sighting, then full silence (a long-dead X17 should not tax every
    turn, and this gateway also answers for non-LGI users).
    """
    state = get_scene_state()
    if not state:
        with _lock:
            seen_recently = _last_seen_mono > 0.0 and (
                time.monotonic() - _last_seen_mono < _ABSENT_BLOCK_GRACE_S)
        if seen_recently:
            return ("[CURRENT SCENE — LIVE X17 TELEMETRY]:\n"
                    "Scene feed unreachable right now — if the question is about "
                    "the operator's screen or camera, say so honestly or use "
                    "look_x17_desktop / look_x17_camera.")
        return ""

    cur = state.get("current") or {}
    if not isinstance(cur, dict):
        cur = {}
    lines = []
    age_s = state.get("age_s")
    header_age = (f", digest {age_s}s old"
                  if isinstance(age_s, (int, float)) else "")
    lines.append(f"[CURRENT SCENE — LIVE X17 TELEMETRY{header_age}]:")

    app = str(cur.get("app") or "").strip()
    window = str(cur.get("active_window") or "").strip()
    activity = str(cur.get("activity") or "unknown")
    screen_line = "Screen: "
    screen_line += f"{app} — " if app else ""
    screen_line += (window[:120] or "(no window title)")
    screen_line += f" (activity: {activity})"
    lines.append(screen_line)

    people = cur.get("people_count")
    cam_mode = str(cur.get("camera_mode") or "off")
    lines.append(
        f"Camera: operator attentive={_fmt_attentive(cur.get('attentive'))}, "
        f"{people if isinstance(people, int) else 'unknown'} person(s) in view, "
        f"webcam mode: {cam_mode}")

    ocr_head = str(cur.get("ocr_head") or "").strip()
    if ocr_head:
        lines.append(f"On screen (OCR head): \"{ocr_head[:160]}\"")

    ann = _fmt_announcement(state)
    if ann:
        lines.append(f"Latest vision event: {ann}")

    lines.append(
        "This is the fused digest only. When it is not enough, look closer: "
        "look_x17_desktop / look_x17_camera pull ONE fresh frame through the "
        "vision ladder (pixels are pull-only); read_x17_scene re-reads the raw digest.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tool schemas — gotify-flat shape ({"name", "description", "parameters"}),   #
# matching every other registry the system prompt renders.                    #
SCENE_TOOL_SCHEMAS = [
    {
        "name": "look_x17_desktop",
        "description": (
            "Capture a FRESH screenshot of the operator's X17 desktop right now "
            "and answer through the vision model. Use when the cached [CURRENT "
            "SCENE] digest is not enough and the question is about what is "
            "actually on screen."),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to look for or answer about the screen",
                },
                "max_width": {
                    "type": "integer",
                    "description": "JPEG width cap 320-1920 (default 1280)",
                    "default": 1280,
                },
                "quality": {
                    "type": "integer",
                    "description": "JPEG quality 30-90 (default 70)",
                    "default": 70,
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "look_x17_camera",
        "description": (
            "Capture ONE fresh frame from the operator's X17 webcam right now "
            "and answer through the vision model. Use for questions about who "
            "or what is physically in view, attention cues, gestures, or "
            "objects in the room — beyond the cached people-count."),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to look for in the camera frame",
                },
                "max_width": {
                    "type": "integer",
                    "description": "JPEG width cap 320-1280 (default 1280)",
                    "default": 1280,
                },
                "quality": {
                    "type": "integer",
                    "description": "JPEG quality 30-90 (default 70)",
                    "default": 70,
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "read_x17_scene",
        "description": (
            "Re-read the latest fused X17 scene digest (active window, desktop "
            "activity, camera attention/occupancy, OCR head, latest vision "
            "event) WITHOUT spending a vision-model pass. Cheap first check — "
            "escalate to look_x17_* only when the digest cannot answer."),
        "parameters": {"type": "object", "properties": {}},
    },
]


def get_scene_tool_schemas_json() -> str:
    """Return scene tool schemas as JSON string for the LLM system prompt."""
    return json.dumps(SCENE_TOOL_SCHEMAS, indent=2)


__all__ = [
    "BURST_RECEIVER_URL", "SERVICE_TOKEN",
    "get_scene_state", "request_x17_snapshot", "get_scene_prompt_block",
    "SCENE_TOOL_SCHEMAS", "get_scene_tool_schemas_json",
]