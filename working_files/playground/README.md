# Stark's Playground - Autonomous Holodeck Module

## Overview
The Playground module provides Stark (Polaris AI) with autonomous capabilities to:
- Generate and publish web applications instantly
- Execute code in a sandboxed environment
- Spawn micro-services on ephemeral ports
- Serve live previews on the LAN

## Architecture

### Directory Structure
```
./freeroam/polaris-gateway/playground/
├── www/          # Live web server root (served on port 8090)
└── scripts/      # Temporary execution sandbox
```

### Services
1. **polaris-playground** (Nginx)
   - Port: 8090
   - Serves: `./freeroam/polaris-gateway/playground/www/`
   - Access: `http://192.168.50.51:8090`

## Available Tools

### 1. `publish_web_asset(filename, content)`
Writes HTML/CSS/JS files to the live web server.

### 2. `list_playground_files(directory)`
Lists files in playground directories.

### 3. `run_sandbox_code(language, code, timeout)`
Executes Python or Bash code in an isolated sandbox (15s timeout, 512MB limit).

### 4. `spawn_service(entry_script, port, language)`
Launches background web services on ports 8091-8099.

### 5. `stop_service(port)`
Stops a running service by port number.

### 6. `list_services()`
Lists all currently running spawned services.

## Security Features
- Path validation (no directory traversal)
- Code execution timeout (15s default)
- Memory limits (512MB)
- Port restrictions (8090-8099 only)

## Testing
1. Rebuild: `docker-compose up -d --build polaris-gateway polaris-playground`
2. Verify: `curl http://localhost:8090`
3. Test: Ask Stark "Create an HTML dashboard for XRP prices"
