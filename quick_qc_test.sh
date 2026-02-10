#!/bin/bash
# Quick test of Server4 analysis endpoints through Server1 proxy

UUID="78805eb3-7b7a-4ec8-a88e-b4d00bb5c20d"

echo "🧪 Testing Server1 → Server4 Analysis Pipeline"
echo "=============================================="
echo ""

echo "📋 Test 1: Get Analysis Summary (via Server1 proxy)"
curl -s "http://localhost:8000/analysis/jobs/${UUID}/summary" | python3 -m json.tool | head -80

echo ""
echo "📋 Test 2: Categorize Files (via Server1 proxy)"
curl -s "http://localhost:8000/analysis/jobs/${UUID}/files/categorize" | python3 -m json.tool | head -40

echo ""
echo "✅ If you see JSON data above, Server1 → Server4 integration is working!"
echo "❌ If you see errors, check that both Server1 and Server4 are running"
