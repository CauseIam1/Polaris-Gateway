# Polaris Sandbox Gateway - Port 7007

## Overview

The **Polaris Sandbox Gateway** is a dedicated web-based interface for executing SSH commands on the **X17** (LGI host, Windows 11) edge machine — `rbuit@192.168.50.227`, LGI workdir `D:/LGI`. It provides a real-time terminal-like dashboard for the AI agent to visualize outputs and manage remote operations. (The legacy Mini Pi target was decommissioned Sep 30, 2026.)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  Polaris Gateway Container                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Sandbox Gateway (Port 7007)                             │   │
│  │  ┌─────────────────┐  ┌──────────────────────────────┐   │   │
│  │  │  Web Dashboard  │  │  SSH Client                  │   │   │
│  │  │  (HTML/JS)      │  │  (ssh_tools.py)              │   │   │
│  │  └─────────────────┘  └──────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              ↓ SSH (port 22)
┌─────────────────────────────────────────────────────────────────┐
│             Alienware X17 (LGI) - 192.168.50.227                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  User: rbuit                                             │   │
│  │  WorkDir: D:/LGI                                         │   │
│  │  SSH Key: /root/.ssh/id_ed25519 (in gateway container)   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Port Allocation Summary

| Port | Service | Purpose |
|------|---------|---------|
| 5000 | Chat Gateway | Live mode - AI chat with local memory |
| 5001 | Burst Receiver | Telemetry and burst message ingestion |
| 7000 | Polaris Dashboard | Main monitoring dashboard |
| **7007** | **Sandbox Gateway** | **X17 (LGI) SSH operations & visualization** |

## Features

### Web Dashboard (http://localhost:7007/)

- **Real-time Command Execution**: Execute SSH commands on the X17 (LGI) with instant output
- **Visual Status Indicators**: Connection status, response time monitoring
- **Command History**: Track all executed commands with timestamps and results
- **Quick Actions**: Pre-configured buttons for common operations:
  - System Status (`uptime` — Linux only; on the X17 use `net stats srv`)
  - Process List (`ps aux` — Linux; on the X17 use `tasklist`)
  - Disk Usage (`df -h` — Linux; on the X17 use `fsutil volume diskfree D:`)
  - Memory Usage (`free -h` — Linux)
  - WorkDir Listing (`cd /d D:/LGI && dir`)
  - Git Status (`cd /d D:/LGI && git status`)

### API Endpoints

#### POST /api/ssh/execute
Execute an SSH command on the X17 (LGI).

```json
{
  "target_host": "192.168.50.227",
  "command": "uptime",
  "timeout": 30
}
```

Response:
```json
{
  "success": true,
  "target_host": "192.168.50.227",
  "command": "uptime",
  "returncode": 0,
  "stdout": " 12:34:56 up 10 days,  2:34,  1 user,  load average: 0.15, 0.10, 0.08",
  "stderr": "",
  "timestamp": "2026-09-06T12:34:56.789Z"
}
```

#### POST /api/ssh/check
Check SSH connectivity to Mini Pi.

```json
{
  "target_host": "192.168.50.227",
  "timeout": 5
}
```

#### GET /api/ssh/history
Get command execution history (default: last 50 commands).

```bash
curl http://localhost:7007/api/ssh/history?limit=20
```

#### GET /api/status
Get gateway status and X17 (LGI) connection info.

```json
{
  "status": "online",
  "lgi_host": "192.168.50.227",
  "lgi_workdir": "D:/LGI",
  "connected": true,
  "response_time_ms": 12,
  "command_count": 42,
  "timestamp": "2026-09-06T12:34:56.789Z"
}
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MINI_PI_HOST` | `192.168.50.227` | X17 (LGI) IP — legacy env name kept; `LGI_X17_HOST` preferred |
| `FREEROAM_DATA_DIR` | `/data/freeroam` | Data directory for logs |

### SSH Configuration

The gateway uses pre-configured SSH keys:
- **Key Path**: `/root/.ssh/id_ed25519` (inside container)
- **User**: `rbuit`
- **Security Options**:
  - `StrictHostKeyChecking=no`
  - `BatchMode=yes`
  - `IdentitiesOnly=yes`

## Usage Examples

### cURL Examples

```bash
# Check connection
curl -X POST http://localhost:7007/api/ssh/check \
  -H "Content-Type: application/json" \
  -d '{"target_host": "192.168.50.227"}'

# Execute command
curl -X POST http://localhost:7007/api/ssh/execute \
  -H "Content-Type: application/json" \
  -d '{
    "target_host": "192.168.50.227",
    "command": "schtasks /run /tn \"LGI Manual\""
  }'

# Get history
curl http://localhost:7007/api/ssh/history?limit=10

# Get status
curl http://localhost:7007/api/status
```

### Python Example

```python
import requests

SANDBOX_URL = "http://localhost:7007"

# Execute command
response = requests.post(
    f"{SANDBOX_URL}/api/ssh/execute",
    json={
        "target_host": "192.168.50.227",
        "command": "uptime",
        "timeout": 30
    }
)

result = response.json()
if result["success"]:
    print("Output:", result["stdout"])
else:
    print("Error:", result["error"])
```

## Files

- `sandbox_gateway.py` - Main Flask application
- `sandbox_static/index.html` - Web dashboard UI
- `ssh_tools.py` - SSH execution utilities (shared)
- `run_gateway.sh` - Gateway launcher (updated to include sandbox)

## Integration with AI Agent

The AI agent can interact with the Sandbox Gateway in two ways:

### 1. Direct HTTP API
Use the REST API endpoints for programmatic control.

### 2. Web Dashboard
Access the web UI at `http://localhost:7007/` for:
- Visual command output rendering
- Real-time status monitoring
- Command history tracking
- Quick action buttons

## Security Notes

- **LAN-Only Access**: The gateway is designed for private LAN operation
- **SSH Key Security**: Keys are stored in the container with restricted permissions
- **Command Validation**: All commands are executed as-is; ensure AI agent prompts are properly sanitized
- **Timeout Protection**: Default 15-second timeout prevents hung connections

## Troubleshooting

### Connection Failed
```bash
# Check if the X17 is reachable
docker exec polaris-gateway ping -c 3 192.168.50.227

# Test SSH manually
docker exec polaris-gateway ssh -i /root/.ssh/id_ed25519 \
  -o StrictHostKeyChecking=no -o IdentitiesOnly=yes \
  rbuit@192.168.50.227 "uptime"
```

### Gateway Not Starting
```bash
# Check logs
docker logs polaris-gateway 2>&1 | grep -i sandbox

# Verify port is not in use
docker exec polaris-gateway netstat -tlnp | grep 7007
```

## Future Enhancements

- [ ] WebSocket support for real-time streaming output
- [ ] File upload/download via SFTP
- [ ] Command scheduling and automation
- [ ] Multi-node support (beyond the X17)
- [ ] Visualization panels for system metrics
- [ ] Command templates and macros

## See Also

- `freeroam.md` - Main FreeRoam system documentation
- `polaris_gateway.md` - Chat Gateway documentation
- `ssh_tools.py` - SSH utilities source code
