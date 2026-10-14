#!/bin/bash

# Polaris Gateway - JARVIS Vision Stream Monitor
# Monitors vision stream connectivity and JARVIS announcements

GATEWAY_HOST="localhost"
GATEWAY_PORT="8083"
API_BASE="http://${GATEWAY_HOST}:${GATEWAY_PORT}"

echo "==========================================="
echo "  POLARIS GATEWAY - JARVIS VISION MONITOR"
echo "==========================================="
echo ""
echo "Monitoring Polaris Gateway at ${API_BASE}"
echo "Press Ctrl+C to exit"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Initial connection test
echo -e "${CYAN}Testing gateway connection...${NC}"
if curl -s "${API_BASE}/api/engine/status" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Gateway is online${NC}"
else
    echo -e "${RED}✗ Gateway is not responding${NC}"
    exit 1
fi

echo ""

# Monitor loop
while true; do
    clear
    echo "==========================================="
    echo "  POLARIS - JARVIS VISION AWARENESS"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "==========================================="
    echo ""
    
    # Get vision status
    STATUS=$(curl -s "${API_BASE}/api/vision/stream/status" 2>/dev/null)
    
    if [ $? -eq 0 ] && [ -n "$STATUS" ]; then
        # Parse status
        STREAM_ACTIVE=$(echo $STATUS | jq -r '.stream_active')
        DEVICE=$(echo $STATUS | jq -r '.device // "Unknown"')
        DETECTIONS=$(echo $STATUS | jq -r '.recent_detections_count')
        LAST_UPDATE=$(echo $STATUS | jq -r '.last_update // "Never"')
        AWAR_STATUS=$(echo $STATUS | jq -r '.status')
        
        # Display status
        if [ "$STREAM_ACTIVE" = "true" ]; then
            echo -e "${GREEN}● VISION STREAM: ONLINE${NC}"
            echo -e "  Device: ${CYAN}${DEVICE}${NC}"
            echo -e "  Status: ${GREEN}${AWAR_STATUS}${NC}"
        else
            echo -e "${YELLOW}○ VISION STREAM: WAITING${NC}"
            echo -e "  Status: ${YELLOW}Awaiting connection from Miss Pi${NC}"
        fi
        
        echo ""
        echo "  Detections buffered: ${DETECTIONS}"
        echo "  Last update: ${LAST_UPDATE}"
        echo ""
        
        # Get JARVIS announcements
        ANNOUNCEMENTS=$(curl -s "${API_BASE}/api/jarvis/announcements" 2>/dev/null)
        ANN_COUNT=$(echo $ANNOUNCEMENTS | jq -r '.count')
        
        if [ "$ANN_COUNT" -gt 0 ]; then
            echo -e "${PURPLE}═══ JARVIS ANNOUNCEMENTS ═══${NC}"
            echo ""
            
            # Display last 5 announcements
            echo $ANNOUNCEMENTS | jq -r '.announcements[-5:] | .[] | "\(.timestamp) | \(.type) | \(.message)"' | \
            while IFS='|' read -r timestamp type message; do
                case $type in
                    "connection")
                        echo -e "${GREEN}[CONNECT]${NC} $message"
                        ;;
                    "detection")
                        echo -e "${BLUE}[DETECT]${NC} $message"
                        ;;
                    "disconnection")
                        echo -e "${RED}[DISCONNECT]${NC} $message"
                        ;;
                    "warning")
                        echo -e "${YELLOW}[WARNING]${NC} $message"
                        ;;
                    *)
                        echo -e "${NC}[INFO]${NC} $message"
                        ;;
                esac
            done
        else
            echo -e "${CYAN}No announcements yet. Waiting for vision data...${NC}"
        fi
        
    else
        echo -e "${RED}✗ Unable to fetch vision status${NC}"
    fi
    
    echo ""
    echo "═══════════════════════════════════════"
    echo "Auto-refresh in 3 seconds... (Ctrl+C to exit)"
    sleep 3
done
