#!/bin/bash
# Test Analyzer integration with Cortex

echo "🧪 Testing Analyzer Analysis Integration"
echo "=========================================="
echo ""

# Job UUID from the user's completed job
JOB_UUID="4d9376a5-5a4b-4642-86cd-78f7a63fab3d"
CORTEX_URL="http://localhost:8000"

echo "📋 Test 1: List job files"
curl -s "${CORTEX_URL}/analysis/jobs/${JOB_UUID}/files" | python3 -m json.tool | head -20

echo ""
echo "📋 Test 2: Categorize files"
curl -s "${CORTEX_URL}/analysis/jobs/${JOB_UUID}/files/categorize" | python3 -m json.tool

echo ""
echo "📋 Test 3: Get analysis summary"
curl -s "${CORTEX_URL}/analysis/jobs/${JOB_UUID}/summary" | python3 -m json.tool | head -50

echo ""
echo "✅ Tests complete!"
echo ""
echo "💡 If you see JSON output above, Analyzer integration is working!"
echo "💡 If you see errors, make sure:"
echo "   1. Analyzer is running on port 8004"
echo "   2. Cortex is running on port 8000 with updated code"
