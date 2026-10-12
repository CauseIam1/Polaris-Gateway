#!/usr/bin/env python3
"""
Polaris Gateway - Unified Chat Server with WebSocket Support
Integrates with FreeRoam chat system (profiles, transcripts, distillation)
Runs on port 5000 (host-mapped to 8082 for WebSocket connections)
"""

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests
import json
import os
from datetime import datetime
import subprocess
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'polaris-secret-key-2026'

# Initialize SocketIO with CORS for LAN access
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ============== NETWORK CONFIGURATION (LAN ONLY) ==============
RICH_IP = "192.168.50.42"
MATT_IP = "192.168.50.98"
# Include Docker bridge network IPs for container-to-container communication
DOCKER_BRIDGE_GATEWAY = "172.17.0.1"  # Docker host gateway
DOCKER_NETWORK_RANGE = ["172.17.0.", "172.18.0."]  # Docker bridge networks
ALLOWED_IPS = [RICH_IP, MATT_IP, DOCKER_BRIDGE_GATEWAY]

# Connected clients tracking
connected_clients = {}
client_rooms = {}

# ============== USER PROFILES CONFIGURATION ==============
# Same structure as freeroam_gateway.py
USER_CONFIG = {
    RICH_IP: {
        "name": "Rich",
        "profile_file": "/home/causeiam/docker-containers/freeroam/data/rich/rich_profile.json",
        "transcript_file": "/home/causeiam/docker-containers/freeroam/data/rich/rich_transcripts.log",
        "lists_file": "/home/causeiam/docker-containers/freeroam/data/rich/rich_managed_lists.json"
    },
    MATT_IP: {
        "name": "Matt",
        "profile_file": "/home/causeiam/docker-containers/freeroam/data/matt/matt_profile.json",
        "transcript_file": "/home/causeiam/docker-containers/freeroam/data/matt/matt_transcripts.log",
        "lists_file": "/home/causeiam/docker-containers/freeroam/data/matt/matt_managed_lists.json"
    }
}

# Ollama configuration
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "my-stark-ai:latest")  # Default to available model
DISTILL_SCRIPT = "/home/causeiam/docker-containers/freeroam/polaris-gateway/distill_memory.py"

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

def load_profile(profile_file):
    """Load user profile from file."""
    if os.path.exists(profile_file):
        with open(profile_file, 'r') as f:
            return json.load(f)
    return {"user_name": "Unknown", "facts_and_preferences": [], "created_at": datetime.now().isoformat()}

def save_profile(profile_file, profile):
    """Save user profile to file."""
    with open(profile_file, 'w') as f:
        json.dump(profile, f, indent=2)

def load_lists(lists_file):
    """Load user's managed lists."""
    if os.path.exists(lists_file):
        with open(lists_file, 'r') as f:
            return json.load(f)
    return {"updated_at": datetime.now().isoformat()}

def save_lists(lists_file, lists):
    """Save user's managed lists."""
    lists["updated_at"] = datetime.now().isoformat()
    with open(lists_file, 'w') as f:
        json.dump(lists, f, indent=2)

def get_today_transcripts(transcript_file):
    """Get today's transcript entries (similar to FreeRoam app's getTodayMessages)."""
    if not os.path.exists(transcript_file):
        return []
    
    today = datetime.now().strftime("%Y-%m-%d")
    transcripts = []
    
    with open(transcript_file, 'r') as f:
        for line in f:
            if line.strip() and today in line:
                transcripts.append(line.strip())
    
    return transcripts[-50:]  # Last 50 messages for context

# ============== WEBSOCKET EVENT HANDLERS ==============

@socketio.on('connect')
def handle_connect():
    """Handle client WebSocket connection."""
    client_ip = get_user_ip()
    client_id = request.sid
    
    print(f"[WebSocket] Client connected: {client_id} from {client_ip}")
    
    # Check if IP is allowed (including Docker network ranges)
    is_allowed = client_ip in ALLOWED_IPS or any(client_ip.startswith(prefix) for prefix in DOCKER_NETWORK_RANGE)
    if not is_allowed:
        print(f"[WebSocket] Connection rejected: IP {client_ip} not authorized")
        emit('error', {'message': 'Access denied: LAN-only access'})
        return False
    
    # Store client info
    connected_clients[client_id] = {
        'ip': client_ip,
        'connected_at': datetime.now().isoformat()
    }
    
    # Join user-specific room for targeted broadcasts
    join_room(client_ip)
    client_rooms[client_id] = client_ip
    
    # Send welcome message
    emit('connection_established', {
        'client_id': client_id,
        'user_ip': client_ip,
        'timestamp': datetime.now().isoformat()
    })
    
    print(f"[WebSocket] Client {client_id} joined room {client_ip}")
    return True

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client WebSocket disconnection."""
    client_id = request.sid
    
    print(f"[WebSocket] Client disconnected: {client_id}")
    
    # Clean up client data
    if client_id in connected_clients:
        client_ip = connected_clients[client_id]['ip']
        leave_room(client_ip)
        del connected_clients[client_id]
    
    if client_id in client_rooms:
        del client_rooms[client_id]

@socketio.on('message')
def handle_message(data):
    """Handle incoming WebSocket messages."""
    client_id = request.sid
    
    if client_id not in connected_clients:
        return
    
    client_info = connected_clients[client_id]
    message_type = data.get('type', 'unknown')
    
    print(f"[WebSocket] Message from {client_id}: {message_type}")
    
    if message_type == 'ping':
        # Respond to heartbeat
        emit('pong', {'timestamp': data.get('timestamp'), 'server_time': datetime.now().isoformat()})
    
    elif message_type == 'chat':
        # Handle chat message - broadcast to all users and process with AI
        content = data.get('content', '')
        timestamp = data.get('timestamp', datetime.now().isoformat())
        sender_name = client_info['name']
        
        print(f"[Broadcast] User chat from {sender_name} to all clients")
        
        # Broadcast user message to ALL connected clients (cross-device sync)
        broadcast_payload = {
            'type': 'user_chat',
            'content': content,
            'sender': sender_name,
            'timestamp': timestamp,
            'is_user': True
        }
        broadcast_to_all(broadcast_payload)
        
        # Now process with AI (same logic as HTTP endpoint)
        process_chat_message_async(client_info, content, timestamp)

@socketio.on_error()
def error_handler(e):
    """Handle WebSocket errors."""
    print(f"[WebSocket] Error: {e}")

# ============== HTTP API ENDPOINTS ==============

@app.before_request
def check_lan_access():
    """Block requests not from allowed LAN IPs (except Socket.IO endpoints)."""
    # Allow Socket.IO polling and WebSocket upgrade requests
    if request.path.startswith('/socket.io') or request.path == '/ws':
        return None
    
    client_ip = get_user_ip()
    is_allowed = client_ip in ALLOWED_IPS or any(client_ip.startswith(prefix) for prefix in DOCKER_NETWORK_RANGE)
    if not is_allowed:
        return jsonify({
            "error": "Access denied: LAN-only access",
            "message": f"Your IP ({client_ip}) is not authorized"
        }), 403

@app.route('/api/chat', methods=['POST'])
def handle_chat_http():
    """
    Handle chat requests - same logic as FreeRoam app.
    Uses profile facts_and_preferences for context.
    """
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    message = data.get('message', '').strip()
    
    if not message:
        return jsonify({"error": "Empty message"}), 400
    
    # Process chat with full FreeRoam context
    return process_chat_message(config, message)

def process_chat_message(config, message):
    """
    Process a chat message using the same logic as FreeRoam app.
    Loads profile facts_and_preferences for LLM context.
    """
    user_name = config["name"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Load profile with facts_and_preferences (same as FreeRoam app)
    profile = load_profile(config["profile_file"])
    facts_and_preferences = profile.get("facts_and_preferences", [])
    
    # Get today's transcripts for immediate context (like getTodayMessages)
    today_context = get_today_transcripts(config["transcript_file"])
    
    # Build context prompt (same as FreeRoam behavior_lib.json approach)
    context = "\n".join(facts_and_preferences) if facts_and_preferences else "No specific facts known."
    recent_chat = "\n".join(today_context[-10:]) if today_context else "No recent chat."
    
    # Log user message to transcript
    os.makedirs(os.path.dirname(config["transcript_file"]), exist_ok=True)
    with open(config["transcript_file"], "a") as f:
        f.write(f"[{timestamp}] {user_name}: {message}\n")
    
    # Prepare LLM prompt with full context
    system_prompt = f"""You are Polaris, an AI assistant integrated with the FreeRoam tactical edge-compute system.
You are speaking with {user_name} via the Polaris Dashboard (web interface).

USER PROFILE & FACTS (from behavior_lib.json equivalent):
{context}

RECENT CHAT HISTORY (today's conversation):
{recent_chat}

Respond naturally as Polaris. Be concise, helpful, and aware of the user's preferences and setup.
This is a text-only interface (no audio/TTS)."""
    
    try:
        # Send to Ollama for response
        ollama_payload = {
            "model": OLLAMA_MODEL,
            "prompt": f"{system_prompt}\n\n{user_name}: {message}\n\nPolaris:",
            "stream": False
        }
        
        response = requests.post(OLLAMA_URL, json=ollama_payload, timeout=60)
        response.raise_for_status()
        
        ai_response = response.json().get('response', 'I received your message.')
        
        # Log AI response to transcript
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] Polaris: {ai_response}\n")
        
        # Broadcast response to dashboard via WebSocket
        broadcast_to_user(user_name, {
            'type': 'chat_stream',
            'content': ai_response,
            'stream_id': f"stream_{datetime.now().timestamp()}",
            'is_complete': True,
            'confidence': 0.95,
            'sender': 'polaris'
        })
        
        return jsonify({
            "status": "success",
            "user": user_name,
            "message": message,
            "response": ai_response,
            "context_used": len(facts_and_preferences)
        })
        
    except requests.exceptions.RequestException as e:
        error_msg = f"LLM service unavailable: {str(e)}"
        print(f"[Chat] Error: {error_msg}")
        
        # Still log the message even if LLM fails
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] Polaris: [ERROR - LLM unavailable]\n")
        
        return jsonify({
            "status": "error",
            "user": user_name,
            "message": message,
            "error": error_msg
        }), 503

def process_chat_message_async(config, message, timestamp=None):
    """
    Async version of process_chat_message for WebSocket handling.
    Processes chat and broadcasts AI response without returning JSON.
    """
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    user_name = config["name"]
    
    # Load profile with facts_and_preferences
    profile = load_profile(config["profile_file"])
    facts_and_preferences = profile.get("facts_and_preferences", [])
    
    # Get today's transcripts for immediate context
    today_context = get_today_transcripts(config["transcript_file"])
    
    # Build context prompt
    context = "\n".join(facts_and_preferences) if facts_and_preferences else "No specific facts known."
    recent_chat = "\n".join(today_context[-10:]) if today_context else "No recent chat."
    
    # Log user message to transcript
    os.makedirs(os.path.dirname(config["transcript_file"]), exist_ok=True)
    with open(config["transcript_file"], "a") as f:
        f.write(f"[{timestamp}] {user_name}: {message}\n")
    
    # Prepare LLM prompt
    system_prompt = f"""You are Polaris, an AI assistant integrated with the FreeRoam tactical edge-compute system.
You are speaking with {user_name} via the Polaris Dashboard (web interface).

USER PROFILE & FACTS (from behavior_lib.json equivalent):
{context}

RECENT CHAT HISTORY (today's conversation):
{recent_chat}

Respond naturally as Polaris. Be concise, helpful, and aware of the user's preferences and setup.
This is a text-only interface (no audio/TTS)."""
    
    try:
        # Send to Ollama for response
        ollama_payload = {
            "model": OLLAMA_MODEL,
            "prompt": f"{system_prompt}\n\n{user_name}: {message}\n\nPolaris:",
            "stream": False
        }
        
        response = requests.post(OLLAMA_URL, json=ollama_payload, timeout=60)
        response.raise_for_status()
        
        ai_response = response.json().get('response', 'I received your message.')
        
        # Log AI response to transcript
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] Polaris: {ai_response}\n")
        
        # Broadcast response to dashboard via WebSocket
        broadcast_to_user(user_name, {
            'type': 'chat_stream',
            'content': ai_response,
            'stream_id': f"stream_{datetime.now().timestamp()}",
            'is_complete': True,
            'confidence': 0.95,
            'sender': 'polaris'
        })
        
    except requests.exceptions.RequestException as e:
        error_msg = f"LLM service unavailable: {str(e)}"
        print(f"[Chat] Error: {error_msg}")
        
        # Still log the message even if LLM fails
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] Polaris: [ERROR - LLM unavailable]\n")

@app.route('/api/action', methods=['POST'])
def handle_action():
    """Handle interactive UI action requests from dashboard."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    action_id = data.get('action_id', '')
    action_data = data.get('data', {})
    
    print(f"[Action] User {config['name']} triggered: {action_id}")
    
    # Process action and potentially broadcast response
    response_payload = {
        "status": "success",
        "action_id": action_id,
        "message": f"Action '{action_id}' received"
    }
    
    return jsonify(response_payload)

def broadcast_to_user(user_name, payload):
    """Broadcast a payload to all clients in a user's room."""
    room = None
    for ip, cfg in USER_CONFIG.items():
        if cfg["name"] == user_name:
            room = ip
            break
    
    if room:
        print(f"[Broadcast] Sending to room {room}: {payload.get('type')}")
        socketio.emit('message', payload, room=room)

def broadcast_to_all(payload):
    """Broadcast a payload to all connected clients."""
    socketio.emit('message', payload)

# ============== UTILITY FUNCTIONS ==============

def send_media_payload(user_ip, media_type, url, title=""):
    """Send a media iframe payload to a specific user."""
    config = get_user_config(user_ip)
    if config:
        broadcast_to_user(config['name'], {
            'type': 'media_iframe',
            'media_type': media_type,
            'url': url,
            'title': title
        })

def send_interactive_payload(user_ip, elements):
    """Send an interactive UI payload to a specific user."""
    config = get_user_config(user_ip)
    if config:
        broadcast_to_user(config['name'], {
            'type': 'interactive_ui',
            'elements': elements
        })

# ============== SERVER STARTUP ==============

if __name__ == '__main__':
    print("🚀 Polaris WebSocket Gateway starting...")
    print("📍 WebSocket Endpoint: ws://0.0.0.0:5000/ws")
    print("📍 HTTP API Endpoint: http://0.0.0.0:5000/api/chat")
    print("🔒 LAN-only access enabled")
    print(f"👤 Allowed users: Rich ({RICH_IP}), Matt ({MATT_IP})")
    print(f"📁 Rich profile: {USER_CONFIG[RICH_IP]['profile_file']}")
    print(f"📁 Matt profile: {USER_CONFIG[MATT_IP]['profile_file']}")
    print("")
    
    # Run with SocketIO server
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
