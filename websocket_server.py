#!/usr/bin/env python3
"""
Polairs Gateway WebSocket Server
Handles real-time bidirectional communication with Polaris Dashboard
Runs on port 8082 (host-mapped from container port 5000)
"""

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import json
import os
from datetime import datetime
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'polaris-secret-key'

# Initialize SocketIO with CORS for LAN access
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ============== NETWORK CONFIGURATION (LAN ONLY) ==============
RICH_IP = "192.168.50.42"
MATT_IP = "192.168.50.98"
X17_LAPTOP_IP = "192.168.50.227"
ALLOWED_IPS = [RICH_IP, MATT_IP, X17_LAPTOP_IP]

# Connected clients tracking
connected_clients = {}
client_rooms = {}  # Map client_id to room (user IP)

# ============== USER PROFILES CONFIGURATION ==============
USER_CONFIG = {
    RICH_IP: {
        "name": "Rich",
        "profile_file": "rich_profile.json",
        "transcript_file": "rich_transcripts.log",
        "lists_file": "rich_managed_lists.json"
    },
    MATT_IP: {
        "name": "Matt",
        "profile_file": "matt_profile.json",
        "transcript_file": "matt_transcripts.log",
        "lists_file": "matt_managed_lists.json"
    }
}

# Ollama configuration
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434/api/generate")
DISTILL_SCRIPT = "distill_memory.py"

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

def load_profile(config):
    """Load user profile from their profile file."""
    profile_file = config["profile_file"]
    if os.path.exists(profile_file):
        with open(profile_file, 'r') as f:
            return json.load(f)
    return {"user_name": config["name"], "facts_and_preferences": [], "created_at": datetime.now().isoformat()}

def save_profile(config, profile):
    """Save user profile to their profile file."""
    with open(config["profile_file"], 'w') as f:
        json.dump(profile, f, indent=2)

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
    with open(config["lists_file"], 'w') as f:
        json.dump(lists, f, indent=2)

# ============== WEBSOCKET EVENT HANDLERS ==============

@socketio.on('connect')
def handle_connect():
    """Handle client WebSocket connection."""
    client_ip = get_user_ip()
    client_id = request.sid
    
    print(f"[WebSocket] Client connected: {client_id} from {client_ip}")
    
    # Check if IP is allowed
    if client_ip not in ALLOWED_IPS:
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
    client_ip = client_info['ip']
    config = get_user_config(client_ip)
    message_type = data.get('type', 'unknown')
    
    print(f"[WebSocket] Message from {client_id}: {message_type}")
    
    if message_type == 'ping':
        # Respond to heartbeat
        emit('pong', {'timestamp': data.get('timestamp')})
    
    elif message_type == 'chat':
        # Handle chat message - uses sender from payload for cross-device sync
        content = data.get('content', '')
        sender_name = data.get('sender')
        timestamp = data.get('timestamp', datetime.now().isoformat())
        
        if content and sender_name:
            print(f"[Broadcast] User chat from {sender_name} (IP: {client_ip}): {content[:50]}...")
            broadcast_chat_from_sender(sender_name, content, timestamp)
            
            # Process the message and get AI response using IP-based config
            if config:
                process_chat_message(config, content)
            else:
                # Fallback: try to find config by sender name
                for ip, user_config in USER_CONFIG.items():
                    if user_config["name"] == sender_name:
                        process_chat_message(user_config, content)
                        break

@socketio.on_error()
def error_handler(e):
    """Handle WebSocket errors."""
    print(f"[WebSocket] Error: {e}")

# ============== HTTP API ENDPOINTS ==============

@app.before_request
def check_lan_access():
    """Block requests not from allowed LAN IPs (except WebSocket upgrade)."""
    # Skip IP check for WebSocket upgrade requests
    if request.path == '/ws':
        return None
    
    client_ip = get_user_ip()
    if client_ip not in ALLOWED_IPS:
        return jsonify({
            "error": "Access denied: LAN-only access",
            "message": f"Your IP ({client_ip}) is not authorized"
        }), 403

@app.route('/api/chat', methods=['POST'])
def handle_chat_http():
    """Handle chat requests with user-specific context (HTTP version)."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    message = data.get('message', '')
    
    if not message:
        return jsonify({"error": "Empty message"}), 400
    
    # Process the chat message
    return process_chat_message(config, message)

def process_chat_message(config, message):
    """Process a chat message and return response."""
    user_name = config["name"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Log the message to transcript
    with open(config["transcript_file"], "a") as f:
        f.write(f"\n[{timestamp}] {user_name}: {message}\n")
    
    # Load profile for context
    profile = load_profile(config)
    
    # Prepare context for LLM
    context = "\n".join(profile.get("facts_and_preferences", []))
    
    # Send to Ollama for response
    try:
        ollama_payload = {
            "model": "llama3.2",
            "prompt": f"Context: {context}\n\nUser ({user_name}): {message}\n\nPolaris:",
            "stream": False
        }
        
        response = requests.post(OLLAMA_URL, json=ollama_payload, timeout=60)
        response.raise_for_status()
        
        ai_response = response.json().get('response', 'I received your message.')
        
        # Log AI response
        with open(config["transcript_file"], "a") as f:
            f.write(f"[{timestamp}] Polaris: {ai_response}\n")
        
        # Broadcast response to dashboard via WebSocket
        broadcast_to_user(config['name'], {
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
            "response": ai_response
        })
        
    except requests.exceptions.RequestException as e:
        error_msg = f"LLM service unavailable: {str(e)}"
        print(f"[Chat] Error: {error_msg}")
        
        return jsonify({
            "status": "error",
            "user": user_name,
            "message": message,
            "error": error_msg
        }), 503

def handle_chat_message(user_ip, content):
    """Handle chat message received via WebSocket."""
    config = get_user_config(user_ip)
    if config:
        user_name = config["name"]
        # Broadcast the user's message to ALL connected clients (cross-device sync)
        broadcast_chat_to_all(user_name, content)
        # Process the message and get AI response
        process_chat_message(config, content)

@app.route('/api/action', methods=['POST'])
def handle_action():
    """Handle interactive UI action requests."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    data = request.json
    action_id = data.get('action_id', '')
    action_data = data.get('data', {})
    
    print(f"[Action] User {config['name']} triggered: {action_id}")
    
    # Process action and potentially broadcast response
    # This is where you'd handle button clicks, menu selections, etc.
    
    return jsonify({
        "status": "success",
        "action_id": action_id,
        "message": f"Action '{action_id}' received"
    })

def broadcast_to_user(user_name, payload):
    """Broadcast a payload to all clients in a user's room."""
    # Find the room for this user
    room = None
    for ip, config in USER_CONFIG.items():
        if config["name"] == user_name:
            room = ip
            break
    
    if room:
        print(f"[Broadcast] Sending to room {room}: {payload.get('type')}")
        socketio.emit('message', payload, room=room)

def broadcast_to_all(payload):
    """Broadcast a payload to all connected clients."""
    socketio.emit('message', payload)

def broadcast_chat_to_all(sender_name, content):
    """Broadcast a chat message from one user to all connected clients."""
    timestamp = datetime.now().isoformat()
    
    # Create chat message payload
    payload = {
        'type': 'user_chat',
        'content': content,
        'sender': sender_name,
        'timestamp': timestamp,
        'stream_id': f"user_{sender_name}_{datetime.now().timestamp()}",
        'is_complete': True
    }
    
    print(f"[Broadcast] User chat from {sender_name} to all clients: {content[:50]}...")
    socketio.emit('message', payload)

def broadcast_chat_from_sender(sender_name, content, timestamp):
    """Broadcast a chat message with provided timestamp from sender to all clients."""
    # Create chat message payload
    payload = {
        'type': 'user_chat',
        'content': content,
        'sender': sender_name,
        'timestamp': timestamp,
        'stream_id': f"user_{sender_name}_{timestamp}",
        'is_complete': True
    }
    
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
    print("🚀 Polaris WebSocket Gateway starting on port 5000...")
    print("📍 WebSocket Endpoint: ws://0.0.0.0:5000/ws")
    print("📍 HTTP API Endpoint: http://0.0.0.0:5000/api/chat")
    print("🔒 LAN-only access enabled")
    print(f"👤 Allowed users: Rich ({RICH_IP}), Matt ({MATT_IP})")
    print("")
    
    # Run with SocketIO server
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
