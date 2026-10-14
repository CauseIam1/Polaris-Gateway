#!/usr/bin/env python3
"""
Polaris Sandbox Gateway - Port 7007
Dedicated sandbox environment for X17 (LGI, Windows 11) SSH operations
Serves web UI for AI agent visualization and command execution
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import os
from datetime import datetime
from typing import Dict, List
import queue

# Import SSH tools
from ssh_tools import ssh_execute, ssh_check_connectivity, SSH_TOOL_SCHEMAS

app = Flask(__name__, static_folder='sandbox_static')
app.config['SECRET_KEY'] = 'polaris-sandbox-gateway-secret-key'

# Enable CORS for sandbox dashboard access
SANDBOX_URLS = ["http://192.168.50.51:7007", "http://localhost:7007", "http://127.0.0.1:7007", "*"]
CORS(app, resources={r"/api/*": {"origins": SANDBOX_URLS, "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type", "Authorization"]}})

# X17 (LGI) Configuration - legacy MINI_PI_* env names kept for compose compatibility
LGI_X17_HOST = os.environ.get("LGI_X17_HOST", os.environ.get("MINI_PI_HOST", "192.168.50.227"))
LGI_X17_WORKDIR = os.environ.get("LGI_X17_WORKDIR", os.environ.get("MINI_PI_WORKDIR", "D:/LGI"))
MINI_PI_HOST = LGI_X17_HOST
MINI_PI_WORKDIR = LGI_X17_WORKDIR

# Command execution history
command_history: List[Dict] = []
MAX_HISTORY = 100
output_queue = queue.Queue()

@app.route('/')
def dashboard():
    """Serve the sandbox dashboard HTML."""
    recent_history = command_history[-20:] if command_history else []
    connected = False
    try:
        conn_result = ssh_check_connectivity(MINI_PI_HOST, timeout=3)
        connected = conn_result.get('connected', False)
    except: pass
    
    return send_from_directory('sandbox_static', 'index.html')

@app.route('/api/ssh/execute', methods=['POST'])
def api_ssh_execute():
    """Execute SSH command on the X17 (LGI)."""
    data = request.json
    if not data: return jsonify({"error": "No data provided"}), 400
    
    target_host = data.get('target_host', MINI_PI_HOST)
    command = data.get('command')
    timeout = data.get('timeout', 15)
    
    if not command: return jsonify({"error": "Command is required"}), 400
    
    result = ssh_execute(target_host, command, timeout)
    command_history.append(result)
    if len(command_history) > MAX_HISTORY: command_history.pop(0)
    
    try: output_queue.put_nowait(result)
    except queue.Full: pass
    
    return jsonify(result)

@app.route('/api/ssh/check', methods=['POST'])
def api_ssh_check():
    """Check SSH connectivity to the X17 (LGI)."""
    data = request.json or {}
    target_host = data.get('target_host', MINI_PI_HOST)
    timeout = data.get('timeout', 5)
    result = ssh_check_connectivity(target_host, timeout)
    return jsonify(result)

@app.route('/api/ssh/history', methods=['GET'])
def api_ssh_history():
    """Get command execution history."""
    limit = request.args.get('limit', 50, type=int)
    return jsonify(command_history[-limit:])

@app.route('/api/ssh/history/clear', methods=['POST'])
def api_ssh_history_clear():
    """Clear command history."""
    command_history.clear()
    return jsonify({"status": "success", "message": "History cleared"})

@app.route('/api/status', methods=['GET'])
def api_status():
    """Get sandbox gateway status."""
    connected = False
    response_time = None
    try:
        conn_result = ssh_check_connectivity(MINI_PI_HOST, timeout=3)
        connected = conn_result.get('connected', False)
        response_time = conn_result.get('response_time_ms')
    except: pass
    
    return jsonify({
        "status": "online",
        "lgi_host": MINI_PI_HOST,
        "lgi_workdir": MINI_PI_WORKDIR,
        "connected": connected,
        "response_time_ms": response_time,
        "command_count": len(command_history),
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })

if __name__ == '__main__':
    print("🔮 Polaris Sandbox Gateway Starting...")
    print(f"📍 Dashboard: http://0.0.0.0:7007/")
    print(f"🔌 LGI X17 Host: {MINI_PI_HOST}")
    print(f"📂 WorkDir: {MINI_PI_WORKDIR}")
    print(f"🛠️  SSH Tools: {len(SSH_TOOL_SCHEMAS)} tools available")
    print("")
    print("API Endpoints:")
    print("  POST /api/ssh/execute    - Execute SSH command")
    print("  POST /api/ssh/check      - Check connectivity")
    print("  GET  /api/ssh/history    - Get command history")
    print("  GET  /api/status         - Gateway status")
    print("")
    
    os.makedirs('sandbox_static', exist_ok=True)
    app.run(host='0.0.0.0', port=7007, debug=False, threaded=True)
