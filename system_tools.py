"""
Polaris Gateway - System Diagnostics & Emergency Toolkit

Provides system monitoring, Docker container management, and emergency action capabilities
for IT department integration and autonomous system maintenance.
"""

import os
import json
import subprocess
import shutil
from datetime import datetime
from typing import Dict, List, Optional
import requests


# ============== SYSTEM MONITORING ==============

def get_system_health() -> Dict:
    """Get comprehensive system health metrics."""
    try:
        # Memory usage
        mem_cmd = ["cat", "/proc/meminfo"]
        mem_result = subprocess.run(mem_cmd, capture_output=True, text=True, timeout=5)
        
        # Disk usage
        disk_cmd = ["df", "-h", "/"]
        disk_result = subprocess.run(disk_cmd, capture_output=True, text=True, timeout=5)
        
        # Uptime
        uptime_cmd = ["cat", "/proc/uptime"]
        uptime_result = subprocess.run(uptime_cmd, capture_output=True, text=True, timeout=5)
        
        # Parse memory
        mem_info = {}
        for line in mem_result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip().lower().replace(" ", "_")
                value = parts[1].strip().replace("kB", "").strip()
                mem_info[key] = int(value) if value.isdigit() else value
        
        mem_total_gb = mem_info.get("memtotal", 0) / 1024 / 1024
        mem_available_gb = mem_info.get("memavailable", 0) / 1024 / 1024
        mem_used_percent = ((mem_total_gb - mem_available_gb) / mem_total_gb * 100) if mem_total_gb > 0 else 0
        
        # Parse uptime
        uptime_seconds = float(uptime_result.stdout.split()[0]) if uptime_result.stdout else 0
        uptime_hours = uptime_seconds / 3600
        
        # Parse disk
        disk_lines = disk_result.stdout.splitlines()
        disk_info = {}
        if len(disk_lines) > 1:
            disk_parts = disk_lines[1].split()
            if len(disk_parts) >= 5:
                disk_info = {
                    "total": disk_parts[1],
                    "used": disk_parts[2],
                    "available": disk_parts[3],
                    "use_percent": disk_parts[4]
                }
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "system": {
                "uptime_hours": round(uptime_hours, 2),
                "memory": {
                    "total_gb": round(mem_total_gb, 2),
                    "available_gb": round(mem_available_gb, 2),
                    "used_percent": round(mem_used_percent, 2)
                },
                "disk": disk_info
            },
            "status": "healthy" if mem_used_percent < 90 else "warning"
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to get system health: {str(e)}"}


def get_docker_stats() -> Dict:
    """Get Docker container statistics for all running containers."""
    try:
        # Get running containers
        ps_cmd = ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"]
        ps_result = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=10)
        
        containers = []
        for line in ps_result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                containers.append({
                    "name": parts[0],
                    "status": parts[1],
                    "ports": parts[2] if len(parts) > 2 else ""
                })
        
        # Get stats for key containers
        key_containers = ["polaris-gateway", "trading-bot", "questdb", "rippled", "ollama", "livecharts"]
        stats = {}
        
        for container in key_containers:
            try:
                stats_cmd = ["docker", "stats", "--no-stream", "--format", 
                            "{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}", container]
                stats_result = subprocess.run(stats_cmd, capture_output=True, text=True, timeout=5)
                if stats_result.stdout:
                    stats_parts = stats_result.stdout.strip().split("\t")
                    if len(stats_parts) >= 3:
                        stats[container] = {
                            "cpu_percent": stats_parts[0],
                            "memory": stats_parts[1],
                            "network_io": stats_parts[2],
                            "status": "running"
                        }
            except Exception:
                stats[container] = {"status": "unreachable"}

def get_questdb_health() -> Dict:
    """Check QuestDB health and connection status."""
    try:
        # Try HTTP endpoint
        try:
            response = requests.get("http://questdb:9000/health", timeout=5)
            http_status = response.status_code
        except Exception:
            http_status = 0
        
        # Query row count
        row_count = 0
        try:
            query_url = "http://questdb:9000/exec?query=SELECT%20count()%20FROM%20amm_balances"
            response = requests.get(query_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if "dataset" in data and len(data["dataset"]) > 0:
                    row_count = data["dataset"][0][0]
        except Exception:
            pass
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "questdb": {
                "http_health": http_status,
                "amm_balances_rows": row_count,
                "status": "healthy" if http_status == 200 and row_count > 0 else "degraded"
            }
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to get QuestDB health: {str(e)}"}


def get_rippled_health() -> Dict:
    """Check rippled node health and sync status."""
    try:
        rpc_payload = {"method": "server_info", "params": [{}]}
        
        try:
            response = requests.post(
                "http://rippled:5005",
                json=rpc_payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "result" in data and "info" in data["result"]:
                    info = data["result"]["info"]
                    return {
                        "success": True,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "rippled": {
                            "status": "healthy" if info.get("server_state") == "full" else "syncing",
                            "server_state": info.get("server_state"),
                            "ledger_index": info.get("validated_ledger", {}).get("ledger_index"),
                            "ledger_age_seconds": info.get("validated_ledger", {}).get("age"),
                            "build_version": info.get("build_version"),
                            "peer_count": info.get("peers", 0),
                            "complete_ledgers": info.get("complete_ledgers")
                        }
                    }
        except Exception:
            pass
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "rippled": {
                "status": "unreachable",
                "error": "Could not connect to rippled RPC"
            }
        }

# ============== EMERGENCY ACTIONS ==============

def emergency_shutdown_trading_bot(approval_code: str) -> Dict:
    """Emergency shutdown of trading-bot container. Requires approval code."""
    EXPECTED_APPROVAL = os.environ.get("EMERGENCY_APPROVAL_CODE", "EMERGENCY-2026")
    
    if approval_code != EXPECTED_APPROVAL:
        return {
            "success": False,
            "error": "Invalid approval code",
            "message": "Emergency action requires valid approval code"
        }
    
    try:
        stop_cmd = ["docker", "stop", "trading-bot"]
        result = subprocess.run(stop_cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            return {
                "success": True,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "action": "emergency_shutdown",
                "target": "trading-bot",
                "message": "Trading-bot container stopped successfully",
                "approval_verified": True
            }
        else:
            return {"success": False, "error": f"Docker stop failed: {result.stderr}"}
    except Exception as e:
        return {"success": False, "error": f"Emergency shutdown failed: {str(e)}"}


def restart_container(container_name: str) -> Dict:
    """Restart a Docker container by name."""
    allowed_containers = [
        "polaris-gateway", "polaris-playground", "polaris-dashboard",
        "livecharts", "questdb", "ollama", "trading-bot"
    ]
    
    if container_name not in allowed_containers:
        return {
            "success": False,
            "error": f"Container {container_name} not in allowed restart list"
        }
    
    try:
        restart_cmd = ["docker", "restart", container_name]
        result = subprocess.run(restart_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            return {
                "success": True,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "action": "restart",
                "target": container_name,
                "message": f"Container {container_name} restarted successfully"
            }
        else:
            return {"success": False, "error": f"Docker restart failed: {result.stderr}"}
    except Exception as e:
        return {"success": False, "error": f"Restart failed: {str(e)}"}


def start_container(container_name: str) -> Dict:
    """Start a Docker container by name."""
    try:
        start_cmd = ["docker", "start", container_name]
        result = subprocess.run(start_cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            return {
                "success": True,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "action": "start",
                "target": container_name,
                "message": f"Container {container_name} started successfully"
            }
        else:
            return {"success": False, "error": f"Docker start failed: {result.stderr}"}
    except Exception as e:
        return {"success": False, "error": f"Start failed: {str(e)}"}


def stop_container(container_name: str) -> Dict:
    """Stop a Docker container by name."""
    allowed_containers = [
        "polaris-gateway", "polaris-playground", "polaris-dashboard",
        "livecharts", "trading-bot"
    ]
    
    if container_name not in allowed_containers:
        return {
            "success": False,
            "error": f"Container {container_name} not in allowed stop list"
        }
    
    try:
        stop_cmd = ["docker", "stop", container_name]
        result = subprocess.run(stop_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            return {
                "success": True,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "action": "stop",
                "target": container_name,
                "message": f"Container {container_name} stopped successfully"
            }
        else:
            return {"success": False, "error": f"Docker stop failed: {result.stderr}"}
    except Exception as e:
        return {"success": False, "error": f"Stop failed: {str(e)}"}


def get_container_logs(container_name: str, lines: int = 50) -> Dict:
    """Get recent logs from a Docker container."""
    try:
        logs_cmd = ["docker", "logs", "--tail", str(lines), container_name]
        result = subprocess.run(logs_cmd, capture_output=True, text=True, timeout=30)
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "container": container_name,
            "lines_requested": lines,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to get logs: {str(e)}"}

    except Exception as e:
        return {"success": False, "error": f"Failed to get rippled health: {str(e)}"}

        

# ============== NETWORK DIAGNOSTICS ==============

def ping_host(hostname: str, count: int = 4) -> Dict:
    """Ping a host to check network connectivity."""
    try:
        ping_cmd = ["ping", "-c", str(count), hostname]
        result = subprocess.run(ping_cmd, capture_output=True, text=True, timeout=count + 5)
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "host": hostname,
            "reachable": result.returncode == 0,
            "output": result.stdout
        }
    except Exception as e:
        return {"success": False, "error": f"Ping failed: {str(e)}"}


def check_port_open(host: str, port: int) -> Dict:
    """Check if a TCP port is open on a host."""
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "host": host,
            "port": port,
            "open": result == 0,
            "status": "open" if result == 0 else "closed"
        }
    except Exception as e:
        return {"success": False, "error": f"Port check failed: {str(e)}"}

        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "containers": containers,
            "key_stats": stats,
            "total_containers": len(containers)
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to get Docker stats: {str(e)}"}