#!/bin/bash
# Quick test of Analyzer analysis endpoints through Cortex proxy

UUID="78805eb3-7b7a-4ec8-a88e-b4d00bb5c20d"

echo "🧪 Testing Cortex → Analyzer Analysis Pipeline"
echo "=============================================="
echo ""

echo "📋 Test 1: Get Analysis Summary (via Cortex proxy)"
curl -s "http://localhost:8000/analysis/jobs/${UUID}/summary" | python3 -m json.tool | head -80

echo ""
echo "📋 Test 2: Categorize Files (via Cortex proxy)"
curl -s "http://localhost:8000/analysis/jobs/${UUID}/files/categorize" | python3 -m json.tool | head -40

echo ""
echo "✅ If you see JSON data above, Cortex → Analyzer integration is working!"
echo "❌ If you see errors, check that both Cortex and Analyzer are running"
