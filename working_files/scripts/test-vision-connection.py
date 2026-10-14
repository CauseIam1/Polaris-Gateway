#!/usr/bin/env python3
"""
Test script to simulate Miss Pi vision stream connection to Polaris Gateway
Use this to verify the JARVIS announcements are working correctly
"""

import socketio
import time
import json
from datetime import datetime

# Configuration
GATEWAY_URL = "http://localhost:8083"

class VisionStreamSimulator:
    def __init__(self):
        self.sio = socketio.Client()
        self.setup_handlers()
        
    def setup_handlers(self):
        @self.sio.event
        def connect():
            print("✅ Connected to Polaris Gateway")
            # Join dashboard rooms to receive announcements
            self.sio.emit('join_radar', {'room': 'radar_dashboard'})
            self.sio.emit('join_vision', {'room': 'vision_dashboard'})
            
        @self.sio.event
        def disconnect():
            print("❌ Disconnected from Polaris Gateway")
            
        @self.sio.on('vision_ack')
        def on_vision_ack(data):
            print(f"📩 ACK: {data}")
            
        @self.sio.on('jarvis_announcement')
        def on_jarvis_announcement(data):
            msg_type = data.get('type', 'info')
            message = data.get('message', '')
            priority = data.get('priority', 'normal')
            
            icons = {
                'system': '🤖',
                'status': '✅',
                'detection': '👁️',
                'warning': '⚠️',
                'info': 'ℹ️'
            }
            icon = icons.get(msg_type, '📢')
            print(f"{icon} JARVIS: [{priority.upper()}] {message}")
    
    def connect_to_gateway(self):
        """Connect to Polaris Gateway Socket.IO server"""
        try:
            print(f"🔌 Connecting to {GATEWAY_URL}...")
            self.sio.connect(GATEWAY_URL)
            return True
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False
    
    def send_vision_connect(self):
        """Send vision stream connection event"""
        print("\n📡 Sending vision stream connection...")
        connect_data = {
            "device": "miss-pi",
            "camera_id": "front",
            "resolution": "640x480",
            "fps": 30
        }
        self.sio.emit('vision_connect', connect_data)
        print(f"📤 Sent: {json.dumps(connect_data, indent=2)}")
        time.sleep(1)
    
    def send_vision_frame(self, frame_id=1):
        """Send a simulated vision detection frame"""
        print(f"\n📡 Sending vision frame #{frame_id}...")
        
        frame_data = {
            "timestamp": time.time(),
            "frame_id": frame_id,
            "detections": [
                {
                    "class": "person",
                    "class_id": 0,
                    "confidence": 0.92,
                    "bbox": [100, 150, 300, 450],
                    "center": [200, 300],
                    "position_desc": "center-left"
                }
            ],
            "narration": "I see a person on the left side of the frame",
            "inference_time_ms": 45.2,
            "camera_settings": {
                "resolution": "640x480",
                "fps": 30
            }
        }
        
        self.sio.emit('vision_frame', frame_data)
        print(f"📤 Sent detection: person (confidence: 0.92)")
        print(f"🗣️  Narration: {frame_data['narration']}")
        time.sleep(0.5)
    
    def send_multiple_detections(self):
        """Send frame with multiple object detections"""
        print(f"\n📡 Sending multi-object detection...")
        
        frame_data = {
            "timestamp": time.time(),
            "frame_id": 999,
            "detections": [
                {"class": "person", "class_id": 0, "confidence": 0.88, "bbox": [100, 150, 300, 450], "center": [200, 300], "position_desc": "left"},
                {"class": "dog", "class_id": 17, "confidence": 0.91, "bbox": [350, 200, 500, 400], "center": [425, 300], "position_desc": "right"},
                {"class": "backpack", "class_id": 39, "confidence": 0.75, "bbox": [150, 100, 250, 200], "center": [200, 150], "position_desc": "center"}
            ],
            "narration": "Multiple objects detected: person, dog, and backpack",
            "inference_time_ms": 52.1,
            "camera_settings": {
                "resolution": "640x480",
                "fps": 30
            }
        }
        
        self.sio.emit('vision_frame', frame_data)
        print(f"📤 Sent 3 detections: person, dog, backpack")
        time.sleep(0.5)
    
    def send_disconnect(self):
        """Send vision stream disconnect event"""
        print("\n📡 Sending vision stream disconnect...")
        self.sio.emit('vision_disconnect', {})
        time.sleep(1)
    
    def run_test(self):
        """Run complete test sequence"""
        print("=" * 60)
        print("  POLARIS GATEWAY - JARVISION TEST SIMULATOR")
        print("=" * 60)
        print()
        
        if not self.connect_to_gateway():
            print("\n❌ Test aborted - could not connect to gateway")
            return False
        
        print("\n✅ Connection successful!")
        print("\n" + "=" * 60)
        print("  Running test sequence...")
        print("=" * 60)
        
        # Test 1: Connection
        self.send_vision_connect()
        time.sleep(2)
        
        # Test 2: Single detection
        print("\n" + "=" * 60)
        print("  TEST 1: Single Object Detection")
        print("=" * 60)
        self.send_vision_frame(frame_id=1)
        time.sleep(2)
        
        # Test 3: Multiple detections
        print("\n" + "=" * 60)
        print("  TEST 2: Multiple Object Detection")
        print("=" * 60)
        self.send_multiple_detections()
        time.sleep(2)
        
        # Test 4: Disconnect
        print("\n" + "=" * 60)
        print("  TEST 3: Stream Disconnect")
        print("=" * 60)
        self.send_disconnect()
        
        print("\n" + "=" * 60)
        print("  TEST COMPLETE")
        print("=" * 60)
        print("\nCheck the Polaris dashboard for JARVIS announcements!")
        print("Or run: ./scripts/vision-monitor.sh")
        
        self.sio.disconnect()
        return True


if __name__ == "__main__":
    simulator = VisionStreamSimulator()
    try:
        simulator.run_test()
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
