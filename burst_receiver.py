#!/usr/bin/env python3
"""
burst_receiver.py - Multi-User Burst Photo & Telemetry Receiver

Receives burst payloads and telemetry from Android clients (Rich and Matt).
Routes files to user-specific folders based on IP address.
LAN-only access - no WireGuard routing.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room
import os
import json
from datetime import datetime
from pathlib import Path
import base64
import shutil
import requests
import concurrent.futures
from typing import Optional, Dict, Any, List
import numpy as np
from collections import deque
import math

# ============== CSI RADAR PROCESSING ENGINE ==============
class CSIRadarEngine:
    """
    Motion sensor arrays to spatial presence converter.

    Converts motion vector arrays into 2D/3D spatial coordinates for
    real-time radar visualization on the Polaris dashboard. Historical
    origin: Miss Pi's WiFi-CSI radar (decommissioned Sep 30, 2026); the
    live feed now comes from the X17's LGI webcam perception stack
    (canonical device id 'x17-webcam').
    """
    
    def __init__(self, window_size=30, movement_threshold=0.15):
        self.window_size = window_size
        self.movement_threshold = movement_threshold
        
        # Rolling window for temporal smoothing
        self.csi_window = deque(maxlen=window_size)
        
        # Spatial tracking state
        self.current_position = None  # (x, y) in normalized coordinates [-1, 1]
        self.velocity = np.array([0.0, 0.0])
        self.last_update = None
        
        # Movement detection
        self.motion_detected = False
        self.motion_confidence = 0.0
        
        # Subcarrier weighting (higher frequencies = better resolution)
        self.subcarrier_weights = None
        
    def initialize_subcarriers(self, num_subcarriers):
        """Initialize subcarrier weights based on frequency"""
        # Higher subcarriers provide better spatial resolution
        self.subcarrier_weights = np.linspace(0.5, 1.5, num_subcarriers)
        
    def process_csi_vector(self, csi_vector: np.ndarray, timestamp: float) -> Dict[str, Any]:
        """
        Process CSI amplitude vector into spatial coordinates
        
        Args:
            csi_vector: Array of amplitude values (one per subcarrier)
            timestamp: Unix timestamp of measurement
            
        Returns:
            Dictionary containing spatial coordinates and metadata
        """
        # Normalize CSI vector
        csi_normalized = self._normalize_csi(csi_vector)
        
        # Add to temporal window
        self.csi_window.append({
            'csi': csi_normalized.copy(),
            'timestamp': timestamp
        })
        
        # Detect motion from temporal variance
        motion = self._detect_motion()
        
        # Calculate spatial coordinates
        position = self._calculate_position(csi_normalized)
        
        # Update velocity
        if self.current_position is not None:
            dt = timestamp - self.last_update if self.last_update else 0.016
            self.velocity = (position - self.current_position) / max(dt, 0.001)
        
        self.current_position = position
        self.last_update = timestamp
        self.motion_detected = motion['detected']
        self.motion_confidence = motion['confidence']
        
        return {
            'x': float(position[0]),
            'y': float(position[1]),
            'z': float(self._calculate_elevation(csi_normalized)),
            'velocity': float(np.linalg.norm(self.velocity)),
            'motion_detected': bool(motion['detected']),  # Convert numpy bool to Python bool
            'confidence': float(motion['confidence']),
            'timestamp': timestamp,
            'subcarrier_count': int(len(csi_vector))
        }
    
    def _normalize_csi(self, csi_vector: np.ndarray) -> np.ndarray:
        """Normalize CSI amplitude vector"""
        csi = np.array(csi_vector, dtype=np.float32)
        
        # Remove DC component
        csi = csi - np.mean(csi)
        
        # Normalize to [-1, 1]
        max_val = np.max(np.abs(csi))
        if max_val > 0:
            csi = csi / max_val
            
        return csi
    
    def _detect_motion(self) -> Dict[str, Any]:
        """Detect motion from temporal variance in CSI window"""
        if len(self.csi_window) < 3:
            return {'detected': False, 'confidence': 0.0}
        
        # Calculate variance across time window
        csi_stack = np.array([item['csi'] for item in self.csi_window])
        temporal_variance = np.var(csi_stack, axis=0)
        
        # Motion score = mean variance weighted by subcarrier importance
        if self.subcarrier_weights is not None:
            motion_score = np.mean(temporal_variance * self.subcarrier_weights)
        else:
            motion_score = np.mean(temporal_variance)
        
        detected = motion_score > self.movement_threshold
        confidence = min(1.0, motion_score / (self.movement_threshold * 2))
        
        return {
            'detected': detected,
            'confidence': float(confidence),
            'score': float(motion_score)
        }
    
    def _calculate_position(self, csi_normalized: np.ndarray) -> np.ndarray:
        """
        Calculate 2D position from CSI vector
        
        Uses phase differences and amplitude ratios to estimate position
        """
        # Simple approach: use weighted centroid of subcarrier amplitudes
        if self.subcarrier_weights is not None:
            weighted_csi = csi_normalized * self.subcarrier_weights
        else:
            weighted_csi = csi_normalized
        
        # Map to 2D coordinates using different projections
        # X coordinate: low vs high frequency subcarriers
        mid_point = len(weighted_csi) // 2
        x = np.mean(weighted_csi[mid_point:]) - np.mean(weighted_csi[:mid_point])
        
        # Y coordinate: odd vs even subcarriers
        y = np.mean(weighted_csi[1::2]) - np.mean(weighted_csi[0::2])
        
        # Normalize to [-1, 1] range
        position = np.array([x, y])
        max_norm = np.linalg.norm(position)
        if max_norm > 1.0:
            position = position / max_norm
            
        return position
    
    def _calculate_elevation(self, csi_normalized: np.ndarray) -> float:
        """
        Calculate estimated elevation (Z coordinate) from CSI
        
        Uses amplitude decay pattern to estimate distance/elevation
        """
        # Higher overall amplitude = closer to sensor
        mean_amplitude = np.mean(np.abs(csi_normalized))
        
        # Map to [-1, 1] where 0 = ground level, 1 = maximum elevation
        elevation = (mean_amplitude - 0.5) * 2.0
        return float(np.clip(elevation, -1.0, 1.0))
    
    def get_status(self) -> Dict[str, Any]:
        """Get current engine status"""
        velocity_magnitude = float(np.linalg.norm(self.velocity)) if self.velocity is not None else 0.0
        return {
            'window_size': int(self.window_size),
            'samples_in_window': int(len(self.csi_window)),
            'motion_detected': bool(self.motion_detected),
            'confidence': float(self.motion_confidence),
            'current_position': self.current_position.tolist() if self.current_position is not None else [0.0, 0.0],
            'velocity': velocity_magnitude
        }

# Global radar engine instance
radar_engine = CSIRadarEngine(window_size=30, movement_threshold=0.15)

app = Flask(__name__)
# Enable CORS for dashboard access (port 7000) and local development
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "http://192.168.50.51:7000",
            "http://localhost:7000",
            "http://127.0.0.1:7000",
            # GEV — God's Eye View globe (compose service 'gev', host :7004)
            "http://192.168.50.51:7004",
            "http://localhost:7004",
            "http://127.0.0.1:7004"
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    },
    r"/socket.io/*": {
        "origins": ["*"],
        "methods": ["GET", "POST"],
        "allow_headers": ["Content-Type"]
    }
})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', logger=True, engineio_logger=True)

# ============== OLLAMA VISION CONFIGURATION ==============
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
VISION_MODEL = os.environ.get("VISION_MODEL", "polaris-ai:latest")  # Vision-capable ladder rung 1
VISION_TIMEOUT = 60  # seconds

# Vision analysis prompts for different scenarios
VISION_PROMPTS = {
    "general": "Analyze this image and provide a concise description. Identify: (1) main objects/subjects, (2) scene context, (3) any visible text, (4) notable actions or activities. Keep response under 150 words.",
    "tactical": "Analyze this tactical/surveillance image. Identify: (1) people/vehicles/structures, (2) potential threats or points of interest, (3) environmental conditions, (4) recommended actions. Be specific and concise.",
    "ocr": "Extract all visible text from this image. Preserve formatting where possible. If no text is visible, state 'No text detected'.",
    "sharpness_check": "Evaluate this image for sharpness and clarity. Rate 1-10 (10=perfect). Note any blur, motion artifacts, or focus issues. Recommend if suitable for detailed analysis."
}

# ============== NETWORK CONFIGURATION (LAN ONLY) ==============
# Static IP addresses from northstar.md
RICH_IP = "192.168.50.42"
MATT_IP = "192.168.50.98"
HOST_IP = "192.168.50.51"  # Dashboard host machine
LGI_X17_IP = "192.168.50.227"  # X17 (LGI) - webcam perception radar source (device 'x17-webcam')
RICH_WG_TUNNEL_IP = "10.20.30.3"  # Rich's phone inside the FreeRoam WireGuard tunnel (freeroam-wireguard)
MATT_WG_TUNNEL_IP = "10.20.30.2"  # Matt's phone inside the FreeRoam WireGuard tunnel (freeroam-wireguard)
DOCKER_HAIRPIN_IP = "172.18.0.1"  # docker-proxy source seen when tunnel/host traffic hairpins into published ports (resolves to Rich per 2026-10-03 WG-primary restore)
ROUTER_SNAT_IP = "192.168.50.1"  # router-LAN source when WG-off home traffic takes a public WAN name and the router hairpin SNATs it (resolves to Rich per 2026-10-03 WG-off-at-home default)

# Allowed IPs for LAN-only operation
# "127.0.0.1" = loopback: in-container GPS/test traffic (GEV tool smoke calls)
ALLOWED_IPS = [RICH_IP, MATT_IP, HOST_IP, LGI_X17_IP, RICH_WG_TUNNEL_IP, MATT_WG_TUNNEL_IP, DOCKER_HAIRPIN_IP, ROUTER_SNAT_IP, "127.0.0.1"]

# ============== USER PROFILES CONFIGURATION ==============
USER_CONFIG = {
    RICH_IP: {
        "name": "Rich",
        "burst_dir": "/data/freeroam/rich/bursts",
        "telemetry_log": "/data/freeroam/rich/telemetry.log"
    },
    MATT_IP: {
        "name": "Matt",
        "burst_dir": "/data/freeroam/matt/bursts",
        "telemetry_log": "/data/freeroam/matt/telemetry.log"
    },
    HOST_IP: {
        "name": "Operator",
        "burst_dir": "/data/freeroam/operator/bursts",
        "telemetry_log": "/data/freeroam/operator/telemetry.log"
    },
    LGI_X17_IP: {
        "name": "LGI (X17)",
        "burst_dir": "/data/freeroam/x17/bursts",
        "telemetry_log": "/data/freeroam/x17/telemetry.log",
        "radar_source": True  # X17 LGI webcam perception telemetry source (device 'x17-webcam')
    },
    # Rich's WireGuard tunnel identity (IP alias → same account storage, same
    # MASQUERADE + published-port hairpin pattern as below; WG tunnel primary
    # restored 2026-10-03)
    RICH_WG_TUNNEL_IP: {
        "name": "Rich",
        "burst_dir": "/data/freeroam/rich/bursts",
        "telemetry_log": "/data/freeroam/rich/telemetry.log"
    },
    # Matt's WireGuard tunnel identities (Rich-pattern IP aliases → same account
    # storage). The WG container MASQUERADEs his 10.20.30.2 onto the docker
    # bridge and the published-port hairpin rewrites it to 172.18.0.1, so both
    # must resolve to Matt exactly like LGI_X17_IP resolves to the X17 vault.
    MATT_WG_TUNNEL_IP: {
        "name": "Matt",
        "burst_dir": "/data/freeroam/matt/bursts",
        "telemetry_log": "/data/freeroam/matt/telemetry.log"
    },
    # Hairpin catch-all → Rich (WG tunnel primary restored 2026-10-03):
    # tunnel uploads MASQUERADE to 172.18.0.1, so bursts land in Rich's dirs
    DOCKER_HAIRPIN_IP: {  # shared with host-shell proxy curls (bursts only originate from phones)
        "name": "Rich",
        "burst_dir": "/data/freeroam/rich/bursts",
        "telemetry_log": "/data/freeroam/rich/telemetry.log"
    },
    # Router-SNAT → Rich (WG-off-at-home default, 2026-10-03): with WG down,
    # the phone on home WiFi taking a public WAN name hairpins through the
    # router, which can SNAT the source to its own LAN IP (192.168.50.1 was
    # observed on the WG handshake); bursts then still land in Rich's dirs.
    ROUTER_SNAT_IP: {
        "name": "Rich",
        "burst_dir": "/data/freeroam/rich/bursts",
        "telemetry_log": "/data/freeroam/rich/telemetry.log"
    },
    # Loopback → Operator (in-container GPS sender tests, e.g. docker exec curl)
    "127.0.0.1": {
        "name": "Operator",
        "burst_dir": "/data/freeroam/operator/bursts",
        "telemetry_log": "/data/freeroam/operator/telemetry.log"
    }
}

MAX_BURST_AGE_HOURS = 24

def get_user_ip():
    """Get the client IP address from the request."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def get_user_config(ip):
    """Get user configuration based on IP address."""
    if ip in USER_CONFIG:
        return USER_CONFIG[ip]
    return None

def get_timestamp_id():
    """Generate unique timestamp-based ID for burst payloads"""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

# ============== VISION ANALYSIS FUNCTIONS ==============

def encode_image_to_base64(image_path: str) -> Optional[str]:
    """
    Encode image file to base64 string for Ollama API
    Returns None if encoding fails
    """
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()
            return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error encoding image {image_path}: {e}")
        return None

def analyze_image_with_ollama(
    image_path: str,
    prompt_type: str = "general",
    custom_prompt: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send image to Ollama for vision analysis
    
    Args:
        image_path: Path to image file
        prompt_type: Type of analysis (general, tactical, ocr, sharpness_check)
        custom_prompt: Optional custom prompt overriding prompt_type
    
    Returns:
        Dictionary with analysis results
    """
    try:
        # Encode image
        image_base64 = encode_image_to_base64(image_path)
        if not image_base64:
            return {
                "success": False,
                "error": "Failed to encode image",
                "model": VISION_MODEL
            }
        
        # Get prompt
        prompt = custom_prompt or VISION_PROMPTS.get(prompt_type, VISION_PROMPTS["general"])
        
        # Build Ollama API request
        ollama_url = f"{OLLAMA_BASE_URL}/api/generate"
        payload = {
            "model": VISION_MODEL,
            "prompt": prompt,
            "images": [image_base64],
            "stream": False,
            "options": {
                "temperature": 0.3,  # Lower temperature for more focused analysis
                "num_predict": 500   # Limit response length
            }
        }
        
        # Send request
        response = requests.post(
            ollama_url,
            json=payload,
            timeout=VISION_TIMEOUT
        )
        response.raise_for_status()
        
        result = response.json()
        
        return {
            "success": True,
            "analysis": result.get("response", ""),
            "model": VISION_MODEL,
            "prompt_type": prompt_type,
            "image_path": image_path,
            "tokens_evaluated": result.get("eval_count", 0),
            "generation_time": result.get("eval_duration", 0) / 1e9  # Convert ns to seconds
        }
        
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Vision analysis timeout",
            "model": VISION_MODEL,
            "timeout_seconds": VISION_TIMEOUT
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "model": VISION_MODEL
        }

def select_sharpest_image(burst_dir: Path) -> Optional[Path]:
    """
    Select the sharpest image from a burst using simple heuristics
    For now, selects the largest file (usually best quality)
    Future: Implement actual sharpness detection with OpenCV
    """
    try:
        image_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
        images = [
            f for f in burst_dir.iterdir()
            if f.is_file() and f.suffix.lower() in image_extensions
        ]
        
        if not images:
            return None
        
        # Select largest file (proxy for quality)
        sharpest = max(images, key=lambda f: f.stat().st_size)
        return sharpest
        
    except Exception as e:
        print(f"Error selecting sharpest image: {e}")
        return None

def analyze_burst_async(burst_id: str, user_config: dict, burst_dir: Path):
    """
    Asynchronously analyze a burst payload
    Analyzes the sharpest image and stores results
    """
    try:
        # Select best image
        best_image = select_sharpest_image(burst_dir)
        if not best_image:
            print(f"No images found in burst {burst_id}")
            return
        
        print(f"🔍 Analyzing burst {burst_id}: {best_image.name}")
        
        # Run vision analysis
        analysis_result = analyze_image_with_ollama(
            str(best_image),
            prompt_type="tactical"  # Default to tactical for FreeRoam use case
        )
        
        # Save analysis results
        analysis_file = burst_dir / "vision_analysis.json"
        analysis_metadata = {
            "burst_id": burst_id,
            "analyzed_at": datetime.now().isoformat(),
            "analyzed_image": best_image.name,
            "analysis": analysis_result,
            "user": user_config["name"]
        }
        
        with open(analysis_file, 'w') as f:
            json.dump(analysis_metadata, f, indent=2)
        
        if analysis_result.get("success"):
            print(f"✅ Vision analysis complete for burst {burst_id}")
        else:
            print(f"⚠️ Vision analysis failed for burst {burst_id}: {analysis_result.get('error')}")
            
    except Exception as e:
        print(f"Error in async burst analysis: {e}")

def analyze_single_image(image_path: str, prompt_type: str = "general") -> Dict[str, Any]:
    """
    Analyze a single image file
    Wrapper for analyze_image_with_ollama with better error handling
    """
    if not os.path.exists(image_path):
        return {
            "success": False,
            "error": f"Image not found: {image_path}"
        }
    
    return analyze_image_with_ollama(image_path, prompt_type)

def ensure_user_storage(user_config):
    """Ensure user's storage directories exist"""
    Path(user_config["burst_dir"]).mkdir(parents=True, exist_ok=True)
    Path(os.path.dirname(user_config["telemetry_log"])).mkdir(parents=True, exist_ok=True)

def save_burst_payload(user_config, burst_id, files_data, metadata):
    """
    Save burst payload to disk with metadata
    Returns the storage path for the burst
    """
    burst_dir = Path(user_config["burst_dir"]) / burst_id
    burst_dir.mkdir(parents=True, exist_ok=True)
    
    # Save metadata
    metadata_file = burst_dir / "metadata.json"
    full_metadata = {
        "burst_id": burst_id,
        "received_at": datetime.now().isoformat(),
        "file_count": len(files_data),
        "user": user_config["name"],
        "client_metadata": metadata
    }
    with open(metadata_file, 'w') as f:
        json.dump(full_metadata, f, indent=2)
    
    # Save each file in the burst
    saved_files = []
    for i, file_data in enumerate(files_data):
        if 'filename' in file_data and 'data' in file_data:
            file_path = burst_dir / file_data['filename']
            try:
                # Decode base64 image data
                file_content = base64.b64decode(file_data['data'])
                with open(file_path, 'wb') as f:
                    f.write(file_content)
                saved_files.append({
                    "filename": file_data['filename'],
                    "path": str(file_path),
                    "size": len(file_content)
                })
            except Exception as e:
                saved_files.append({
                    "filename": file_data['filename'],
                    "error": str(e)
                })
    
    # Update index with saved file info
    full_metadata["saved_files"] = saved_files
    with open(metadata_file, 'w') as f:
        json.dump(full_metadata, f, indent=2)
    
    return str(burst_dir), full_metadata

def log_telemetry(user_config, telemetry_entry):
    """Append telemetry entry to user's rolling log"""
    with open(user_config["telemetry_log"], 'a') as f:
        f.write(json.dumps(telemetry_entry) + "\n")

def cleanup_old_bursts(burst_dir, cutoff):
    """Remove bursts older than MAX_BURST_AGE_HOURS"""
    try:
        for user_burst_dir in Path(burst_dir).iterdir():
            if user_burst_dir.is_dir():
                for burst_subdir in user_burst_dir.iterdir():
                    if burst_subdir.is_dir():
                        metadata_file = burst_subdir / "metadata.json"
                        if metadata_file.exists():
                            with open(metadata_file, 'r') as f:
                                metadata = json.load(f)
                            received_ts = datetime.fromisoformat(metadata.get("received_at", "")).timestamp()
                            if received_ts < cutoff:
                                shutil.rmtree(burst_subdir)
    except Exception as e:
        print(f"Cleanup error: {e}")

@app.before_request
def check_lan_access():
    """Block requests not from allowed LAN IPs."""
    client_ip = get_user_ip()
    if client_ip not in ALLOWED_IPS:
        return jsonify({
            "error": "Access denied: LAN-only access",
            "message": f"Your IP ({client_ip}) is not authorized"
        }), 403

@app.route('/api/burst', methods=['POST'])
def receive_burst():
    """
    Receive burst photo payload from Android client
    Routes to user-specific folder based on IP address
    
    JSON Payload Structure:
    {
        "files": [
            {"filename": "burst_001.jpg", "data": "<base64>"},
            {"filename": "burst_002.jpg", "data": "<base64>"}
        ],
        "metadata": {
            "trigger_type": "watch_ptt",
            "timestamp": "2026-08-18T13:00:00Z",
            "location": {"lat": 0, "lng": 0}
        }
    }
    """
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    
    if not user_config:
        return jsonify({
            "error": "Unknown user",
            "message": f"IP {client_ip} not configured"
        }), 404
    
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        
        files = data.get("files", [])
        metadata = data.get("metadata", {})
        
        # Validate minimum payload
        if not files or len(files) == 0:
            return jsonify({"error": "No files in burst payload"}), 400
        
        # Ensure user storage exists
        ensure_user_storage(user_config)
        
        # Generate unique burst ID
        burst_id = get_timestamp_id()
        
        # Save burst to user's folder
        storage_path, saved_metadata = save_burst_payload(
            user_config=user_config,
            burst_id=burst_id,
            files_data=files,
            metadata=metadata
        )
        
        # Log telemetry event
        log_telemetry(user_config, {
            "event_type": "burst_received",
            "burst_id": burst_id,
            "timestamp": datetime.now().isoformat(),
            "file_count": len(files)
        })
        
        # Cleanup old bursts periodically
        cutoff = datetime.now().timestamp() - (MAX_BURST_AGE_HOURS * 3600)
        cleanup_old_bursts(user_config["burst_dir"], cutoff)
        
        print(f"📸 Burst received from {user_config['name']}: {burst_id} ({len(files)} files)")
        
        # Trigger async vision analysis (non-blocking)
        burst_path = Path(storage_path)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        executor.submit(analyze_burst_async, burst_id, user_config, burst_path)
        
        return jsonify({
            "status": "success",
            "user": user_config["name"],
            "burst_id": burst_id,
            "storage_path": storage_path,
            "file_count": len(files),
            "vision_analysis": "processing",  # Analysis runs asynchronously
            "analysis_available_in": "~30-60 seconds"
        })
        
    except Exception as e:
        print(f"Burst receive error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/telemetry', methods=['POST'])
def receive_telemetry():
    """
    Receive text-only telemetry from Android client
    Routes to user-specific telemetry log based on IP address
    
    JSON Payload Structure:
    {
        "type": "voice_transcript" | "status" | "location",
        "content": "transcribed text or status data",
        "timestamp": "2026-08-18T13:00:00Z",
        "metadata": {}
    }
    """
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    
    if not user_config:
        return jsonify({
            "error": "Unknown user",
            "message": f"IP {client_ip} not configured"
        }), 404
    
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        
        telemetry_entry = {
            "received_at": datetime.now().isoformat(),
            "type": data.get("type", "unknown"),
            "content": data.get("content", ""),
            "timestamp": data.get("timestamp", datetime.now().isoformat()),
            "metadata": data.get("metadata", {})
        }
        
        # Ensure user storage exists
        ensure_user_storage(user_config)
        
        # Log telemetry to user's log
        log_telemetry(user_config, telemetry_entry)
        
        # GEV (God's Eye View) upgrade: legacy "location" telemetry now ALSO
        # updates the live GPS fix store and broadcasts to the GEV globe room.
        # Backward compatible — voice_transcript/status flow is untouched.
        if telemetry_entry["type"] == "location":
            _loc_payload = {}
            _meta = data.get("metadata")
            if isinstance(_meta, dict):
                _loc_payload.update(_meta)
            _content = data.get("content")
            if isinstance(_content, str) and _content.strip().startswith("{"):
                try:
                    _parsed = json.loads(_content)
                    if isinstance(_parsed, dict):
                        _loc_payload.update(_parsed)
                except (ValueError, TypeError):
                    pass
            _ingest_gps_fix(_loc_payload, user_config)

        print(f"📝 Telemetry received from {user_config['name']}: {telemetry_entry['type']}")
        
        return jsonify({
            "status": "success",
            "user": user_config["name"],
            "logged_at": telemetry_entry["received_at"]
        })
        
    except Exception as e:
        print(f"Telemetry receive error: {e}")
        return jsonify({"error": str(e)}), 500

# ============== GEV GPS INGEST (God's Eye View — GEV.md) ==============
# Live operator-position store feeding the GEV tactical globe. Sources:
#   1. /api/gps        — dedicated GPS POST (Android sender contract)
#   2. /api/telemetry  — legacy "type":"location" entries (backward compatible)
# Consumers: 'gev_location' socket broadcasts (room gev_dashboard) for the
# globe, and GET /api/gps/last for gev_get_operator_position (Polaris tool,
# same-container localhost). Identity resolves through USER_CONFIG exactly
# like telemetry/bursts; the last-fix read is an open LAN-published feed.

import threading  # noqa: E402  (idempotent; used for fix-store locking)
import time  # noqa: E402  (idempotent)

GPS_FIX_HISTORY = 500
GPS_STALE_SECONDS = 120
GEV_GPS_ROOM = "gev_dashboard"

_gps_lock = threading.Lock()
LAST_GPS_FIX = {}   # device -> normalized fix dict
GPS_TRAIL = {}      # device -> list of fix dicts (bounded)


def _ingest_gps_fix(data, user_config):
    """
    Normalize + store one GPS fix, then broadcast 'gev_location' to GEV viewers.
    Accepts lat|latitude and lng|lon|longitude keys. Returns the fix or None.
    """
    try:
        lat = data.get("lat", data.get("latitude"))
        lng = data.get("lng", data.get("lon", data.get("longitude")))
        if lat is None or lng is None:
            return None
        lat = float(lat)
        lng = float(lng)
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            print(f"⚠️ GPS fix rejected (out of bounds): lat={lat}, lng={lng}")
            return None
        device = str(data.get("device") or user_config.get("name", "unknown")).strip().lower()
        fix = {
            "device": device,
            "lat": lat,
            "lng": lng,
            "alt": float(data.get("alt", data.get("altitude", 0.0)) or 0.0),
            "speed": float(data.get("speed", 0.0) or 0.0),
            "heading": float(data.get("heading", 0.0) or 0.0),
            "accuracy": float(data.get("accuracy", 0.0) or 0.0),
            "ts": datetime.now().isoformat(),
            "ts_epoch": time.time(),
        }
        with _gps_lock:
            LAST_GPS_FIX[device] = fix
            trail = GPS_TRAIL.setdefault(device, [])
            trail.append(fix)
            if len(trail) > GPS_FIX_HISTORY:
                del trail[: len(trail) - GPS_FIX_HISTORY]
        socketio.emit('gev_location', fix, room=GEV_GPS_ROOM)
        print(f"📍 GPS fix stored for {device}: {lat:.5f}, {lng:.5f} (±{fix['accuracy']:.0f} m) → {GEV_GPS_ROOM}")
        return fix
    except (TypeError, ValueError) as e:
        print(f"⚠️ GPS fix malformed: {e}")
        return None


@app.route('/api/gps', methods=['POST'])
def receive_gps():
    """
    GEV GPS ingest — dedicated operator-position feed (Android sender contract).

    JSON Payload:
    {
        "device": "red_magic",        # optional (falls back to user identity)
        "lat": -25.344, "lng": 131.035,
        "alt": 0.0, "speed": 0.0, "heading": 0.0, "accuracy": 5.0
    }
    """
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    if not user_config:
        return jsonify({
            "error": "Unknown GPS source",
            "message": f"IP {client_ip} not configured"
        }), 404

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        fix = _ingest_gps_fix(data, user_config)
        if fix is None:
            return jsonify({"error": "GPS fix missing lat/lng or out of bounds"}), 400
        return jsonify({
            "status": "success",
            "user": user_config["name"],
            "device": fix["device"],
            "fix": fix
        })
    except Exception as e:
        print(f"GPS receive error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/gps/last', methods=['GET'])
def last_gps():
    """
    GEV last operator position — read by gev_get_operator_position (Polaris
    tool; same container → localhost) and dashboard debugging. Open read on
    the LAN-published feed; ?device= filters one field unit. Default device
    preference: 'rich' (operator).
    """
    try:
        device = request.args.get("device")
        with _gps_lock:
            if device:
                fix = LAST_GPS_FIX.get(device.strip().lower())
            elif LAST_GPS_FIX:
                fix = LAST_GPS_FIX.get("rich") or next(iter(LAST_GPS_FIX.values()))
            else:
                fix = None
        if not fix:
            return jsonify({"ok": False, "error": "No GPS fix received yet"})
        age = max(0.0, time.time() - fix.get("ts_epoch", 0.0))
        return jsonify({
            "ok": True,
            "fix": fix,
            "age_seconds": round(age, 1),
            "stale": age > GPS_STALE_SECONDS
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/gps/trail', methods=['GET'])
def gps_trail():
    """Bounded recent-fix trail for one device (?device=, default rich)."""
    try:
        device = (request.args.get("device") or "rich").strip().lower()
        with _gps_lock:
            trail = list(GPS_TRAIL.get(device, []))
        return jsonify({"ok": True, "device": device, "count": len(trail), "trail": trail})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/burst/list', methods=['GET'])
def list_bursts():
    """List recent burst payloads for current user"""
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    
    if not user_config:
        return jsonify({"error": "User not found"}), 404
    
    try:
        bursts = []
        burst_dir = Path(user_config["burst_dir"])
        if burst_dir.exists():
            for user_burst_dir in sorted(burst_dir.iterdir(), reverse=True):
                if user_burst_dir.is_dir():
                    metadata_file = user_burst_dir / "metadata.json"
                    if metadata_file.exists():
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        bursts.append({
                            "burst_id": metadata.get("burst_id"),
                            "received_at": metadata.get("received_at"),
                            "file_count": metadata.get("file_count", 0)
                        })
        
        return jsonify({"bursts": bursts[:50]})  # Return last 50 bursts
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/burst/<burst_id>', methods=['GET'])
def get_burst(burst_id):
    """Get details of a specific burst payload for current user"""
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    
    if not user_config:
        return jsonify({"error": "User not found"}), 404
    
    try:
        burst_dir = Path(user_config["burst_dir"]) / burst_id
        if not burst_dir.exists():
            return jsonify({"error": "Burst not found"}), 404
        
        metadata_file = burst_dir / "metadata.json"
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            return jsonify(metadata)
        
        return jsonify({"error": "Metadata not found"}), 404
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/telemetry/recent', methods=['GET'])
def get_recent_telemetry():
    """Get recent telemetry for current user"""
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    
    if not user_config:
        return jsonify({"error": "User not found"}), 404
    
    try:
        if not os.path.exists(user_config["telemetry_log"]):
            return jsonify({"telemetry": []})
        
        with open(user_config["telemetry_log"], 'r') as f:
            lines = f.readlines()
        
        # Return last 100 entries
        telemetry = []
        for line in lines[-100:]:
            try:
                telemetry.append(json.loads(line.strip()))
            except:
                pass
        
        return jsonify({"telemetry": telemetry})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============== VISION ANALYSIS ENDPOINTS ==============

@app.route('/api/vision/analyze', methods=['POST'])
def analyze_image():
    """
    Analyze a single image with Ollama vision model
    
    JSON Payload:
    {
        "image_path": "/absolute/path/to/image.jpg",  // OR
        "image_data": "<base64_encoded_image>",       // OR
        "filename": "temp_image.jpg",
        "prompt_type": "general|tactical|ocr|sharpness_check",
        "custom_prompt": "Optional custom prompt"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
        
        prompt_type = data.get("prompt_type", "general")
        custom_prompt = data.get("custom_prompt")
        
        # Handle image input
        image_path = None
        
        if "image_path" in data:
            image_path = data["image_path"]
            if not os.path.exists(image_path):
                return jsonify({
                    "success": False,
                    "error": f"Image not found: {image_path}"
                }), 404
        
        elif "image_data" in data and "filename" in data:
            # Save temporary file from base64
            import tempfile
            temp_dir = tempfile.gettempdir()
            image_path = os.path.join(temp_dir, data["filename"])
            
            try:
                image_content = base64.b64decode(data["image_data"])
                with open(image_path, 'wb') as f:
                    f.write(image_content)
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": f"Failed to decode image: {e}"
                }), 400
        
        else:
            return jsonify({
                "error": "Must provide either 'image_path' or 'image_data' with 'filename'"
            }), 400
        
        # Run analysis
        result = analyze_single_image(image_path, prompt_type)
        
        # Cleanup temp file if created
        if "image_data" in data:
            try:
                os.remove(image_path)
            except:
                pass
        
        if result.get("success"):
            return jsonify(result)
        else:
            return jsonify(result), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/vision/analyze-burst/<burst_id>', methods=['GET'])
def get_burst_analysis(burst_id):
    """
    Get vision analysis results for a specific burst
    Returns cached analysis if available, or triggers new analysis
    """
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    
    if not user_config:
        return jsonify({"error": "User not found"}), 404
    
    try:
        burst_dir = Path(user_config["burst_dir"]) / burst_id
        if not burst_dir.exists():
            return jsonify({"error": "Burst not found"}), 404
        
        # Check for existing analysis
        analysis_file = burst_dir / "vision_analysis.json"
        if analysis_file.exists():
            with open(analysis_file, 'r') as f:
                analysis = json.load(f)
            
            return jsonify({
                "status": "cached",
                "burst_id": burst_id,
                "analysis": analysis
            })
        
        # No analysis exists, trigger async analysis
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        executor.submit(analyze_burst_async, burst_id, user_config, burst_dir)
        
        return jsonify({
            "status": "processing",
            "burst_id": burst_id,
            "message": "Vision analysis started. Check back in ~30-60 seconds.",
            "check_endpoint": f"/api/vision/analyze-burst/{burst_id}"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/vision/models', methods=['GET'])
def list_vision_models():
    """
    List available vision-capable models
    """
    try:
        # Query Ollama for available models
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        response.raise_for_status()
        
        models_data = response.json()
        vision_models = []
        
        for model in models_data.get("models", []):
            capabilities = model.get("capabilities", [])
            if "vision" in capabilities:
                vision_models.append({
                    "name": model.get("name"),
                    "capabilities": capabilities,
                    "context_length": model.get("details", {}).get("context_length", 0),
                    "parameter_size": model.get("details", {}).get("parameter_size", "unknown")
                })
        
        return jsonify({
            "vision_models": vision_models,
            "default_model": VISION_MODEL,
            "ollama_url": OLLAMA_BASE_URL
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "ollama_reachable": False
        }), 500

@app.route('/api/vision/test', methods=['GET'])
def test_vision_connection():
    """
    Test vision analysis pipeline with a simple check
    """
    try:
        # Check Ollama connectivity
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        response.raise_for_status()
        
        # Check if vision model is available
        models_data = response.json()
        model_available = any(
            m.get("name") == VISION_MODEL 
            for m in models_data.get("models", [])
        )
        
        return jsonify({
            "status": "operational" if model_available else "model_not_found",
            "ollama_connected": True,
            "vision_model": VISION_MODEL,
            "model_available": model_available,
            "prompt_types": list(VISION_PROMPTS.keys())
        })
        
    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "ollama_unreachable",
            "ollama_url": OLLAMA_BASE_URL
        }), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============== CSI RADAR ENDPOINTS ==============
@app.route('/api/radar/ingest', methods=['POST'])
def ingest_csi_data():
    """
    Ingest motion/CSI vector data and broadcast to radar dashboard.
    Live source: X17 LGI webcam perception; Miss Pi kept as historical origin.

    JSON Payload:
    {
        "csi_vector": [amplitude_1, amplitude_2, ...],  # Required
        "timestamp": 1234567890.123,                      # Optional (auto-generated if missing)
        "device_id": "x17-webcam",                        # Optional (canonical X17 camera device id)
        "metadata": {                                     # Optional
            "sample_rate": 100,
            "num_subcarriers": 30
        }
    }
    """
    try:
        data = request.get_json()
        if not data or 'csi_vector' not in data:
            return jsonify({"error": "Missing csi_vector in payload"}), 400
        
        csi_vector = np.array(data['csi_vector'], dtype=np.float32)
        timestamp = data.get('timestamp', datetime.now().timestamp())
        device_id = data.get('device_id', 'unknown')
        metadata = data.get('metadata', {})
        
        # Initialize subcarriers if this is the first sample or if length changed
        num_subcarriers = len(csi_vector)
        if radar_engine.subcarrier_weights is None or len(radar_engine.subcarrier_weights) != num_subcarriers:
            print(f"📡 Initializing subcarrier weights for {num_subcarriers} subcarriers (previously: {len(radar_engine.subcarrier_weights) if radar_engine.subcarrier_weights is not None else 'None'})")
            radar_engine.initialize_subcarriers(num_subcarriers)
        
        # Process CSI vector into spatial coordinates
        spatial_data = radar_engine.process_csi_vector(csi_vector, timestamp)
        
        # Add device metadata
        spatial_data['device_id'] = device_id
        spatial_data['metadata'] = metadata
        
        # Broadcast to connected dashboard clients
        socketio.emit('radar_update', spatial_data, room='radar_dashboard')
        print(f"📡 Broadcast radar_update to radar_dashboard room: x={spatial_data['x']:.2f}, y={spatial_data['y']:.2f}, motion={spatial_data['motion_detected']}")
        
        # Ensure all values are JSON-serializable (convert numpy types)
        response_data = {
            "status": "success",
            "processed": {
                "x": float(spatial_data['x']),
                "y": float(spatial_data['y']),
                "z": float(spatial_data['z']),
                "velocity": float(spatial_data['velocity']),
                "motion_detected": bool(spatial_data['motion_detected']),
                "confidence": float(spatial_data['confidence']),
                "timestamp": float(spatial_data['timestamp']),
                "subcarrier_count": int(spatial_data['subcarrier_count']),
                "device_id": str(device_id),
                "metadata": metadata
            }
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        import traceback
        print(f"❌ CSI ingest error: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

# ============== WEBSOCKET EVENT HANDLERS ==============
@socketio.on('connect')
def handle_connect():
    """Handle client WebSocket connection"""
    sid = request.sid
    print(f"🔌 Client connected: {sid}")
    
    # Send initial radar engine status
    emit('radar_status', radar_engine.get_status(), to=sid)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client WebSocket disconnection"""
    global vision_camera_sid
    sid = request.sid
    print(f"🔌 Client disconnected: {sid}")
    # Ghost-flag fix: if the CAMERA socket died — clean app close or raw
    # transport drop (socket.io always fires server-side disconnect on TCP
    # close) — drop the active flag via the shared offline path so the
    # status endpoint and dashboards go honest immediately. Viewer sockets
    # never hold this sid, so dashboard tab-closes can no longer ghost the
    # flag either way.
    if sid and sid == vision_camera_sid:
        vision_camera_sid = None
        announce_vision_offline("camera socket disconnected")

@socketio.on('join_radar')
def handle_join_radar(data):
    """
    Join the radar dashboard room for real-time updates
    
    Expected data:
    {
        "room": "radar_dashboard"
    }
    """
    room = data.get('room', 'radar_dashboard') if data else 'radar_dashboard'
    join_room(room)
    print(f"📡 Client {request.sid} joined room: {room}")
    
    # Send current radar state
    emit('radar_status', radar_engine.get_status(), room=request.sid)

@socketio.on('leave_radar')
def handle_leave_radar(data):
    """Leave the radar dashboard room"""
    room = data.get('room', 'radar_dashboard') if data else 'radar_dashboard'
    leave_room(room)
    print(f"📡 Client {request.sid} left room: {room}")


@socketio.on('join_gev')
def handle_join_gev(data):
    """
    Join the GEV (God's Eye View) globe room for live operator GPS fixes.
    Expected data: { "room": "gev_dashboard" } — mirrors the join_radar pattern.
    """
    room = data.get('room', 'gev_dashboard') if data else 'gev_dashboard'
    join_room(room)
    print(f"🌍 Client {request.sid} joined room: {room}")
    # Replay the last stored fix so the globe paints instantly on join
    with _gps_lock:
        initial_fix = LAST_GPS_FIX.get('rich') or (next(iter(LAST_GPS_FIX.values())) if LAST_GPS_FIX else None)
    if initial_fix:
        emit('gev_location', initial_fix, room=request.sid)


@socketio.on('leave_gev')
def handle_leave_gev(data):
    """Leave the GEV globe room"""
    room = data.get('room', 'gev_dashboard') if data else 'gev_dashboard'
    leave_room(room)
    print(f"🌍 Client {request.sid} left room: {room}")

@socketio.on('request_radar_snapshot')
def handle_radar_snapshot(data):
    """
    Send a one-time snapshot of current radar state
    
    Response includes:
    - Current tracked position
    - Motion status
    - Recent trajectory (from CSI window)
    """
    try:
        # Build trajectory from CSI window
        trajectory = []
        for item in list(radar_engine.csi_window)[-10:]:  # Last 10 samples
            pos = radar_engine._calculate_position(item['csi'])
            trajectory.append({
                'x': float(pos[0]),
                'y': float(pos[1]),
                'timestamp': item['timestamp']
            })
        
        snapshot = {
            'current_position': radar_engine.current_position.tolist() if radar_engine.current_position is not None else None,
            'velocity': radar_engine.velocity.tolist(),
            'motion_detected': radar_engine.motion_detected,
            'motion_confidence': radar_engine.motion_confidence,
            'trajectory': trajectory,
            'engine_status': radar_engine.get_status()
        }
        
        emit('radar_snapshot', snapshot, room=request.sid)
        
    except Exception as e:
        emit('error', {'message': str(e)}, room=request.sid)

# ============== VISION STREAM HANDLERS ==============
# Store recent vision detections for dashboard display
vision_detections = deque(maxlen=100)  # Last 100 detections
vision_stream_active = False
last_vision_update = None
vision_device_name = "Unknown"
# Ghost-flag fix: sid of the LAST GENUINE camera socket (from the X17 LGI
# app's vision_connect/vision_frame). Dashboard viewers only join_vision and
# must never touch this state — a viewer socket death is not a stream death.
vision_camera_sid = None

# JARVIS-style announcement cache (last 5 announcements)
jarvis_announcements = deque(maxlen=5)

@socketio.on('vision_connect')
def handle_vision_connect(data):
    """
    Handle vision stream connection from the X17 (LGI webcam perception)
    Expected data:
    {
        "device": "x17-lgi",
        "camera_id": "front" | "rear" | "wide",
        "resolution": "640x480",
        "fps": 30
    }
    """
    global vision_stream_active, vision_device_name
    sid = request.sid
    
    device = "Unknown"
    camera_id = "front"
    resolution = "640x480"
    fps = 30
    
    if data:
        device = data.get('device', 'unknown')
        camera_id = data.get('camera_id', 'front')
        resolution = data.get('resolution', '640x480')
        fps = data.get('fps', 30)
    
    vision_device_name = device
    vision_stream_active = True
    vision_camera_sid = sid  # remember WHO the camera is; its socket death clears the flag
    _ensure_vision_watchdog()
    
    # JARVIS-style announcement
    announcement = f"Vision stream online. {device} {camera_id} camera active at {resolution} {fps} FPS."
    jarvis_announcements.append({
        'type': 'connection',
        'message': announcement,
        'timestamp': datetime.now().isoformat(),
        'device': device,
        'camera': camera_id
    })
    
    print(f"👁️ {announcement}")
    print(f"👁️ Device: {device}, Camera: {camera_id}, Resolution: {resolution}@{fps}fps")
    
    # Emit to all dashboard clients — type 'connection' lets the vision
    # dashboard's status pill positively key on the REAL camera coming up.
    socketio.emit('jarvis_announcement', {
        'message': announcement,
        'type': 'connection',
        'priority': 'high'
    }, room='vision_dashboard')
    
    socketio.emit('jarvis_announcement', {
        'message': f"{device} vision feed integrated into Polaris awareness matrix.",
        'type': 'status',
        'priority': 'normal'
    }, room='radar_dashboard')
    
    emit('vision_ack', {'status': 'connected', 'stream_id': sid, 'device': device}, to=sid)

@socketio.on('vision_frame')
def handle_vision_frame(data):
    """
    Process real-time vision detection frames from the X17 (LGI perception)
    Expected data format from YOLO daemon:
    {
        "timestamp": 1739123456.789,
        "frame_id": 12345,
        "detections": [
            {
                "class": "person",
                "class_id": 0,
                "confidence": 0.92,
                "bbox": [x1, y1, x2, y2],
                "center": [x, y],
                "position_desc": "center-left"
            }
        ],
        "narration": "I see a person on the left",
        "inference_time_ms": 45.2,
        "camera_settings": {
            "resolution": "640x480",
            "fps": 30
        }
    }
    """
    global last_vision_update, vision_stream_active, vision_camera_sid
    
    if not data:
        return
    
    try:
        # Add timestamp for tracking
        data['received_at'] = datetime.now().isoformat()
        data['stream_id'] = request.sid
        
        # Ghost-flag fix: frames prove the camera is alive. Adopt this socket
        # as the current camera (covers an LG app reconnect that skips its
        # announce) and re-arm the active flag if a stale-sweep or a missed
        # disconnect had cleared it. Device name stays as last announced.
        vision_camera_sid = request.sid
        if not vision_stream_active:
            vision_stream_active = True

        # Store in recent detections buffer
        vision_detections.append(data)
        last_vision_update = datetime.now()
        
        # Count detections
        num_detections = len(data.get('detections', []))
        narration = data.get('narration', '')
        
        # Extract detection classes (always initialize, even if empty)
        classes_detected = [d.get('class_name', d.get('class', 'unknown')) for d in data.get('detections', [])]
        confidence_scores = [d.get('confidence', 0) for d in data.get('detections', [])]
        
        # JARVIS-style detection announcements
        if num_detections > 0:
            
            # Only announce high-confidence detections (>0.7) or new objects
            high_confidence_detections = [
                c for c, conf in zip(classes_detected, confidence_scores) if conf > 0.7
            ]
            
            if high_confidence_detections:
                # Create contextual announcement
                unique_classes = list(set(high_confidence_detections))
                if len(unique_classes) == 1:
                    announcement = f"Detecting {unique_classes[0]}. {narration}"
                elif len(unique_classes) <= 3:
                    objects_list = ', '.join(unique_classes)
                    announcement = f"Multiple objects detected: {objects_list}. {narration}"
                else:
                    announcement = f"Awareness update: {len(unique_classes)} distinct object classes in view."
                
                # Cache announcement (limit to 1 per 5 seconds to avoid spam)
                now = datetime.now()
                if not jarvis_announcements or \
                   (now - datetime.fromisoformat(jarvis_announcements[-1]['timestamp'])).total_seconds() > 5:
                    jarvis_announcements.append({
                        'type': 'detection',
                        'message': announcement,
                        'timestamp': now.isoformat(),
                        'objects': unique_classes,
                        'count': num_detections
                    })
                    
                    # Emit to dashboards
                    socketio.emit('jarvis_announcement', {
                        'message': announcement,
                        'type': 'detection',
                        'priority': 'normal',
                        'objects': unique_classes
                    }, room='vision_dashboard')
            
            print(f"👁️ Vision frame #{data.get('frame_id', '?')}: {num_detections} detection(s) - {classes_detected}")
            if narration:
                print(f"   🗣️  {narration}")
        
        # Broadcast to vision dashboard room
        socketio.emit('vision_update', data, room='vision_dashboard')
        
        # (Finding B) The 'polaris_vision' fan-out was deleted: it emitted into
        # room 'radar_dashboard', where nothing listens for it — its only
        # listener in the whole tree (Polaris_page.html) sits on the vision
        # socket, which joined 'vision_dashboard'. Dead orphan, and narration
        # already reaches dashboards via the 'vision_update' emit above.

    except Exception as e:
        print(f"❌ Error processing vision frame: {e}")
        emit('vision_error', {'message': str(e)}, to=request.sid)

@socketio.on('vision_disconnect')
def handle_vision_disconnect(data):
    """Handle vision stream disconnection"""
    global vision_stream_active, vision_device_name
    sid = request.sid
    
    device = vision_device_name
    vision_stream_active = False
    
    # JARVIS-style disconnection announcement
    announcement = f"Vision stream offline. {device} feed disconnected."
    jarvis_announcements.append({
        'type': 'disconnection',
        'message': announcement,
        'timestamp': datetime.now().isoformat(),
        'device': device
    })
    
    print(f"👁️ {announcement}")
    
    # Emit to dashboards — type 'disconnection' keys the vision dashboard's
    # status pill OFF when the camera feed ends.
    socketio.emit('jarvis_announcement', {
        'message': announcement,
        'type': 'disconnection',
        'priority': 'high'
    }, room='vision_dashboard')
    
    socketio.emit('jarvis_announcement', {
        'message': f"{device} vision feed lost. Awaiting reconnection.",
        'type': 'warning',
        'priority': 'high'
    }, room='radar_dashboard')

def announce_vision_offline(reason):
    """
    Shared offline path for every way the camera feed can end: an explicit
    vision_disconnect from the LG app, its socket dying without one (app
    killed / network drop), or the stale-feed watchdog. Keeps
    vision_stream_active honest so /api/vision/stream/status and the
    dashboards reflect reality without needing a gateway restart.
    Device name is intentionally preserved: it stays the last genuine
    camera announce until the camera announces again.
    """
    global vision_stream_active
    device = vision_device_name
    vision_stream_active = False

    announcement = f"Vision stream offline. {device} feed disconnected ({reason})."
    jarvis_announcements.append({
        'type': 'disconnection',
        'message': announcement,
        'timestamp': datetime.now().isoformat(),
        'device': device
    })
    print(f"👁️ {announcement}")

    # Emit to dashboards
    socketio.emit('jarvis_announcement', {
        'message': announcement,
        'type': 'disconnection',
        'priority': 'high'
    }, room='vision_dashboard')

    socketio.emit('jarvis_announcement', {
        'message': f"{device} vision feed lost. Awaiting reconnection.",
        'type': 'warning',
        'priority': 'high'
    }, room='radar_dashboard')


_vision_watchdog_started = False

def _ensure_vision_watchdog():
    """Start the stale-feed watchdog once, lazily, on the first camera announce."""
    global _vision_watchdog_started
    if _vision_watchdog_started:
        return
    _vision_watchdog_started = True
    socketio.start_background_task(_vision_staleness_watchdog)


def _vision_staleness_watchdog():
    """
    Stale-feed watchdog: if the stream is flagged active but no vision_frame
    has arrived for 20 s, the feed is ghosting (LG app kept its socket open
    but stopped delivering frames, or the disconnect event itself was lost).
    Marks the feed offline once; if frames resume, the frame handler flips
    the flag back on and dashboards recover with the next update.
    """
    stale_after = 20.0  # seconds without a vision_frame while flagged active
    while True:
        socketio.sleep(5)
        try:
            if vision_stream_active and last_vision_update is not None:
                quiet = (datetime.now() - last_vision_update).total_seconds()
                if quiet > stale_after:
                    print(f"👁️ Vision feed stale: no frames for {int(quiet)}s — marking offline")
                    announce_vision_offline(f"no frames for {int(quiet)}s")
        except Exception as e:
            print(f"❌ Vision staleness watchdog error: {e}")

@socketio.on('radar_motion')
def handle_radar_motion(data):
    """
    Receive pre-processed motion event from the X17 LGI webcam perception
    daemon (device id defaults to 'x17-webcam'); legacy Miss Pi CSI payloads
    (no explicit coordinates) remain supported.

    Expected payload from the X17 perception daemon:
    {
        "timestamp": 1234567890.123,
        "confidence": 0.85,
        "intensity": 0.23,
        "duration": 5.2,
        "csi_summary": {
            "mean_amplitude": 0.52,
            "std_amplitude": 0.08,
            "variance_score": 0.23,
            "samples_analyzed": 20
        },
        "device_id": "x17-webcam"
    }
    """
    try:
        if not data:
            print("⚠️  Received empty radar_motion event")
            return
        
        timestamp = data.get('timestamp', datetime.now().timestamp())
        confidence = float(data.get('confidence', 0.0))
        intensity = float(data.get('intensity', 0.0))
        duration = float(data.get('duration', 0.0))
        device_id = data.get('device_id', 'x17-webcam')
        csi_summary = data.get('csi_summary', {})
        
        # Spatial coordinates for the dashboard: real trackers (LGI webcam
        # perception) send actual positions — pass them through untouched.
        # Legacy Miss Pi payloads without coordinates keep the historical
        # intensity-driven simulation so nothing downstream breaks.
        real_x, real_y = data.get('x'), data.get('y')
        if real_x is not None or real_y is not None:
            base_x = float(real_x if real_x is not None else 0.0)
            base_y = float(real_y if real_y is not None else 0.0)
            motion_variation = 0.0  # real coordinates: no simulated jitter
        else:
            base_x = math.sin(timestamp * 0.5) * 0.3  # Slow oscillation
            base_y = math.cos(timestamp * 0.3) * 0.3  # Slow oscillation
            motion_variation = intensity * 0.5  # Scale intensity to position range
        
        # Add motion-induced variation based on intensity (simulation path only)
        x = base_x + (np.random.random() - 0.5) * motion_variation
        y = base_y + (np.random.random() - 0.5) * motion_variation
        
        # Clamp to valid range [-1, 1]
        x = max(-1.0, min(1.0, x))
        y = max(-1.0, min(1.0, y))
        
        # Create spatial data payload matching CSIRadarEngine output format
        spatial_data = {
            'x': float(x),
            'y': float(y),
            'z': float(data.get('z', intensity * 0.5)),       # elevation (real or intensity proxy)
            'velocity': float(data.get('velocity', intensity * 2.0)),  # velocity (real or intensity proxy)
            'motion_detected': True,
            'confidence': confidence,
            'timestamp': timestamp,
            'subcarrier_count': int(csi_summary.get('samples_analyzed', 20)),
            'device_id': device_id,
            'metadata': {
                'edge_processed': True,
                'duration': duration,
                'csi_summary': csi_summary
            }
        }
        
        # Broadcast to connected dashboard clients
        socketio.emit('radar_update', spatial_data, room='radar_dashboard')
        print(f"🎯 Radar motion event: x={x:.2f}, y={y:.2f}, conf={confidence:.2f}, intensity={intensity:.2f}")
        
    except Exception as e:
        import traceback
        print(f"❌ Radar motion handler error: {e}")
        print(f"Traceback: {traceback.format_exc()}")

@socketio.on('join_vision')
def handle_join_vision(data):
    """
    Join the vision dashboard room for real-time vision updates
    Expected data:
    {
        "room": "vision_dashboard"
    }
    """
    room = data.get('room', 'vision_dashboard') if data else 'vision_dashboard'
    join_room(room)
    print(f"👁️ Client {request.sid} joined vision room: {room}")
    
    # Send recent vision history (last 10 detections)
    recent_detections = list(vision_detections)[-10:]
    if recent_detections:
        emit('vision_history', {
            'detections': recent_detections,
            'stream_active': vision_stream_active,
            'last_update': last_vision_update.isoformat() if last_vision_update else None
        }, room=request.sid)

@app.route('/api/vision/stream/status', methods=['GET'])
def get_vision_stream_status():
    """Get current vision stream status with JARVIS announcements"""
    return jsonify({
        'stream_active': vision_stream_active,
        'device': vision_device_name,
        'recent_detections_count': len(vision_detections),
        'last_update': last_vision_update.isoformat() if last_vision_update else None,
        'status': 'active' if vision_stream_active and last_vision_update else 'waiting',
        'jarvis_announcements': list(jarvis_announcements),
        'awareness_summary': {
            'total_detections_processed': len(vision_detections),
            'stream_uptime': 'active' if vision_stream_active else 'offline'
        }
    })


@app.route('/api/jarvis/announcements', methods=['GET'])
def get_jarvis_announcements():
    """Get recent JARVIS-style announcements for dashboard display"""
    return jsonify({
        'announcements': list(jarvis_announcements),
        'count': len(jarvis_announcements),
        'stream_active': vision_stream_active,
        'device': vision_device_name
    })

def health_check():
    """Health check endpoint"""
    client_ip = get_user_ip()
    user_config = get_user_config(client_ip)
    
    return jsonify({
        "status": "healthy",
        "service": "burst_receiver",
        "timestamp": datetime.now().isoformat(),
        "user": user_config["name"] if user_config else "unknown",
        "client_ip": client_ip
    })

if __name__ == '__main__':
    print("🚀 Burst Receiver starting on port 5001...")
    print("📍 Burst Endpoint: http://0.0.0.0:5001/api/burst")
    print("📍 Telemetry Endpoint: http://0.0.0.0:5001/api/telemetry")
    print("📍 CSI Radar Ingest: http://0.0.0.0:5001/api/radar/ingest")
    print("👁️ Vision Stream: ws://0.0.0.0:5001/socket.io/ (vision_frame)")
    print("👁️ Vision Status: http://0.0.0.0:5001/api/vision/stream/status")
    print("🔌 WebSocket: ws://0.0.0.0:5001/socket.io/")
    print("🔒 LAN-only access enabled")
    print(f"👤 Allowed users: Rich ({RICH_IP}), Matt ({MATT_IP}), LGI X17 ({LGI_X17_IP})")
    print("")
    socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)