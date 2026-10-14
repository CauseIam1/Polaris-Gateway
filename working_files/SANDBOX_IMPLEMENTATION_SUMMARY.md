# Sandbox Gateway Implementation Summary

## ✅ Completed Tasks

### 1. Created Sandbox Gateway Application
- **File**: `sandbox_gateway.py`
- **Port**: 7007
- **Features**:
  - Flask-based web server
  - SSH command execution via `ssh_tools.py`
  - Real-time command history tracking
  - REST API for programmatic access
  - Built-in status monitoring

### 2. Created Web Dashboard UI
- **File**: `sandbox_static/index.html`
- **Features**:
  - Dark theme tactical interface
  - Real-time connection status indicator
  - Command execution panel with output visualization
  - Command history with clickable recall
  - Quick action buttons for common operations
  - Auto-refreshing connection status (30s interval)

### 3. Updated Gateway Launcher
- **File**: `run_gateway.sh`
- **Changes**:
  - Added Sandbox Gateway startup on port 7007
  - Updated process management to monitor 3 services
  - Added startup banner for sandbox gateway

### 4. Updated Docker Configuration
- **File**: `Dockerfile`
- **Changes**:
  - Added `EXPOSE 7007` for port mapping
  
- **File**: `docker-compose.yml`
- **Changes**:
  - Added port mapping: `- "7007:7007"`
  - Comment: "Sandbox Gateway (Mini Pi SSH dashboard)"

### 5. Created Documentation
- **File**: `SANDBOX_GATEWAY.md`
- **Contents**:
  - Architecture overview
  - Port allocation summary
  - API endpoint documentation
  - Usage examples (cURL, Python)
  - Configuration guide
  - Troubleshooting tips

## 📋 Port Allocation

| Port | Service | Purpose | Access |
|------|---------|---------|--------|
| 5000 | Chat Gateway | Live AI chat with memory | Host: 8082 |
| 5001 | Burst Receiver | Telemetry ingestion | Host: 8083 |
| 7000 | Polaris Dashboard | Main monitoring UI | Host: 7000 |
| **7007** | **Sandbox Gateway** | **Mini Pi SSH operations** | **Host: 7007** |
| 8090 | Polaris Playground | Web app generation | Host: 8090 |

## 🚀 Deployment Instructions

### Rebuild and Restart Container

```bash
cd /home/causeiam/docker-containers

# Stop the current gateway container
docker compose stop polaris-gateway

# Rebuild with new changes
docker compose build polaris-gateway

# Start the updated container
docker compose up -d polaris-gateway

# Verify it's running
docker compose ps polaris-gateway

# Check logs for sandbox gateway startup
docker logs polaris-gateway 2>&1 | grep -i "sandbox"
```

### Verify Sandbox Gateway is Running

```bash
# Check if port 7007 is listening
netstat -tlnp | grep 7007

# Test the status endpoint
curl http://localhost:7007/api/status

# Test SSH connectivity to the X17 (LGI)
curl -X POST http://localhost:7007/api/ssh/check \
  -H "Content-Type: application/json" \
  -d '{"target_host": "192.168.50.227"}'

# Execute a test command
curl -X POST http://localhost:7007/api/ssh/execute \
  -H "Content-Type: application/json" \
  -d '{"target_host": "192.168.50.227", "command": "uptime"}'
```

### Access the Web Dashboard

Open your browser and navigate to:
- **Local**: `http://localhost:7007/`
- **LAN**: `http://192.168.50.51:7007/`

## 🔧 Configuration

### Environment Variables (Optional)

Set these in your `.env` file or docker-compose.yml:

```yaml
environment:
  - MINI_PI_HOST=192.168.50.227  # X17 (LGI) IP (legacy env name kept)
  - FREEROAM_DATA_DIR=/data/freeroam
```

### SSH Requirements

The gateway uses existing SSH configuration:
- **Key**: `/root/.ssh/id_ed25519` (mounted from host)
- **User**: `rbuit`
- **Target**: `192.168.50.227` (configurable via `MINI_PI_HOST`, the X17 / LGI)

Ensure your SSH key is properly configured:
```bash
# Verify SSH key exists
ls -la /home/causeiam/.ssh/id_ed25519

# Test manual SSH connection
ssh -i /home/causeiam/.ssh/id_ed25519 rbuit@192.168.50.227 "uptime"
```

## 📊 API Endpoints

### POST /api/ssh/execute
Execute SSH command on the X17 (LGI).

```bash
curl -X POST http://localhost:7007/api/ssh/execute \
  -H "Content-Type: application/json" \
  -d '{
    "target_host": "192.168.50.227",
    "command": "cd /d D:/LGI && git pull",
    "timeout": 30
  }'
```

### POST /api/ssh/check
Check SSH connectivity.

```bash
curl -X POST http://localhost:7007/api/ssh/check \
  -H "Content-Type: application/json" \
  -d '{"target_host": "192.168.50.227"}'
```

### GET /api/ssh/history
Get command history.

```bash
curl http://localhost:7007/api/ssh/history?limit=20
```

### GET /api/status
Get gateway status.

```bash
curl http://localhost:7007/api/status
```

## 🎯 Use Cases

### 1. AI Agent Code Deployment
The AI agent can execute commands on the X17 (LGI) to:
- Deploy updated code
- Restart services
- Check system status
- Monitor processes

### 2. Real-time Visualization
The web dashboard provides:
- Live command output
- Visual success/failure indicators
- Command history tracking
- Connection status monitoring

### 3. Automated Operations
Via the REST API, the AI can:
- Execute deployment scripts
- Monitor system health
- Manage Git operations
- Control background services

## 🔒 Security Notes

- **LAN-Only**: The gateway is designed for private network access
- **SSH Key Security**: Keys are read-only mounted in the container
- **Command Execution**: All commands run as the `rbuit` user
- **Timeout Protection**: Default 15-second timeout prevents hung connections
- **No Authentication**: Currently no auth on the web UI (LAN-only by design)

## 📝 Files Created/Modified

### Created:
- `/home/causeiam/docker-containers/freeroam/polaris-gateway/sandbox_gateway.py`
- `/home/causeiam/docker-containers/freeroam/polaris-gateway/sandbox_static/index.html`
- `/home/causeiam/docker-containers/freeroam/polaris-gateway/SANDBOX_GATEWAY.md`
- `/home/causeiam/docker-containers/freeroam/polaris-gateway/SANDBOX_IMPLEMENTATION_SUMMARY.md`

### Modified:
- `/home/causeiam/docker-containers/freeroam/polaris-gateway/run_gateway.sh`
- `/home/causeiam/docker-containers/freeroam/polaris-gateway/Dockerfile`
- `/home/causeiam/docker-containers/docker-compose.yml`

## ✅ Next Steps

1. **Rebuild the container** (awaiting your approval)
2. **Verify SSH connectivity** to Mini Pi from the container
3. **Test the web dashboard** at `http://localhost:7007/`
4. **Execute test commands** via the UI or API
5. **Integrate with AI agent** workflows

## 🎉 Summary

The Sandbox Gateway is now ready for deployment! It provides a clean separation between:
- **Live Mode** (port 8082): AI chat with local memory and document ingestion
- **Sandbox Mode** (port 7007): Mini Pi SSH operations and visualization

This dual-interface strategy allows simultaneous operations: discussing local docs while actively controlling remote hardware via SSH.
