# Polaris Gateway - JARVIS Vision System

## Overview
The Polaris Gateway now features JARVIS-style voice announcements and real-time awareness monitoring for the Miss Pi vision stream integration. This system provides contextual, human-readable announcements about vision events, making the dashboard feel like an intelligent awareness system.

## Features

### 🎯 Smart Announcements
- **Connection Events**: Announces when Miss Pi vision stream comes online
- **Detection Events**: Contextual announcements for detected objects (person, dog, car, etc.)
- **Disconnection Events**: Alerts when vision feed is lost
- **Priority System**: High-priority alerts for system events, normal priority for detections

### 🧠 Intelligent Filtering
- Only announces high-confidence detections (>70% confidence)
- Rate-limited to prevent announcement spam (max 1 per 5 seconds)
- Groups multiple objects into single contextual announcements
- Maintains last 5 announcements in memory for dashboard display

## Architecture

### Socket.IO Events
```
Miss Pi (vision_daemon.py)
    ↓ vision_connect
    ↓ vision_frame (with Smart Filtering)
    ↓ vision_disconnect
Polaris Gateway (burst_receiver.py)
    ↓ Processes events
    ↓ Generates JARVIS announcements
    ↓ Emits to dashboards
Polaris Dashboard / Vision Dashboard
    ↓ Displays announcements
    ↓ Real-time updates
```

### API Endpoints

#### GET `/api/vision/stream/status`
Returns current vision stream status with JARVIS announcements:
```json
{
  "stream_active": true,
  "device": "miss-pi",
  "recent_detections_count": 42,
  "last_update": "2026-09-09T03:30:00",
  "status": "active",
  "jarvis_announcements": [...],
  "awareness_summary": {
    "total_detections_processed": 42,
    "stream_uptime": "active"
  }
}
```

#### GET `/api/jarvis/announcements`
Returns recent JARVIS announcements for dashboard display:
```json
{
  "announcements": [
    {
      "type": "connection",
      "message": "Vision stream online. miss-pi front camera active at 640x480 30 FPS.",
      "timestamp": "2026-09-09T03:30:00",
      "device": "miss-pi",
      "camera": "front"
    },
    {
      "type": "detection",
      "message": "Detecting person. I see a person on the left side of the frame",
      "timestamp": "2026-09-09T03:30:05",
      "objects": ["person"],
      "count": 1
    }
  ],
  "count": 2,
  "stream_active": true,
  "device": "miss-pi"
}
```

### Socket.IO Events (Client → Server)

#### `vision_connect`
Sent when Miss Pi vision daemon connects:
```python
{
    "device": "miss-pi",
    "camera_id": "front",
    "resolution": "640x480",
    "fps": 30
}
```

#### `vision_frame`
Sent for each detection frame (already filtered by SmartFilter):
```python
{
    "timestamp": 1739123456.789,
    "frame_id": 12345,
    "detections": [...],
    "narration": "I see a person on the left",
    "inference_time_ms": 45.2
}
```

#### `vision_disconnect`
Sent when vision stream disconnects (empty payload)

### Socket.IO Events (Server → Client)

#### `jarvis_announcement`
Broadcast to all dashboard clients:
```python
{
    "message": "Vision stream online. miss-pi front camera active...",
    "type": "system" | "status" | "detection" | "warning",
    "priority": "high" | "normal",
    "objects": ["person", "dog"]  # Only for detection type
}
```

## Testing

### Option 1: Live Test with Miss Pi
```bash
# SSH into Miss Pi (192.168.50.179)
ssh user@192.168.50.179

# Start the vision daemon
python3 vision_daemon.py
```

### Option 2: Simulated Test
```bash
# Run the test simulator
cd /home/causeiam/docker-containers/freeroam/polaris-gateway
python3 scripts/test-vision-connection.py
```

### Monitor Announcements
```bash
# Run the JARVIS monitor
./scripts/vision-monitor.sh
```

### Quick API Check
```bash
# Check vision status
curl -s http://localhost:8083/api/vision/stream/status | jq

# Get JARVIS announcements
curl -s http://localhost:8083/api/jarvis/announcements | jq
```

## Dashboard Integration

### React/Vue Component Example
```javascript
// Connect to Socket.IO
const socket = io('http://localhost:8083');

// Join vision room
socket.emit('join_vision', { room: 'vision_dashboard' });

// Listen for JARVIS announcements
socket.on('jarvis_announcement', (data) => {
  const { message, type, priority } = data;
  
  // Display announcement with appropriate styling
  displayAnnouncement(message, type, priority);
});

// Or poll the API
async function getAnnouncements() {
  const response = await fetch('http://localhost:8083/api/jarvis/announcements');
  const data = await response.json();
  return data.announcements;
}
```

### Announcement Types & Icons
```javascript
const announcementIcons = {
  'connection': '🟢',  // Green - System online
  'detection': '👁️',   // Blue - Object detected
  'disconnection': '🔴', // Red - System offline
  'warning': '⚠️',     // Yellow - Issue detected
  'status': '✅',      // Green - Status update
  'info': 'ℹ️'         // Gray - General info
};
```

## Deployment

### Rebuild Container
```bash
cd /home/causeiam/docker-containers/freeroam/polaris-gateway
docker compose build polaris-gateway
docker compose up -d polaris-gateway
```

### Verify Deployment
```bash
# Check container is running
docker ps | grep polaris-gateway

# Check logs for handler loading
docker logs polaris-gateway | grep "vision_"

# Test API endpoint
curl http://localhost:8083/api/vision/stream/status
```

## Troubleshooting

### No Announcements Appearing
1. Check if vision stream is active: `curl http://localhost:8083/api/vision/stream/status`
2. Verify Miss Pi daemon is running
3. Check Socket.IO connection in browser console

### Too Many Announcements
- The system rate-limits to 1 per 5 seconds
- Only high-confidence (>0.7) detections are announced
- Adjust thresholds in `burst_receiver.py` line 1211

### Connection Issues
1. Verify port 8083 is mapped: `docker ps | grep polaris-gateway`
2. Check firewall rules
3. Test Socket.IO connection manually

## Future Enhancements

- [ ] Text-to-speech audio announcements
- [ ] Configurable announcement verbosity levels
- [ ] Historical announcement log with search
- [ ] Custom announcement templates per object type
- [ ] Integration with Polaris radar for spatial awareness context

---

**System Status**: ✅ Ready for Vision Stream
**Next Step**: Start `vision_daemon.py` on Miss Pi and monitor announcements
