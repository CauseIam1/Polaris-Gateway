#!/usr/bin/env python3
"""
Polaris Gateway - Continuous Autonomous Learning System
Transforms Polaris from batch daily memory to real-time self-learning agent
Runs on port 5000 with WebSocket support and heuristics-based adaptation
"""

from flask import Flask, request, jsonify, Response
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests
import json
import os
import threading
import time
import re
import tempfile
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum

# Import memory modules for dual-memory architecture
from memory_db import get_memory_db
from vector_memory import get_vector_memory

# Import file tools for Phase 1: Local File Manipulation Suite
from file_tools import (read_doc, write_doc, append_doc, list_docs, delete_doc,
                        list_workspace_files, read_workspace_file,
                        TOOL_SCHEMAS, get_tool_schemas_json, MAX_READ_PAGE_CHARS)

# Import playground tools for Phase 2: Stark Holodeck (web publishing, code sandbox, service spawning)
from playground_tools import (
    publish_web_asset, list_playground_files, run_sandbox_code,
    spawn_service, stop_service, list_services,
    PLAYGROUND_TOOL_SCHEMAS, get_playground_tool_schemas_json
)

# Import SSH tools for Phase 3: Remote Command Execution (X17 LGI + Mainframe control)
from ssh_tools import (
    ssh_execute, ssh_check_connectivity, ssh_deploy_file, ssh_fetch_file,
    SSH_TOOL_SCHEMAS, get_ssh_tool_schemas_json
)

# Import Gotify notification service for Phase 4: Push Notifications
from gotify_service import (
    send_gotify_notification,
    GOTIFY_TOOL_SCHEMAS, get_gotify_tool_schemas_json
)

# Import Memory Vault query tool for Phase 5: Long-term memory consultation
from memory_tools import (
    memory_vault_query, memory_journal_write,
    MEMORY_TOOL_SCHEMAS, get_memory_tool_schemas_json
)

# Import X17 live-eyes bridge (scene digest + on-demand pixel looks via the
# Burst Receiver's scene relay — v1.7, Oct 13, 2026)
from scene_service import (
    get_scene_state, request_x17_snapshot, get_scene_prompt_block,
    SCENE_TOOL_SCHEMAS, get_scene_tool_schemas_json
)

# Import GEV (God's Eye View) tactical globe command bridge (GEV.md contract)
from gev_tools import (
    gev_set_center, gev_toggle_layer, gev_query_object, gev_get_operator_position,
    GEV_TOOL_SCHEMAS, get_gev_tool_schemas_json
)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'polaris-continuous-learning-2026'

# CORS Configuration - Allow dashboard origins
CORS_ORIGINS = ["http://localhost:7000", "http://192.168.50.51:7000", "*"]

@app.after_request
def add_cors_headers(response):
    """Add CORS headers to all responses for dashboard access."""
    origin = request.headers.get('Origin')
    if origin:
        response.headers.add('Access-Control-Allow-Origin', origin)
    else:
        response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Forwarded-For')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response

# Initialize SocketIO with CORS for LAN access.
# Production driver: gevent when running under gunicorn (no werkzeug 500-spam
# on WS session close); threading/werkzeug when the gateway runs directly.
try:
    if 'gunicorn' in (sys.argv[0] if sys.argv else ''):
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')
        print("[Gateway] SocketIO async_mode: gevent (production driver)")
    else:
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
        print("[Gateway] SocketIO async_mode: threading (werkzeug direct-run mode)")
except Exception as _sio_err:
    print(f"[Gateway] SocketIO gevent init failed ({_sio_err}) - falling back to threading")
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ============== TASK STATE MANAGER ==============
# Real-time tracking of background operations for dashboard visibility

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TaskType(Enum):
    SSH_CONNECTION = "ssh_connection"
    SSH_COMMAND = "ssh_command"
    CONTAINER_SCAN = "container_scan"
    FILE_OPERATION = "file_operation"
    GOTIFY_NOTIFICATION = "gotify_notification"
    MEMORY_OPERATION = "memory_operation"
    PLAYGROUND_OPERATION = "playground_operation"
    CUSTOM = "custom"

@dataclass
class TaskState:
    """Represents the state of a background task."""
    task_id: str
    task_type: str
    description: str
    status: str
    created_at: str
    updated_at: str
    progress: int = 0  # 0-100 percentage
    metadata: Optional[Dict] = None
    error: Optional[str] = None
    result: Optional[Dict] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress": self.progress,
            "metadata": self.metadata,
            "error": self.error,
            "result": self.result
        }

class TaskStateManager:
    """
    Thread-safe global state manager for tracking background tasks.
    Provides real-time visibility into Polaris operations.
    """
    
    def __init__(self, max_history: int = 50):
        self._lock = threading.Lock()
        self._active_tasks: Dict[str, TaskState] = {}
        self._completed_tasks: List[TaskState] = []
        self._max_history = max_history
        self._system_busy = False
        
    def register_task(
        self,
        task_type: TaskType,
        description: str,
        metadata: Optional[Dict] = None
    ) -> str:
        """Register a new task and return its ID."""
        task_id = f"{task_type.value}_{int(time.time() * 1000)}"
        now = datetime.utcnow().isoformat() + "Z"
        
        task = TaskState(
            task_id=task_id,
            task_type=task_type.value,
            description=description,
            status=TaskStatus.PENDING.value,
            created_at=now,
            updated_at=now,
            progress=0,
            metadata=metadata
        )
        
        with self._lock:
            self._active_tasks[task_id] = task
            self._system_busy = True
            socketio.emit('task_registered', task.to_dict(), room='status')
        
        print(f"[TaskManager] Registered task {task_id}: {description}")
        return task_id
    
    def start_task(self, task_id: str, progress: int = 0) -> bool:
        """Mark a task as running."""
        with self._lock:
            if task_id not in self._active_tasks:
                return False
            
            task = self._active_tasks[task_id]
            task.status = TaskStatus.RUNNING.value
            task.progress = progress
            task.updated_at = datetime.utcnow().isoformat() + "Z"
            socketio.emit('task_started', task.to_dict(), room='status')
        
        print(f"[TaskManager] Started task {task_id}")
        return True
    
    def update_progress(self, task_id: str, progress: int, metadata: Optional[Dict] = None) -> bool:
        """Update task progress percentage."""
        with self._lock:
            if task_id not in self._active_tasks:
                return False
            
            task = self._active_tasks[task_id]
            task.progress = min(100, max(0, progress))
            task.updated_at = datetime.utcnow().isoformat() + "Z"
            if metadata:
                task.metadata = metadata
            socketio.emit('task_progress', task.to_dict(), room='status')
        
        return True
    
    def complete_task(self, task_id: str, result: Optional[Dict] = None) -> bool:
        """Mark a task as completed."""
        with self._lock:
            if task_id not in self._active_tasks:
                return False
            
            task = self._active_tasks[task_id]
            task.status = TaskStatus.COMPLETED.value
            task.progress = 100
            task.updated_at = datetime.utcnow().isoformat() + "Z"
            task.result = result
            
            self._completed_tasks.append(task)
            del self._active_tasks[task_id]
            
            if len(self._completed_tasks) > self._max_history:
                self._completed_tasks = self._completed_tasks[-self._max_history:]
            
            self._system_busy = len(self._active_tasks) > 0
            socketio.emit('task_completed', task.to_dict(), room='status')
        
        print(f"[TaskManager] Completed task {task_id}")
        return True
    
    def fail_task(self, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        with self._lock:
            if task_id not in self._active_tasks:
                return False
            
            task = self._active_tasks[task_id]
            task.status = TaskStatus.FAILED.value
            task.error = error
            task.updated_at = datetime.utcnow().isoformat() + "Z"
            
            self._completed_tasks.append(task)
            del self._active_tasks[task_id]
            
            self._system_busy = len(self._active_tasks) > 0
            socketio.emit('task_failed', task.to_dict(), room='status')
        
        print(f"[TaskManager] Failed task {task_id}: {error}")
        return True
    
    def get_status(self) -> Dict:
        """Get current system status summary."""
        with self._lock:
            active_count = len(self._active_tasks)
            return {
                "system_busy": self._system_busy or active_count > 0,
                "active_tasks_count": active_count,
                "completed_tasks_count": len(self._completed_tasks),
                "active_tasks": [task.to_dict() for task in self._active_tasks.values()],
                "recent_completed": [task.to_dict() for task in self._completed_tasks[-10:]],
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
    
    def get_active_tasks(self) -> List[Dict]:
        """Get list of currently active tasks."""
        with self._lock:
            return [task.to_dict() for task in self._active_tasks.values()]
    
    def get_completed_tasks(self, limit: int = 20) -> List[Dict]:
        """Get list of recently completed tasks."""
        with self._lock:
            return [task.to_dict() for task in self._completed_tasks[-limit:]]

# Initialize global task state manager
task_manager = TaskStateManager(max_history=50)

# ============== NETWORK CONFIGURATION (LAN ONLY) ==============
# ============== NETWORK CONFIGURATION (LAN ONLY) ==============
RICH_IP = "192.168.50.42"
MATT_IP = "192.168.50.98"
OPERATOR_IP = "192.168.50.51"  # Dashboard/Operator IP
DASHBOARD_TEST_IP = "192.168.50.227"  # Dashboard testing machine / the X17 (LGI) host (device 'x17-webcam')
RICH_WG_TUNNEL_IP = "10.20.30.3"  # Rich's phone inside the FreeRoam WireGuard tunnel (freeroam-wireguard)
MATT_WG_TUNNEL_IP = "10.20.30.2"  # Matt's phone inside the FreeRoam WireGuard tunnel (freeroam-wireguard)
DOCKER_HAIRPIN_IP = "172.18.0.1"  # docker-proxy source for tunnel/host traffic hairpinning into published ports (resolves to Rich per 2026-10-03 WG-primary restore)
ROUTER_SNAT_IP = "192.168.50.1"  # router-LAN source for WG-off home traffic taking a public WAN name (router hairpin SNAT; resolves to Rich per 2026-10-03 WG-off-at-home default)
DOCKER_BRIDGE_GATEWAY = "172.17.0.1"
ALLOWED_IPS = [RICH_IP, MATT_IP, OPERATOR_IP, DASHBOARD_TEST_IP, RICH_WG_TUNNEL_IP, MATT_WG_TUNNEL_IP, DOCKER_HAIRPIN_IP, DOCKER_BRIDGE_GATEWAY, ROUTER_SNAT_IP]

# Docker bridge network ranges (allow all containers on bridge networks)
ALLOWED_IP_PREFIXES = ["172.17.", "172.18.", "172.19.", "172.20."]

connected_clients = {}
client_rooms = {}

# ============== NODE SWARM REGISTRY ==============
# In-memory registry for edge device tracking
NODE_REGISTRY_LOCK = threading.Lock()
NODE_REGISTRY = {}  # node_id -> {ip, last_heartbeat, status, metadata}
NODE_STALE_THRESHOLD = 60  # seconds without heartbeat before marking offline

USER_CONFIG = {
    RICH_IP: {
        "name": "Rich",
        "profile_file": "/data/freeroam/rich/rich_profile.json",
        "transcript_file": "/data/freeroam/rich/rich_transcripts.log",
        "behavior_lib": "/data/freeroam/rich/behavior_lib_rich.json",
        "lists_file": "/data/freeroam/rich/rich_lists.json"
    },
    MATT_IP: {
        "name": "Matt",
        "profile_file": "/data/freeroam/matt/matt_profile.json",
        "transcript_file": "/data/freeroam/matt/matt_transcripts.log",
        "behavior_lib": "/data/freeroam/matt/behavior_lib_matt.json",
        "lists_file": "/data/freeroam/matt/matt_lists.json"
    },
    OPERATOR_IP: {
        "name": "Operator",
        "profile_file": "/data/freeroam/operator/operator_profile.json",
        "transcript_file": "/data/freeroam/operator/operator_transcripts.log",
        "behavior_lib": "/data/freeroam/operator/behavior_lib_operator.json",
        "lists_file": "/data/freeroam/operator/operator_lists.json"
    },
    DASHBOARD_TEST_IP: {
        "name": "Dashboard",
        "profile_file": "/data/freeroam/operator/operator_profile.json",
        "transcript_file": "/data/freeroam/operator/operator_transcripts.log",
        "behavior_lib": "/data/freeroam/operator/behavior_lib_operator.json",
        "lists_file": "/data/freeroam/operator/operator_lists.json"
    },
    # Rich's WireGuard tunnel identity (IP alias → same account storage, same
    # MASQUERADE + published-port hairpin pattern as below; WG tunnel primary
    # restored 2026-10-03)
    RICH_WG_TUNNEL_IP: {
        "name": "Rich",
        "profile_file": "/data/freeroam/rich/rich_profile.json",
        "transcript_file": "/data/freeroam/rich/rich_transcripts.log",
        "behavior_lib": "/data/freeroam/rich/behavior_lib_rich.json",
        "lists_file": "/data/freeroam/rich/rich_lists.json"
    },
    # Matt's WireGuard tunnel identities (Rich-pattern IP aliases → same
    # account: the WG container MASQUERADEs his 10.20.30.2 and the published-port
    # hairpin rewrites it to 172.18.0.1 before it reaches the gateway)
    MATT_WG_TUNNEL_IP: {
        "name": "Matt",
        "profile_file": "/data/freeroam/matt/matt_profile.json",
        "transcript_file": "/data/freeroam/matt/matt_transcripts.log",
        "behavior_lib": "/data/freeroam/matt/behavior_lib_matt.json",
        "lists_file": "/data/freeroam/matt/matt_lists.json"
    },
    # Hairpin catch-all → Rich (WG primary restored 2026-10-03): every tunnel
    # phone MASQUERADEs to 172.18.0.1; WS sessions announce their identity
    # explicitly and win regardless, so this only steers IP-derived calls
    DOCKER_HAIRPIN_IP: {  # shared with host-shell proxy curls (app sends explicit user_id anyway)
        "name": "Rich",
        "profile_file": "/data/freeroam/rich/rich_profile.json",
        "transcript_file": "/data/freeroam/rich/rich_transcripts.log",
        "behavior_lib": "/data/freeroam/rich/behavior_lib_rich.json",
        "lists_file": "/data/freeroam/rich/rich_lists.json"
    },
    # Router-SNAT → Rich (WG-off-at-home default, 2026-10-03): WG-down phone
    # traffic taking the public WAN name from home hairpins through the router,
    # which can SNAT the source to its own LAN IP (192.168.50.1); IP-derived
    # calls then stay in Rich's bucket (announced identities win regardless).
    ROUTER_SNAT_IP: {  # shared router-LAN SNAT source
        "name": "Rich",
        "profile_file": "/data/freeroam/rich/rich_profile.json",
        "transcript_file": "/data/freeroam/rich/rich_transcripts.log",
        "behavior_lib": "/data/freeroam/rich/behavior_lib_rich.json",
        "lists_file": "/data/freeroam/rich/rich_lists.json"
    }
}

# Default config for Docker bridge network users (fallback when NAT masks real IP)
DEFAULT_USER_CONFIG = {
    "name": "Dashboard",
    "profile_file": "/data/freeroam/operator/operator_profile.json",
    "transcript_file": "/data/freeroam/operator/operator_transcripts.log",
    "behavior_lib": "/data/freeroam/operator/behavior_lib_operator.json",
    "lists_file": "/data/freeroam/operator/operator_lists.json"
}

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434/api/generate")
# Default matches the deployed compose env (docker-compose.yml sets
# OLLAMA_MODEL=polaris-ai:latest: num_ctx 8192, base glm-5.3-flash).
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "polaris-ai:latest")
# ── Polaris Desktop Vision (LGI) ──────────────────────────────────────────
# Model ladder for chat turns that carry a screenshot (image_base64). The
# primary is the operator's Polaris brain; if it rejects images or errors,
# the proven vision-capable model from burst_receiver.py (/api/vision/*)
# answers instead. Override either via env (docker-compose.yml).
VISION_LLM_MODEL = os.environ.get("VISION_LLM_MODEL", "polaris-ai:latest")
VISION_LLM_FALLBACK_MODEL = os.environ.get(
    "VISION_LLM_FALLBACK_MODEL", "mistral-large-3:675b-cloud")
VISION_LLM_TIMEOUT = 120  # seconds — multimodal turns are slower than text
HEURISTICS_FILE = "/app/polaris_heuristics.json"

HEURISTICS_LOCK = threading.Lock()
STAGED_HEURISTICS_LOCK = threading.Lock()
STAGED_HEURISTICS = {}  # In-memory staging buffer: rule_id -> heuristic_data

# ============== DUAL-MEMORY ARCHITECTURE ==============
# Initialize SQLite and ChromaDB memory systems
MEMORY_DB_LOCK = threading.Lock()
MEMORY_DB_INSTANCE = None  # Lazy-loaded singleton
VECTOR_MEMORY_INSTANCE = None  # Lazy-loaded singleton

def get_memory_db_instance():
    """Get or create the MemoryDB singleton instance."""
    global MEMORY_DB_INSTANCE
    with MEMORY_DB_LOCK:
        if MEMORY_DB_INSTANCE is None:
            MEMORY_DB_INSTANCE = get_memory_db()
        return MEMORY_DB_INSTANCE

def get_vector_memory_instance():
    """Get or create the VectorMemory singleton instance."""
    global VECTOR_MEMORY_INSTANCE
    if VECTOR_MEMORY_INSTANCE is None:
        VECTOR_MEMORY_INSTANCE = get_vector_memory()
    return VECTOR_MEMORY_INSTANCE

CORRECTION_PATTERNS = [
    r"\bno\b", r"\bdon't\b", r"\bdo not\b", r"\bstop\b", r"\binstead\b",
    r"\bwrong\b", r"\bincorrect\b", r"\bthat's not\b", r"\bi don't want",
    r"\breset\b", r"\bstart over\b", r"\btry again\b"
]

# ============== NODE SWARM MANAGEMENT FUNCTIONS ==============

def register_node(node_id, ip, metadata=None):
    """Register or update a node in the swarm registry."""
    with NODE_REGISTRY_LOCK:
        NODE_REGISTRY[node_id] = {
            "node_id": node_id,
            "ip": ip,
            "last_heartbeat": time.time(),
            "status": "online",
            "metadata": metadata or {}
        }
    print(f"[Node Swarm] Registered node: {node_id} from {ip}")

def update_node_heartbeat(node_id, ip, metadata=None):
    """Update heartbeat for an existing node."""
    with NODE_REGISTRY_LOCK:
        if node_id in NODE_REGISTRY:
            NODE_REGISTRY[node_id]["last_heartbeat"] = time.time()
            NODE_REGISTRY[node_id]["status"] = "online"
            if metadata:
                NODE_REGISTRY[node_id]["metadata"].update(metadata)
            print(f"[Node Swarm] Heartbeat from: {node_id}")
            return True
        return False

def get_all_nodes():
    """Get all nodes with updated status based on heartbeat age."""
    current_time = time.time()
    with NODE_REGISTRY_LOCK:
        nodes = []
        for node_id, node_data in NODE_REGISTRY.items():
            heartbeat_age = current_time - node_data["last_heartbeat"]
            if heartbeat_age > NODE_STALE_THRESHOLD:
                node_data["status"] = "offline"
            elif heartbeat_age > 30:
                node_data["status"] = "stale"
            else:
                node_data["status"] = "online"
            node_data["heartbeat_age"] = round(heartbeat_age, 1)
            nodes.append(node_data.copy())
        return nodes

def cleanup_stale_nodes():
    """Remove nodes that have been offline for extended period."""
    current_time = time.time()
    with NODE_REGISTRY_LOCK:
        to_remove = []
        for node_id, node_data in NODE_REGISTRY.items():
            heartbeat_age = current_time - node_data["last_heartbeat"]
            if heartbeat_age > NODE_STALE_THRESHOLD * 5:  # 5 minutes
                to_remove.append(node_id)
        for node_id in to_remove:
            del NODE_REGISTRY[node_id]
            print(f"[Node Swarm] Removed stale node: {node_id}")

def node_cleanup_loop():
    """Background thread to periodically cleanup stale nodes."""
    while True:
        time.sleep(30)
        cleanup_stale_nodes()

# ============== SESSION STATE MANAGEMENT ==============
# Sliding window buffer for continuous learning context
SLIDING_WINDOW_SIZE = 20  # Number of exchanges to keep in short-term memory (raised from 10: restarts were dropping recent context that still sat in chat_history.json; hydrate_sessions_from_history() now refills windows at boot)
MAX_EXCHANGE_RENDER_CHARS = 400  # Per-exchange cap when rendering the sliding window into the system prompt (raised from 200 alongside the window widening to 20)
# ============== SESSION STATE MANAGEMENT ==============
TOKEN_LIMIT = 2500  # Target token count for context window
TOKEN_ESTIMATE_FACTOR = 4  # 1 token ≈ 4 characters

# ===== DEFENSIVE CONTEXT GUARDRAIL (postmortem: 00:43:18 context-loss incident) =====
# The deployed model (polaris-ai:latest) runs num_ctx 32768 (v3.4, raised from
# 8192). If the assembled system prompt + user prompt + reply ever exceed that
# window, Ollama silently truncates from the FRONT — erasing persona, KNOWN
# FACTS and directives. If build_system_prompt's output exceeds
# SYSTEM_PROMPT_TOKEN_GUARD (chars/4 estimate), the sliding window is shrunk
# stepwise (WINDOW_SHRINK_STEPS) and the prompt re-rendered so fixed blocks
# always survive future growth. The guard sits at 9000 est. chars (chars/4),
# raised from 6500 on Oct 4, 2026 to fund the 20-exchange window plus the
# blueprint digest; num_ctx headroom still reserves room for tool results
# (read_doc pages up to MAX_READ_PAGE_CHARS) plus user + reply + thinking.
SYSTEM_PROMPT_TOKEN_GUARD = 9000   # persona/history budget; raised from 6500 to fund the 20-exchange window + blueprint digest (num_ctx is 32768, so this leaves ample headroom)
WINDOW_SHRINK_STEPS = [12, 6, 3]   # exchange caps tried, in order, when the guard trips; 3 is the FLOOR - the window is never collapsed to zero

# Reset command patterns
RESET_COMMANDS = [
    r'^/clear$',
    r'^/reset$',
    r'^\s*new\s+chat\s*$',
    r'^\s*reset\s+chat\s*$',
    r'^\s*start\s+over\s*$',
    r'^\s*clear\s+context\s*$',
    r'^\s*clear\s+history\s*$',
    r'^\s*new\s+conversation\s*$'
]

# Chat history storage (user_identity -> list of messages)
CHAT_HISTORY_LOCK = threading.Lock()
CHAT_HISTORY = {}  # In-memory: {"rich": [...], "matt": [...]}
CHAT_HISTORY_FILE = "/data/freeroam/chat_history.json"

# Load chat history from disk on startup
def load_chat_history_from_disk():
    global CHAT_HISTORY
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, 'r') as f:
                CHAT_HISTORY = json.load(f)
            print(f"[Chat History] Loaded {len(CHAT_HISTORY)} user histories from disk")
        except Exception as e:
            print(f"[Chat History] Error loading from disk: {e}")
            CHAT_HISTORY = {}
    else:
        print("[Chat History] No existing history file, starting fresh")

def scrub_chat_history_leaks():
    """One-boot scrub of raw tool-call JSON that leaked into chat history
    BEFORE the unconditional strip fix. Runs strip_tool_calls_from_response()
    over every stored entry, keeps a one-time sidecar backup of the pre-scrub
    snapshot, then re-persists.

    Called from the server entry points (both __main__ and WSGI import paths)
    — NOT from load_chat_history_from_disk(), because that loader executes at
    import time while strip_tool_calls_from_response is defined later in this
    module (name is resolved at call time, so this is safe from the entry
    blocks which run after the module is fully loaded).
    """
    changed = 0
    with CHAT_HISTORY_LOCK:
        for bucket in CHAT_HISTORY.values():
            if not isinstance(bucket, list):
                continue
            for msg in bucket:
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content") or msg.get("message")
                if not content or not isinstance(content, str):
                    continue
                cleaned = strip_tool_calls_from_response(content).strip()
                if cleaned != content:
                    if "content" in msg:
                        msg["content"] = cleaned
                    if "message" in msg:
                        msg["message"] = cleaned
                    changed += 1
    if changed:
        # Backup BEFORE persisting the cleaned snapshot (the on-disk file is
        # still the pre-scrub version at this point — the save thread hasn't
        # started yet).
        try:
            backup_path = CHAT_HISTORY_FILE + ".pre-scrub"
            if not os.path.exists(backup_path):
                with open(CHAT_HISTORY_FILE, "r") as src:
                    data = src.read()
                with open(backup_path, "w") as dst:
                    dst.write(data)
        except Exception as e:
            print(f"[Chat History] Pre-scrub backup failed (skipped): {e}")
        threading.Thread(target=save_chat_history_to_disk, daemon=True).start()
    print(f"[Chat History] Startup scrub: {changed} leaked entries cleaned")

# Save chat history to disk
def save_chat_history_to_disk():
    with CHAT_HISTORY_LOCK:
        try:
            os.makedirs(os.path.dirname(CHAT_HISTORY_FILE), exist_ok=True)
            with open(CHAT_HISTORY_FILE, 'w') as f:
                json.dump(CHAT_HISTORY, f, indent=2)
        except Exception as e:
            print(f"[Chat History] Error saving to disk: {e}")

def utc_now_iso():
    """UTC ISO-8601 timestamp with 'Z' suffix (e.g. 2026-09-24T10:15:30.123456Z).

    Android's Instant.parse() ONLY accepts the 'Z'-suffixed form. The legacy
    formats ('%Y-%m-%d %H:%M:%S', naive .isoformat(), '07:48 PM') all fail and
    force the app's parseTimestamp fallback to now(), which corrupts message
    ordering and makes the sync loop believe the server has newer messages
    than it really does (wholesale-replacing the window with stale history).
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def append_chat_history(user_identity, role, content, sender):
    """Append one message to CHAT_HISTORY[user_identity] with a Z-ISO UTC
    timestamp, cap the bucket at 200 messages, and persist to disk async.

    Both sides of every exchange (user prompt AND Polaris reply) must be
    appended here so synced clients fetching GET /api/chat/history see the
    full conversation — the phone renders user bubbles from role=='user'
    entries and Polaris bubbles from role=='assistant' entries.
    """
    # (Shared-window unification) Alias dashboard/operator into the shared
    # bucket at the storage layer so every write path converges.
    user_identity = normalize_chat_identity(user_identity)
    with CHAT_HISTORY_LOCK:
        if user_identity not in CHAT_HISTORY:
            CHAT_HISTORY[user_identity] = []
        CHAT_HISTORY[user_identity].append({
            "role": role,
            "content": content,
            "timestamp": utc_now_iso(),
            "sender": sender
        })
        # Keep only last 200 messages
        if len(CHAT_HISTORY[user_identity]) > 200:
            CHAT_HISTORY[user_identity] = CHAT_HISTORY[user_identity][-200:]
    # Persist to disk outside the lock (async)
    threading.Thread(target=save_chat_history_to_disk, daemon=True).start()

# Initialize chat history on startup
load_chat_history_from_disk()

class SessionState:
    """Manages per-user session state with sliding window buffer."""
    
    def __init__(self):
        self.sessions = {}  # user_identity -> session_data (changed from client_ip)
        self.lock = threading.Lock()
    
    def get_or_create_session(self, user_identity):
        """Get existing session or create new one. (Shared-window
        unification: aliases collapse here, covering every read/write that
        funnels through this method.)"""
        user_identity = normalize_chat_identity(user_identity)
        with self.lock:
            if user_identity not in self.sessions:
                self.sessions[user_identity] = {
                    "session_id": f"session_{user_identity}_{datetime.now().timestamp()}",
                    "created_at": datetime.now().isoformat(),
                    "last_activity": datetime.now().isoformat(),
                    "sliding_window": [],  # List of {role, content, timestamp}
                    "topic_history": [],   # List of {topic, timestamp, confidence}
                    "current_topic": None,
                    "exchange_count": 0
                }
            return self.sessions[user_identity]
    
    def add_exchange(self, user_identity, role, content):
        """Add an exchange to the sliding window buffer."""
        session = self.get_or_create_session(user_identity)
        with self.lock:
            session["last_activity"] = datetime.now().isoformat()
            session["exchange_count"] += 1
            
            # Add to sliding window
            session["sliding_window"].append({
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat()
            })
            
            # Maintain window size
            if len(session["sliding_window"]) > SLIDING_WINDOW_SIZE:
                session["sliding_window"].pop(0)
    
    def update_topic(self, user_identity, topic, confidence=0.8):
        """Update current topic and track topic history."""
        session = self.get_or_create_session(user_identity)
        with self.lock:
            # Detect topic switch
            if session["current_topic"] != topic:
                if session["current_topic"]:
                    session["topic_history"].append({
                        "topic": session["current_topic"],
                        "ended_at": datetime.now().isoformat(),
                        "duration_exchanges": len([e for e in session["sliding_window"] if e["role"] == "user"])
                    })
                
                session["current_topic"] = topic
                session["topic_history"].append({
                    "topic": topic,
                    "started_at": datetime.now().isoformat(),
                    "confidence": confidence
                })
                
                # Keep only last 20 topic switches
                if len(session["topic_history"]) > 20:
                    session["topic_history"].pop(0)
    
    def reset_session(self, user_identity):
        """Reset session state for a user (clear sliding window)."""
        user_identity = normalize_chat_identity(user_identity)
        with self.lock:
            if user_identity in self.sessions:
                # Clear sliding window but keep session metadata
                self.sessions[user_identity]["sliding_window"] = []
                self.sessions[user_identity]["current_topic"] = None
                self.sessions[user_identity]["topic_history"] = []
                self.sessions[user_identity]["exchange_count"] = 0
                self.sessions[user_identity]["last_activity"] = datetime.now().isoformat()
                return True
            return False
    
    def get_token_count(self, user_identity):
        """Estimate current token count in sliding window."""
        session = self.get_or_create_session(user_identity)
        with self.lock:
            total_chars = sum(len(exchange["content"]) for exchange in session["sliding_window"])
            return total_chars // TOKEN_ESTIMATE_FACTOR
    
    def get_context_window(self, user_identity):
        """Get the current sliding window context for prompt building."""
        session = self.get_or_create_session(user_identity)
        with self.lock:
            return session["sliding_window"].copy()
    
    def clear_session(self, user_identity):
        """Clear session state for a user."""
        user_identity = normalize_chat_identity(user_identity)
        with self.lock:
            if user_identity in self.sessions:
                del self.sessions[user_identity]
                return True
            return False

# Global session state manager
SESSION_MANAGER = SessionState()

def get_user_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def get_user_config(ip):
    # First try exact IP match
    config = USER_CONFIG.get(ip)
    if config:
        return config
    
    # If IP is from allowed Docker bridge networks, use default config
    # This handles NAT scenarios where the real user IP is masked
    for prefix in ALLOWED_IP_PREFIXES:
        if ip.startswith(prefix):
            return DEFAULT_USER_CONFIG
    
    return None

def get_config_by_identity(identity):
    """Resolve a USER_CONFIG by user identity name ('rich', 'matt', 'operator',
    'dashboard'). First matching entry wins. Used by POST /api/chat to honor an
    explicit user_id in the request body (e.g. the Dashboard frontend sending
    user_id='rich' on behalf of Rich) instead of resolving identity purely by
    IP. Returns None for unknown identities so callers fall back to the
    IP-derived config."""
    identity = (identity or "").strip().lower()
    for cfg in USER_CONFIG.values():
        if cfg.get("name", "").lower() == identity:
            return cfg
    return None


# ── (Shared-window unification) Identity aliases ────────────────────────────
# One live conversation window per operator cluster: LGI voice (the X17 posts
# /api/chat WITHOUT a user_id -> resolves to "dashboard"), Mainframe operator
# curls -> "operator", Dashboard renders, and the phone all share the "rich"
# bucket + sliding session. Matt keeps his own private bucket and must never
# be aliased into the shared window.
SHARED_IDENTITY_ALIASES = {
    "dashboard": "rich",
    "operator": "rich",
}

# ── PROJECT BLUEPRINTS: live digest + vault sync (cross-session context fix) ──
# Authoritative docs live in the repo and are bind-mounted read-only:
#   /data/workspace/**                       -> the docker-containers tree (ro)
#   /data/freeroam/polaris-gateway/ingest/** -> gateway-local mirrored snapshots
# Fix B: a compact heading digest of each doc is rendered into EVERY system
# prompt (rebuilt only when the file's mtime changes) alongside instructions to
# open the full document before answering. Fix C: fuller outlines are mirrored
# into the Memory Vault (category "architecture") at boot so semantic search
# can retrieve them mid-chat. The repo MDs remain the editable source of truth.
POLARIS_DOC_SOURCES = {
    "northstar.md": [
        "/data/freeroam/polaris-gateway/ingest/northstar.md",
        "/data/workspace/northstar.md",
    ],
    "freeroam/Polaris-gateway.md": [
        "/data/freeroam/polaris-gateway/ingest/Polaris-gateway.md",
        "/data/workspace/freeroam/Polaris-gateway.md",
    ],
    "LGI/LGI.md": [
        "/data/workspace/LGI/LGI.md",
        "/data/freeroam/polaris-gateway/ingest/LGI.md",
    ],
}
_BLUEPRINT_DIGEST_CACHE = {}   # (path, mtime_ns, size) -> digest text
DOC_BLUEPRINT_SYNC_STATE_FILE = "/data/freeroam/doc_blueprint_sync.json"
DOC_DIGEST_MAX_CHARS = 1100    # per-doc cap inside the system-prompt digest
DOC_OUTLINE_MAX_CHARS = 11000  # per-doc cap for the vault mirror (< MAX_CONTENT_CHARS=12000)


def _resolve_blueprint_path(doc_key):
    """First existing candidate path for a blueprint doc (ro-repo mount first)."""
    for candidate in POLARIS_DOC_SOURCES.get(doc_key, []):
        try:
            if candidate and os.path.isfile(candidate):
                return candidate
        except OSError:
            continue
    return None


def _extract_doc_outline(path, max_chars=DOC_DIGEST_MAX_CHARS, lead_chars=160):
    """Compact outline of a markdown doc: title + headings + one short lead
    line after each level-1/2 heading. Produces a MAP of the document, never
    the full text - the model must open the original for details. Fully
    bounded (max_chars) and tolerant of unreadable/empty files."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        return f"(unreadable: {exc})"

    out = []
    title = None
    pending_lead = False
    budget = max(1, int(max_chars))
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            level = 0
            while level < len(stripped) and stripped[level] == "#":
                level += 1
            heading = stripped[level:].strip()
            if heading and budget > 0:
                title = title or heading
                entry = ("  " * min(level - 1, 3)) + heading
                entry = entry[:budget]
                out.append(entry)
                budget -= len(entry) + 1
                pending_lead = level <= 2
            continue
        if pending_lead and not stripped.startswith(("-", "*", ">", "|", "`", "[")):
            lead = stripped[: int(lead_chars)] + ("…" if len(stripped) > int(lead_chars) else "")
            if budget - len(lead) - 4 <= 0:
                break
            out.append("  · " + lead)
            budget -= len(lead) + 4
            pending_lead = False
    if out and budget <= 0:
        out.append("… [digest truncated]")
    return "\n".join(out)


def get_project_digest_block():
    """Fix B: compact LIVE digest of the authoritative project blueprints for
    the system prompt. Cached by (path, mtime_ns, size) and rebuilt only when
    a file changes on disk, so prompt content stays stable across turns and
    always reflects the latest source edits. Returns "" when nothing is
    reachable, letting the prompt degrade gracefully."""
    try:
        parts = []
        for doc_key in POLARIS_DOC_SOURCES:
            path = _resolve_blueprint_path(doc_key)
            if not path:
                continue
            try:
                st = os.stat(path)
                cache_key = (path, st.st_mtime_ns, st.st_size)
            except OSError:
                continue
            text = _BLUEPRINT_DIGEST_CACHE.get(cache_key)
            if text is None:
                text = _extract_doc_outline(path, DOC_DIGEST_MAX_CHARS)
                if len(_BLUEPRINT_DIGEST_CACHE) > 9:
                    _BLUEPRINT_DIGEST_CACHE.clear()
                _BLUEPRINT_DIGEST_CACHE[cache_key] = text
            parts.append(f"◆ {doc_key}: {text}")
        if not parts:
            return ""
        return (
            "[PROJECT BLUEPRINTS - LIVE DIGEST OF YOUR AUTHORITATIVE DOCS]:\n"
            + "\n".join(parts) + "\n"
            "These lines are a MAP, not the full documents. Before answering any question that "
            "touches the North Star ecosystem, its architecture, or the LGI, open the relevant "
            "document FIRST and ground your answer in it:\n"
            '- northstar.md / freeroam/Polaris-gateway.md -> read_doc(filename="northstar.md") or read_doc(filename="Polaris-gateway.md")\n'
            '- LGI/LGI.md -> read_workspace_file("LGI/LGI.md")\n'
            "Never reconstruct how the ecosystem works from memory when you can read its blueprint."
        )
    except Exception as exc:
        print(f"[Blueprint Digest] unexpected failure: {exc}")
        return ""


def normalize_chat_identity(user_identity):
    """Collapse alias identities (dashboard/operator) into the shared 'rich'
    bucket. Safe on None/empty; tolerant of casing and tail whitespace."""
    identity = (user_identity or "").strip().lower()
    return SHARED_IDENTITY_ALIASES.get(identity, identity)

def resolve_config_with_override(client_ip, requested_identity):
    """Resolve a config from an explicit client-declared identity (query
    param / JSON body field / WebSocket 'identity' announce), using the
    SAME trust policy as the POST /api/chat override: the requester must
    first pass the standard IP gate (exact USER_CONFIG match or an allowed
    Docker bridge prefix), and the requested identity must match a
    configured user name. This lets tunneled clients (the phone over
    WireGuard is NAT'd to a Docker bridge IP like 172.18.0.1) keep their
    own bucket instead of the empty Dashboard default. Returns the override
    config when honored, or None so callers keep the IP-derived config."""
    if not get_user_config(client_ip):
        # Client is not allowed by the IP gate at all: no override.
        return None
    if not requested_identity:
        return None
    return get_config_by_identity(requested_identity)

def load_json_file(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            return json.load(f)
    return None

def save_json_file(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

def load_profile(config):
    profile_file = config["profile_file"]
    if os.path.exists(profile_file):
        with open(profile_file, 'r') as f:
            return json.load(f)
    return {"user_name": "Unknown", "facts_and_preferences": [], "created_at": datetime.now().isoformat()}

def load_behavior_lib(config):
    behavior_file = config.get("behavior_lib")
    if behavior_file and os.path.exists(behavior_file):
        with open(behavior_file, 'r') as f:
            return json.load(f)
    return {"preferences": {}, "behavioral_rules": []}
def load_lists(config):
    """Load user's managed lists."""
    lists_file = config["lists_file"]
    if os.path.exists(lists_file):
        with open(lists_file, 'r') as f:
            return json.load(f)
    return {"updated_at": datetime.now().isoformat()}

def save_lists(config, lists):
    """Save user's managed lists."""
    lists["updated_at"] = datetime.now().isoformat()
    lists_file = config["lists_file"]
    os.makedirs(os.path.dirname(lists_file), exist_ok=True)
    with open(lists_file, 'w') as f:
        json.dump(lists, f, indent=2)

def load_behavior_lib(config):
    behavior_file = config.get("behavior_lib")
    if behavior_file and os.path.exists(behavior_file):
        with open(behavior_file, 'r') as f:
            return json.load(f)
    return {"preferences": {}, "behavioral_rules": []}

def load_heuristics():
    with HEURISTICS_LOCK:
        if os.path.exists(HEURISTICS_FILE):
            with open(HEURISTICS_FILE, 'r') as f:
                return json.load(f)
def detect_reset_command(message: str) -> bool:
    """Check if message is a reset/clear command."""
    message_lower = message.strip().lower()
    for pattern in RESET_COMMANDS:
        if re.match(pattern, message_lower):
            return True
    return False

def detect_correction(user_prompt: str) -> bool:
        return {"version": "1.0.0", "last_updated": None, "heuristics": []}

def save_heuristic(heuristic_data):
    with HEURISTICS_LOCK:
        heuristics = load_heuristics()
        heuristics["heuristics"].append(heuristic_data)
        heuristics["last_updated"] = datetime.now().isoformat()
        save_json_file(HEURISTICS_FILE, heuristics)
        return True

def extract_rule_from_correction(user_prompt: str, previous_response: str) -> Optional[Dict]:
    extraction_prompt = f"""
You are a rule extraction engine. Analyze this user correction and extract a clear, actionable heuristic rule.

USER'S CORRECTION: "{user_prompt}"
PREVIOUS AI RESPONSE: "{previous_response}"

Extract a rule that will prevent this mistake in the future. Return ONLY valid JSON:
{{"rule": "Clear instruction", "category": "system_interaction|communication_style|task_execution|user_preference"}}

If no clear rule can be extracted, return an empty object {{}}.
"""
    try:
        payload = {"model": "llama3.2", "prompt": extraction_prompt, "format": "json", "stream": False}
        response = requests.post(OLLAMA_URL, json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            response_text = result.get("response", "")
            extracted = json.loads(response_text)
            if extracted and "rule" in extracted:
                return extracted
        return None
    except Exception as e:
        print(f"[Learning Hook] Error extracting rule: {e}")
        return None

def detect_correction(user_prompt: str) -> bool:
    user_prompt_lower = user_prompt.lower()
    for pattern in CORRECTION_PATTERNS:
        if re.search(pattern, user_prompt_lower):
            return True
    return False

def detect_topic(user_message, ollama_response=None):
    """
    Implicitly detect the current topic from user message and context.
    Uses keyword extraction and semantic clustering.
    """
    # Extract key nouns and technical terms
    keywords = re.findall(r'\b(?:XRP|AMM|arbitrage|liquidity|pool|token|trade|XRPL|wallet|balance|position|Reversion|Splash|Strike|Matrix|Holodeck|dashboard|chart|QuestDB|query|database|North Star|Polaris|FreeRoam|memory|learning|heuristic|session|topic|buffer|sliding|window)\b', user_message, re.IGNORECASE)
    
    if keywords:
        # Return most frequent keyword as topic
        from collections import Counter
        topic_counter = Counter(keywords)
        primary_topic = topic_counter.most_common(1)[0][0]
        return primary_topic.lower()
    
    # Fallback: classify by message length and structure
    if len(user_message.split()) > 20:
        return "complex_query"
    elif "?" in user_message:
        return "question"
    else:
        return "general_chat"

def extract_topic_confidence(user_message, detected_topic):
    """
    Calculate confidence score for topic detection based on signal strength.
    """
    confidence = 0.5  # Base confidence
    
    # Boost confidence for multiple keyword matches
    keyword_matches = len(re.findall(r'\b(?:XRP|AMM|arbitrage|liquidity|pool|token|trade|XRPL|wallet|balance|position|Reversion|Splash|Strike|Matrix|Holodeck|dashboard|chart|QuestDB|query|database|North Star|Polaris|FreeRoam|memory|learning|heuristic|session|topic|buffer|sliding|window)\b', user_message, re.IGNORECASE))
    
    if keyword_matches >= 3:
        confidence = 0.95
    elif keyword_matches >= 2:
        confidence = 0.85
    elif keyword_matches >= 1:
        confidence = 0.75
    
    # Check for explicit topic markers
    if re.search(r'\b(about|regarding|concerning|regards to)\b', user_message, re.IGNORECASE):
        confidence = min(confidence + 0.1, 1.0)
    
    return confidence

def queue_learning_task(user_ip: str, user_prompt: str, previous_response: str):
    def learning_worker():
        time.sleep(0.5)
        config = get_user_config(user_ip)
        if not config:
            return
        print(f"[Learning Hook] Detected correction from {config['name']}, extracting rule...")
        extracted_rule = extract_rule_from_correction(user_prompt, previous_response)
        if extracted_rule and "rule" in extracted_rule:
            heuristic_id = f"h_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            heuristic_data = {
                "id": heuristic_id,
                "category": extracted_rule.get("category", "user_preference"),
                "rule": extracted_rule["rule"],
                "confidence": 0.8,
                "source_event": "implicit_correction",
                "user": config["name"],
                "created_at": datetime.now().isoformat()
            }
            # Stage the rule instead of auto-appending
            with STAGED_HEURISTICS_LOCK:
                STAGED_HEURISTICS[heuristic_id] = heuristic_data
            print(f"[Learning Hook] ⏳ Staged heuristic {heuristic_id} for approval: {extracted_rule['rule']}")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(config["transcript_file"], "a") as f:
                f.write(f"[{timestamp}] [SYSTEM LEARNING] Staged heuristic for approval: {extracted_rule['rule']}\n")
            # Emit WebSocket event for real-time dashboard notification
            socketio.emit('new_staged_rule', heuristic_data, broadcast=True)
        else:
            print(f"[Learning Hook] No actionable rule extracted")
    learning_thread = threading.Thread(target=learning_worker, daemon=True)
    learning_thread.start()


def execute_tool_call(tool_name: str, arguments: Dict) -> Dict:
    """
    Execute a tool call based on tool name and arguments.
    Returns the result of the tool execution.
    """
    try:
        # File manipulation tools (ingest/)
        if tool_name == "read_doc":
            # v3.4 doc paging: forward the caller's offset/length so large
            # documents can be walked in slices within the model's context.
            return read_doc(
                arguments.get("filename", ""),
                offset=arguments.get("offset", 0),
                length=arguments.get("length", MAX_READ_PAGE_CHARS),
            )
        elif tool_name == "read_workspace_file":
            # v1.4.2: READ-ONLY window into the mounted North Star workspace
            # (compose binds /home/causeiam/docker-containers at /data/workspace)
            # — no SSH round-trip, always live.
            return read_workspace_file(
                arguments.get("path", ""),
                offset=arguments.get("offset", 0),
                length=arguments.get("length", MAX_READ_PAGE_CHARS),
            )
        elif tool_name == "list_workspace_files":
            return list_workspace_files(
                arguments.get("query", ""),
                extension=arguments.get("extension", ""),
            )
        elif tool_name == "write_doc":
            filename = arguments.get("filename", "")
            text = arguments.get("text", "")
            
            task_id = task_manager.register_task(
                TaskType.FILE_OPERATION,
                f"Write document: {filename}",
                metadata={"filename": filename, "operation": "write", "text_length": len(text)}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = write_doc(filename, text)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Write operation failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        elif tool_name == "append_doc":
            filename = arguments.get("filename", "")
            text = arguments.get("text", "")
            
            task_id = task_manager.register_task(
                TaskType.FILE_OPERATION,
                f"Append to document: {filename}",
                metadata={"filename": filename, "operation": "append", "text_length": len(text)}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = append_doc(filename, text)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Append operation failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        elif tool_name == "list_docs":
            return list_docs(arguments.get("pattern", "*.md"))
        elif tool_name == "delete_doc":
            filename = arguments.get("filename", "")
            
            task_id = task_manager.register_task(
                TaskType.FILE_OPERATION,
                f"Delete document: {filename}",
                metadata={"filename": filename, "operation": "delete"}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = delete_doc(filename)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Delete operation failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        # Playground Holodeck tools (web & code sandbox)
        elif tool_name == "publish_web_asset":
            filename = arguments.get("filename", "")
            content = arguments.get("content", "")
            
            task_id = task_manager.register_task(
                TaskType.PLAYGROUND_OPERATION,
                f"Publish web asset: {filename}",
                metadata={"filename": filename, "content_length": len(content)}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = publish_web_asset(filename, content)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Publish operation failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        elif tool_name == "list_playground_files":
            return list_playground_files(arguments.get("directory", "all"))
        elif tool_name == "run_sandbox_code":
            language = arguments.get("language", "python")
            code = arguments.get("code", "")
            timeout = arguments.get("timeout", 15)
            
            task_id = task_manager.register_task(
                TaskType.PLAYGROUND_OPERATION,
                f"Run sandbox code ({language}): {code[:50]}...",
                metadata={"language": language, "timeout": timeout, "code_length": len(code)}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = run_sandbox_code(language, code, timeout)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Sandbox code execution failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        elif tool_name == "spawn_service":
            entry_script = arguments.get("entry_script", "")
            port = arguments.get("port", 8091)
            language = arguments.get("language", "python")
            
            task_id = task_manager.register_task(
                TaskType.PLAYGROUND_OPERATION,
                f"Spawn service on port {port}: {entry_script}",
                metadata={"entry_script": entry_script, "port": port, "language": language}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = spawn_service(entry_script, port, language)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Service spawn failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        elif tool_name == "stop_service":
            port = arguments.get("port", 0)
            
            task_id = task_manager.register_task(
                TaskType.PLAYGROUND_OPERATION,
                f"Stop service on port {port}",
                metadata={"port": port}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = stop_service(port)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Service stop failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        elif tool_name == "list_services":
            return list_services()
        # SSH Remote Execution tools (X17 LGI + Mainframe control)
        elif tool_name == "ssh_execute":
            target_host = arguments.get("target_host", "")
            command = arguments.get("command", "")
            timeout = arguments.get("timeout", 15)
            ssh_user = arguments.get("ssh_user", "rbuit")
            
            task_id = task_manager.register_task(
                TaskType.SSH_COMMAND,
                f"SSH command on {target_host}: {command[:50]}...",
                metadata={"target_host": target_host, "command": command, "timeout": timeout, "ssh_user": ssh_user}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = ssh_execute(target_host, command, timeout, ssh_user)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                elif result.get("reason") in ("host_unreachable", "timeout", "ssh_connection_failure", "dns_failure"):
                    # The tool did its job: it determined the device is OFF/unreachable.
                    # That is a valid status answer, not a task failure.
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "SSH command failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
                
        elif tool_name == "ssh_check_connectivity":
            target_host = arguments.get("target_host", "")
            timeout = arguments.get("timeout", 5)
            ssh_user = arguments.get("ssh_user", "rbuit")
            
            task_id = task_manager.register_task(
                TaskType.SSH_COMMAND,
                f"SSH connectivity check to {target_host}",
                metadata={"target_host": target_host, "timeout": timeout}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = ssh_check_connectivity(target_host, timeout, ssh_user)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                elif result.get("reason") in ("host_unreachable", "timeout", "ssh_connection_failure", "dns_failure"):
                    # An unreachable device IS the answer to a connectivity check.
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "SSH connectivity check failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
                
        # SSH file transfer tools (ingest vault <-> remote node)
        elif tool_name == "ssh_deploy_file":
            filename = arguments.get("filename", "")
            dest_remote = arguments.get("dest_remote", "D:/LGI/")
            ssh_user = arguments.get("ssh_user", "rbuit")
            target_host = arguments.get("target_host", "192.168.50.227")

            task_id = task_manager.register_task(
                TaskType.SSH_COMMAND,
                f"scp deploy {filename} to {ssh_user}@{target_host}:{dest_remote}",
                metadata={"filename": filename, "dest_remote": dest_remote, "target_host": target_host, "ssh_user": ssh_user}
            )
            task_manager.start_task(task_id, progress=10)

            try:
                result = ssh_deploy_file(filename, dest_remote, target_host, ssh_user)
                task_manager.update_progress(task_id, progress=90)

                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                elif result.get("reason") in ("host_unreachable", "timeout", "ssh_connection_failure", "dns_failure"):
                    # An unreachable device IS the answer, not a tool malfunction.
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "scp deploy failed"))

                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise

        elif tool_name == "ssh_fetch_file":
            remote_path = arguments.get("remote_path", "")
            filename = arguments.get("filename", "")
            ssh_user = arguments.get("ssh_user", "rbuit")
            target_host = arguments.get("target_host", "192.168.50.227")

            task_id = task_manager.register_task(
                TaskType.SSH_COMMAND,
                f"scp fetch {ssh_user}@{target_host}:{remote_path} into vault",
                metadata={"remote_path": remote_path, "filename": filename, "target_host": target_host, "ssh_user": ssh_user}
            )
            task_manager.start_task(task_id, progress=10)

            try:
                result = ssh_fetch_file(remote_path, filename, target_host, ssh_user)
                task_manager.update_progress(task_id, progress=90)

                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                elif result.get("reason") in ("host_unreachable", "timeout", "ssh_connection_failure", "dns_failure"):
                    # An unreachable device IS the answer, not a tool malfunction.
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "scp fetch failed"))

                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise

        # Gotify Notification Service
        elif tool_name == "send_gotify_notification":
            title = arguments.get("title", "")
            message = arguments.get("message", "")
            priority = arguments.get("priority", 5)
            extras = arguments.get("extras")
            
            task_id = task_manager.register_task(
                TaskType.GOTIFY_NOTIFICATION,
                f"Send Gotify notification: {title}",
                metadata={"title": title, "priority": priority}
            )
            task_manager.start_task(task_id, progress=10)
            
            try:
                result = send_gotify_notification(title, message, priority, extras)
                task_manager.update_progress(task_id, progress=90)
                
                if result.get("success"):
                    task_manager.complete_task(task_id, result=result)
                else:
                    task_manager.fail_task(task_id, result.get("error", "Gotify notification failed"))
                
                return result
            except Exception as e:
                task_manager.fail_task(task_id, str(e))
                raise
        # GEV (God's Eye View) tactical globe bridge — tools live in gev_tools.py
        elif tool_name == "gev_set_center":
            return gev_set_center(
                lon=arguments.get("lon"),
                lat=arguments.get("lat"),
                height=arguments.get("height", 6000),
            )
        elif tool_name == "gev_toggle_layer":
            return gev_toggle_layer(
                layer=arguments.get("layer", ""),
                enabled=arguments.get("enabled", True),
            )
        elif tool_name == "gev_query_object":
            return gev_query_object(query=arguments.get("query", "all"))
        elif tool_name == "gev_get_operator_position":
            return gev_get_operator_position(device=arguments.get("device"))
        # Memory Vault Query Tool (read-only long-term memory consultation)
        elif tool_name == "memory_vault_query":
            query = arguments.get("query", "")
            category = arguments.get("category", "")
            mode = arguments.get("mode", "hybrid")
            limit = arguments.get("limit", 5)
            path = arguments.get("path", "")
            
            return memory_vault_query(query=query, category=category, mode=mode, limit=limit, path=path)
        # Memory Vault Journal Write (v1.5 voice journal: the ONLY chat-side
        # vault write - append-only journal entries; no edit/delete/core paths)
        elif tool_name == "memory_journal_write":
            entry = arguments.get("entry", "")
            title = arguments.get("title", "")
            tags = arguments.get("tags", "")
            
            return memory_journal_write(entry=entry, title=title, tags=tags)
        # X17 Live Eyes (v1.7, 2026-10-13): scene digest + on-demand pixel
        # looks through the Burst Receiver's scene relay. Pixels flow only
        # when the model calls these — never streamed.
        elif tool_name in ("look_x17_desktop", "look_x17_camera"):
            source = "camera" if tool_name == "look_x17_camera" else "desktop"
            try:
                max_width = int(arguments.get("max_width") or 1280)
            except (TypeError, ValueError):
                max_width = 1280
            try:
                quality = int(arguments.get("quality") or 70)
            except (TypeError, ValueError):
                quality = 70
            return _x17_look(source, question=arguments.get("question", ""),
                             max_width=max_width, quality=quality)
        elif tool_name == "read_x17_scene":
            state = get_scene_state(force=True)
            if not state:
                return {"success": False,
                        "error": "X17 scene feed unavailable (receiver unreachable "
                                 "or the scene pusher on the X17 is offline)"}
            return {"success": True, "scene": state}
        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"success": False, "error": f"Tool execution error: {str(e)}"}


def _extract_balanced_json(text, start):
    """
    Extract a brace-balanced JSON object starting at text[start] (must be '{').
    Quote- and escape-aware: braces inside string values do not affect depth,
    so tool calls whose commands contain nested braces parse correctly.
    Returns the complete object substring, or None if unbalanced.
    """
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _repair_json_escapes(s):
    """
    Repair invalid JSON escape sequences by doubling lone backslashes that do
    not begin a valid JSON escape. Valid escapes are left untouched, so an
    already-correct JSON string is never modified.
    """
    valid_next = '"\\/bfnrtu'
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n and s[i + 1] in valid_next:
            out.append(s[i:i + 2])
            i += 2
            continue
        if c == '\\':
            out.append('\\\\')
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def _safe_parse_tool_json(json_str):
    r"""
    Parse a candidate tool-call JSON string. Tries strict json.loads first,
    then retries after repairing invalid backslash escapes (models often emit
    shell regexes like '[m]iss_pi\.py' with a single backslash, which is
    invalid JSON and previously caused the tool call to be silently dropped).
    Returns the parsed dict or None.
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_repair_json_escapes(json_str))
    except json.JSONDecodeError:
        return None


def parse_tool_calls_from_response(ai_response: str) -> List[Dict]:
    """
    Parse tool calls from AI response.
    Looks for JSON-formatted tool call patterns in the response.
    Returns list of tool calls with name and arguments.
    """
    tool_calls = []
    
    # Step 1: Extract every JSON tool call using a brace-balanced, escape-aware
    # scanner. Handles markdown-fenced AND plain-text tool calls, nested braces
    # inside commands (e.g. awk templates), and repairs the single-backslash
    # regexes models emit (e.g. grep '[m]iss_pi\.py' written as invalid JSON).
    import re
    for marker in re.finditer(r'\{\s*"tool"\s*:', ai_response):
        candidate = _extract_balanced_json(ai_response, marker.start())
        if not candidate:
            continue
        tool_data = _safe_parse_tool_json(candidate)
        if tool_data and isinstance(tool_data, dict) and "tool" in tool_data and "arguments" in tool_data:
            tc = {"name": tool_data["tool"], "arguments": tool_data["arguments"]}
            # Avoid duplicates (same call emitted twice, or fenced + inline)
            if not any(existing["name"] == tc["name"] and existing["arguments"] == tc["arguments"] for existing in tool_calls):
                tool_calls.append(tc)
    
    # Step 3: Look for function call format (legacy fallback)
    # read_doc(filename="notes.md")
    func_pattern = r'(\w+)\s*\(\s*(?:filename\s*=\s*"([^"]+)"|text\s*=\s*"([^"]*)"|content\s*=\s*"([^"]*)"|language\s*=\s*"([^"]+)"|code\s*=\s*"([^"]*)"|timeout\s*=\s*(\d+)|port\s*=\s*(\d+)|entry_script\s*=\s*"([^"]+)"|directory\s*=\s*"([^"]+)")?\s*\)'
    func_matches = re.findall(func_pattern, ai_response)
    
    for match in func_matches:
        tool_name = match[0]
        if tool_name in ["read_doc", "write_doc", "append_doc", "list_docs", "delete_doc", 
                         "publish_web_asset", "list_playground_files", "run_sandbox_code", 
                         "spawn_service", "stop_service", "list_services"]:
            arguments = {}
            if match[1]:  # filename captured
                arguments["filename"] = match[1]
            if match[2]:  # text captured
                arguments["text"] = match[2]
            if match[3]:  # content captured
                arguments["content"] = match[3]
            if match[4]:  # language captured
                arguments["language"] = match[4]
            if match[5]:  # code captured
                arguments["code"] = match[5]
            if match[6]:  # timeout captured
                arguments["timeout"] = int(match[6])
            if match[7]:  # port captured
                arguments["port"] = int(match[7])
            if match[8]:  # entry_script captured
                arguments["entry_script"] = match[8]
            if match[9]:  # directory captured
                arguments["directory"] = match[9]
            
            # Avoid duplicates from JSON pattern
            if not any(tc["name"] == tool_name and tc["arguments"] == arguments for tc in tool_calls):
                tool_calls.append({"name": tool_name, "arguments": arguments})
    
    return tool_calls


# Tool names recognized by the parser, used to strip botched/legacy tool-call
# remnants from the chat bubble. Built from the imported schema registries
# (covers BOTH schema shapes: flat {"name": ...} entries and OpenAI-style
# {"type": "function", "function": {"name": ...}} entries) plus the legacy
# inline-call names executed by parse_tool_calls_from_response Step 3.
LEGACY_INLINE_TOOL_NAMES = frozenset((
    "read_doc", "write_doc", "append_doc", "list_docs", "delete_doc",
    "publish_web_asset", "list_playground_files", "run_sandbox_code",
    "spawn_service", "stop_service", "list_services",
))


def _known_tool_names():
    """Union of all tool names from every imported schema registry plus the
    legacy inline-call names, so the stripper can recognize tool-shaped text
    even when its JSON is too malformed for _safe_parse_tool_json."""
    names = set(LEGACY_INLINE_TOOL_NAMES)
    for registry in (TOOL_SCHEMAS, PLAYGROUND_TOOL_SCHEMAS, SSH_TOOL_SCHEMAS,
                     GOTIFY_TOOL_SCHEMAS, MEMORY_TOOL_SCHEMAS, GEV_TOOL_SCHEMAS,
                     SCENE_TOOL_SCHEMAS):
        try:
            if isinstance(registry, dict):
                names.update(str(k) for k in registry.keys())
                for value in registry.values():
                    if isinstance(value, dict) and isinstance(value.get("name"), str):
                        names.add(value["name"])
            else:
                for entry in registry:
                    if isinstance(entry, dict):
                        name = entry.get("name")
                        if not isinstance(name, str):
                            inner = entry.get("function")
                            if isinstance(inner, dict):
                                name = inner.get("name")
                        if isinstance(name, str):
                            names.add(name)
        except Exception:
            # A malformed registry must never break the chat pipeline.
            continue
    return names


def strip_tool_calls_from_response(ai_response: str) -> str:
    """
    Remove tool call JSON patterns from AI response text.
    This prevents raw tool call JSON from being shown to the user.

    Closes three leak vectors that previously let tool-call residue reach the
    chat bubble (parser execution semantics are untouched - text cleanup only):
    1. Malformed tool JSON: models sometimes emit shell commands with invalid
       backslash escapes that _safe_parse_tool_json cannot repair. If the
       candidate still names a KNOWN tool ({"tool": "ssh_execute", ...}), the
       balanced span is stripped anyway - a debug line records what was
       removed, so operator debugging evidence survives, just never in the
       user's bubble. JSON that does not name a known tool (e.g. the user's
       own pasted JSON echoed back) stays visible.
    2. Legacy inline calls (read_doc(filename="..."), run_sandbox_code(code="...")
       etc.) are executed by parse_tool_calls_from_response but were never
       removed from the bubble; they are stripped here with the same
       name/argument shape the parser matches.
    3. Empty ```json fences left behind after their inner JSON was removed.
    """
    import re

    known = _known_tool_names()

    candidates = []
    for marker in re.finditer(r'\{\s*"tool"\s*:', ai_response):
        candidate = _extract_balanced_json(ai_response, marker.start())
        if not candidate:
            continue
        tool_data = _safe_parse_tool_json(candidate)
        if tool_data and isinstance(tool_data, dict) and "tool" in tool_data and "arguments" in tool_data:
            candidates.append(candidate)
            continue
        # Leak vector 1: unparseable candidate that still names a known tool is
        # a botched tool call - strip it (evidence stays in the debug log).
        head = re.match(r'\{\s*"tool"\s*:\s*"([^"]+)"', candidate)
        if head and head.group(1) in known:
            candidates.append(candidate)
            print(f"[DEBUG] Stripped malformed tool JSON for known tool "
                  f"'{head.group(1)}' ({len(candidate)} chars) from chat bubble")

    # Remove AFTER extraction so finditer index positions stay valid
    for candidate in candidates:
        ai_response = ai_response.replace(candidate, '', 1)

    # Leak vector 2: legacy inline tool calls (same shapes the parser executes)
    if known:
        name_alt = "|".join(re.escape(n) for n in sorted(known))
        legacy_pattern = re.compile(
            r'\b(?:' + name_alt + r')\s*\(\s*(?:filename\s*=\s*"[^"]*"'
            r'|text\s*=\s*"[^"]*"|content\s*=\s*"[^"]*"|language\s*=\s*"[^"]*"'
            r'|code\s*=\s*"[^"]*"|timeout\s*=\s*\d+|port\s*=\s*\d+'
            r'|entry_script\s*=\s*"[^"]*"|directory\s*=\s*"[^"]*")?\s*\)'
        )
        ai_response = legacy_pattern.sub('', ai_response)

    # Leak vector 3: code fences left empty by the stripping above
    ai_response = re.sub(r'```[A-Za-z0-9_+.-]*\s*\n\s*```', '', ai_response)
    ai_response = re.sub(r'```[A-Za-z0-9_+.-]*\s*```', '', ai_response)

    # Clean up any resulting double newlines or whitespace
    ai_response = re.sub(r'\n\s*\n\s*\n', '\n\n', ai_response)

    return ai_response.strip()


def _truncate_tool_results(tool_results, max_field_chars=1500, max_content_chars=MAX_READ_PAGE_CHARS):
    """
    (Edit E) Defensive helper for the tool-report LLM turn: shallow-copy each
    result dict and truncate large text fields (stdout/stderr/html/etc.) so
    the follow-up prompt stays small enough for the model to report on.
    (v3.4) Document content gets its own larger budget: read_doc pages are
    already capped at MAX_READ_PAGE_CHARS by the tool itself, so a full page
    now reaches the model instead of being chopped at 1500 chars. Other tools
    that return "content" (memory vault, livecharts) self-cap under this
    budget, so their behavior is unchanged.
    """
    slim = []
    for result in tool_results:
        if not isinstance(result, dict):
            slim.append({"result": str(result)[:max_field_chars]})
            continue
        r = dict(result)
        for key in ("stdout", "stderr", "output", "content", "result", "response", "html", "text"):
            cap = max_content_chars if key == "content" else max_field_chars
            val = r.get(key)
            if isinstance(val, str) and len(val) > cap:
                r[key] = val[:cap] + f"... [truncated, {len(val)} chars total]"
        slim.append(r)
    return slim


def generate_follow_up_message(tool_calls, tool_results=None):
    """
    FALLBACK ONLY (Edit E): result-aware follow-up message used when the
    post-tool LLM reporting turn fails. Built from the ACTUAL tool results —
    never fabricates success. (The old version emitted hardcoded success text
    like "🔧 Remote operations complete! Miss Pi is all set." regardless of
    outcome — removed.)
    """
    if not tool_calls:
        return ""
    if not tool_results:
        return "✅ Task complete!"
    
    parts = []
    for tool_call, result in zip(tool_calls, tool_results):
        name = tool_call.get('name', 'tool') if isinstance(tool_call, dict) else str(tool_call)
        if isinstance(result, dict):
            success = result.get('success', None)
            if success is False:
                error_field = str(result.get('error') or '').strip()
                stderr_field = str(result.get('stderr') or '').strip()
                # Combine distinct sources; dedupe when error already embeds stderr
                if stderr_field and stderr_field not in error_field:
                    detail = (f"{error_field} — {stderr_field}") if error_field else stderr_field
                else:
                    detail = error_field or stderr_field
                detail = detail[:300] or 'no error detail recorded'
                parts.append(f"⚠️ {name} FAILED: {detail}")
            elif success is True:
                parts.append(f"✅ {name} completed.")
            else:
                # No explicit success flag recorded — stay honest, never assume success
                parts.append(f"⏳ {name} ran, but no outcome flag was recorded.")
        else:
            parts.append(f"⚠️ {name} returned an unreadable result.")
    return " ".join(parts)


def build_system_prompt(config: Dict, heuristics_data: Dict, behavior_lib: Dict, user_identity: str = None) -> str:
    """Build system prompt with token-aware sliding window context."""
    user_name = config["name"]
    profile = load_profile(config)
    memory_context = "\n".join([f"- {fact}" for fact in profile.get("facts_and_preferences", [])])
    
    heuristics_block = ""
    if heuristics_data.get("heuristics"):
        heuristics_lines = []
        for h in heuristics_data["heuristics"]:
            if h.get("confidence", 0) >= 0.7:
                heuristics_lines.append(f"- [{h['category']}] {h['rule']}")
        if heuristics_lines:
            heuristics_block = "\n[SYSTEM HEURISTICS & SELF-LEARNED RULES]:\n" + "\n".join(heuristics_lines)
    
    behavior_block = ""
    if behavior_lib:
        prefs = behavior_lib.get("preferences", {})
        rules = behavior_lib.get("behavioral_rules", [])
        if prefs or rules:
            behavior_block = "\n[USER BEHAVIOR PREFERENCES]:\n"
            if prefs:
                behavior_block += f"- Communication style: {prefs.get('communication_style', 'balanced')}\n"
                behavior_block += f"- Response length: {prefs.get('response_length', 'moderate')}\n"
            if rules:
                for rule in rules:
                    behavior_block += f"- {rule}\n"
    
    # Add session context with token-aware sliding window.
    # _window_block() renders the [RECENT CONVERSATION CONTEXT] block from the
    # sliding window (token-capped, newest exchanges kept). max_exchanges lets
    # the defensive guardrail at the bottom of this function shrink the window
    # (6 -> 3 -> 0) without touching token accounting: None = no extra cap.
    def _window_block(max_exchanges: int = None) -> str:
        session_context_block = ""
        if user_identity:
            context_window = SESSION_MANAGER.get_context_window(user_identity)
            session = SESSION_MANAGER.get_or_create_session(user_identity)

            if context_window:
                # Build context iteratively until token limit reached
                selected_exchanges = []
                current_token_count = 0

                # Iterate backwards from newest to oldest
                for exchange in reversed(context_window):
                    # Guardrail: optional hard cap on exchange count
                    if max_exchanges is not None and len(selected_exchanges) >= max_exchanges:
                        break
                    # Estimate tokens for this exchange (add some buffer for formatting)
                    exchange_tokens = len(exchange["content"]) // TOKEN_ESTIMATE_FACTOR + 10

                    if current_token_count + exchange_tokens <= TOKEN_LIMIT:
                        selected_exchanges.insert(0, exchange)  # Insert at beginning to maintain order
                        current_token_count += exchange_tokens
                    else:
                        break  # Token limit reached

                if selected_exchanges:
                    session_context_block = "\n[RECENT CONVERSATION CONTEXT]:\n"
                    for exchange in selected_exchanges:
                        role_label = "You" if exchange["role"] == "assistant" else user_name
                        # Truncate very long messages but keep them readable
                        content = exchange["content"]
                        if len(content) > MAX_EXCHANGE_RENDER_CHARS:
                            content = content[:MAX_EXCHANGE_RENDER_CHARS] + "... [truncated]"
                        session_context_block += f"- {role_label}: {content}\n"

            if session.get("current_topic"):
                session_context_block += f"\n[CURRENT TOPIC]: {session['current_topic']}\n"
        return session_context_block

    session_context_block = _window_block()

    # Fix B: live heading digest of the authoritative project blueprints,
    # rebuilt from disk only when mtimes change. Empty string = no docs found.
    project_digest_block = get_project_digest_block()

    # Live Eyes (v1.7, 2026-10-13): a ~5s-cached fused digest of the X17's
    # moment (active window / desktop activity / camera attention + occupancy
    # / OCR head / latest vision event) injected into EVERY turn — she
    # always-knows without burning a vision pass. Pixels stay pull-only
    # (look_x17_* tools). Empty string when the feed has been dark beyond the
    # honesty grace window, so a dead X17 never taxes every prompt.
    scene_block = get_scene_prompt_block()

    # Add tool capabilities section
    tools_block = f"""
[AVAILABLE TOOLS]:
You have access to the following tools:

1. File Manipulation Tools (ingest/ directory):
{get_tool_schemas_json()}

2. Playground Holodeck Tools (Web & Code Sandbox):
{get_playground_tool_schemas_json()}

3. SSH Remote Execution Tools (X17 LGI + Mainframe Control):
{get_ssh_tool_schemas_json()}

4. Gotify Notification Service (Push Notifications):
{get_gotify_tool_schemas_json()}

5. Memory Vault Tools (Long-Term Lessons, Knowledge + Voice Journal):
{get_memory_tool_schemas_json()}

6. GEV God's Eye View Tools (Tactical Globe Control — fly camera, layers, operator GPS position):
{get_gev_tool_schemas_json()}

7. X17 Live Eyes Tools (scene digest + fresh desktop/camera looks):
{get_scene_tool_schemas_json()}

[TOOL USAGE EXAMPLES]:
User: "Can you create an HTML dashboard for tracking XRP prices?"
Polaris: "On it! I'm serving your XRP dashboard live on port 8090 right now."
{{"tool": "publish_web_asset", "arguments": {{"filename": "xrp_tracker.html", "content": "<!DOCTYPE html><html><body><h1>XRP Tracker</h1></body></html>"}}}}

User: "List all files in my playground."
Polaris: "Here is what we've got cooking in the playground!"
{{"tool": "list_playground_files", "arguments": {{"directory": "both"}}}}

User: "Run a python script to calculate Fibonacci sequence."
Polaris: "Running that math script in the sandbox now."
{{"tool": "run_sandbox_code", "arguments": {{"language": "python", "code": "def fib(n):\n    return n if n <= 1 else fib(n-1) + fib(n-2)\nprint([fib(i) for i in range(10)])"}}}}

User: "Read my trading notes."
Polaris: "Pulling up your trading notes now."
{{"tool": "read_doc", "arguments": {{"filename": "trading_notes.md"}}}}

User: "Can you look at trading-bot.md?"
Polaris: "Pulling it up from the workspace right now!"
{{"tool": "read_workspace_file", "arguments": {{"path": "trading-bot.md"}}}}
(If the path guess is wrong, find the file first:)
{{"tool": "list_workspace_files", "arguments": {{"query": "trading-bot"}}}}

User: "Check the active process status on the X17."
Polaris: "Pinging the X17 and grabbing that process list for you!"
{{"tool": "ssh_execute", "arguments": {{"target_host": "192.168.50.227", "command": "tasklist | findstr /i python"}}}}

User: "Is the X17 on?"
Polaris: "Checking on it right now!"
{{"tool": "ssh_check_connectivity", "arguments": {{"target_host": "192.168.50.227"}}}}
(If the tool result says reachable=false, reason=host_unreachable, answer:) Polaris: "The X17 is OFF - it is not on the network right now."

User: "Log today's arbitrage loop summary to my trading journal."
Polaris: "Saving that arbitrage summary to your journal now!"
{{"tool": "write_doc", "arguments": {{"filename": "trading_journal.md", "text": "Ran 14 profitable multi-hop arbitrage loops; recycled LP fees back into our AMM pools."}}}}

User: "Send me a notification when the trading bot starts."
Polaris: "I'll notify you as soon as the bot is running!"
{{"tool": "send_gotify_notification", "arguments": {{"title": "Trading Bot Started", "message": "The trading bot is now live and monitoring the market.", "priority": 8}}}}

User: "Alert me about the XRP price spike."
Polaris: "Sending you a priority notification right now!"
{{"tool": "send_gotify_notification", "arguments": {{"title": "XRP Price Alert", "message": "XRP just spiked 5% in the last hour!", "priority": 9}}}}

User: "Fly my GEV globe over those coordinates."
Polaris: "Flying the God's Eye View there now!"
{{"tool": "gev_set_center", "arguments": {{"lon": -117.16, "lat": 32.72, "height": 4000}}}}

User: "Show the satellite imagery on my globe."
Polaris: "Layering Esri World Imagery over the dark basemap!"
{{"tool": "gev_toggle_layer", "arguments": {{"layer": "imagery", "enabled": true}}}}

User: "Where is Rich right now?"
Polaris: "Pulling the operator's last GPS fix from the burst receiver!"
{{"tool": "gev_get_operator_position", "arguments": {{}}}}

User: "What have we learned about QuestDB timestamp bindings?"
Polaris: "Let me check my lessons vault for prior guidance on that!"
{{"tool": "memory_vault_query", "arguments": {{"query": "QuestDB JDBC timestamp binding", "category": "lesson"}}}}

User: "Polaris, log this: radar missed a motion ping at 14:32, webcam feed was fine."
Polaris: "Logged to today's journal."
{{"tool": "memory_journal_write", "arguments": {{"entry": "Radar missed a motion ping at 14:32 while the webcam feed stayed healthy - flagged for follow-up investigation.", "title": "Radar missed motion ping", "tags": "radar, followup"}}}}

User: "What's actually on my screen right now, exactly?"
Polaris: "Taking a fresh look at your desktop!"
{{"tool": "look_x17_desktop", "arguments": {{"question": "What is on the screen right now?"}}}}

[WORKSPACE FILE ACCESS - THE NORTH STAR REPO, READ-ONLY]:
The operator's ENTIRE workspace /home/causeiam/docker-containers (blueprints,
LGI code, compose files, trading-bot.md …) is mounted read-only into this
container at /data/workspace. When the operator asks "can you look at
trading-bot.md", "find the vision code", or wants to discuss any repo file —
THIS is the fast path, no SSH round-trip needed:
- FIND: {{"tool": "list_workspace_files", "arguments": {{"query": "trading-bot"}}}}
  → {{"matches": ["trading-bot.md", …]}} (relative repo paths)
- READ: {{"tool": "read_workspace_file", "arguments": {{"path": "trading-bot.md"}}}}
  → paged content exactly like read_doc (offset / next_offset for long files)
- Unknown filename? LIST first, then read the match. Subpaths welcome
  (e.g. "freeroam/Polaris-gateway.md", "LGI/lgi.py", "docker-compose.yml").
- STRICTLY READ-ONLY: repo edits happen via the operator and Cline only.
  Your writable area remains the ingest vault (write_doc) - bare filenames or one subdirectory level ('rich/yourfile.md').

[TOOL EXECUTION RULE]:
IMPORTANT: When the user asks you to create a web page, run code, view files, or manage documents, you MUST include the exact JSON tool call on a new line in your response so the system can execute it immediately.

Format: {{"tool": "tool_name", "arguments": {{"arg1": "value1", "arg2": "value2"}}}}

The system will detect this JSON pattern, execute the tool, and return the result for you to share with the user.
"""
    
    def _final_prompt(window_block: str) -> str:
        return f"""
You are Polaris, an intelligent, witty, and bubbly AI companion for {user_name}.

{project_digest_block}
[PROJECT NORTH STAR - YOUR PRIMARY ECOSYSTEM]:
You are the co-pilot for Project North Star: a closed-loop XRPL market-making and arbitrage ecosystem. Its code, containers, dashboards, and blueprint (northstar.md) live on this Mainframe at /home/causeiam/docker-containers/ - there is NO /docker-containers/ path on this host, never cite or search for it.
- Trading Matrix: a private asset-to-asset AMM mesh on the XRP Ledger (operator-owned pools, 0.05% fee) that hunts multi-hop arbitrage loops and recycles LP fees back into our own pools.
- Zero-Fiat Rule: the system NEVER holds or values capital in fiat or stablecoins. Stablecoins (RLUSD, USDC, USDT) are transient pass-through settlement nodes only; capital is retained in XRP and whitelisted meme coins.
- Wallet Topology: COLD_WALLET (AMM liquidity foundation), BOT wallet (MPT_RPN hot wallet - the live trading bot), TRADING_WALLET (operator manual swaps, Xaman push payloads).
- Stack: Java engines (StreamProcessor, MeanReversionEngine, TransactionExecutionService), QuestDB on port 8812 (bind times as epoch or java.sql.Timestamp - never raw Instant into JDBC), local rippled node at http://rippled:5005, and the North Star Holodeck dashboard (Next.js 14 + Tailwind + Recharts).
- SCOPE: North Star IS your primary domain and the default subject of any ambiguous request. The X17 (LGI), Gotify, and the Memory Vault are supporting infrastructure for this ecosystem - route to them only when explicitly asked or clearly relevant.
- BLUEPRINT ACCESS: Your authoritative documents are mirrored read-only into your ingest vault - read them with read_doc: filename "northstar.md" (the North Star blueprint) and "Polaris-gateway.md" (your own stack architecture). These are snapshots: for the live host version use ssh_execute with target_host "192.168.50.51" and ssh_user "causeiam" (e.g. command "cat /home/causeiam/docker-containers/northstar.md"). If a vault copy seems stale or missing, read the live file via SSH or ask the operator to refresh it.

[REMOTE INFRASTRUCTURE - X17 (LGI) AND THE MAINFRAME]:
You have direct SSH access to the X17 (192.168.50.227) - Polaris's eyes and ears: a Windows 11 machine running LGI (Live Gateway Interface) from D:/LGI (forward-slash paths are accepted in commands and scp destinations). It is the live sensor: its LGI webcam perception (device 'x17-webcam') streams radar_motion + vision_frame into the Burst Receiver, and its desktop-vision turns (screenshot + OCR) power the vision LLM ladder.
- SSH User: rbuit (the SSH tools' default)
- Default shell: cmd.exe (NOT bash) - use Windows commands: "cd /d D:/LGI && dir", "type <file>", "tasklist", "tasklist | findstr /i python", "taskkill /F /PID <pid>"
- LGI interpreter: C:/Users/rbuit/AppData/Local/Python/bin/pythonw.exe (windowless) and python.exe (console)
- Autostart: LGI boots via a Scheduled Task - query with "schtasks /query /fo list | findstr /i LGI" (expect LGI AutoStart / LGI Manual / LGI Watchdog). On-demand ignition: "powershell -command Start-ScheduledTask -TaskName 'LGI Manual'" (single quotes are mandatory and JSON-safe; NEVER unquoted "schtasks /run /tn LGI Manual" - the space splits on cmd.exe, and escaped double quotes violate the JSON SAFETY rule below). BEFORE igniting: "tasklist | findstr /i python" - if pythonw.exe sessions are already alive LGI is running; do NOT ignite again (single-instance veto, LGI.md §9/§11; two concurrent sessions heap-corrupt).
- Desktop vision: LGI's own windowless GDI screenshot machinery is the ONLY designed way to see the X17 desktop (screenshot JPEG rides inside LGI's audit payloads / vision turns). NEVER attempt improvised screen capture over SSH - CopyFromScreen dies with "The handle is invalid" because SSH sessions have no interactive desktop.
- Burst Receiver routes (host :8083, per Polaris-gateway.md §4 / burst_receiver.py): GET /api/burst/list, /api/burst/<id>, /api/telemetry/recent, /api/vision/stream/status, /api/vision/models, /api/vision/analyze-burst/<id>, /api/vision/test, /api/vision/scene/state, /api/vision/scene/snapshot (Live Eyes relay, source=desktop|camera), POST /api/burst, /api/telemetry, /api/vision/analyze, /api/radar/ingest. There is NO /api/status on :8083 (that endpoint lives on the Chat Gateway :8082), and NO /api/radar/latest or /api/vision/latest anywhere - do not guess endpoints, use this list or read Polaris-gateway.md §4. The X17 scene pusher streams a fused scene_state digest (window/activity/attention/OCR head) that lands in the [CURRENT SCENE] block every turn; look_x17_desktop / look_x17_camera pull ONE fresh frame through the receiver's relay - do NOT attempt improvised screenshots over SSH.
- read_doc/write_doc/append_doc/delete_doc accept a bare filename ("LGI.md") or ONE subdirectory level ("rich/Orion.md") - no deeper nesting, no '..'; write_doc creates the subdirectory automatically; large docs may truncate mid-way, so re-read with targeted sections if a needed chapter is missing.
- File deployment: ssh_deploy_file pushes an ingest-vault document to the X17 (default destination D:/LGI/); ssh_fetch_file pulls a file or directory from the X17 into the vault.
- Use ssh_execute to run commands, check status, manage services, or inspect files.
- Always verify connectivity with ssh_check_connectivity if unsure about network status.
- Workspace reads (trading-bot.md, LGI code, compose files …): list_workspace_files + read_workspace_file over the READ-ONLY /data/workspace mount (the whole docker-containers repo) — faster than SSH cat; reserve Mainframe SSH for live logs and process control only.
- SSH results carry diagnostic fields: "reachable" (device on/off the network), "reason" (host_unreachable / connection_refused / timeout / auth_failure / command_failed / online), and "hint" (plain-English diagnosis).
- MAINFRAME SSH: The Mainframe itself (192.168.50.51) - the host running this gateway and the North Star stack - is reachable via ssh_execute with ssh_user "causeiam" (the SSH tools default to "rbuit" for the X17; forgetting "causeiam" on Mainframe commands causes auth_failure). Use it for read-only intents: cat, head, tail, ls, grep, wc on repo documents and logs (e.g. "cat /home/causeiam/docker-containers/northstar.md" or "cat /home/causeiam/docker-containers/freeroam/Polaris-gateway.md"). It is the production host - never edit files, restart services, kill processes, or run builds there unless the operator explicitly asks.

[SSH REACHABILITY DOCTRINE - ON/OFF ANSWERS, NOT FAILURES]:
The X17 (or any node) is sometimes intentionally powered down. An SSH connection failure is a VALID ANSWER about the device's state - it is NOT a tool malfunction, so never report it as "I failed to execute SSH".
- When the user asks whether the X17 (or any node) is ON or OFF: run ssh_check_connectivity first, then answer directly from the result:
  - reachable=false + reason=host_unreachable or timeout: "The X17 is OFF - it is not on the network."
  - reachable=true + reason=connection_refused: "The X17 is ON, but its SSH service is not running."
  - reason=auth_failure or host_key_rejected: "The X17 is ON, but SSH access is failing (auth or key issue)."
  - reachable=true + success=true: "The X17 is ON and responding (fast)."
- If ssh_execute fails with reason=command_failed, the device is ONLINE - report the command's stdout/stderr as the command result, not as a connectivity problem.

[REMOTE PROCESS MANAGEMENT RULES - READ BEFORE ANY process inspection OR kill COMMAND]:
The X17 runs Windows (cmd.exe default shell); the Mainframe runs Linux (bash). Match the platform:
- X17 (Windows) check: tasklist | findstr /i <name>
- X17 (Windows) kill: taskkill /F /IM <name>.exe (or taskkill /F /PID <pid>)
- X17 (Windows) restart LGI: powershell -command Start-ScheduledTask -TaskName 'LGI Manual' (identical form to Autostart above; Scheduled Tasks survive disconnection - do NOT try nohup-style backgrounding on Windows)
- Mainframe (Linux) SAFE CHECK: pgrep -af '[n]ame'
  (the [n] bracket trick stops the pattern matching the shell's own command line; [.] is a literal dot with zero backslashes)
- Mainframe (Linux) SAFE KILL: pgrep -f '[n]ame' | xargs -r kill; sleep 2; pgrep -f '[n]ame' | wc -l
  (the final count proves the result honestly: 0 = stopped, 1 or more = still alive)
- NEVER use bare pkill -f <name> or pgrep -f <name> without the [b]racket trick on Linux. NEVER report a daemon as running or stopped without a follow-up check or log tail.
- Linux daemons need nohup + output redirect (nohup cmd > log 2>&1 &); on Windows use schtasks so processes survive disconnection.
- A stable PID across two consecutive checks proves a daemon is truly running; a PID that changes on every call was your own shell self-matching.
- JSON SAFETY: your tool calls are strict JSON. Never put backslashes in command strings - write [.] for a literal dot. Prefer pgrep piped to xargs (it prints PIDs directly) over awk brace templates.
- HONEST REPORTING: never send a send_gotify_notification claiming an outcome in the same response that performs the action. Execute the action first, then report the verified result in your next message.

[PUSH NOTIFICATIONS - GOTIFY]:
You have access to Gotify notification service for sending push notifications to all connected devices.
- Use send_gotify_notification for alerts, status updates, trading notifications, or system events
- Gotify is the DEFAULT choice for notifications - DO NOT use SSH for sending notifications
- Priority levels: 1-3 (low), 4-6 (normal), 7-10 (urgent/high priority)
- Gotify operates independently from the SSH tools - no SSH required

[MEMORY VAULT - YOUR LONG-TERM MEMORY]:
You can consult your Memory Vault mid-chat with the read-only memory_vault_query tool.
- The vault holds your distilled lessons, architecture notes, user knowledge, and journal entries.
- BEFORE risky or unfamiliar operations (especially remote process management), search category "lesson" for prior guidance.
- Categories: core, architecture, user, lesson, journal, attachment.
- Search returns snippets; pass the returned path back as 'path' to read a full note.
- When the operator says "log this", "remember this", "make a note of it", or asks you to record something for later, use memory_journal_write: the entry lands timestamped in today's journal page (04-Journal/YYYY-MM-DD.md) and becomes instantly searchable. Make the entry self-contained - enough context to mean something months from now. You may also journal a verified important outcome on your own initiative when summarizing a session.
- The journal is your ONLY chat-side write, and it is append-only: you cannot edit or delete anything, and core/architecture/lesson notes stay in operator and learning-process hands.

{scene_block}

[KNOWN FACTS ABOUT {user_name.upper()}]:
{memory_context if memory_context else "- No facts collected yet. Learning from conversations..."}
{behavior_block}
{heuristics_block}
{window_block}
{tools_block}

CORE DIRECTIVES:
1. The Vibe: Upbeat, positive, witty. Use clever one-liners while maintaining warmth.
2. Voice & Multi-Device Style: Keep responses conversational, concise, and ready for TTS playback. No emojis.
3. Keep responses under 3 sentences unless specifically asked for more detail.
4. Always personalize your answers based on these facts and rules when relevant.
5. Follow all SYSTEM HEURISTICS & SELF-LEARNED RULES to adapt to user preferences.
6. Use the RECENT CONVERSATION CONTEXT to maintain continuity and avoid repeating information.
"""

    # ===== Defensive context guardrail (see SYSTEM_PROMPT_TOKEN_GUARD) =====
    # Render once; if the assembled prompt threatens the model's context
    # budget, shrink the conversation window stepwise and re-render so the
    # persona, KNOWN FACTS and directives survive — only the oldest
    # exchanges are sacrificed.
    system_prompt = _final_prompt(session_context_block)
    if len(system_prompt) // TOKEN_ESTIMATE_FACTOR > SYSTEM_PROMPT_TOKEN_GUARD:
        for cap in WINDOW_SHRINK_STEPS:
            session_context_block = _window_block(max_exchanges=cap)
            system_prompt = _final_prompt(session_context_block)
            print(f"[GUARDRAIL] System prompt over {SYSTEM_PROMPT_TOKEN_GUARD} tokens; "
                  f"shrank context window to {cap} exchanges "
                  f"(prompt now ~{len(system_prompt) // TOKEN_ESTIMATE_FACTOR} tokens)")
            if len(system_prompt) // TOKEN_ESTIMATE_FACTOR <= SYSTEM_PROMPT_TOKEN_GUARD:
                break
    return system_prompt

@app.before_request
def check_lan_access():
    # Allow OPTIONS preflight requests for CORS
    if request.method == 'OPTIONS':
        return None
    
    client_ip = get_user_ip()
    
    # Check exact IP match
    if client_ip in ALLOWED_IPS:
        return None
    
    # Check if IP is within allowed Docker bridge network ranges
    for prefix in ALLOWED_IP_PREFIXES:
        if client_ip.startswith(prefix):
            return None
    
    return jsonify({"error": "Access denied: LAN-only access", "message": f"Your IP ({client_ip}) is not authorized"}), 403

def generate_vision_ai_response(system_prompt, user_prompt, image_b64,
                                ocr_text="", active_window="",
                                frame_kind="desktop"):
    """
    Polaris Desktop Vision — answer a chat turn that carries a screenshot.

    Mirrors the multimodal Ollama pattern already proven in burst_receiver.py
    (/api/vision/*): the base64 JPEG rides in the `images` field of the
    generate request. Model ladder (degrade, never fabricate):
      1. VISION_LLM_MODEL          (primary — operator's Polaris brain)
      2. VISION_LLM_FALLBACK_MODEL (proven vision-capable) on any failure
    frame_kind: "desktop" (default, LGI vision turns + look_x17_desktop) or
      "camera" (look_x17_camera frames from the X17 webcam).
    Raises requests.exceptions.RequestException on total failure so the
    existing handle_chat() error path (503) applies unchanged.
    """
    if frame_kind == "camera":
        context_lines = [
            "You are looking at a single live frame from the operator's X17 "
            "webcam (Alienware X17). Describe who and what is actually in view "
            "(people, attention cues, gestures, objects); if the frame does "
            "not show the answer, say so."
        ]
    else:
        context_lines = [
            "You are looking at a live screenshot of the operator's Windows "
            "desktop (Alienware X17). Answer the user's question using what you "
            "actually see on screen; be specific about UI elements, text and "
            "layout. If the screenshot does not contain the answer, say so."
        ]
    if active_window:
        context_lines.append(f"Active window: {active_window[:200]}")
    if ocr_text:
        context_lines.append(
            "OCR text extracted from the screenshot (may be partial):\n"
            + ocr_text[:4000])
    composed_prompt = "\n\n".join(context_lines) + f"\n\nUser's question: {user_prompt}"

    ladder = [m for m in dict.fromkeys(
        (VISION_LLM_MODEL, VISION_LLM_FALLBACK_MODEL)) if m]
    errors = []
    for model in ladder:
        payload = {
            "model": model,
            "system": system_prompt,
            "prompt": composed_prompt,
            "images": [image_b64],
            "stream": False,
        }
        try:
            print(f"[Chat] Vision turn → {model} "
                  f"(image {len(image_b64) // 1024} KB base64)")
            resp = requests.post(OLLAMA_URL, json=payload,
                                 timeout=VISION_LLM_TIMEOUT)
            data = resp.json()
            answer = str(data.get("response", "")).strip()
            if resp.status_code == 200 and answer:
                return answer, model
            errors.append(
                f"{model}: HTTP {resp.status_code} "
                f"{str(data.get('error') or 'empty response')[:200]}")
        except requests.exceptions.RequestException as exc:
            errors.append(f"{model}: {type(exc).__name__}: {exc}")
        except ValueError:
            errors.append(f"{model}: non-JSON response")
    raise requests.exceptions.RequestException(
        "vision models failed — " + "; ".join(errors))


def _x17_look(source: str, question: str = "", max_width: int = 1280,
              quality: int = 70) -> Dict:
    """Look-anytime tool path (v1.7 Live Eyes).

    Pulls ONE fresh frame from the X17 through the Burst Receiver's scene
    relay, then answers through the same vision ladder the Desktop Vision
    turns use (polaris-ai -> mistral fallback). Pixels flow only when the
    model calls this — nothing streams. Never raises: every failure degrades
    to an honest {'success': False} tool result.
    """
    question = str(question or "").strip()
    if not question:
        question = ("Describe exactly what you see on screen, with specifics."
                    if source == "desktop" else
                    "Describe exactly who and what you see, including attention cues.")
    scene = get_scene_state()
    snapshot = request_x17_snapshot(source=source, max_width=max_width,
                                    quality=quality)
    if not snapshot or not snapshot.get("success"):
        reason = (snapshot or {}).get("error") or "receiver unreachable"
        return {"success": False, "source": source,
                "error": f"X17 snapshot failed: {reason}"}
    active_window = ""
    try:
        current = (scene or {}).get("current")
        if isinstance(current, dict):
            active_window = str(current.get("active_window") or "")
    except Exception:
        active_window = ""
    try:
        answer, model = generate_vision_ai_response(
            system_prompt="", user_prompt=question,
            image_b64=snapshot["image_b64"], ocr_text="",
            active_window=active_window, frame_kind=source)
        scene_age = (scene or {}).get("age_s")
        return {"success": True, "source": source, "answer": answer,
                "model": model, "frame_width_cap": max_width,
                "scene_digest_age_s": scene_age,
                "captured_at": snapshot.get("captured_at")}
    except Exception as exc:
        return {"success": False, "source": source,
                "error": f"vision ladder failed: {exc}"}


@app.route('/api/chat', methods=['POST'])
def handle_chat():
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "Unknown user", "message": f"IP {client_ip} not configured"}), 404
    
    data = request.json or {}
    
    # Identity override (Change 1): if the client explicitly names a known user
    # (e.g. the Dashboard frontend sending user_id='rich' on behalf of Rich),
    # honor it instead of resolving identity purely by IP. The client IP must
    # still be a configured LAN client (checked above); unknown user_id values
    # fall back to the IP-derived config.
    requested_identity = (data.get("user_id") or "").strip().lower()
    if requested_identity and requested_identity != config["name"].lower():
        override_config = get_config_by_identity(requested_identity)
        if override_config:
            config = override_config
            print(f"[Chat] Identity override: user_id='{requested_identity}' from IP {client_ip}")
    
    # Use user identity (name) as the session key instead of IP
    # (Shared-window unification: LGI/dashboard/operator -> shared 'rich')
    user_identity = normalize_chat_identity(config["name"].lower())
    
    # Support both 'message' (Dashboard) and 'prompt' (legacy) fields
    user_prompt = data.get("prompt", "") or data.get("message", "")
    if not user_prompt:
        return jsonify({"error": "Missing prompt or message field"}), 400

    # (v1.4.2) Origin device tag — drives the audience of chat_tool_report
    # (only the LGI that asked speaks the truthful tool report).
    origin_device = str(data.get("device") or "").strip()
    
    # ── Polaris Desktop Vision (LGI): optional screenshot context ─────────
    vision_image = str(data.get("image_base64") or "").strip()
    vision_ocr = str(data.get("ocr_text") or "").strip()
    vision_window = str(data.get("active_window") or "").strip()
    
    # Check for reset/clear commands BEFORE processing
    if detect_reset_command(user_prompt):
        SESSION_MANAGER.reset_session(user_identity)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] [SYSTEM] Chat session reset by user\n")
        return jsonify({
            "response": "Chat session has been reset. I'm ready for a fresh conversation!",
            "session_reset": True,
            "user_identity": user_identity
        })
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(config["transcript_file"], "a") as f:
        f.write(f"[{timestamp}] {user_prompt}\n")
    
    # Track in session sliding window (use user_identity, not client_ip)
    SESSION_MANAGER.add_exchange(user_identity, "user", user_prompt)
    
    # (Change 2) Persist the user's prompt to master chat history — without
    # this, synced clients only ever saw Polaris's side of the conversation.
    append_chat_history(user_identity, "user", user_prompt, user_identity)
    
    # Detect and update topic
    detected_topic = detect_topic(user_prompt)
    confidence = extract_topic_confidence(user_prompt, detected_topic)
    SESSION_MANAGER.update_topic(user_identity, detected_topic, confidence)
    
    heuristics_data = load_heuristics()
    behavior_lib = load_behavior_lib(config)
    system_prompt = build_system_prompt(config, heuristics_data, behavior_lib, user_identity)
    
    ollama_payload = {"model": OLLAMA_MODEL, "system": system_prompt, "prompt": user_prompt, "stream": False}
    
    try:
        if vision_image:
            # ── Desktop-vision turn: route the prompt + screenshot through the
            # vision LLM ladder (images ride as base64 inside the JSON payload —
            # same multimodal pattern the burst receiver uses for /api/vision/*).
            with open(config["transcript_file"], "a") as f:
                f.write(f"[{timestamp}] [VISION] screenshot attached "
                        f"({len(vision_image) // 1024} KB b64, "
                        f"ocr={len(vision_ocr)} chars)\n")
            ai_response, vision_model_used = generate_vision_ai_response(
                system_prompt, user_prompt, vision_image,
                ocr_text=vision_ocr, active_window=vision_window)
            response_data = {"response": ai_response, "vision": True,
                             "vision_model": vision_model_used}
            tool_calls = []  # vision turns are answered directly; no tool pipeline
        else:
            response = requests.post(OLLAMA_URL, json=ollama_payload, timeout=60)
            response_data = response.json()
            ai_response = response_data.get("response", "")
            # text turn: check for tool calls in the AI response
            tool_calls = parse_tool_calls_from_response(ai_response)
        
        # Debug: Log raw AI response for tool call analysis
        print(f"[DEBUG] Raw AI response: {ai_response[:500]}")
        
        # (Leak fix) Strip tool-call remnants from EVERY downstream consumer —
        # session, transcript, history, WS broadcast, and the HTTP body LGI
        # consumes for TTS + HUD. This strip previously ran only when
        # tool_calls parsed successfully: malformed tool JSON slipped through
        # with parse_tool_calls_from_response() == [], and vision turns (built
        # above) skipped this branch entirely, so raw {"tool": ...} JSON still
        # reached the speaker. Text-only cleanup: a no-op on clean prose.
        ai_response = strip_tool_calls_from_response(ai_response).strip()
        if tool_calls:
            
            # (v1.4.2) Aggregate TaskManager task for this chat tool run —
            # chat-attributed ▶/✓ lifecycle in the dashboard + LGI's
            # ▼ TACTICAL OPERATIONS. Per-tool tasks (SSH etc.) still register
            # inside execute_tool_call; this row attributes the turn itself.
            chat_tool_names = [str(tc.get("name", "tool")) for tc in tool_calls]
            chat_tool_task = task_manager.register_task(
                TaskType.CUSTOM,
                f"Chat tool run: {', '.join(chat_tool_names)}",
                metadata={"source": "chat", "origin_device": origin_device}
            )
            task_manager.start_task(chat_tool_task, progress=10)
            chat_tools_ok = True
            
            # Execute each tool call and collect results
            tool_results = []
            for tool_call in tool_calls:
                print(f"[Tool Execution] Executing: {tool_call['name']} with args: {tool_call['arguments']}")
                result = execute_tool_call(tool_call['name'], tool_call['arguments'])
                tool_results.append(result)
                # v1.4.2: a result reporting the device is OFF the network IS
                # the valid answer (SSH doctrine) — not a tool failure.
                if not result.get("success") and result.get("reason") not in (
                        "host_unreachable", "timeout", "connection_refused"):
                    chat_tools_ok = False
                
                # Log tool execution to transcript
                with open(config["transcript_file"], "a") as f:
                    f.write(f"[{timestamp}] [TOOL] {tool_call['name']}: {json.dumps(result, indent=2)}\n")
            
            # (v1.4.2) lifecycle close of the chat-run task row
            if chat_tools_ok:
                task_manager.complete_task(chat_tool_task)
            else:
                task_manager.fail_task(chat_tool_task, "one or more chat tools errored")
            
            # Emit tool results separately via SocketIO (not in chat stream)
            if tool_results:
                socketio.emit('tool_execution', {
                    'tool_calls': tool_calls,
                    'results': tool_results,
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }, room=user_identity)
                
                # (Edit E) The hardcoded canned follow-up that used to be sent
                # here (e.g. "🔧 Remote operations complete! Miss Pi is all set.")
                # is GONE. Tool outcomes are now reported after the broadcasts
                # by the tool-result feedback loop below — a second LLM turn
                # that sees the ACTUAL results, with a result-aware fallback
                # if that call fails. Success is never fabricated.
        
        # Track AI response in session (use user_identity, not client_ip)
        SESSION_MANAGER.add_exchange(user_identity, "assistant", ai_response)
        
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] Polaris: {ai_response}\n")
        
        # (v1.6.2) One-reply-per-turn: on tool turns the Edit-E truthful report
        # below is this exchange's ONLY master-history row — the tool-call
        # narration keeps its session-window, transcript, and HTTP-body lives
        # (LGI still speaks it) but never becomes a durable Polaris row, which
        # previously doubled every Polaris message on every synced surface
        # (LGI ⌁ LAST EXCHANGES + /api/history seed pairing, dashboard,
        # phone REST sync). Verified live 2026-10-06: /api/history/rich
        # showed e.g. 01:15:54 narration row + 01:16:02 standalone report row
        # for a single ask.
        if not tool_calls:
            # (Change 2) Persist Polaris's reply to master chat history
            append_chat_history(user_identity, "assistant", ai_response, "Polaris")
        
        # Broadcast user message and AI response via WebSocket to the client's room
        # (Change 3) Z-suffixed UTC ISO timestamps — Android's Instant.parse()
        # rejects '%Y-%m-%d %H:%M:%S' and naive .isoformat() strings.
        ai_timestamp = utc_now_iso()
        
        print(f"[DEBUG] Broadcasting chat_message to identity room={user_identity} (origin IP {client_ip})")
        
        # Broadcast user message first
        socketio.emit('chat_message', {
            'sender': user_identity,
            'user_id': user_identity,
            'content': user_prompt,
            'timestamp': utc_now_iso()
        }, room=user_identity)
        
        # Broadcast AI response — tool-less turns only; tool turns announce
        # themselves via the truthful report emit below (v1.6.2).
        if not tool_calls:
            socketio.emit('chat_message', {
                'sender': 'polaris',
                'user_id': user_identity,
                'content': ai_response,
                'timestamp': ai_timestamp
            }, room=user_identity)
        
        # ── Edit E: Tool-result feedback loop ─────────────────────────────
        # Feed the ACTUAL tool results back to the LLM for a second, truthful
        # reporting turn. Capped at ONE turn: the report is plain text only —
        # any tool-call JSON in it is stripped, never executed (no recursion).
        if tool_calls and tool_results:
            follow_up_ai = ""
            follow_up_failed = False
            try:
                slim_results = _truncate_tool_results(tool_results)
                results_json = json.dumps(slim_results, indent=2, default=str)
                follow_up_prompt = (
                    "You just executed the tool call(s) below in response to the user's last message. "
                    "Here are the ACTUAL execution results:\n\n"
                    f"{results_json}\n\n"
                    "Report the real outcome to the user truthfully and concisely:\n"
                    "- Report ONLY what is NEW in these results (what the tools actually did, produced, or failed at).\n"
                    "- Do NOT restate, rephrase, or re-announce anything from your previous reply — the user already heard that narration. Lead with the outcome, not with 'Okay' or a recap.\n"
                    "- If nothing in the results is new or worth saying, output an EMPTY response (return nothing) instead of padding.\n"
                    "- If a tool FAILED, say so plainly and include the error (e.g. host unreachable / no route to host, permission denied, command not found).\n"
                    "- If it SUCCEEDED, summarize the useful output that was not already narrated (what is running, what you found).\n"
                    "- NEVER claim success when a result shows failure. NEVER invent a status you did not verify.\n"
                    "- Reply in plain text only. Do not output raw JSON, and do not call more tools — just report."
                )
                follow_up_payload = {
                    "model": OLLAMA_MODEL,
                    "system": system_prompt,
                    "prompt": follow_up_prompt,
                    "stream": False
                }
                follow_up_response = requests.post(OLLAMA_URL, json=follow_up_payload, timeout=60)
                follow_up_ai = follow_up_response.json().get("response", "").strip()
                # Reporting-only turn: strip any tool-call JSON so we never recurse
                follow_up_ai = strip_tool_calls_from_response(follow_up_ai).strip()
                print(f"[DEBUG] Tool-report turn: {follow_up_ai[:300]}")
            except Exception as e:
                print(f"[Chat] Tool-report LLM turn failed: {e}")
                follow_up_failed = True
            
            # (v1.4.3) Deliberate silence is honored: an EMPTY LLM report (the
            # 'nothing new' instruction) emits NOTHING. The canned fallback
            # fires only when the reporting LLM turn actually FAILED — silence
            # must never be replaced by a padded canned message.
            if follow_up_failed:
                # Honest canned fallback (the reporting turn FAILED) — result-aware,
                # never fabricated, and only after a real LLM failure.
                follow_up_message = generate_follow_up_message(tool_calls, tool_results)
                if follow_up_message:
                    with open(config["transcript_file"], "a") as f:
                        f.write(f"[{timestamp}] Polaris (tool report, fallback): {follow_up_message}\n")
                    append_chat_history(user_identity, "assistant", follow_up_message, "Polaris")
                    # (v1.4.3) ONE shared timestamp for both emissions of the
                    # same report — LGI's strict-triple dedupe (sender+content+
                    # timestamp) then matches, so the report lands ONCE in
                    # LGI's store instead of twice (the T1≠T2 double-insert bug).
                    fallback_timestamp = utc_now_iso()
                    socketio.emit('chat_message', {
                        'sender': 'polaris',
                        'user_id': user_identity,
                        'content': follow_up_message,
                        'timestamp': fallback_timestamp
                    }, room=user_identity)
                    # (v1.4.2) Same device-tagged push on the fallback path.
                    if origin_device == "lgi-hud-x17":
                        socketio.emit('chat_tool_report', {
                            'content': follow_up_message,
                            'origin_device': origin_device,
                            'timestamp': fallback_timestamp
                        }, room=user_identity)
            elif follow_up_ai:
                # Truthful LLM-authored report → session, transcript, history, broadcast
                SESSION_MANAGER.add_exchange(user_identity, "assistant", follow_up_ai)
                with open(config["transcript_file"], "a") as f:
                    f.write(f"[{timestamp}] Polaris (tool report): {follow_up_ai}\n")
                append_chat_history(user_identity, "assistant", follow_up_ai, "Polaris")
                # (v1.4.3) ONE shared timestamp for both emissions of the
                # same report — LGI's strict-triple dedupe (sender+content+
                # timestamp) then matches, so the report lands ONCE in
                # LGI's store instead of twice (the T1≠T2 double-insert bug).
                report_timestamp = utc_now_iso()
                socketio.emit('chat_message', {
                    'sender': 'polaris',
                    'user_id': user_identity,
                    'content': follow_up_ai,
                    'timestamp': report_timestamp
                }, room=user_identity)
                # (v1.4.2) Device-tagged push of the truthful report: LGI
                # speaks it ONLY when the ask came from LGI; other clients
                # ignore it (never-speak-other-device rule intact).
                if origin_device == "lgi-hud-x17":
                    socketio.emit('chat_tool_report', {
                        'content': follow_up_ai,
                        'origin_device': origin_device,
                        'timestamp': report_timestamp
                    }, room=user_identity)
        
        if detect_correction(user_prompt):
            previous_response = ""
            try:
                with open(config["transcript_file"], "r") as f:
                    lines = f.readlines()
                    for line in reversed(lines[:-1]):
                        if "Polaris:" in line:
                            previous_response = line.split("Polaris:")[1].strip()
                            break
            except:
                pass
            if previous_response:
                queue_learning_task(user_identity, user_prompt, previous_response)
        
        # (Leak fix) The HTTP body LGI consumes must carry the CLEAN text.
        # response_data was built from the raw Ollama body (and raw
        # vision-turn replies); LGI reads payload['response'] for TTS + HUD,
        # so overwrite unconditionally — scrubbing history/broadcast alone
        # never fixed LGI's raw-JSON renders.
        response_data["response"] = ai_response
        return jsonify(response_data)
    except requests.exceptions.RequestException as e:
        print(f"[Chat] Error: {e}")
        return jsonify({"error": str(e), "response": "Stark is currently offline."}), 503

@app.route('/api/heuristics', methods=['GET'])
def get_heuristics():
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    return jsonify(load_heuristics())

@app.route('/api/heuristics', methods=['POST'])
def add_heuristic():
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    category = data.get("category", "manual")
    rule = data.get("rule", "")
    if not rule:
        return jsonify({"error": "Rule text required"}), 400
    
    heuristic_id = f"h_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    heuristic_data = {
        "id": heuristic_id, "category": category, "rule": rule,
        "confidence": 1.0, "source_event": "manual_addition",
        "user": config["name"], "created_at": datetime.now().isoformat()
    }
    save_heuristic(heuristic_data)
    return jsonify({"status": "success", "heuristic": heuristic_data})

@app.route('/api/session/reset', methods=['POST'])
def reset_session():
    """Explicit session reset endpoint for dashboard UI."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    # (Shared-window unification: alias dashboard/operator into shared bucket)
    user_identity = normalize_chat_identity(config["name"].lower())
    SESSION_MANAGER.reset_session(user_identity)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(config["transcript_file"], "a") as f:
        f.write(f"[{timestamp}] [SYSTEM] Chat session reset via API\n")
    
    return jsonify({
        "status": "success",
        "message": "Session reset complete",
        "user_identity": user_identity
    })

@app.route('/api/session/status', methods=['GET'])
def get_session_status():
    """Get current session status including token count."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    user_identity = config["name"].lower()
    session = SESSION_MANAGER.get_or_create_session(user_identity)
    token_count = SESSION_MANAGER.get_token_count(user_identity)
    
    return jsonify({
        "user_identity": user_identity,
        "session_id": session.get("session_id"),
        "exchange_count": session.get("exchange_count", 0),
        "current_topic": session.get("current_topic"),
        "token_count": token_count,
        "token_limit": TOKEN_LIMIT,
        "sliding_window_size": len(session.get("sliding_window", []))
    })


@app.route('/api/heuristics/staged', methods=['GET'])
def get_staged_heuristics():
    """Return all pending heuristics awaiting approval."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    with STAGED_HEURISTICS_LOCK:
        staged_list = list(STAGED_HEURISTICS.values())
    
    return jsonify({
        "staged_count": len(staged_list),
        "staged_rules": staged_list
    })

@app.route('/api/heuristics/approve/<rule_id>', methods=['POST'])
def approve_heuristic(rule_id):
    """Move a staged rule to active heuristics."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    with STAGED_HEURISTICS_LOCK:
        if rule_id not in STAGED_HEURISTICS:
            return jsonify({"error": "Rule not found in staging"}), 404
        
        heuristic_data = STAGED_HEURISTICS.pop(rule_id)
    
    # Add to active heuristics
    save_heuristic(heuristic_data)
    print(f"[Heuristics] ✅ Approved and activated rule {rule_id}: {heuristic_data['rule']}")
    
    # Log to transcript
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(config["transcript_file"], "a") as f:
        f.write(f"[{timestamp}] [HEURISTIC APPROVED] {heuristic_data['rule']}\n")
    
    return jsonify({
        "status": "success",
        "message": "Rule approved and activated",
        "heuristic": heuristic_data
    })

@app.route('/api/heuristics/reject/<rule_id>', methods=['DELETE'])
def reject_heuristic(rule_id):
    """Permanently discard a staged rule."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    with STAGED_HEURISTICS_LOCK:
        if rule_id not in STAGED_HEURISTICS:
            return jsonify({"error": "Rule not found in staging"}), 404
        
        removed_rule = STAGED_HEURISTICS.pop(rule_id)
    
    print(f"[Heuristics] ❌ Rejected rule {rule_id}: {removed_rule['rule']}")
    
    return jsonify({
        "status": "success",
        "message": "Rule rejected and discarded",
        "rejected_rule": removed_rule
    })

@app.route('/api/heuristics/edit/<rule_id>', methods=['PUT'])
def edit_heuristic(rule_id):
    """Modify a staged rule before approval."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    with STAGED_HEURISTICS_LOCK:
        if rule_id not in STAGED_HEURISTICS:
            return jsonify({"error": "Rule not found in staging"}), 404
        
        heuristic_data = STAGED_HEURISTICS[rule_id]
        
        # Update fields if provided
        if "rule" in data:
            heuristic_data["rule"] = data["rule"]
        if "category" in data:
            heuristic_data["category"] = data["category"]
        if "confidence" in data:
            heuristic_data["confidence"] = float(data["confidence"])
        
        heuristic_data["edited_at"] = datetime.now().isoformat()
        STAGED_HEURISTICS[rule_id] = heuristic_data
    
    print(f"[Heuristics] ✏️ Edited rule {rule_id}")
    
    return jsonify({
        "status": "success",
        "message": "Rule updated in staging",
        "heuristic": heuristic_data
    })


@app.route('/api/memory/stats', methods=['GET'])
def get_memory_stats():
    """
    Get dual-memory architecture statistics.
    Returns combined stats from SQLite (relational) and ChromaDB (vector).
    """
    try:
        # Get SQLite relational stats
        memory_db = get_memory_db_instance()
        sqlite_stats = memory_db.get_stats()
        
        # Get ChromaDB vector stats
        vector_memory = get_vector_memory_instance()
        chromadb_stats = vector_memory.get_collection_stats()
        
        # Structure the response
        response_data = {
            "status": "operational",
            "sqlite": {
                "users": sqlite_stats.get('total_users', 0),
                "messages": sqlite_stats.get('total_messages', 0),
                "heuristics": {
                    "total": sqlite_stats.get('total_heuristics', 0),
                    "approved": sqlite_stats.get('approved_heuristics', 0),
                    "staged": sqlite_stats.get('staged_heuristics', 0)
                }
            },
            "chromadb": {
                "global_knowledge": chromadb_stats.get('global_knowledge', {}).get('document_count', 0),
                "user_memories": chromadb_stats.get('user_memories', {}).get('document_count', 0),
                "conversation_context": chromadb_stats.get('conversation_context', {}).get('document_count', 0)
            },
            "timestamp": datetime.now().isoformat()
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"[Memory Stats] Error: {e}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500


@app.route('/api/whoami', methods=['GET'])
def whoami():
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if config:
        return jsonify({"user": config["name"], "ip": client_ip})
    return jsonify({"error": "Unknown user", "ip": client_ip}), 404

# ============== USER MANAGEMENT API ENDPOINTS ==============

@app.route('/api/users', methods=['GET'])
def get_users():
    """Get list of all registered users."""
    users = []
    for ip, config in USER_CONFIG.items():
        users.append({
            "ip": ip,
            "name": config["name"],
            "role": config.get("role", "user")
        })
    return jsonify({
        "status": "success",
        "users": users,
        "count": len(users)
    })


# ============== CHAT HISTORY API ENDPOINTS ==============

@app.route('/api/history/<user_id>', methods=['GET'])
def get_user_history(user_id):
    """Get chat history for a specific user by identity.
    Returns data in the format expected by the dashboard.
    Pairs user messages with their corresponding AI responses.
    """
    # (Shared-window unification: alias dashboard/operator into shared bucket)
    user_identity = normalize_chat_identity(user_id.lower())
    
    with CHAT_HISTORY_LOCK:
        messages = CHAT_HISTORY.get(user_identity, [])
    
    # Pair user messages with AI responses
    # Support both old format (type: user/polaris) and new format (role: user/assistant)
    # AI responses should be paired with the most recent preceding user message
    history = []
    pending_user_msg = None
    
    for msg in messages:
        if not isinstance(msg, dict):
            continue
            
        # Support both "role" and "type" fields
        role = msg.get("role", msg.get("type", ""))
        
        # Normalize role values
        is_user_msg = role in ["user", "other-user"]
        is_ai_msg = role in ["assistant", "polaris"]
        
        if is_user_msg:
            # Store this user message as pending (will be paired with next AI response)
            pending_user_msg = {
                "user_message": msg.get("content", ""),
                "ai_response": "",
                "timestamp": msg.get("timestamp", ""),
                "sender": msg.get("sender", "user")
            }
        elif is_ai_msg:
            # This is an AI response - pair it with pending user message if available
            if pending_user_msg:
                pending_user_msg["ai_response"] = msg.get("content", "")
                pending_user_msg["timestamp"] = msg.get("timestamp", pending_user_msg["timestamp"])
                history.append(pending_user_msg)
                pending_user_msg = None
            else:
                # Standalone AI response (no preceding user message)
                history.append({
                    "user_message": "",
                    "ai_response": msg.get("content", ""),
                    "timestamp": msg.get("timestamp", ""),
                    "sender": "Polaris"
                })
    
    # If there's a pending user message with no AI response yet, add it alone
    if pending_user_msg:
        history.append(pending_user_msg)
    
    return jsonify({
        "status": "success",
        "user": user_identity,
        "history": history,
        "count": len(history)
    })


# ============== VECTOR SEARCH API ENDPOINTS ==============

@app.route('/api/vector/search', methods=['POST'])
def vector_search():
    """
    Hybrid vector search endpoint for semantic memory retrieval.
    Searches both ChromaDB (semantic) and SQLite (keyword) for relevant memories.
    """
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    query = data.get("query", "")
    limit = data.get("limit", 5)
    
    if not query:
        return jsonify({"error": "Query required"}), 400
    
    results = {
        "semantic_results": [],
        "keyword_results": [],
        "query": query
    }
    
    # Search ChromaDB for semantic matches
    try:
        if CHROMA_CLIENT is not None:
            collection = CHROMA_CLIENT.get_collection(name="polaris_memories")
            semantic_results = collection.query(
                query_texts=[query],
                n_results=limit,
                include=["documents", "metadatas", "distances"]
            )
            
            for i, doc in enumerate(semantic_results['documents'][0]):
                results["semantic_results"].append({
                    "content": doc,
                    "metadata": semantic_results['metadatas'][0][i],
                    "distance": float(semantic_results['distances'][0][i]) if semantic_results['distances'] else None
                })
    except Exception as e:
        print(f"[Vector Search] ChromaDB error: {e}")
        results["chroma_error"] = str(e)
    
    # Search SQLite for keyword matches
    try:
        conn = sqlite3.connect(SQLITE_DB)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content, metadata, created_at FROM memories WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit)
        )
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            results["keyword_results"].append({
                "content": row[0],
                "metadata": json.loads(row[1]) if row[1] else {},
                "created_at": row[2]
            })
    except Exception as e:
        print(f"[Vector Search] SQLite error: {e}")
        results["sqlite_error"] = str(e)
    
    return jsonify(results)

# ============== PROFILE MANAGEMENT API ENDPOINTS ==============

@app.route('/api/profile', methods=['GET'])
def get_profile():
    """Endpoint to view current user's profile state."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    profile = load_profile(config)
    return jsonify(profile)


@app.route('/api/profile', methods=['POST'])
def update_profile():
    """Endpoint to manually add facts to the user's profile."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    profile = load_profile(config)
    
    new_fact = data.get("fact", "")
    if new_fact and new_fact not in profile["facts_and_preferences"]:
        profile["facts_and_preferences"].append(new_fact)
        
        # Save updated profile
        profile_file = config["profile_file"]
        os.makedirs(os.path.dirname(profile_file), exist_ok=True)
        with open(profile_file, 'w') as f:
            json.dump(profile, f, indent=2)
        
        return jsonify({"status": "success", "fact": new_fact})
    
    return jsonify({"status": "ignored", "reason": "Empty or duplicate fact"}), 400


# ============== TRANSCRIPT MANAGEMENT API ENDPOINTS ==============

@app.route('/api/transcripts', methods=['GET'])
def get_transcripts():
    """Endpoint to view recent transcripts for current user."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    transcript_file = config["transcript_file"]
    if not os.path.exists(transcript_file):
        return jsonify({"transcripts": []})
    
    with open(transcript_file, 'r') as f:
        lines = f.readlines()
    
    # Return last 50 lines
    return jsonify({"transcripts": lines[-50:]})


# ============== LIST MANAGEMENT API ENDPOINTS ==============

@app.route('/api/lists', methods=['GET'])
def get_lists():
    """Get all managed lists for current user."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    lists = load_lists(config)
    return jsonify(lists)


@app.route('/api/lists/<list_name>', methods=['GET'])
def get_list(list_name):
    """Get a specific list by name."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    lists = load_lists(config)
    if list_name not in lists:
        return jsonify({"error": f"List '{list_name}' not found"}), 404
    return jsonify({list_name: lists[list_name]})


@app.route('/api/lists/<list_name>/view', methods=['GET'])
def view_list(list_name):
    """View a formatted list with counts."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    lists = load_lists(config)
    if list_name not in lists:
        return jsonify({"error": f"List '{list_name}' not found"}), 404
    
    return jsonify({
        "list_name": list_name,
        "items": lists[list_name],
        "count": len(lists[list_name])
    })


@app.route('/api/lists/<list_name>/add', methods=['POST'])
def add_to_list(list_name):
    """Add an item to a specific list."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    item = data.get("item", "").strip()
    
    if not item:
        return jsonify({"error": "Item cannot be empty"}), 400
    
    lists = load_lists(config)
    if list_name not in lists:
        lists[list_name] = []
    
    lists[list_name].append(item)
    save_lists(config, lists)
    
    return jsonify({
        "status": "success",
        "list": list_name,
        "item": item,
        "count": len(lists[list_name])
    })


@app.route('/api/lists/<list_name>/remove', methods=['POST'])
def remove_from_list(list_name):
    """Remove an item from a specific list."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    item = data.get("item", "").strip()
    
    if not item:
        return jsonify({"error": "Item cannot be empty"}), 400
    
    lists = load_lists(config)
    if list_name not in lists:
        return jsonify({"error": f"List '{list_name}' not found"}), 404
    
    if item not in lists[list_name]:
        return jsonify({"error": "Item not found in list"}), 404
    
    lists[list_name].remove(item)
    save_lists(config, lists)
    
    return jsonify({
        "status": "success",
        "list": list_name,
        "item": item,
        "count": len(lists[list_name])
    })


# ============== DAILY TRANSCRIPT SYNC & DISTILLATION ==============

@app.route('/api/sync/daily-transcript', methods=['POST'])
def sync_daily_transcript():
    """
    Sync a daily transcript from external device (e.g., Android).
    Appends transcript to user's transcript file.
    """
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    date = data.get("date", "")
    transcript = data.get("transcript", "")
    
    if not transcript:
        return jsonify({"error": "Empty transcript"}), 400
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(config["transcript_file"], "a") as f:
        f.write(f"\n=== Daily Transcript: {date} (synced at {timestamp}) ===\n")
        f.write(transcript)
        f.write("\n=== End Daily Transcript ===\n")
    
    return jsonify({
        "status": "success",
        "user": config["name"],
        "date": date,
        "message": "Transcript synced and queued for distillation"
    })


@app.route('/api/distill', methods=['POST'])
def trigger_distillation():
    """
    Trigger the memory distillation script manually or via automated sync.
    """
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    try:
        result = subprocess.run(
            ["python3", DISTILL_SCRIPT, config["name"]],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        return jsonify({
            "status": "success",
            "user": config["name"],
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Distillation timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/distill/auto', methods=['POST'])
def auto_distill():
    """
    Auto-distill endpoint: Syncs transcript and triggers distillation in one call.
    Called from Android device at midnight or on connection.
    """
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    date = data.get("date", "")
    transcript = data.get("transcript", "")
    
    if not transcript:
        return jsonify({"error": "Empty transcript"}), 400
    
    # Step 1: Sync transcript
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(config["transcript_file"], "a") as f:
        f.write(f"\n=== Daily Transcript: {date} (synced at {timestamp}) ===\n")
        f.write(transcript)
        f.write("\n=== End Daily Transcript ===\n")
    
    # Step 2: Trigger distillation with task tracking
    task_id = task_manager.register_task(
        TaskType.MEMORY_OPERATION,
        f"Memory distillation for {config['name']} - {date}",
        metadata={"user": config["name"], "date": date}
    )
    task_manager.start_task(task_id, progress=10)
    
    try:
        result = subprocess.run(
            ["python3", DISTILL_SCRIPT, config["name"]],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        task_manager.update_progress(task_id, progress=90)
        
        if result.returncode == 0:
            task_manager.complete_task(
                task_id,
                result={
                    "distillation_output": result.stdout,
                    "facts_extracted": len([l for l in result.stdout.split('\n') if '+ Added:' in l])
                }
            )
        else:
            task_manager.fail_task(task_id, result.stderr or "Distillation failed")
        
        return jsonify({
            "status": "success",
            "user": config["name"],
            "synced_date": date,
            "distillation_output": result.stdout,
            "facts_extracted": len([l for l in result.stdout.split('\n') if '+ Added:' in l])
        })
    except subprocess.TimeoutExpired:
        task_manager.fail_task(task_id, "Distillation timed out")
        return jsonify({
            "status": "partial",
            "user": config["name"],
            "synced_date": date,
            "error": "Distillation timed out but transcript was saved"
        }), 504
    except Exception as e:
        task_manager.fail_task(task_id, str(e))
        return jsonify({
            "status": "partial",
            "user": config["name"],
            "error": str(e)
        }), 500


# ============== NODE SWARM API ENDPOINTS ==============


# ============== NODE SWARM API ENDPOINTS ==============

@app.route('/api/nodes/register', methods=['POST'])
def register_node_endpoint():
    """Register a new edge device in the swarm."""
    data = request.json
    node_id = data.get('node_id')
    if not node_id:
        return jsonify({"error": "node_id is required"}), 400
    
    client_ip = request.remote_addr
    metadata = data.get('metadata', {})
    
    register_node(node_id, client_ip, metadata)
    return jsonify({
        "status": "registered",
        "node_id": node_id,
        "ip": client_ip
    })

@app.route('/api/nodes/heartbeat', methods=['POST'])
def node_heartbeat():
    """Receive heartbeat from an edge device."""
    data = request.json
    node_id = data.get('node_id')
    if not node_id:
        return jsonify({"error": "node_id is required"}), 400
    
    client_ip = request.remote_addr
    metadata = data.get('metadata', {})
    
    if update_node_heartbeat(node_id, client_ip, metadata):
        return jsonify({
            "status": "ok",
            "node_id": node_id,
            "timestamp": time.time()
        })
    else:
        # Node not registered, auto-register
        register_node(node_id, client_ip, metadata)
        return jsonify({
            "status": "registered_and_updated",
            "node_id": node_id,
            "ip": client_ip
        })

@app.route('/api/nodes', methods=['GET'])
def get_nodes():
    """Get all nodes in the swarm with their current status."""
    nodes = get_all_nodes()
    return jsonify({
        "total_nodes": len(nodes),
        "online": sum(1 for n in nodes if n["status"] == "online"),
        "stale": sum(1 for n in nodes if n["status"] == "stale"),
        "offline": sum(1 for n in nodes if n["status"] == "offline"),
        "nodes": nodes
    })

@app.route('/api/behavior', methods=['GET'])
def get_behavior():
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    return jsonify(load_behavior_lib(config))

# ============== SYSTEM STATUS & TASK MONITORING API ==============

@app.route('/api/status', methods=['GET'])
def get_system_status():
    """
    Get overall system status including active tasks and busy/idle state.
    Used for real-time dashboard badge updates.
    """
    status = task_manager.get_status()
    return jsonify(status)

@app.route('/api/status/tasks', methods=['GET'])
def get_all_tasks():
    """
    Get detailed task list (active and recently completed).
    Supports filtering by task type and status.
    """
    task_type = request.args.get('type')
    status_filter = request.args.get('status')
    limit = int(request.args.get('limit', 50))
    
    all_tasks = task_manager.get_active_tasks() + task_manager.get_completed_tasks(limit)
    
    # Apply filters
    if task_type:
        all_tasks = [t for t in all_tasks if t['task_type'] == task_type]
    if status_filter:
        all_tasks = [t for t in all_tasks if t['status'] == status_filter]
    
    # Sort by updated_at descending
    all_tasks.sort(key=lambda x: x['updated_at'], reverse=True)
    
    return jsonify({
        "total": len(all_tasks),
        "tasks": all_tasks[:limit]
    })

@app.route('/api/status/tasks/active', methods=['GET'])
def get_active_tasks_only():
    """Get only currently running tasks."""
    tasks = task_manager.get_active_tasks()
    return jsonify({
        "count": len(tasks),
        "tasks": tasks
    })

@app.route('/api/status/tasks/completed', methods=['GET'])
def get_completed_tasks_only():
    """Get recently completed tasks."""
    limit = int(request.args.get('limit', 20))
    tasks = task_manager.get_completed_tasks(limit)
    return jsonify({
        "count": len(tasks),
        "tasks": tasks
    })


@app.route('/status')
def serve_status_dashboard():
    """
    Serve the real-time status dashboard HTML.
    Accessible at http://gateway:5000/status
    """
    from flask import send_from_directory
    return send_from_directory('.', 'status_dashboard.html')


# ============== SESSION MANAGEMENT API ENDPOINTS ==============

@app.route('/api/session', methods=['GET'])
def get_session():
    """Get current session state for the authenticated user."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    # Use user_identity as the key
    user_identity = config["name"].lower()
    session = SESSION_MANAGER.get_or_create_session(user_identity)
    return jsonify({
        "status": "success",
        "user": config["name"],
        "session": session
    })

@app.route('/api/session/clear', methods=['POST'])
def clear_session():
    """Clear the session buffer for the authenticated user."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    # Use user_identity as the key
    user_identity = config["name"].lower()
    cleared = SESSION_MANAGER.clear_session(user_identity)
    if cleared:
        return jsonify({
            "status": "success",
            "user": config["name"],
            "message": "Session buffer cleared"
        })
    else:
        return jsonify({
            "status": "info",
            "user": config["name"],
            "message": "No active session to clear"
        })

@app.route('/api/session/topics', methods=['GET'])
def get_topic_history():
    """Get topic switch history for the authenticated user."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    # Use user_identity as the key
    user_identity = config["name"].lower()
    session = SESSION_MANAGER.get_or_create_session(user_identity)
    return jsonify({
        "status": "success",
        "user": config["name"],
        "current_topic": session.get("current_topic"),
        "topic_history": session.get("topic_history", [])
    })

# ============== CHAT HISTORY API ENDPOINTS ==============

@app.route('/api/chat/history', methods=['GET', 'POST', 'DELETE'])
def chat_history_handler():
    """Handle GET, POST, DELETE for chat history."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    # (WireGuard identity fix) Honor an explicit user_id so tunneled clients
    # (the phone over WireGuard is NAT'd to a Docker bridge IP like 172.18.0.1,
    # which maps to the empty "Dashboard" bucket) still resolve their own
    # bucket. GET reads it from the query string, POST/DELETE from the JSON
    # body. Same trust policy as the POST /api/chat user_id override.
    if request.method == 'GET':
        requested_identity = (request.args.get("user_id") or "").strip()
    else:
        identity_body = request.get_json(silent=True) or {}
        requested_identity = (identity_body.get("user_id") or "").strip()
    override_config = resolve_config_with_override(client_ip, requested_identity)
    if override_config:
        print(f"[Chat History] Identity override: user_id='{requested_identity}' from IP {client_ip}")
        config = override_config
    
    # Use user_identity as the key
    # (Shared-window unification: alias dashboard/operator into shared bucket)
    user_identity = normalize_chat_identity(config["name"].lower())
    
    if request.method == 'GET':
        """Get full chat history for the authenticated user."""
        with CHAT_HISTORY_LOCK:
            messages = CHAT_HISTORY.get(user_identity, [])
        
        return jsonify({
            "status": "success",
            "user": config["name"],
            "messages": messages
        })
    
    elif request.method == 'POST':
        """Save/replace full chat history for the authenticated user."""
        data = request.json
        messages = data.get("messages", [])
        
        with CHAT_HISTORY_LOCK:
            CHAT_HISTORY[user_identity] = messages
        
        print(f"[Chat History] Saved {len(messages)} messages for user '{user_identity}'")
        
        # Persist to disk outside the lock (async)
        threading.Thread(target=save_chat_history_to_disk, daemon=True).start()
        
        return jsonify({
            "status": "success",
            "user": config["name"],
            "message_count": len(messages)
        })
    
    elif request.method == 'DELETE':
        """Clear all chat history for the authenticated user."""
        with CHAT_HISTORY_LOCK:
            CHAT_HISTORY[user_identity] = []
        
        print(f"[Chat History] Cleared history for user '{user_identity}'")
        
        # Persist to disk outside the lock (async)
        threading.Thread(target=save_chat_history_to_disk, daemon=True).start()
        
        return jsonify({
            "status": "cleared",
            "user": config["name"]
        })

@app.route('/api/chat/history/sync', methods=['POST'])
def sync_chat_message():
    """Sync a single chat message to the server history."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    # (WireGuard identity fix) Honor an explicit user_id in the body (same
    # policy as POST /api/chat) so tunneled clients sync into their own
    # bucket instead of the Dashboard default.
    sync_body_probe = request.get_json(silent=True) or {}
    requested_identity = (sync_body_probe.get("user_id") or "").strip()
    override_config = resolve_config_with_override(client_ip, requested_identity)
    if override_config:
        print(f"[Chat History] Identity override (sync): user_id='{requested_identity}' from IP {client_ip}")
        config = override_config
    
    # Use user_identity as the key
    # (Shared-window unification: alias dashboard/operator into shared bucket)
    user_identity = normalize_chat_identity(config["name"].lower())
    
    data = request.json
    message = data.get("message")
    if not message:
        return jsonify({"error": "Message required"}), 400
    
    with CHAT_HISTORY_LOCK:
        if user_identity not in CHAT_HISTORY:
            CHAT_HISTORY[user_identity] = []
        CHAT_HISTORY[user_identity].append(message)
        # Keep only last 200 messages to avoid memory bloat
        if len(CHAT_HISTORY[user_identity]) > 200:
            CHAT_HISTORY[user_identity] = CHAT_HISTORY[user_identity][-200:]
    
    # Persist to disk outside the lock (async)
    threading.Thread(target=save_chat_history_to_disk, daemon=True).start()
    
    return jsonify({
        "status": "success",
        "user": config["name"],
        "message_count": len(CHAT_HISTORY.get(user_identity, []))
    })


# ============== WEBSOCKET SUPPORT ==============

# (WireGuard identity fix) Client-declared identity per Socket.IO session.
# The Android app announces {'type': 'identity', 'identity': 'Rich', ...}
# immediately after connecting. Keyed by sid so each connection tracks its
# own override; cleaned up on disconnect.
WS_IDENTITIES = {}


def _ws_config_for(client_ip, sid):
    """Resolve config for a WS event: IP-derived first, then honor the
    identity this session announced (same trust policy as the POST
    /api/chat user_id override). Returns None for unrecognized clients."""
    config = get_user_config(client_ip)
    if not config:
        return None
    announced = WS_IDENTITIES.get(sid)
    if announced:
        override = resolve_config_with_override(client_ip, announced)
        if override:
            config = override
    return config


@socketio.on('connect')
def handle_connect():
    client_ip = request.remote_addr
    config = get_user_config(client_ip)
    if config:
        user_name = config["name"]
        connected_clients[request.sid] = {"ip": client_ip, "name": user_name}
        room = client_ip
        join_room(room)
        client_rooms[request.sid] = room
        # (Looking-Glass unification) Every socket ALSO joins its resolved
        # identity room so all of a user's devices (phone, dashboard, LGI)
        # receive every live chat emission — replies are addressed to the
        # identity room, not to whichever device happened to send them.
        identity_room = normalize_chat_identity(config["name"].lower())
        join_room(identity_room)
        print(f"[WebSocket] {user_name} connected from {client_ip} (rooms: {room}, {identity_room})")
        emit('message', {'type': 'connection', 'status': 'connected', 'user': user_name, 'message': f'Welcome back, {user_name}! Polaris is online.'})

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in connected_clients:
        del connected_clients[request.sid]
    if request.sid in client_rooms:
        del client_rooms[request.sid]
    # (WireGuard identity fix) Drop any announced identity for this session
    WS_IDENTITIES.pop(request.sid, None)
@socketio.on('join_status_room')
def handle_join_status_room():
    """Join the status room for real-time task updates."""
    client_ip = request.remote_addr
    config = get_user_config(client_ip)
    if config:
        user_name = config["name"]
        join_room('status')
        print(f"[WebSocket] {user_name} joined status room")
        emit('message', {'type': 'status', 'message': f'Joined status room for real-time task updates'})
        # Send current active tasks on join
        active_tasks = task_manager.get_active_tasks()
        if active_tasks:
            emit('active_tasks', active_tasks)

@socketio.on('leave_status_room')
def handle_leave_status_room():
    """Leave the status room."""
    leave_room('status')
    print(f"[WebSocket] Client left status room")

@socketio.on('join_tasks')
def handle_join_tasks(data=None):
    """Join the TASK FEED socket into room `status` for real-time task updates.

    The dashboard TASK FEED (Polaris_page.html) emits `join_tasks` on connect
    (payload: {room: 'tasks_dashboard'}); task lifecycle events are broadcast
    to room `status`, so the payload room name is ignored and the feed socket
    is joined there. Joins unconditionally (passive feed listener).
    """
    client_ip = request.remote_addr
    try:
        config = get_user_config(client_ip)
        user_name = config["name"] if config else client_ip
    except Exception:
        user_name = client_ip
    join_room('status')
    print(f"[WebSocket] {user_name} (TASK FEED) joined status room via join_tasks")
    emit('message', {'type': 'status', 'message': 'Joined task feed for real-time task updates'})
    # Send current active tasks on join (mirrors join_status_room)
    try:
        active_tasks = task_manager.get_active_tasks()
        if active_tasks:
            emit('active_tasks', active_tasks)
    except Exception as e:
        print(f"[WebSocket] Failed to send active_tasks snapshot to TASK FEED: {e}")

@socketio.on('request_history')
def handle_request_history(data=None):
    """(WireGuard identity fix) The Android app requests its rolling
    40-message history on connect via emit('request_history',
    {device_id: <identity>}). This handler previously did not exist, so the
    request was silently dropped. Resolves identity from the payload or the
    session's announced identity (same override policy as POST /api/chat),
    falling back to the IP-derived config."""
    client_ip = request.remote_addr
    config = get_user_config(client_ip)
    if not config:
        emit('chat_history', {'status': 'error', 'error': 'User not recognized'})
        return
    
    data = data or {}
    requested = (data.get('device_id') or data.get('user_id')
                 or WS_IDENTITIES.get(request.sid) or "").strip()
    override_config = resolve_config_with_override(client_ip, requested)
    if override_config:
        config = override_config
    
    # (Shared-window unification: alias dashboard/operator into shared bucket)
    user_identity = normalize_chat_identity(config["name"].lower())
    # (Looking-Glass unification) The requester also lives in its identity
    # room so subsequent replies stream to it even if it never announced.
    join_room(user_identity)
    # (Looking-Glass seed v2) Replay the last 40 messages: the phone seeds
    # its opening window with the latest 5 messages per SIDE (Rich + Polaris)
    # from this replay, so a lopsided final-10 (e.g. a long Polaris streak
    # after tool reports) can no longer starve one side of the seed.
    with CHAT_HISTORY_LOCK:
        history = CHAT_HISTORY.get(user_identity, [])[-40:]
    
    # Map to the shape the Android WS client expects (it reads
    # 'message'/'sender') while keeping the REST keys ('content'/'role')
    # for any other consumers.
    messages = [{
        "id": idx,
        "message": m.get("content", ""),
        "content": m.get("content", ""),
        "role": m.get("role", ""),
        "sender": m.get("sender", ""),
        "timestamp": m.get("timestamp", "")
    } for idx, m in enumerate(history)]
    
    emit('chat_history', {
        'status': 'success',
        'user': config['name'],
        'user_id': user_identity,
        'messages': messages
    })
    print(f"[WebSocket] Sent {len(messages)} history messages to {config['name']} (sid {request.sid}) from IP {client_ip}")


@socketio.on('feed_ping')
def handle_feed_ping(data=None):
    """LGI feed-socket liveness echo (Oct 13, 2026 silent-wedge fix).

    A half-open TCP corpse never raises on the client side (sends queue;
    the engineio read loop parks in recv() forever), so LGI's reaper asks
    for a server echo every 15 s and force-closes when the echo goes stale.
    Distinct from the burst receiver's vision_ping/vision_pong channel so
    neither server ever answers the other's pings.
    """
    try:
        payload = data if isinstance(data, dict) else {}
        emit('feed_pong', {'t': payload.get('t'), 'ts': datetime.now().isoformat()})
    except Exception as e:
        print(f"❌ feed_ping error: {e}")

@socketio.on('message')
def handle_message(data):
    """Handle incoming messages from dashboard - supports both chat and Polaris queries"""
    client_ip = request.remote_addr
    print(f"[DEBUG] Received message from {client_ip}: {data}")
    
    # Handle different message types
    msg_type = data.get('type', 'chat')
    
    # (WireGuard identity fix) The Android app announces its identity right
    # after connecting: {'type': 'identity', 'identity': 'Rich', ...}.
    # Previously this fell through every branch and was silently ignored, so
    # a tunneled phone (NAT'd to a Docker bridge IP) stayed stuck on the
    # Dashboard identity with an empty history bucket.
    if msg_type == 'identity':
        announced = (data.get('identity') or data.get('user_id') or "").strip()
        if announced:
            if get_user_config(client_ip):
                WS_IDENTITIES[request.sid] = announced
                resolved = _ws_config_for(client_ip, request.sid)
                # (Looking-Glass unification) Re-join with the announced
                # identity's normalized bucket — a NAT'd client may have
                # resolved the Dashboard bucket at connect time.
                if resolved:
                    join_room(normalize_chat_identity(resolved["name"].lower()))
                print(f"[WebSocket] Identity announce from {client_ip}: '{announced}' -> resolved as '{resolved['name']}'")
                emit('message', {'type': 'connection', 'status': 'connected', 'user': resolved['name'], 'message': f"Identity confirmed: {resolved['name']}"})
            else:
                emit('message', {'type': 'error', 'content': 'User not recognized'})
        return
    
    config = _ws_config_for(client_ip, request.sid)
    if not config:
        emit('message', {'type': 'error', 'content': 'User not recognized'})
        return
    
    # Use user_identity (not client_ip) for all session operations
    # (Shared-window unification: alias dashboard/operator into shared bucket)
    user_identity = normalize_chat_identity(config["name"].lower())
    user_name = config["name"]

    # (Looking-Glass unification) Idempotent identity-room join for every
    # WS message path ('chat', 'polaris', ...) — non-announcing clients
    # (plain dashboard browsers) and any session that skipped
    # request_history still land in their identity room here.
    join_room(user_identity)

    if msg_type == 'chat':
        # User-to-user chat message - broadcast to all clients
        sender = data.get('sender', user_name)
        content = data.get('content', '')
        timestamp = data.get('timestamp', datetime.now().isoformat())
        
        # Store in master chat history (keyed by user_identity)
        with CHAT_HISTORY_LOCK:
            if user_identity not in CHAT_HISTORY:
                CHAT_HISTORY[user_identity] = []
            CHAT_HISTORY[user_identity].append({
                "role": "user",
                "content": content,
                "timestamp": timestamp,
                "sender": sender
            })
            # Keep only last 200 messages
            if len(CHAT_HISTORY[user_identity]) > 200:
                CHAT_HISTORY[user_identity] = CHAT_HISTORY[user_identity][-200:]
        
        # Persist to disk asynchronously
        threading.Thread(target=save_chat_history_to_disk, daemon=True).start()
        
        # (Looking-Glass unification) Broadcast to the sender's identity
        # room — all of that user's devices see it; other users do not.
        emit('message', {
            'type': 'user_chat',
            'content': content,
            'sender': 'user',
            'senderName': sender,
            'timestamp': timestamp
        }, room=user_identity)
        
        print(f"[WebSocket Chat] {sender}: {content}")
        
    elif msg_type == 'polaris' or msg_type == 'chat_stream':
        # Direct Polaris query
        user_prompt = data.get("content", data.get("message", ""))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] [WS] {user_name}: {user_prompt}\n")
        
        # Track in session sliding window (use user_identity, not client_ip)
        SESSION_MANAGER.add_exchange(user_identity, "user", user_prompt)
        
        # Detect and update topic
        detected_topic = detect_topic(user_prompt)
        confidence = extract_topic_confidence(user_prompt, detected_topic)
        SESSION_MANAGER.update_topic(user_identity, detected_topic, confidence)
        
        # (Change 4) Persist the user's prompt to master chat history — the
        # WS 'polaris' path previously appended ONLY Polaris's reply, so the
        # user side of WS chats never reached synced clients.
        append_chat_history(user_identity, "user", user_prompt, user_identity)

        # Broadcast the user's prompt back to the sending client so its own
        # bubble renders instantly. MainActivity deliberately does NOT add
        # the sent message locally ("wait for WebSocket broadcast to prevent
        # duplicates") — without this emit the phone only ever sees its own
        # message via the 3s REST poll.
        emit('chat_message', {
            'sender': user_identity,
            'user_id': user_identity,
            'content': user_prompt,
            'message': user_prompt,  # Android's handleChatMessage reads 'message'
            'timestamp': utc_now_iso()
        }, room=user_identity)
        
        heuristics_data = load_heuristics()
        behavior_lib = load_behavior_lib(config)
        # (00:43:18 incident fix) The session window must be READ under the
        # same key it is WRITTEN under: add_exchange() above stores under
        # user_identity ("rich"), so reading with client_ip made
        # get_context_window() hit a fresh EMPTY session — every WS turn
        # shipped no [RECENT CONVERSATION CONTEXT] block at all.
        system_prompt = build_system_prompt(config, heuristics_data, behavior_lib, user_identity)
        
        stream_id = f"stream_{datetime.now().timestamp()}"
        
        try:
            ollama_payload = {"model": OLLAMA_MODEL, "system": system_prompt, "prompt": user_prompt, "stream": True}
            response = requests.post(OLLAMA_URL, json=ollama_payload, timeout=60, stream=True)
            
            # (Tool-Stream Fix) The raw Ollama stream can emit tool-call JSON
            # ({"tool": ...}) inline — the old loop streamed every chunk raw,
            # so the phone rendered tool JSON in the chat bubble. Mirror the
            # REST /api/chat pipeline: hold back a trailing window so a tool
            # marker split across chunks can never leak mid-stream, freeze
            # the stream once a marker appears, then parse -> strip -> execute
            # after the stream ends and broadcast only clean prose.
            tool_marker_re = re.compile(r'\{\s*"tool"\s*:')
            STREAM_HOLD_BACK = 48   # chars kept un-emitted to hide a partial marker
            stream_cursor = 0       # chars of full_response already emitted
            stream_frozen = False   # True once a tool call has been detected

            full_response = ""
            for line in response.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        chunk_text = chunk.get("response", "")
                        full_response += chunk_text
                        if stream_frozen:
                            continue
                        marker = tool_marker_re.search(full_response)
                        if marker:
                            # Emit only the prose preceding the tool call
                            # (trim any dangling code fence), then freeze.
                            stream_frozen = True
                            pre = full_response[stream_cursor:marker.start()]
                            pre = re.sub(r'(```+\s*(json|JSON)?\s*)$', '', pre.rstrip()).rstrip()
                            stream_cursor = marker.start()
                            if pre:
                                emit('message', {'type': 'chat_stream', 'content': pre, 'stream_id': stream_id, 'is_complete': False, 'sender': 'polaris'}, room=user_identity)
                        else:
                            # Stream live but keep the last few chars back so
                            # a half-arrived tool marker is never displayed.
                            safe_end = max(stream_cursor, len(full_response) - STREAM_HOLD_BACK)
                            if safe_end > stream_cursor:
                                emit('message', {'type': 'chat_stream', 'content': full_response[stream_cursor:safe_end], 'stream_id': stream_id, 'is_complete': False, 'sender': 'polaris'}, room=user_identity)
                                stream_cursor = safe_end
                    except json.JSONDecodeError:
                        continue

            # Tool pipeline (mirrors REST /api/chat): parse -> strip -> execute
            tool_calls = parse_tool_calls_from_response(full_response)
            tool_results = []
            # (Leak fix) Strip unconditionally: when Ollama emits malformed
            # tool JSON, parse returns [] and the raw JSON previously flowed
            # straight into session, transcript, history, and the phone's
            # chat bubble.
            clean_response = strip_tool_calls_from_response(full_response).strip()
            if tool_calls:
                for tool_call in tool_calls:
                    print(f"[Tool Execution] Executing: {tool_call['name']} with args: {tool_call['arguments']}")
                    result = execute_tool_call(tool_call['name'], tool_call['arguments'])
                    tool_results.append(result)
                    with open(config["transcript_file"], "a") as f:
                        f.write(f"[{timestamp}] [TOOL] {tool_call['name']}: {json.dumps(result, indent=2)}\n")
                if tool_results:
                    emit('tool_execution', {'tool_calls': tool_calls, 'results': tool_results, 'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, room=user_identity)

            # Final flush of the held-back tail (stream is over, safe to emit).
            # (Leak fix) Strip unconditionally — if the stream never froze on
            # a tool-call boundary (parse failure), the held-back tail may
            # still contain malformed tool JSON.
            flush_text = strip_tool_calls_from_response(full_response[stream_cursor:])
            if flush_text:
                emit('message', {'type': 'chat_stream', 'content': flush_text, 'stream_id': stream_id, 'is_complete': False, 'sender': 'polaris'}, room=user_identity)
            
            # Track AI response in session — CLEAN text only (use
            # user_identity, not client_ip). Raw tool JSON must not pollute
            # the sliding-window context of future turns.
            SESSION_MANAGER.add_exchange(user_identity, "assistant", clean_response)

            emit('message', {'type': 'chat_stream', 'content': '', 'stream_id': stream_id, 'is_complete': True, 'sender': 'polaris'}, room=user_identity)

            with open(config["transcript_file"], "a") as f:
                f.write(f"[{timestamp}] [WS] Polaris: {clean_response}\n")

            # (v1.6.2) Store AI response in master chat history (Z-ISO UTC
            # timestamp) — tool-less turns only: on tool turns the truthful
            # report below is this exchange's single assistant row/broadcast
            # (the streamed narration above remains the live typing UX).
            if not tool_calls:
                append_chat_history(user_identity, "assistant", clean_response, "Polaris")

            # (v1.6.2) Broadcast the completed reply so the phone renders it
            # the moment the stream finishes — tool-less turns only (tool
            # turns' chat_message is the truthful report below; the streamed
            # narration already rendered live). Android's handleChatMessage()
            # reads 'sender' + 'message' (REST payloads use 'content'), so
            # emit both spellings.
            if not tool_calls:
                emit('chat_message', {
                    'sender': 'polaris',
                    'user_id': user_identity,
                    'content': clean_response,
                    'message': clean_response,
                    'timestamp': utc_now_iso()
                }, room=user_identity)
            
            # Follow-up report turn (mirrors REST /api/chat): have Polaris
            # report the REAL tool results to the user. Falls back to the
            # canned result-aware message if the LLM turn fails.
            if tool_calls and tool_results:
                follow_up_ai = ""
                follow_up_failed = False
                try:
                    slim_results = _truncate_tool_results(tool_results)
                    results_json = json.dumps(slim_results, indent=2, default=str)
                    follow_up_prompt = (
                        "You just executed the tool call(s) below in response to the user's last message. "
                        "Here are the ACTUAL execution results:\n\n"
                        f"{results_json}\n\n"
                        "Report the real outcome to the user truthfully and concisely:\n"
                        "- Report ONLY what is NEW in these results (what the tools actually did, produced, or failed at).\n"
                        "- Do NOT restate, rephrase, or re-announce anything from your previous reply — the user already heard that narration. Lead with the outcome, not with 'Okay' or a recap.\n"
                        "- If nothing in the results is new or worth saying, output an EMPTY response (return nothing) instead of padding.\n"
                        "- If a tool FAILED, say so plainly and include the error (e.g. host unreachable / no route to host, permission denied, command not found).\n"
                        "- If it SUCCEEDED, summarize the useful output that was not already narrated (what is running, what you found).\n"
                        "- NEVER claim success when a result shows failure. NEVER invent a status you did not verify.\n"
                        "- Reply in plain text only. Do not output raw JSON, and do not call more tools — just report."
                    )
                    follow_up_payload = {"model": OLLAMA_MODEL, "system": system_prompt, "prompt": follow_up_prompt, "stream": False}
                    follow_up_response = requests.post(OLLAMA_URL, json=follow_up_payload, timeout=60)
                    follow_up_ai = follow_up_response.json().get("response", "").strip()
                    follow_up_ai = strip_tool_calls_from_response(follow_up_ai).strip()
                except Exception as e:
                    print(f"[Chat] Tool-report LLM turn failed: {e}")
                    follow_up_failed = True
                # (v1.4.3) Honors deliberate silence: the canned fallback fires
                # ONLY when the reporting LLM turn failed, never when the model
                # chose to say nothing ('nothing new' instruction).
                if follow_up_failed:
                    follow_up_ai = generate_follow_up_message(tool_calls, tool_results) or ""
                if follow_up_ai:
                    SESSION_MANAGER.add_exchange(user_identity, "assistant", follow_up_ai)
                    with open(config["transcript_file"], "a") as f:
                        f.write(f"[{timestamp}] Polaris (tool report): {follow_up_ai}\n")
                    append_chat_history(user_identity, "assistant", follow_up_ai, "Polaris")
                    emit('chat_message', {
                        'sender': 'polaris',
                        'user_id': user_identity,
                        'content': follow_up_ai,
                        'message': follow_up_ai,
                        'timestamp': utc_now_iso()
                    }, room=user_identity)

            if detect_correction(user_prompt):
                queue_learning_task(user_identity, user_prompt, clean_response)
        except Exception as e:
            emit('message', {'type': 'error', 'content': f'Error: {str(e)}', 'sender': 'polaris'}, room=client_ip)

# ============== MEMORY VAULT API (Obsidian-style vault + supervisor triage) ==============

# Route blueprints for the Memory Vault and supervisor audit endpoints
from memory_routes import memory_bp          # /api/memory/vault/*
from supervisor_routes import supervisor_bp  # /api/supervisor/*

app.register_blueprint(memory_bp)
app.register_blueprint(supervisor_bp)

# ── Boot-time context-loss fixes: session hydration (Fix A) + blueprint vault sync (Fix C) ──
def hydrate_sessions_from_history():
    """Fix A: rebuild each live session's sliding window from CHAT_HISTORY.

    load_chat_history_from_disk() refreshes CHAT_HISTORY at import, but
    SessionState windows start EMPTY, so a restart throws away recent
    exchange context even though it sits in /data/freeroam/chat_history.json.
    Replays persisted turns into each identity's window via the canonical
    add_exchange() (caps at SLIDING_WINDOW_SIZE). Nothing is persisted here,
    so the startup scrub cannot run again on restored content. Idempotent:
    identities that already carry window content are skipped."""
    try:
        load_chat_history_from_disk()
    except Exception as exc:
        print(f"[Session Hydration] chat_history load failed: {exc}")
    with CHAT_HISTORY_LOCK:
        buckets = dict(CHAT_HISTORY)
    if not buckets:
        print("[Session Hydration] no persisted history; skipping")
        return
    restored = 0
    for identity, messages in buckets.items():
        if not isinstance(messages, list):
            continue
        session = SESSION_MANAGER.get_or_create_session(identity)
        if session.get("sliding_window"):
            continue  # window already warm -> already hydrated
        for msg in messages[-SLIDING_WINDOW_SIZE:]:
            if not isinstance(msg, dict):
                continue
            role, content = msg.get("role"), msg.get("content")
            if role in ("user", "assistant", "system") and isinstance(content, str) and content:
                SESSION_MANAGER.add_exchange(identity, role, content)
                restored += 1
    print(f"[Session Hydration] restored {restored} turns into windows ({SLIDING_WINDOW_SIZE}-exchange cap per identity)")


def sync_project_docs_to_vault():
    """Fix C: mirror the authoritative repo blueprints into the Memory Vault
    (category "architecture", deterministic title -> same vault file across
    restarts) so they are semantically searchable mid-chat. The vault copy is
    a MIRROR: repo MDs stay the editable source of truth; this re-upserts
    only when a file's mtime/size changed, re-creating if the tracked vault
    file vanished. Restart-idempotent via DOC_BLUEPRINT_SYNC_STATE_FILE."""
    try:
        from memory_vault import get_memory_vault, MAX_CONTENT_CHARS
    except ImportError as exc:
        print(f"[Doc Sync] memory_vault unavailable, skipping vault sync: {exc}")
        return

    state = {}
    try:
        if os.path.isfile(DOC_BLUEPRINT_SYNC_STATE_FILE):
            with open(DOC_BLUEPRINT_SYNC_STATE_FILE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[Doc Sync] state file unreadable ({exc}); starting fresh")
        state = {}

    vault = get_memory_vault()
    vault_dir = getattr(vault, "vault_root", None)
    created, updated, skipped = [], [], []
    for doc_key in POLARIS_DOC_SOURCES:
        path = _resolve_blueprint_path(doc_key)
        if not path:
            print(f"[Doc Sync] {doc_key}: no readable source found, skipping")
            continue
        try:
            st = os.stat(path)
        except OSError as exc:
            print(f"[Doc Sync] {doc_key}: stat failed ({exc}), skipping")
            continue
        entry = state.get(doc_key) or {}
        vault_path = entry.get("vault_path")
        mirrored = entry.get("mtime_ns") == st.st_mtime_ns and entry.get("size") == st.st_size
        vault_exists = bool(vault_path) and (
            (vault_dir and os.path.isfile(os.path.join(str(vault_dir), vault_path)))
            or os.path.isfile(vault_path)
        )
        outline = _extract_doc_outline(path, DOC_OUTLINE_MAX_CHARS, lead_chars=400)
        content = (
            f"# Blueprint Mirror - {doc_key}\n"
            f"_Auto-synced from {path} (mtime {datetime.fromtimestamp(st.st_mtime).isoformat()}); "
            f"the repo file is the source of truth. Heading outline below - open the original "
            f"for full detail._\n\n{outline}\n"
        )[: min(int(MAX_CONTENT_CHARS) - 200, DOC_OUTLINE_MAX_CHARS)]
        doc_title = f"Blueprint - {doc_key}"
        tags = ["blueprint", "northstar", doc_key.split("/")[-1]]
        try:
            if mirrored and vault_exists:
                skipped.append(doc_key)
            elif vault_exists:
                vault.update_document(vault_path, content)
                state[doc_key] = {"vault_path": vault_path, "mtime_ns": st.st_mtime_ns,
                                  "size": st.st_size, "last_synced": utc_now_iso()}
                updated.append(doc_key)
            else:
                result = vault.create_document("architecture", doc_title, content, tags=tags,
                                               user="operator", source="doc-sync")
                state[doc_key] = {"vault_path": result.get("path"), "mtime_ns": st.st_mtime_ns,
                                  "size": st.st_size, "last_synced": utc_now_iso()}
                created.append(doc_key)
        except Exception as exc:
            print(f"[Doc Sync] {doc_key}: vault upsert failed: {exc}")
    try:
        with open(DOC_BLUEPRINT_SYNC_STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
    except OSError as exc:
        print(f"[Doc Sync] could not persist sync state: {exc}")
    print(f"[Doc Sync] blueprint mirror - created: {created or '-'} | updated: {updated or '-'} | unchanged: {skipped or '-'}")


# ============== SERVER STARTUP ==============

def _start_background_systems():
    """Boot-time background systems, shared by gunicorn import and direct run."""
    # Context-loss fix (A+C): refill session windows from chat_history.json and
    # upsert authoritative project docs into the Memory Vault BEFORE traffic.
    try:
        hydrate_sessions_from_history()
    except Exception as e:
        print(f"[Boot] session hydration failed: {e}")
    try:
        sync_project_docs_to_vault()
    except Exception as e:
        print(f"[Boot] project doc vault sync failed: {e}")

    # Start Memory Vault watchdog (re-embeds host-edited markdown files)
    try:
        from memory_vault import start_vault_watchdog
        start_vault_watchdog(interval=30.0)
    except Exception as e:
        print(f"[MemoryVault] watchdog failed to start: {e}")

    # Start node cleanup background thread
    cleanup_thread = threading.Thread(target=node_cleanup_loop, daemon=True)
    cleanup_thread.start()


if __name__ != '__main__':
    # Running under a WSGI server (gunicorn): start boot-time systems on import.
    # Startup history scrub (leak fix) — strip_tool_calls_from_response is
    # fully defined by the time the module finishes loading.
    try:
        scrub_chat_history_leaks()
    except Exception as e:
        print(f"[Chat History] Startup scrub failed: {e}")
    _start_background_systems()

if __name__ == '__main__':
    # Startup history scrub (leak fix): clean already-stored raw tool-call
    # JSON now that the module is fully loaded.
    try:
        scrub_chat_history_leaks()
    except Exception as e:
        print(f"[Chat History] Startup scrub failed: {e}")
    _start_background_systems()
    
    print("\U0001F680 Polaris Continuous Learning Gateway starting...")
    print("\U0001F4CD WebSocket Endpoint: ws://0.0.0.0:5000/ws")
    print("\U0001F4CD HTTP API Endpoint: http://0.0.0.0:5000/api/chat")
    print("\U0001F9E0 Continuous Learning: ENABLED")
    print("\U0001F4DA Heuristics File: " + HEURISTICS_FILE)
    print("\U0001F512 LAN-only access enabled")
    print(f"\U0001F464 Allowed users: Rich ({RICH_IP}), Matt ({MATT_IP}), Operator ({OPERATOR_IP}), Dashboard/X17 ({DASHBOARD_TEST_IP})")
    print("\U0001F310 Node Swarm Tracking: ENABLED")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
