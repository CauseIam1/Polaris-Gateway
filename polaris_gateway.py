#!/usr/bin/env python3
"""
FreeRoam Polaris Gateway - Unified Server
Runs both Chat Gateway (port 5000) and Burst Receiver (port 5001)
"""

import os
import sys
import threading
import subprocess
from pathlib import Path

# Get the script directory
SCRIPT_DIR = Path(__file__).parent.absolute()
DATA_DIR = Path(os.environ.get('FREEROAM_DATA_DIR', '/home/causeiam/docker-containers/freeroam/data'))

def ensure_directories():
    """Create required data directories."""
    (DATA_DIR / 'bursts').mkdir(parents=True, exist_ok=True)
    (DATA_DIR / 'logs').mkdir(parents=True, exist_ok=True)

def run_burst_receiver():
    """Run the Burst Receiver server on port 5001."""
    env = os.environ.copy()
    env['BURST_STORAGE_DIR'] = str(DATA_DIR / 'bursts')
    env['TELEMETRY_LOG'] = str(DATA_DIR / 'telemetry.log')
    
    os.chdir(SCRIPT_DIR)
    subprocess.run([sys.executable, 'burst_receiver.py'], env=env)

def run_chat_gateway():
    """Run the Chat Gateway server on port 5000."""
    os.chdir(SCRIPT_DIR)
    subprocess.run([sys.executable, 'freeroam_gateway.py'])

def main():
    print("🚀 FreeRoam Polaris Gateway Starting...")
    print(f"📂 Data Directory: {DATA_DIR}")
    print("")
    
    ensure_directories()
    
    # Start Burst Receiver in a thread
    burst_thread = threading.Thread(target=run_burst_receiver, daemon=True)
    burst_thread.start()
    
    # Run Chat Gateway in main thread (or also as thread)
    chat_thread = threading.Thread(target=run_chat_gateway, daemon=True)
    chat_thread.start()
    
    # Wait for both threads
    burst_thread.join()
    chat_thread.join()

if __name__ == '__main__':
    main()