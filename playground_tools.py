"""
Stark's Playground - Autonomous Holodeck Tool Module

Provides web asset publishing, code sandbox execution, and service management
for the Polaris Gateway continuous learning system.
"""

import os
import json
import subprocess
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Playground base directory (mounted from host)
PLAYGROUND_BASE = "/data/freeroam/polaris-gateway/playground"
PLAYGROUND_WWW = os.path.join(PLAYGROUND_BASE, "www")
PLAYGROUND_SCRIPTS = os.path.join(PLAYGROUND_BASE, "scripts")

# Service tracking
ACTIVE_SERVICES: Dict[int, Dict] = {}  # port -> {pid, script, started}

# LAN IP for URLs (should match your network configuration)
LAN_IP = os.environ.get("LAN_IP", "192.168.50.51")


def _validate_path(path: str, base_dir: str) -> Optional[str]:
    """Validate and resolve a path, preventing directory traversal."""
    if not path:
        return None
    
    # Reject absolute paths or directory traversal
    if path.startswith("/") or ".." in path:
        return None
    
    # Resolve the full path
    full_path = os.path.normpath(os.path.join(base_dir, path))
    
    # Ensure it's within the base directory
    if not full_path.startswith(base_dir):
        return None
    
    return full_path


def _ensure_directories():
    """Ensure playground directories exist."""
    os.makedirs(PLAYGROUND_WWW, exist_ok=True)
    os.makedirs(PLAYGROUND_SCRIPTS, exist_ok=True)


def publish_web_asset(filename: str, content: str) -> Dict:
    """
    Publish a web asset (HTML/CSS/JS) to the live web server.
    """
    try:
        _ensure_directories()
        
        # Validate filename
        if not filename or ".." in filename or filename.startswith("/"):
            return {"success": False, "error": "Invalid filename"}
        
        # Security: only allow safe extensions
        allowed_extensions = [".html", ".htm", ".css", ".js", ".json", ".txt", ".md"]
        ext = os.path.splitext(filename)[1].lower()
        if ext not in allowed_extensions:
            return {
                "success": False,
                "error": f"Extension {ext} not allowed. Use: {', '.join(allowed_extensions)}"
            }
        
        # Write the file
        file_path = os.path.join(PLAYGROUND_WWW, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        file_size = os.path.getsize(file_path)
        live_url = f"http://{LAN_IP}:8090/{filename}"
        
        return {
            "success": True,
            "message": f"Published {filename} to live web server",
            "live_url": live_url,
            "file_size": file_size,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as e:
        return {"success": False, "error": f"Failed to publish asset: {str(e)}"}


def list_playground_files(directory: str = "all") -> Dict:
    """
    List files in playground directories.
    """
    try:
        _ensure_directories()
        
        result = {"success": True, "directories": {}}
        
        dirs_to_list = []
        if directory == "all":
            dirs_to_list = [("www", PLAYGROUND_WWW), ("scripts", PLAYGROUND_SCRIPTS)]
        elif directory == "www":
            dirs_to_list = [("www", PLAYGROUND_WWW)]
        elif directory == "scripts":
            dirs_to_list = [("scripts", PLAYGROUND_SCRIPTS)]
        else:
            return {"success": False, "error": f"Unknown directory: {directory}"}
        
        for dir_name, dir_path in dirs_to_list:
            files = []
            if os.path.exists(dir_path):
                for entry in os.listdir(dir_path):
                    entry_path = os.path.join(dir_path, entry)
                    stat = os.stat(entry_path)
                    files.append({
                        "name": entry,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "is_directory": os.path.isdir(entry_path)
                    })
            result["directories"][dir_name] = files
        
        return result
        
    except Exception as e:
        return {"success": False, "error": f"Failed to list files: {str(e)}"}


def run_sandbox_code(language: str, code: str, timeout: int = 15) -> Dict:
    """
    Execute code in a sandboxed environment with resource limits.
    """
    try:
        _ensure_directories()
        
        # Validate timeout
        timeout = min(timeout, 60)  # Max 60 seconds
        timeout = max(timeout, 1)   # Min 1 second
        
        # Validate language
        language = language.lower()
        if language not in ["python", "bash"]:
            return {"success": False, "error": f"Unsupported language: {language}"}
        
        # Create a temporary script
        timestamp = int(time.time() * 1000)
        if language == "python":
            script_name = f"sandbox_{timestamp}.py"
            interpreter = ["python3", "-u"]  # Unbuffered output
        else:  # bash
            script_name = f"sandbox_{timestamp}.sh"
            interpreter = ["bash"]
        
        script_path = os.path.join(PLAYGROUND_SCRIPTS, script_name)
        
        # Write the script
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
        os.chmod(script_path, 0o755)
        
        # Execute with resource limits
        start_time = time.time()
        
        try:
            result = subprocess.run(
                interpreter + [script_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=PLAYGROUND_SCRIPTS,
                env={**os.environ, "PYTHONUNBUFFERED": "1"}
            )
            
            execution_time = time.time() - start_time
            
            return {
                "success": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "execution_time": f"{execution_time:.2f}s",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Execution timeout after {timeout} seconds",
                "timeout": timeout
            }
        finally:
            # Cleanup: remove the temporary script
            try:
                os.remove(script_path)
            except:
                pass
        
    except Exception as e:
        return {"success": False, "error": f"Sandbox execution error: {str(e)}"}


def spawn_service(entry_script: str, port: int, language: str = "python") -> Dict:
    """
    Spawn a background service on a specific port.
    """
    try:
        _ensure_directories()
        
        # Validate port range
        if port < 8091 or port > 8099:
            return {"success": False, "error": "Port must be between 8091 and 8099"}
        
        # Check if port is already in use
        if port in ACTIVE_SERVICES:
            return {"success": False, "error": f"Port {port} already in use"}
        
        # Validate script exists
        script_path = os.path.join(PLAYGROUND_SCRIPTS, entry_script)
        if not os.path.exists(script_path):
            return {"success": False, "error": f"Script not found: {entry_script}"}
        
        # Determine interpreter
        language = language.lower()
        if language == "python":
            interpreter = ["python3", "-u"]
        elif language == "node":
            interpreter = ["node"]
        elif language == "bash":
            interpreter = ["bash"]
        else:
            return {"success": False, "error": f"Unsupported language: {language}"}
        
        # Start the process
        env = {**os.environ, "PORT": str(port), "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(
            interpreter + [script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=PLAYGROUND_SCRIPTS,
            env=env,
            start_new_session=True  # New process group
        )
        
        # Track the service
        ACTIVE_SERVICES[port] = {
            "pid": process.pid,
            "script": entry_script,
            "started": datetime.utcnow().isoformat() + "Z",
            "process": process
        }
        
        live_url = f"http://{LAN_IP}:{port}"
        
        return {
            "success": True,
            "message": f"Service spawned successfully on port {port}",
            "live_url": live_url,
            "port": port,
            "pid": process.pid,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as e:
        return {"success": False, "error": f"Failed to spawn service: {str(e)}"}


def stop_service(port: int) -> Dict:
    """
    Stop a running service by port number.
    """
    try:
        if port not in ACTIVE_SERVICES:
            return {"success": False, "error": f"No service running on port {port}"}
        
        service_info = ACTIVE_SERVICES[port]
        pid = service_info["pid"]
        
        try:
            # Kill the process group
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            
            # Wait briefly for graceful shutdown
            time.sleep(0.5)
            
            # Force kill if still running
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except:
                pass
            
            del ACTIVE_SERVICES[port]
            
            return {
                "success": True,
                "message": f"Service on port {port} stopped",
                "pid": pid,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
        except Exception as e:
            return {"success": False, "error": f"Failed to stop service: {str(e)}"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_services() -> Dict:
    """
    List all currently running spawned services.
    """
    try:
        services = []
        
        for port, info in ACTIVE_SERVICES.items():
            # Check if process is still running
            process = info.get("process")
            is_running = False
            if process:
                try:
                    process.poll()
                    is_running = process.returncode is None
                except:
                    is_running = False
            
            services.append({
                "port": port,
                "script": info["script"],
                "pid": info["pid"],
                "started": info["started"],
                "running": is_running,
                "live_url": f"http://{LAN_IP}:{port}"
            })
        
        return {
            "success": True,
            "services": services,
            "count": len(services),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


# Export all tool functions
__all__ = [
    "publish_web_asset",
    "list_playground_files",
    "run_sandbox_code",
    "spawn_service",
    "stop_service",
    "list_services"
]


# Tool schemas for LLM function calling
PLAYGROUND_TOOL_SCHEMAS = [
    {
        "name": "publish_web_asset",
        "description": "Publish a web asset (HTML/CSS/JS) to the live web server at port 8090",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename with extension (e.g., dashboard.html)"},
                "content": {"type": "string", "description": "Complete file content"}
            },
            "required": ["filename", "content"]
        }
    },
    {
        "name": "list_playground_files",
        "description": "List files in playground directories (www, scripts, or all)",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "enum": ["www", "scripts", "all"], "description": "Directory to list"}
            },
            "required": []
        }
    },
    {
        "name": "run_sandbox_code",
        "description": "Execute code in a sandboxed environment with 60s timeout limit",
        "parameters": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "bash"], "description": "Programming language"},
                "code": {"type": "string", "description": "Code to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (1-60, default 15)"}
            },
            "required": ["language", "code"]
        }
    },
    {
        "name": "spawn_service",
        "description": "Spawn a background service on port 8091-8099",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_script": {"type": "string", "description": "Script filename in scripts/ directory"},
                "port": {"type": "integer", "description": "Port number (8091-8099)"},
                "language": {"type": "string", "enum": ["python", "node", "bash"], "description": "Script language"}
            },
            "required": ["entry_script", "port"]
        }
    },
    {
        "name": "stop_service",
        "description": "Stop a running service by port number",
        "parameters": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "Port of service to stop"}
            },
            "required": ["port"]
        }
    },
    {
        "name": "list_services",
        "description": "List all currently running spawned services",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


def get_playground_tool_schemas_json() -> str:
    """Return tool schemas as JSON string for LLM context."""
    return json.dumps(PLAYGROUND_TOOL_SCHEMAS, indent=2)
