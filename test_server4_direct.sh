#!/bin/bash
# Test Server4 directly (no auth required)

echo "🧪 Testing Server4 Direct Connection"
echo "====================================="
echo ""

# Job UUID from the user's completed job
JOB_UUID="4d9376a5-5a4b-4642-86cd-78f7a63fab3d"
SERVER4_URL="http://localhost:8004"

echo "🔍 Checking if Server4 is running..."
if curl -s "${SERVER4_URL}/health" > /dev/null 2>&1; then
    echo "✅ Server4 is running on port 8004"
else
    echo "❌ Server4 is NOT running on port 8004"
    echo "   Start it with: uvicorn server4.app:app --host 0.0.0.0 --port 8004"
    exit 1
fi

echo ""
echo "📋 Test 1: List job files"
curl -s "${SERVER4_URL}/analysis/jobs/${JOB_UUID}/files" | python3 -m json.tool 2>/dev/null | head -30

echo ""
echo "📋 Test 2: Categorize files"
curl -s "${SERVER4_URL}/analysis/jobs/${JOB_UUID}/files/categorize" | python3 -m json.tool 2>/dev/null

echo ""
echo "📋 Test 3: Get analysis summary (first 50 lines)"
curl -s "${SERVER4_URL}/analysis/summary/${JOB_UUID}" | python3 -m json.tool 2>/dev/null | head -50

echo ""
echo "✅ Tests complete!"
echo ""
echo "💡 Next: Test through Server1 with authentication"
