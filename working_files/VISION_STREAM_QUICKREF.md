# Vision Stream Quick Reference

## Socket.IO Events

### Client → Gateway
```javascript
// Connect handshake
socket.emit('vision_connect', {
    device: 'miss-pi',
    camera_id: 'front',
    resolution: '640x480',
    fps: 30
});

// Send detection frame
socket.emit('vision_frame', {
    timestamp: 1739123456.789,
    frame_id: 12345,
    detections: [...],
    narration: "I see a person on the left",
    inference_time_ms: 45.2
});

// Disconnect
socket.emit('vision_disconnect');

// Join room
socket.emit('join_vision', { room: 'vision_dashboard' });
```

### Gateway → Client
```javascript
// Acknowledge connection
socket.on('vision_ack', (data) => {
    // data.status = 'connected'
});

// Broadcast detection
socket.on('vision_update', (data) => {
    // Full detection frame
});

// Send history on join
socket.on('vision_history', (data) => {
    // Last 10 detections
});

// Integration event (radar room)
socket.on('polaris_vision', (data) => {
    // { detections_count, narration, primary_object }
});

// Error
socket.on('vision_error', (data) => {
    // data.message = error description
});
```

## API Endpoints

### GET /api/vision/stream/status
```bash
curl http://localhost:8083/api/vision/stream/status
```

**Response:**
```json
{
  "stream_active": true,
  "recent_detections_count": 45,
  "last_update": "2026-09-09T02:30:15",
  "status": "active"
}
```

## Dashboard UI Elements

### Vision Dropdown
- **Stream Status**: "STREAM ACTIVE" / "WAITING FOR STREAM"
- **Narration**: Natural language description
- **Detections**: Count of objects detected
- **Primary Object**: Top detection with confidence %
- **Last Update**: Timestamp of last frame
- **Connection**: "CONNECTED" / "DISCONNECTED"

### JavaScript Functions
```javascript
initVision()           // Initialize vision system
connectVisionSocket()  // Establish WebSocket
updateVisionDisplay(data)  // Update UI
```

## YOLO Classes (80 COCO)
```
0: person         1: bicycle     2: car       3: motorcycle
4: airplane       5: bus         6: train     7: truck
8: boat          9: traffic light 10: fire hydrant
11: stop sign    12: parking meter 13: bench   14: bird
15: cat          16: dog        17: horse    18: sheep
19: cow          20: elephant   21: bear     22: zebra
23: giraffe      24: backpack   25: umbrella 26: handbag
27: tie          28: suitcase   29: frisbee  30: skis
31: snowboard    32: sports ball 33: kite    34: baseball bat
35: baseball glove 36: skateboard 37: surfboard 38: tennis racket
39: bottle       40: wine glass 41: cup     42: fork
43: knife        44: spoon      45: bowl    46: banana
47: apple        48: sandwich   49: orange  50: broccoli
51: carrot       52: hot dog    53: pizza   54: donut
55: cake         56: chair      57: couch   58: potted plant
59: bed          60: dining table 61: toilet 62: tv
63: laptop       64: mouse      65: remote  66: keyboard
67: cell phone   68: microwave  69: oven    70: toaster
71: sink         72: refrigerator 73: book  74: clock
75: vase         76: scissors   77: teddy bear 78: hair drier
79: toothbrush
```

## Position Descriptions
Generated based on bounding box center:
- `center`, `left`, `right`, `top`, `bottom`
- `center-left`, `center-right`, `top-left`, `top-right`
- `bottom-left`, `bottom-right`

## Narration Examples
- "I see a person on the left"
- "I see a car in the center"
- "I see a dog on the right"
- "I see 2 people on the left and center"

## Debug Commands
```bash
# Check if daemon is running on Miss Pi
ps aux | grep vision_daemon

# Test gateway endpoint
curl http://localhost:8083/api/vision/stream/status

# Monitor vision logs
docker logs freeroam-polaris-gateway-1 | grep "👁️"

# Browser console (F12)
# Look for [Vision] prefixed logs
```

## Common Issues

| Issue | Solution |
|-------|----------|
| Stream not connecting | Check Miss Pi network, verify daemon running |
| No detections | Verify camera works, check YOLO model loaded |
| UI not updating | Browser console for errors, verify room join |
| High latency | Reduce inference frequency or resolution |