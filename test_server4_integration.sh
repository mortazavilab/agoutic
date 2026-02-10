#!/bin/bash
# Test Server4 integration with Server1

echo "🧪 Testing Server4 Analysis Integration"
echo "=========================================="
echo ""

# Job UUID from the user's completed job
JOB_UUID="4d9376a5-5a4b-4642-86cd-78f7a63fab3d"
SERVER1_URL="http://localhost:8000"

echo "📋 Test 1: List job files"
curl -s "${SERVER1_URL}/analysis/jobs/${JOB_UUID}/files" | python3 -m json.tool | head -20

echo ""
echo "📋 Test 2: Categorize files"
curl -s "${SERVER1_URL}/analysis/jobs/${JOB_UUID}/files/categorize" | python3 -m json.tool

echo ""
echo "📋 Test 3: Get analysis summary"
curl -s "${SERVER1_URL}/analysis/jobs/${JOB_UUID}/summary" | python3 -m json.tool | head -50

echo ""
echo "✅ Tests complete!"
echo ""
echo "💡 If you see JSON output above, Server4 integration is working!"
echo "💡 If you see errors, make sure:"
echo "   1. Server4 is running on port 8004"
echo "   2. Server1 is running on port 8000 with updated code"
