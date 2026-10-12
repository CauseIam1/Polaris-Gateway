#!/usr/bin/env python3
"""
Polaris SSH Tools - Remote Command Execution Suite
Secure SSH execution for the X17 (LGI) and other trusted nodes
"""

import os
import subprocess
import json
from typing import Dict, Optional
from datetime import datetime


def _classify_ssh_failure(returncode: int, stderr: str) -> Dict:
    """
    Classify an SSH failure into a semantic diagnosis Polaris can act on.

    SSH exit code 255 means the SSH connection itself could not be established.
    Any other exit code means the SSH session SUCCEEDED and the remote command
    itself failed - which proves the device is online.

    Returns a Dict with:
        reachable - bool: is the device powered and on the network at all?
        reason    - machine-readable failure code
        hint      - plain-English diagnosis for the LLM to relay
    """
    text = (stderr or "").lower()

    if returncode != 255:
        return {
            "reachable": True,
            "reason": "command_failed",
            "hint": "Device is ONLINE and reachable - the remote command ran but exited non-zero"
        }

    if "no route to host" in text:
        return {
            "reachable": False,
            "reason": "host_unreachable",
            "hint": "Device is OFF or not on the network (no route to host - zero network response)"
        }
    if "connection refused" in text:
        return {
            "reachable": True,
            "reason": "connection_refused",
            "hint": "Device is ON (network reachable) but its SSH service is not running"
        }
    if "timed out" in text or "timeout" in text:
        return {
            "reachable": False,
            "reason": "timeout",
            "hint": "Device did not respond in time - it is OFF, off the network, or hung"
        }
    if "permission denied" in text:
        return {
            "reachable": True,
            "reason": "auth_failure",
            "hint": "Device is ON - SSH authentication failed (credentials or key issue)"
        }
    if "could not resolve hostname" in text or "name or service not known" in text:
        return {
            "reachable": False,
            "reason": "dns_failure",
            "hint": "Hostname could not be resolved - check the hostname or IP address"
        }
    if "host key verification failed" in text:
        return {
            "reachable": True,
            "reason": "host_key_rejected",
            "hint": "Device is ON - SSH rejected the host key"
        }
    return {
        "reachable": False,
        "reason": "ssh_connection_failure",
        "hint": "SSH could not establish a connection - the device is likely OFF or off the network"
    }


def ssh_execute(target_host: str, command: str, timeout: int = 15, ssh_user: str = "rbuit") -> Dict:
    """
    Execute a shell command via SSH on a remote node.
    
    Args:
        target_host: IP address or hostname of the remote node (e.g., the X17 / LGI host)
        command: Shell command to execute remotely (cmd.exe on the X17/Windows, bash on the Mainframe/Linux)
        timeout: Execution timeout in seconds (default: 15)
        ssh_user: SSH username on the target node (default 'rbuit' for the X17; use 'causeiam' for the Mainframe host at 192.168.50.51)
    
    Returns:
        Dict with success status, stdout, stderr, returncode, and metadata
    """
    try:
        # Build SSH command with security options
        ssh_command = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={min(timeout, 30)}",
            "-o", "IdentitiesOnly=yes",
            "-i", "/root/.ssh/id_ed25519",
            f"{ssh_user}@{target_host}",
            command
        ]
        
        # Execute the command
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        # Format the response
        response = {
            "success": result.returncode == 0,
            "target_host": target_host,
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout.strip() if result.stdout else "",
            "stderr": result.stderr.strip() if result.stderr else "",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        # Add reachability info and semantic diagnosis
        if result.returncode == 0:
            response["reachable"] = True
        else:
            # Classify the failure so the LLM can answer on/off questions
            diag = _classify_ssh_failure(result.returncode, result.stderr or "")
            response["reachable"] = diag["reachable"]
            response["reason"] = diag["reason"]
            response["hint"] = diag["hint"]
            response["error"] = (
                f"{diag['hint']}. "
                f"(SSH command failed with return code {result.returncode}"
                + (f": {result.stderr.strip()}" if result.stderr else "")
                + ")"
            )
        
        return response
        
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "target_host": target_host,
            "command": command,
            "reachable": False,
            "reason": "timeout",
            "hint": "Device did not respond in time - it is OFF, off the network, or hung",
            "error": f"Command timed out after {timeout} seconds - device appears OFF or unreachable",
            "returncode": -1,
            "stdout": "",
            "stderr": f"Timeout: Command exceeded {timeout} second limit",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
    except FileNotFoundError:
        return {
            "success": False,
            "target_host": target_host,
            "command": command,
            "error": "SSH client not found. Please ensure ssh is installed and in PATH",
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as e:
        return {
            "success": False,
            "target_host": target_host,
            "command": command,
            "error": f"SSH execution error: {str(e)}",
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

def ssh_check_connectivity(target_host: str, timeout: int = 5, ssh_user: str = "rbuit") -> Dict:
    """
    Check SSH connectivity to a remote node without executing commands.
    
    Args:
        target_host: IP address or hostname of the remote node
        timeout: Connection timeout in seconds
        ssh_user: SSH username on the target node (default 'rbuit' for the X17; use 'causeiam' for the Mainframe host at 192.168.50.51)
    
    Returns:
        Dict with connectivity status and response time
    """
    try:
        ssh_command = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={timeout}",
            "-o", "IdentitiesOnly=yes",
            "-i", "/root/.ssh/id_ed25519",
            f"{ssh_user}@{target_host}",
            "echo 'connection_test'"
        ]
        
        start_time = datetime.now()
        result = subprocess.run(
            ssh_command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        end_time = datetime.now()
        
        response_time_ms = int((end_time - start_time).total_seconds() * 1000)
        
        if result.returncode == 0 and "connection_test" in result.stdout:
            return {
                "success": True,
                "target_host": target_host,
                "connected": True,
                "reachable": True,
                "reason": "online",
                "hint": "Device is ON and SSH is working",
                "response_time_ms": response_time_ms,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        else:
            # Classify the failure so the LLM can answer on/off questions directly
            diag = _classify_ssh_failure(result.returncode, result.stderr or "")
            return {
                "success": False,
                "target_host": target_host,
                "connected": False,
                "reachable": diag["reachable"],
                "reason": diag["reason"],
                "hint": diag["hint"],
                "error": (
                    f"{diag['hint']} (connection test failed"
                    + (f": {result.stderr.strip()}" if result.stderr else "")
                    + ")"
                ),
                "stderr": result.stderr.strip() if result.stderr else "",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "target_host": target_host,
            "connected": False,
            "reachable": False,
            "reason": "timeout",
            "hint": "Device did not respond in time - it is OFF, off the network, or hung",
            "error": f"Connection timed out after {timeout} seconds - device appears OFF or unreachable",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
    except Exception as e:
        return {
            "success": False,
            "target_host": target_host,
            "connected": False,
            "reachable": False,
            "reason": "ssh_connection_failure",
            "hint": f"Connectivity check could not run: {str(e)}",
            "error": f"Connectivity check failed: {str(e)}",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }


# Default remote-execution target: the X17 (LGI) - Polaris's eyes and ears.
# MissPi (192.168.50.179) was fully decommissioned Sep 30, 2026; its user/IP
# defaults were replaced with rbuit@192.168.50.227 (LGI workdir D:/LGI).
X17_DEFAULT_HOST = "192.168.50.227"
X17_DEFAULT_USER = "rbuit"
X17_DEFAULT_WORKDIR = "D:/LGI"

# Polaris's ingest vault - local source/target for ssh_deploy_file/ssh_fetch_file
VAULT_DIR = "/data/freeroam/polaris-gateway/ingest"


def _scp_base_command(timeout: int) -> list:
    """Shared scp arguments (same key and security posture as ssh_execute)."""
    return [
        "scp",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={min(timeout, 30)}",
        "-o", "IdentitiesOnly=yes",
        "-i", "/root/.ssh/id_ed25519",
    ]


def ssh_deploy_file(filename: str, dest_remote: str = X17_DEFAULT_WORKDIR + "/",
                    target_host: str = X17_DEFAULT_HOST, ssh_user: str = X17_DEFAULT_USER,
                    timeout: int = 30) -> Dict:
    """
    Push a document from Polaris's ingest vault to a remote node via scp -r.

    Args:
        filename: Name of the ingest-vault document to deploy (see file_tools list_docs)
        dest_remote: Remote destination (default 'D:/LGI/' - the X17 LGI workdir;
            Windows accepts forward-slash paths)
        target_host: Remote node (default X17_DEFAULT_HOST - the X17 / LGI host)
        ssh_user: SSH username (default 'rbuit' for the X17)
        timeout: Transfer timeout in seconds (default: 30)

    Returns:
        Dict with success status, source/destination, scp stdout/stderr,
        returncode, and reachable/reason/hint diagnostics on failure.
    """
    src_local = os.path.join(VAULT_DIR, os.path.basename(filename))
    dest_spec = f"{ssh_user}@{target_host}:{dest_remote}"
    try:
        if not os.path.exists(src_local):
            return {
                "success": False,
                "target_host": target_host,
                "source": src_local,
                "destination": dest_spec,
                "error": f"Vault document not found: {filename} (vault dir: {VAULT_DIR})",
                "returncode": -1,
                "stdout": "",
                "stderr": "",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

        result = subprocess.run(
            _scp_base_command(timeout) + ["-r", src_local, dest_spec],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        response = {
            "success": result.returncode == 0,
            "target_host": target_host,
            "source": src_local,
            "destination": dest_spec,
            "returncode": result.returncode,
            "stdout": result.stdout.strip() if result.stdout else "",
            "stderr": result.stderr.strip() if result.stderr else "",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        if result.returncode != 0:
            diag = _classify_ssh_failure(result.returncode, result.stderr or "")
            response["reachable"] = diag["reachable"]
            response["reason"] = diag["reason"]
            response["hint"] = diag["hint"]
            response["error"] = (
                f"{diag['hint']}. (scp deploy failed with return code {result.returncode}"
                + (f": {result.stderr.strip()}" if result.stderr else "") + ")"
            )
        return response

    except subprocess.TimeoutExpired:
        return {
            "success": False, "target_host": target_host, "source": src_local,
            "destination": dest_spec, "reachable": False, "reason": "timeout",
            "hint": "Device did not respond in time - it is OFF, off the network, or hung",
            "error": f"scp deploy timed out after {timeout} seconds",
            "returncode": -1, "stdout": "", "stderr": "",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    except Exception as e:
        return {
            "success": False, "target_host": target_host, "source": src_local,
            "destination": dest_spec, "reachable": False,
            "reason": "scp_deploy_failure",
            "hint": f"scp deploy could not run: {str(e)}",
            "error": f"scp deploy error: {str(e)}",
            "returncode": -1, "stdout": "", "stderr": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }


def ssh_fetch_file(remote_path: str, filename: str = "",
                   target_host: str = X17_DEFAULT_HOST, ssh_user: str = X17_DEFAULT_USER,
                   timeout: int = 60) -> Dict:
    """
    Pull a file or directory from a remote node into Polaris's ingest vault via scp -r.

    Args:
        remote_path: Absolute remote path (e.g., 'D:/LGI/lgi.log' on the X17;
            Windows accepts forward-slash paths)
        filename: Optional vault name (defaults to the remote file/dir basename)
        target_host: Remote node (default X17_DEFAULT_HOST - the X17 / LGI host)
        ssh_user: SSH username (default 'rbuit' for the X17)
        timeout: Transfer timeout in seconds (default: 60)

    Returns:
        Dict with success status, source/destination, scp stdout/stderr,
        returncode, and reachable/reason/hint diagnostics on failure.
    """
    src_spec = f"{ssh_user}@{target_host}:{remote_path}"
    dest_local = os.path.join(VAULT_DIR, os.path.basename(filename or remote_path.rstrip("/")))
    try:
        os.makedirs(VAULT_DIR, exist_ok=True)
        result = subprocess.run(
            _scp_base_command(timeout) + ["-r", src_spec, dest_local],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        response = {
            "success": result.returncode == 0,
            "target_host": target_host,
            "source": src_spec,
            "dest_local": dest_local,
            "returncode": result.returncode,
            "stdout": result.stdout.strip() if result.stdout else "",
            "stderr": result.stderr.strip() if result.stderr else "",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        if result.returncode == 0:
            response["hint"] = f"Fetched into the ingest vault as {os.path.basename(dest_local)}"
        else:
            diag = _classify_ssh_failure(result.returncode, result.stderr or "")
            response["reachable"] = diag["reachable"]
            response["reason"] = diag["reason"]
            response["hint"] = diag["hint"]
            response["error"] = (
                f"{diag['hint']}. (scp fetch failed with return code {result.returncode}"
                + (f": {result.stderr.strip()}" if result.stderr else "") + ")"
            )
        return response

    except subprocess.TimeoutExpired:
        return {
            "success": False, "target_host": target_host, "source": src_spec,
            "dest_local": dest_local, "reachable": False, "reason": "timeout",
            "hint": "Device did not respond in time - it is OFF, off the network, or hung",
            "error": f"scp fetch timed out after {timeout} seconds",
            "returncode": -1, "stdout": "", "stderr": "",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    except Exception as e:
        return {
            "success": False, "target_host": target_host, "source": src_spec,
            "dest_local": dest_local, "reachable": False,
            "reason": "scp_fetch_failure",
            "hint": f"scp fetch could not run: {str(e)}",
            "error": f"scp fetch error: {str(e)}",
            "returncode": -1, "stdout": "", "stderr": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }


# Tool schemas for LLM function calling
SSH_TOOL_SCHEMAS = [
    {
        "name": "ssh_execute",
        "description": "Execute a shell command via SSH on a remote node (e.g., the X17 / LGI host at 192.168.50.227 as user rbuit, or the Mainframe host at 192.168.50.51 with ssh_user 'causeiam'). Use for checking system status, running scripts, managing services, or file operations. Results include 'reachable', 'reason' and 'hint' fields: if SSH cannot connect they tell you the device's state (e.g. reachable=false + reason=host_unreachable means the device is OFF) - report that state directly instead of calling it a failure.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_host": {
                    "type": "string",
                    "description": "IP address or hostname of the remote node (e.g., '192.168.50.227' for the X17 / LGI host)"
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute remotely. The X17 (192.168.50.227) is Windows with a cmd.exe default shell: use Windows commands - tasklist, taskkill /F /PID <pid>, findstr, type, schtasks /run /tn <task> - and forward-slash paths like D:/LGI. The Mainframe (192.168.50.51) is Linux bash: commands run inside a remote bash -c shell whose own cmdline contains the script name you type, so bare 'pgrep -f <name>' or 'pkill -f <name>' ALWAYS self-match (false 'already running', or killing your own shell). To check or kill processes there use: ps aux | grep '[n]ame' | grep -vE 'bash|grep'. To start Linux daemons use: nohup <cmd> > <log> 2>&1 &"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (1-60, default: 15)",
                    "default": 15
                },
                "ssh_user": {
                    "type": "string",
                    "description": "SSH username on the target node. Default 'rbuit' (X17 / LGI host). For the Mainframe host (192.168.50.51) use 'causeiam'",
                    "default": "rbuit"
                }
            },
            "required": ["target_host", "command"]
        }
    },
    {
        "name": "ssh_check_connectivity",
        "description": "Check SSH connectivity to a remote node without executing commands. Use FIRST when the user asks whether a device is ON or OFF, powered up, or reachable. Results include 'reachable' (device on/off the network), 'reason' (host_unreachable / connection_refused / timeout / auth_failure / online) and 'hint' (plain-English diagnosis) - answer on/off questions directly from these fields.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_host": {
                    "type": "string",
                    "description": "IP address or hostname of the remote node"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Connection timeout in seconds (1-30, default: 5)",
                    "default": 5
                },
                "ssh_user": {
                    "type": "string",
                    "description": "SSH username on the target node. Default 'rbuit' (X17 / LGI host). For the Mainframe host (192.168.50.51) use 'causeiam'",
                    "default": "rbuit"
                }
            },
            "required": ["target_host"]
        }
    },
    {
        "name": "ssh_deploy_file",
        "description": "Push a document from Polaris's ingest vault to a remote node via scp -r. Use it to ship scripts/configs/updates to the X17 (default destination D:/LGI/, user rbuit; Windows targets accept forward-slash paths like 'D:/LGI/'). The vault document name resolves inside the ingest vault directory (browse it with list_docs). Results include 'reachable', 'reason' and 'hint' diagnostics when the transfer fails.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the ingest-vault document to deploy (e.g., 'update.py')"
                },
                "dest_remote": {
                    "type": "string",
                    "description": "Destination path on the remote node (default 'D:/LGI/' for the X17; end directories with '/')"
                },
                "target_host": {
                    "type": "string",
                    "description": "IP address or hostname of the remote node (default '192.168.50.227' for the X17 / LGI host)"
                },
                "ssh_user": {
                    "type": "string",
                    "description": "SSH username on the target node (default 'rbuit' for the X17)",
                    "default": "rbuit"
                }
            },
            "required": ["filename"]
        }
    },
    {
        "name": "ssh_fetch_file",
        "description": "Pull a file or directory from a remote node into Polaris's ingest vault via scp -r (e.g., fetch a log or script from the X17 at 192.168.50.227, user rbuit; Windows targets accept forward-slash paths like 'D:/LGI/...'). Results include 'reachable', 'reason' and 'hint' diagnostics when the transfer fails.",
        "parameters": {
            "type": "object",
            "properties": {
                "remote_path": {
                    "type": "string",
                    "description": "Absolute path of the file/directory on the remote node (e.g., 'D:/LGI/lgi.log' on the X17)"
                },
                "filename": {
                    "type": "string",
                    "description": "Optional name to save it as in the ingest vault (defaults to the remote file/directory name)"
                },
                "target_host": {
                    "type": "string",
                    "description": "IP address or hostname of the remote node (default '192.168.50.227' for the X17 / LGI host)"
                },
                "ssh_user": {
                    "type": "string",
                    "description": "SSH username on the target node (default 'rbuit' for the X17)",
                    "default": "rbuit"
                }
            },
            "required": ["remote_path"]
        }
    }
]


def get_ssh_tool_schemas_json() -> str:
    """Return SSH tool schemas as JSON string for LLM system prompt."""
    return json.dumps(SSH_TOOL_SCHEMAS, indent=2)


# Export all tool functions
__all__ = [
    "ssh_execute",
    "ssh_check_connectivity",
    "ssh_deploy_file",
    "ssh_fetch_file",
    "SSH_TOOL_SCHEMAS",
    "get_ssh_tool_schemas_json"
]