# GEV (God's Eye View) command-bridge tools — GEV.md contract
# HTTP client side for the gev container (compose service 'gev', host :7004 →
# service :5001). Commands posted to /api/gev/command are broadcast by the GEV
# Socket.IO bridge to the operator's viewer screens as 'gev_update' events.
# gev_get_operator_position reads the operator's last GPS fix from the burst
# receiver, which runs in this same container (run_gateway.sh, port 5001) —
# localhost is auth-consistent and adds no CORS surface.

import json
import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("gev_tools")

# compose service DNS; override with GEV_SERVICE_URL for standalone runs
GEV_SERVICE_URL = os.environ.get("GEV_SERVICE_URL", "http://gev:5001")
# Burst receiver lives beside the gateway in the same container (port 5001)
GEV_GPS_SOURCE_URL = os.environ.get("GEV_GPS_SOURCE_URL", "http://127.0.0.1:5001")

_TIMEOUT_SECONDS = 8

# Tool schemas for LLM function calling (flat gotify-style shape)
GEV_TOOL_SCHEMAS = [
    {
        "name": "gev_set_center",
        "description": "Fly the operator's GEV (God's Eye View) Cesium globe camera to a longitude/latitude. Use for tactical site checks and 'look at X' requests. No SSH or remote-device interaction required.",
        "parameters": {
            "type": "object",
            "properties": {
                "lon": {"type": "number", "description": "Longitude in decimal degrees (-180..180)"},
                "lat": {"type": "number", "description": "Latitude in decimal degrees (-90..90)"},
                "height": {"type": "number", "description": "Camera eye height in meters above the ellipsoid (default 6000; ~500 for street level, ~30000 for a region overview)"}
            },
            "required": ["lon", "lat"]
        }
    },
    {
        "name": "gev_toggle_layer",
        "description": "Show or hide a basemap/overlay layer on the GEV globe. Layers: 'osm' (OpenStreetMap street grid), 'imagery' (Esri World Imagery satellite), 'buildings3d' (OSM Buildings 3D — requires GEV_ION_TOKEN). The dark CARTO basemap is always on.",
        "parameters": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "enum": ["osm", "imagery", "buildings3d"], "description": "Layer id to toggle"},
                "enabled": {"type": "boolean", "description": "true = show the layer, false = hide it"}
            },
            "required": ["layer", "enabled"]
        }
    },
    {
        "name": "gev_query_object",
        "description": "Ask the operator's GEV globe screen for a status census (which layers are on, current camera position, tracked operator-fix age). Use to verify the globe is live around your GEV commands.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Census selector: 'all' (default) or 'operator' for just the operator-fix summary"}
            }
        }
    },
    {
        "name": "gev_get_operator_position",
        "description": "Read the operator's last GPS fix from the FreeRoam burst receiver. Returns lat/lng, altitude, speed, heading, accuracy and fix age so Polaris can keep track of the operator. Returns success=false with a clear error when no fix has arrived yet.",
        "parameters": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Optional device key (e.g. 'rich', 'matt'); omit for the default operator device"}
            }
        }
    }
]

def get_gev_tool_schemas_json() -> str:
    """Return GEV tool schemas as JSON string for the LLM system prompt."""
    return json.dumps(GEV_TOOL_SCHEMAS, indent=2)


def _post_command(op: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """POST one command to the GEV bridge; errors normalize into the result."""
    try:
        response = requests.post(
            f"{GEV_SERVICE_URL}/api/gev/command",
            json={"op": op, "args": args},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning(f"GEV bridge unreachable at {GEV_SERVICE_URL}: {exc}")
        return {"success": False, "error": f"GEV service unreachable: {exc}"}


def gev_set_center(lon: Any, lat: Any, height: Any = 6000) -> Dict[str, Any]:
    """Fly the GEV globe camera to (lon, lat) at the given eye height (meters)."""
    try:
        lon_f, lat_f = float(lon), float(lat)
        if lon_f != lon_f or lat_f != lat_f:  # NaN guard
            raise ValueError("coordinates are not finite numbers")
    except (TypeError, ValueError) as exc:
        return {"success": False, "error": f"Invalid coordinates for gev_set_center: {exc}"}
    if not (-180.0 <= lon_f <= 180.0 and -90.0 <= lat_f <= 90.0):
        return {"success": False, "error": f"Coordinates out of range: lon={lon_f}, lat={lat_f}"}
    try:
        height_f = float(height) if height is not None else 6000.0
        if height_f <= 0:
            raise ValueError
    except (TypeError, ValueError):
        height_f = 6000.0
    result = _post_command("set_center", {"lon": lon_f, "lat": lat_f, "height": height_f})
    if result.get("success"):
        result["summary"] = (
            f"GEV camera flying to {lat_f:.4f}, {lon_f:.4f} at {height_f:.0f} m "
            f"({result.get('viewers_connected', '?')} viewer(s) connected)"
        )
    return result


def gev_toggle_layer(layer: Any, enabled: Any = True) -> Dict[str, Any]:
    """Show/hide an overlay layer ('osm' | 'imagery' | 'buildings3d') on the globe."""
    layer_id = str(layer or "").strip().lower()
    if layer_id not in ("osm", "imagery", "buildings3d"):
        return {"success": False, "error": f"Unknown layer '{layer}' (known: osm, imagery, buildings3d)"}
    result = _post_command("toggle_layer", {"layer": layer_id, "enabled": bool(enabled)})
    if result.get("success"):
        result["summary"] = (
            f"GEV layer '{layer_id}' {'shown' if enabled else 'hidden'} "
            f"({result.get('viewers_connected', '?')} viewer(s) connected)"
        )
    return result


def gev_query_object(query: Any = "all") -> Dict[str, Any]:
    """Request a census from the viewer screen; returns the last stored census."""
    result = _post_command("query_object", {"query": str(query or "all").strip().lower() or "all"})
    if result.get("success"):
        try:
            response = requests.get(f"{GEV_SERVICE_URL}/api/gev/census", timeout=_TIMEOUT_SECONDS)
            if response.ok:
                result = dict(result)
                result["census"] = response.json().get("census", {})
                result["census_note"] = "Census is viewer-reported over the socket; it reflects the last report."
        except requests.RequestException:
            pass  # census is best-effort; the command result stands on its own
    return result


def gev_get_operator_position(device: Optional[str] = None) -> Dict[str, Any]:
    """Read the operator's last GPS fix from the burst receiver's live store."""
    params = {"device": device} if device else {}
    try:
        response = requests.get(
            f"{GEV_GPS_SOURCE_URL}/api/gps/last", params=params, timeout=_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        payload = response.json()
        fix = payload.get("fix") if isinstance(payload, dict) else None
        if payload.get("ok") and isinstance(fix, dict):
            summary = (
                f"Operator fix: {float(fix.get('lat', 0)):.4f}, {float(fix.get('lng', 0)):.4f} "
                f"(±{fix.get('accuracy', '?')} m, age {payload.get('age_seconds', '?')} s)"
            )
            if payload.get("stale"):
                summary += " — STALE (>120 s)"
            return {
                "success": True,
                "fix": fix,
                "age_seconds": payload.get("age_seconds"),
                "stale": bool(payload.get("stale")),
                "summary": summary,
            }
        return {"success": False, "error": "No GPS fix received yet — nothing has posted to the burst receiver's GPS feed"}
    except requests.RequestException as exc:
        logger.warning(f"Burst receiver GPS feed unreachable at {GEV_GPS_SOURCE_URL}: {exc}")
        return {"success": False, "error": f"GPS source unreachable: {exc}"}
    except (ValueError, KeyError, IndexError) as exc:
        return {"success": False, "error": f"Malformed GPS response: {exc}"}


# Export all tool functions
__all__ = [
    "gev_set_center",
    "gev_toggle_layer",
    "gev_query_object",
    "gev_get_operator_position",
    "GEV_TOOL_SCHEMAS",
    "get_gev_tool_schemas_json"
]