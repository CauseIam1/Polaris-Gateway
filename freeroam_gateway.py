#!/usr/bin/env python3
"""
Polaris Gateway - Next-Gen Memory Architecture
Dual-database system: SQLite (relational) + ChromaDB (vector)
Replaces flat JSON files with structured memory engine
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import requests
import json
import os
from datetime import datetime
import subprocess
from typing import Optional, Dict, List
import threading

# Import memory modules
from memory_db import get_memory_db, PolarisMemoryDB, DEFAULT_DB_PATH
from vector_memory import get_vector_memory, PolarisVectorMemory

# Import JARVIS toolkit modules
import system_tools
import livecharts_tools
import www_tools

# ============== NETWORK CONFIGURATION (LAN ONLY) ==============
# Static IP addresses from northstar.md
RICH_IP = "192.168.50.42"
MATT_IP = "192.168.50.98"
OPERATOR_IP = "192.168.50.51"
DASHBOARD_IP = "192.168.50.227"  # Dashboard/Testing machine (the X17 / LGI host)

# Allowed IPs for LAN-only operation
ALLOWED_IPS = [RICH_IP, MATT_IP, OPERATOR_IP, DASHBOARD_IP, "172.18.0.1", "172.18.0.14"]

# ============== USER PROFILES CONFIGURATION ==============
# IP to user_id mapping
IP_TO_USER = {
    RICH_IP: "rich",
    MATT_IP: "matt",
    OPERATOR_IP: "operator",
    DASHBOARD_IP: "dashboard",
    "172.18.0.1": "rich",  # Docker host gateway (maps to rich for testing)
    "172.18.0.14": "rich"  # Dashboard container IP (maps to rich for testing)
}

# Dashboard URLs for CORS
DASHBOARD_URLS = [
    "http://192.168.50.51:7000",
    "http://192.168.50.227:7000",
    "http://localhost:7000",
    "http://127.0.0.1:7000"
]

app = Flask(__name__)
app.config['SECRET_KEY'] = 'polaris-gateway-secret-key'
# Enable CORS for dashboard access (port 7000)
CORS(app, resources={
    r"/api/*": {
        "origins": DASHBOARD_URLS,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})
socketio = SocketIO(app, cors_allowed_origins="*")

# Ollama configuration
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434/api/generate")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")

# Memory singletons (lazy-loaded)
_memory_db: Optional[PolarisMemoryDB] = None
_vector_memory: Optional[PolarisVectorMemory] = None

def get_db() -> PolarisMemoryDB:
    """Get SQLite memory database singleton."""
    global _memory_db
    if _memory_db is None:
        _memory_db = get_memory_db()
    return _memory_db

def get_vector() -> PolarisVectorMemory:
    """Get ChromaDB vector memory singleton."""
    global _vector_memory
    if _vector_memory is None:
        _vector_memory = get_vector_memory()
    return _vector_memory

def get_user_ip() -> str:
    """Get the client IP address from the request."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def get_user_id(ip: str) -> Optional[str]:
    """Get user_id from IP address."""
    return IP_TO_USER.get(ip)

@app.before_request
def check_lan_access():
    """Block requests not from allowed LAN IPs."""
    client_ip = get_user_ip()
    if client_ip not in ALLOWED_IPS:
        return jsonify({
            "error": "Access denied: LAN-only access",
            "message": f"Your IP ({client_ip}) is not authorized"
        }), 403

@app.route('/api/chat', methods=['POST'])
def handle_chat():
    """
    Handle chat requests with dual-memory context assembly.
    
    Flow:
    1. Identify user via request body user_id (priority) or IP fallback
    2. Fetch recent chat history from SQLite (last 10 turns)
    3. Query ChromaDB for semantic memories (hybrid search)
    4. Fetch approved heuristics from SQLite
    5. Build enriched system prompt
    6. Send to Ollama
    7. Log to SQLite chat_history
    8. Add to ChromaDB conversation context
    9. Broadcast via Socket.IO
    """
    data = request.json
    client_ip = get_user_ip()
    
    # Priority: user_id from request body > IP mapping
    user_id = data.get("user_id")
    if not user_id:
        user_id = get_user_id(client_ip)
    
    if not user_id:
        return jsonify({
            "error": "Unknown user",
            "message": f"IP {client_ip} not configured and no user_id provided"
        }), 404
    
    user_prompt = data.get("prompt", "")
    client_source = data.get("client_source", "dashboard_browser")
    session_id = data.get("session_id", f"{user_id}_default")
    
    if not user_prompt:
        return jsonify({"error": "Empty prompt"}), 400
    
    # ========== PRE-EXECUTION CONTEXT ASSEMBLY ==========
    
    db = get_db()
    vector = get_vector()
    
    # 1. Fetch recent relational chat history (last 10 messages)
    recent_history = db.get_recent_chat_history(user_id, limit=10)
    history_context = "\n".join([
        f"{msg['sender']}: {msg['message']}" 
        for msg in recent_history
    ])
    
    # 2. Hybrid semantic search across all memory sources
    try:
        hybrid_results = vector.hybrid_search(
            user_id=user_id,
            query=user_prompt,
            limit=5
        )
    except Exception as e:
        print(f"[Gateway] Hybrid search failed: {e}")
        hybrid_results = []
    
    semantic_context = "\n".join([
        f"- {r['metadata'].get('content', r['metadata'].get('text', ''))}"
        for r in hybrid_results
    ])
    
    # 3. Fetch approved heuristics (user-specific rules) - NO LIMIT
    approved_heuristics = db.get_heuristics_by_status('approved', user_id=user_id)
    print(f"[Gateway] Loaded {len(approved_heuristics)} approved heuristics for {user_id}")
    heuristics_context = "\n".join([
        f"- {h['rule_content']} (confidence: {h['confidence_score']:.2f})"
        for h in approved_heuristics
    ])
    
    # 4. Get user profile from SQLite
    user_info = db.get_user_profile(user_id)
    display_name = user_info['display_name'] if user_info else user_id.capitalize()
    
    # ========== BUILD ENRICHED SYSTEM PROMPT ==========
    
    system_prompt = f"""
You are Polaris, an intelligent AI assistant for {display_name}.

[RECENT CONVERSATION HISTORY]:
{history_context if history_context else "No recent conversation history."}

[RELEVANT LONG-TERM MEMORIES]:
{semantic_context if semantic_context else "No relevant memories found."}

[APPROVED LEARNING RULES]:
{heuristics_context if heuristics_context else "No approved rules yet."}

[INSTRUCTIONS]:
- Personalize responses based on the context above
- Be concise and conversational (under 3 sentences unless asked for detail)
- Use dry humor when appropriate
- Always prioritize being helpful
"""
    
    # ========== SEND TO OLLAMA ==========
    
    try:
        ollama_payload = {
            "model": "my-stark-ai",
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False
        }
        
        response = requests.post(OLLAMA_URL, json=ollama_payload, timeout=120)
        response.raise_for_status()
        ollama_response = response.json().get("response", "")
        
        # ========== POST-EXECUTION LOGGING ==========
        
        timestamp = datetime.now()
        
        # Log user message to SQLite
        db.add_chat_message(
            user_id=user_id,
            sender="user",
            message=user_prompt,
            client_source=client_source
        )
        
        # Log Polaris response to SQLite
        db.add_chat_message(
            user_id=user_id,
            sender="polaris",
            message=ollama_response,
            client_source=client_source
        )
        
        # Add to ChromaDB conversation context
        try:
            vector.add_conversation_context(
                session_id=session_id,
                context_id=f"turn_{timestamp.timestamp()}",
                content=f"User: {user_prompt}\nPolaris: {ollama_response}",
                metadata={
                    "user_id": user_id,
                    "timestamp": timestamp.isoformat(),
                    "client_source": client_source
                }
            )
        except Exception as e:
            print(f"[Gateway] Failed to add to vector context: {e}")
        
        # Asynchronously add to user memories (for long-term retention)
        try:
            vector.add_user_memory(
                user_id=user_id,
                memory_id=f"mem_{timestamp.timestamp()}",
                content=user_prompt,
                memory_type="user_input",
                metadata={"timestamp": timestamp.isoformat()}
            )
        except Exception as e:
            print(f"[Gateway] Failed to add to long-term memory: {e}")
        
        # Broadcast user message first via Socket.IO to sync all connected clients
        socketio.emit('chat_message', {
            "user_id": user_id,
            "sender": "user",
            "message": user_prompt,
            "timestamp": timestamp.isoformat(),
            "client_source": client_source
        })
        
        # Broadcast Polaris response via Socket.IO to sync all connected clients
        socketio.emit('chat_message', {
            "user_id": user_id,
            "sender": "polaris",
            "message": ollama_response,
            "timestamp": timestamp.isoformat(),
            "client_source": client_source
        })
        
        return jsonify({
            "status": "success",
            "user": display_name,
            "response": ollama_response,
            "timestamp": timestamp.isoformat()
        })
        
    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": "Ollama request failed",
            "message": str(e)
        }), 503
    except Exception as e:
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500
# ============== CHAT HISTORY API ENDPOINTS ==============

@app.route('/api/chat/history', methods=['GET', 'POST', 'DELETE'])
def chat_history_handler():
    """Handle GET, POST, DELETE for chat history."""
    db = get_db()
    
    # Priority: user_id from request body > query param > IP mapping
    data = request.get_json() if request.is_json else {}
    user_id = data.get('user_id') or request.args.get('user')
    
    if not user_id:
        user_ip = get_user_ip()
        user_id = get_user_id(user_ip)
    
    if not user_id:
        return jsonify({"error": "Unknown user"}), 400
    
    if request.method == 'GET':
        """Get complete chat history for the current user."""
        messages = db.get_all_chat_history(user_id)
        return jsonify({"user_id": user_id, "messages": messages})
    
    elif request.method == 'POST':
        """Save or replace chat history for the current user."""
        data = request.get_json()
        messages = data.get('messages', [])
        
        # Clear existing history and insert new messages
        db.clear_chat_history(user_id)
        
        # Insert each message
        for msg in messages:
            msg_type = msg.get('type', 'user')
            content = msg.get('content', '')
            sender = msg.get('sender', user_id)
            timestamp = msg.get('timestamp', datetime.utcnow().isoformat())
            
            # Determine sender type
            if msg_type == 'polaris':
                actual_sender = 'polaris'
            elif msg_type == 'other-user':
                actual_sender = sender
            else:
                actual_sender = user_id
            
            db.save_message(
                user_id=user_id,
                sender=actual_sender,
                client_source='dashboard',
                message=content,
                timestamp=timestamp
            )
        
        return jsonify({"status": "success", "count": len(messages)})
    
    elif request.method == 'DELETE':
        """Clear all chat history for the current user."""
        success = db.clear_chat_history(user_id)
        if success:
            return jsonify({"status": "cleared", "user_id": user_id})
        else:
            return jsonify({"error": "Failed to clear history"}), 500



@app.route('/api/memory/status', methods=['GET'])
def memory_status():
    """Get memory system status and statistics."""
    try:
        db = get_db()
        vector = get_vector()
        
        db_stats = db.get_stats()
        vector_stats = vector.get_collection_stats()
        
        return jsonify({
            "status": "healthy",
            "sqlite": {
                "database": DEFAULT_DB_PATH,
                "users": db_stats.get('total_users', 0),
                "chat_messages": db_stats.get('total_messages', 0),
                "heuristics": db_stats.get('total_heuristics', 0)
            },
            "chromadb": {
                "path": "/data/polaris_vector_db",
                "collections": vector_stats
            }
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/memory/stats', methods=['GET'])
def memory_stats():
    """Get memory system statistics in dashboard format."""
    try:
        db = get_db()
        vector = get_vector()
        
        db_stats = db.get_stats()
        vector_stats = vector.get_collection_stats()
        
        # Return format expected by dashboard
        return jsonify({
            "sqlite": {
                "users": db_stats.get('total_users', 0),
                "messages": db_stats.get('total_messages', 0),
                "heuristics": {
                    "staged": 0,
                    "approved": 0,
                    "total": db_stats.get('total_heuristics', 0)
                }
            },
            "chromadb": {
                "global_knowledge": vector_stats.get('global_knowledge', 0) if isinstance(vector_stats, dict) else 0,
                "user_memories": vector_stats.get('user_memories', 0) if isinstance(vector_stats, dict) else 0,
                "conversation_context": vector_stats.get('conversation_context', 0) if isinstance(vector_stats, dict) else 0
            }
        })
    except Exception as e:
        return jsonify({
            "sqlite": {"users": 0, "messages": 0, "heuristics": {"staged": 0, "approved": 0, "total": 0}},
            "chromadb": {"global_knowledge": 0, "user_memories": 0, "conversation_context": 0}
        }), 500

@app.route('/api/users', methods=['GET'])
def list_users():
    """List all registered users."""
    db = get_db()
    users = db.list_users()
    return jsonify({"users": users})

@app.route('/api/history/<user_id>', methods=['GET'])
def get_history(user_id: str):
    """Get most recent 5 chat messages for a specific user."""
    db = get_db()
    messages = db.get_recent_chat_history(user_id, limit=5)
    # Reverse to show newest last (for chat display)
    messages.reverse()
    return jsonify({"user_id": user_id, "messages": messages, "count": len(messages)})

@app.route('/api/history', methods=['GET'])
def get_all_users_history():
    """Get most recent 10 chat messages from Rich's conversation (5 from Rich, 5 from Polaris)."""
    db = get_db()
    # Fetch last 10 messages from Rich's conversation (includes both Rich's messages and Polaris responses)
    messages = db.get_recent_chat_history('rich', limit=10)
    # Reverse to show newest last (for chat display)
    messages.reverse()
    return jsonify({"messages": messages, "count": len(messages)})

@app.route('/api/heuristics', methods=['GET'])
def list_heuristics():
    """List heuristics with optional filtering."""
    db = get_db()
    user_id = request.args.get('user_id')
    status = request.args.get('status', 'staged')
    
    if user_id:
        heuristics = db.get_heuristics_by_status(status, user_id=user_id)
    else:
        heuristics = db.get_heuristics_by_status(status)
    
    return jsonify({"heuristics": heuristics})

@app.route('/api/heuristics/<int:rule_id>/approve', methods=['POST'])
def approve_heuristic(rule_id: int):
    """Approve a staged heuristic."""
    db = get_db()
    if db.update_heuristic_status(rule_id, 'approved'):
        return jsonify({"status": "success", "rule_id": rule_id, "new_status": "approved"})
    return jsonify({"error": "Failed to update heuristic"}), 500

@app.route('/api/heuristics/<int:rule_id>/reject', methods=['POST'])
def reject_heuristic(rule_id: int):
    """Reject a staged heuristic."""
    db = get_db()
    if db.update_heuristic_status(rule_id, 'rejected'):
        return jsonify({"status": "success", "rule_id": rule_id, "new_status": "rejected"})
    return jsonify({"error": "Failed to update heuristic"}), 500

@app.route('/api/vector/search', methods=['POST'])
def vector_search():
    """Perform hybrid vector search across all memory sources."""
    data = request.json
    query = data.get("query", "")
    user_id = data.get("user_id")
    limit = data.get("limit", 5)
    
    if not query:
        return jsonify({"error": "Empty query"}), 400
    
    vector = get_vector()
    
    if user_id:
        results = vector.hybrid_search(user_id=user_id, query=query, limit=limit)
    else:
        results = vector.search_global_knowledge(query, limit=limit)
    
    return jsonify({"query": query, "results": results})

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    print("[SocketIO] Client connected")
    emit('connected', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print("[SocketIO] Client disconnected")

@socketio.on('request_history')
def handle_request_history(data):
    """Handle chat history request from client."""
    from memory_db import get_memory_db
    
    client_ip = get_user_ip()
    user_id = get_user_id(client_ip)
    
    # Allow query parameter override for device_id
    device_id = data.get('device_id')
    if device_id:
        user_id = device_id
    
    if not user_id:
        emit('chat_history', {
            'error': 'Unknown user',
            'messages': []
        })
        return
    
    print(f"[SocketIO] History request from {user_id}")
    
    db = get_memory_db()
    messages = db.get_all_chat_history(user_id)
    
    # Return last 10 messages (rolling history)
    recent_messages = messages[-10:] if len(messages) > 10 else messages
    
    emit('chat_history', {
        'user_id': user_id,
        'messages': recent_messages,
        'count': len(recent_messages)
    })
    print(f"[SocketIO] Sent {len(recent_messages)} messages to {user_id}")

def initialize_system():
    """Initialize memory systems and seed default data."""
    print("🧠 Initializing Polaris Memory Systems...")
    
    try:
        db = get_db()
        print("✓ SQLite memory database initialized")
        
        # Seed default users if not exists
        db.create_or_update_user("rich", "Rich", "42")
        db.create_or_update_user("matt", "Matt", "98")
        db.create_or_update_user("operator", "Operator", "51")
        print("✓ Default users seeded")
        
    except Exception as e:
        print(f"⚠ SQLite initialization warning: {e}")
    
    try:
        vector = get_vector()
        print("✓ ChromaDB vector memory initialized")
    except Exception as e:
        print(f"⚠ ChromaDB initialization warning: {e}")
        print("  (ChromaDB requires container environment with chromadb installed)")
    
    print("")

@app.route('/api/whoami', methods=['GET'])
def whoami():
    """Return current user identification."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if config:
        return jsonify({
            "user": config["name"],
            "ip": client_ip,
            "profile_file": config["profile_file"],
            "transcript_file": config["transcript_file"]
        })
    else:
        return jsonify({
            "error": "Unknown user",
            "ip": client_ip
        }), 404

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
        save_profile(config, profile)
        return jsonify({"status": "success", "fact": new_fact})
    
    return jsonify({"status": "ignored", "reason": "Empty or duplicate fact"}), 400

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

# ============== LIST MANAGEMENT ENGINE ==============

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
    
    original_count = len(lists[list_name])
    lists[list_name] = [i for i in lists[list_name] if i.lower() != item.lower()]
    save_lists(config, lists)
    
    if len(lists[list_name]) == original_count:
        return jsonify({"error": f"Item '{item}' not found in list"}), 404
    
    return jsonify({
        "status": "success",
        "list": list_name,
        "item": item,
        "count": len(lists[list_name])
    })

@app.route('/api/lists/<list_name>/clear', methods=['POST'])
def clear_list(list_name):
    """Clear all items from a specific list."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    lists = load_lists(config)
    if list_name not in lists:
        return jsonify({"error": f"List '{list_name}' not found"}), 404
    
    removed_count = len(lists[list_name])
    lists[list_name] = []
    save_lists(config, lists)
    
    return jsonify({
        "status": "success",
        "list": list_name,
        "removed_count": removed_count
    })

@app.route('/api/lists/<list_name>/read', methods=['GET'])
def read_list_formatted(list_name):
    """Get a list formatted for TTS display."""
    client_ip = get_user_ip()
    config = get_user_config(client_ip)
    
    if not config:
        return jsonify({"error": "User not found"}), 404
    
    lists = load_lists(config)
    if list_name not in lists:
        return jsonify({"error": f"List '{list_name}' not found"}), 404
    
    items = lists[list_name]
    if not items:
        formatted = f"Your {list_name.replace('_', ' ')} is empty."
    else:
        formatted = f"Your {list_name.replace('_', ' ')} ({len(items)} items):\n"
        for i, item in enumerate(items, 1):
            formatted += f"  {i}. {item}\n"
    
    return jsonify({
        "list": list_name,
        "count": len(items),
        "formatted": formatted,
        "items": items
    })

# ============== DAILY TRANSCRIPT SYNC & DISTILLATION ==============

@app.route('/api/sync/daily-transcript', methods=['POST'])
def sync_daily_transcript():
    """
    Receive daily transcript from Android device and append to user's transcript log.
    Expects JSON: {"date": "YYYY-MM-DD", "transcript": "full text content"}
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
    
    # Append to user's transcript log with date header
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
    
    # Step 2: Trigger distillation
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
            "synced_date": date,
            "distillation_output": result.stdout,
            "facts_extracted": len([l for l in result.stdout.split('\n') if '+ Added:' in l])
        })
    except subprocess.TimeoutExpired:
        return jsonify({
            "status": "partial",
            "user": config["name"],
            "synced_date": date,
            "error": "Distillation timed out but transcript was saved"
        }), 504
    except Exception as e:
        return jsonify({
            "status": "partial",
            "user": config["name"],
            "error": str(e)
        }), 500

# ============== JARVIS SYSTEM ADMINISTRATION API ==============

@app.route('/api/jarvis/system/health', methods=['GET'])
def jarvis_system_health():
    """Get comprehensive system health metrics."""
    return jsonify(system_tools.get_system_health())

@app.route('/api/jarvis/docker/stats', methods=['GET'])
def jarvis_docker_stats():
    """Get Docker container statistics."""
    return jsonify(system_tools.get_docker_stats())

@app.route('/api/jarvis/questdb/health', methods=['GET'])
def jarvis_questdb_health():
    """Get QuestDB health status."""
    return jsonify(system_tools.get_questdb_health())

@app.route('/api/jarvis/rippled/health', methods=['GET'])
def jarvis_rippled_health():
    """Get rippled node health status."""
    return jsonify(system_tools.get_rippled_health())

@app.route('/api/jarvis/container/restart', methods=['POST'])
def jarvis_restart_container():
    """Restart a Docker container."""
    data = request.json
    container_name = data.get("container_name", "")
    return jsonify(system_tools.restart_container(container_name))

@app.route('/api/jarvis/container/start', methods=['POST'])
def jarvis_start_container():
    """Start a Docker container."""
    data = request.json
    container_name = data.get("container_name", "")
    return jsonify(system_tools.start_container(container_name))

@app.route('/api/jarvis/container/stop', methods=['POST'])
def jarvis_stop_container():
    """Stop a Docker container."""
    data = request.json
    container_name = data.get("container_name", "")
    return jsonify(system_tools.stop_container(container_name))

@app.route('/api/jarvis/container/logs', methods=['GET'])
def jarvis_container_logs():
    """Get container logs."""
    container_name = request.args.get("container_name", "")
    lines = int(request.args.get("lines", 50))
    return jsonify(system_tools.get_container_logs(container_name, lines))

@app.route('/api/jarvis/emergency/shutdown-trading-bot', methods=['POST'])
def jarvis_emergency_shutdown():
    """Emergency shutdown of trading-bot."""
    data = request.json
    approval_code = data.get("approval_code", "")
    return jsonify(system_tools.emergency_shutdown_trading_bot(approval_code))

@app.route('/api/jarvis/network/ping', methods=['GET'])
def jarvis_ping():
    """Ping a host."""
    hostname = request.args.get("hostname", "")
    count = int(request.args.get("count", 4))
    return jsonify(system_tools.ping_host(hostname, count))

@app.route('/api/jarvis/network/check-port', methods=['GET'])
def jarvis_check_port():
    """Check if a port is open."""
    host = request.args.get("host", "")
    port = int(request.args.get("port", 0))
    return jsonify(system_tools.check_port_open(host, port))

# ============== JARVIS LIVECHARTS API ==============

@app.route('/api/jarvis/livecharts/info', methods=['GET'])
def jarvis_livecharts_info():
    """Get LiveCharts setup information."""
    return jsonify(livecharts_tools.get_livecharts_info())

@app.route('/api/jarvis/livecharts/read', methods=['GET'])
def jarvis_livecharts_read():
    """Read LiveCharts.html content."""
    return jsonify(livecharts_tools.read_livecharts_html())

@app.route('/api/jarvis/livecharts/update', methods=['POST'])
def jarvis_livecharts_update():
    """Update LiveCharts.html content."""
    data = request.json
    content = data.get("content", "")
    backup = data.get("backup", True)
    return jsonify(livecharts_tools.update_livecharts_html(content, backup))

@app.route('/api/jarvis/livecharts/search', methods=['GET'])
def jarvis_livecharts_search():
    """Search in LiveCharts.html."""
    pattern = request.args.get("pattern", "")
    return jsonify(livecharts_tools.search_in_livecharts(pattern))

@app.route('/api/jarvis/livecharts/nginx', methods=['GET'])
def jarvis_livecharts_nginx():
    """Read LiveCharts nginx.conf."""
    return jsonify(livecharts_tools.read_nginx_conf())

# ============== JARVIS WWW FOLDER API ==============

@app.route('/api/jarvis/www/info', methods=['GET'])
def jarvis_www_info():
    """Get www folder setup information."""
    return jsonify(www_tools.get_www_info())

@app.route('/api/jarvis/www/list', methods=['GET'])
def jarvis_www_list():
    """List contents of www directory."""
    path = request.args.get("path", "")
    return jsonify(www_tools.list_www_directory(path))

@app.route('/api/jarvis/www/read', methods=['GET'])
def jarvis_www_read():
    """Read a file from www folder."""
    file_path = request.args.get("file", "")
    return jsonify(www_tools.read_www_file(file_path))

@app.route('/api/jarvis/www/write', methods=['POST'])
def jarvis_www_write():
    """Write a file to www folder."""
    data = request.json
    file_path = data.get("file_path", "")
    content = data.get("content", "")
    backup = data.get("backup", True)
    return jsonify(www_tools.write_www_file(file_path, content, backup))

@app.route('/api/jarvis/www/delete', methods=['POST'])
def jarvis_www_delete():
    """Delete a file from www folder."""
    data = request.json
    file_path = data.get("file_path", "")
    return jsonify(www_tools.delete_www_file(file_path))

@app.route('/api/jarvis/www/search', methods=['GET'])
def jarvis_www_search():
    """Search in www folder files."""
    pattern = request.args.get("pattern", "")
    extension = request.args.get("extension", ".html")
    return jsonify(www_tools.search_in_www(pattern, extension))

# ============== MAIN APPLICATION ==============

if __name__ == '__main__':
    print("🚀 Polaris Gateway starting on port 5000...")
    print("📍 Endpoint: http://0.0.0.0:5000/api/chat")
    print("🔒 LAN-only access enabled")
    print(f"👤 Allowed users: Rich ({RICH_IP}), Matt ({MATT_IP}), Operator ({OPERATOR_IP})")
    print("🧠 Memory Architecture: SQLite + ChromaDB")
    print("")
    
    initialize_system()
    
    # Run with Socket.IO
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)