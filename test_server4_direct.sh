#!/bin/bash
# Test Analyzer directly (no auth required)

echo "🧪 Testing Analyzer Direct Connection"
echo "====================================="
echo ""

# Job UUID from the user's completed job
JOB_UUID="4d9376a5-5a4b-4642-86cd-78f7a63fab3d"
ANALYZER_URL="http://localhost:8004"

echo "🔍 Checking if Analyzer is running..."
if curl -s "${ANALYZER_URL}/health" > /dev/null 2>&1; then
    echo "✅ Analyzer is running on port 8004"
else
    echo "❌ Analyzer is NOT running on port 8004"
    echo "   Start it with: uvicorn analyzer.app:app --host 0.0.0.0 --port 8004"
    exit 1
fi

echo ""
echo "📋 Test 1: List job files"
curl -s "${ANALYZER_URL}/analysis/jobs/${JOB_UUID}/files" | python3 -m json.tool 2>/dev/null | head -30

echo ""
echo "📋 Test 2: Categorize files"
curl -s "${ANALYZER_URL}/analysis/jobs/${JOB_UUID}/files/categorize" | python3 -m json.tool 2>/dev/null

echo ""
echo "📋 Test 3: Get analysis summary (first 50 lines)"
curl -s "${ANALYZER_URL}/analysis/summary/${JOB_UUID}" | python3 -m json.tool 2>/dev/null | head -50

echo ""
echo "✅ Tests complete!"
echo ""
echo "💡 Next: Test through Cortex with authentication"
