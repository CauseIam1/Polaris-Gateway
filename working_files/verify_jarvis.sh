#!/bin/bash
# JARVIS Toolkit Verification Script
# Run this after rebuilding the container to verify all endpoints

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8082}"

echo "=== JARVIS Toolkit Verification ==="
echo "Gateway URL: $GATEWAY_URL"
echo ""

# Test System Health
echo "1. Testing System Health..."
curl -s "$GATEWAY_URL/api/jarvis/system/health" | jq '.status' || echo "FAILED"

# Test Docker Stats
echo "2. Testing Docker Stats..."
curl -s "$GATEWAY_URL/api/jarvis/docker/stats" | jq '.total_containers' || echo "FAILED"

# Test QuestDB Health
echo "3. Testing QuestDB Health..."
curl -s "$GATEWAY_URL/api/jarvis/questdb/health" | jq '.questdb.status' || echo "FAILED"

# Test Rippled Health
echo "4. Testing Rippled Health..."
curl -s "$GATEWAY_URL/api/jarvis/rippled/health" | jq '.rippled.status' || echo "FAILED"

# Test LiveCharts Info
echo "5. Testing LiveCharts Info..."
curl -s "$GATEWAY_URL/api/jarvis/livecharts/info" | jq '.status' || echo "FAILED"

# Test WWW Info
echo "6. Testing WWW Info..."
curl -s "$GATEWAY_URL/api/jarvis/www/info" | jq '.status' || echo "FAILED"

# Test WWW List
echo "7. Testing WWW List..."
curl -s "$GATEWAY_URL/api/jarvis/www/list?path=/" | jq '.item_count' || echo "FAILED"

echo ""
echo "=== Verification Complete ==="
echo "If all tests show values (not FAILED), JARVIS toolkit is operational!"
