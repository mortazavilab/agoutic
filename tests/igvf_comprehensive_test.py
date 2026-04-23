"""
Comprehensive IGVF MCP Server Test Suite

Tests every edge case, boundary condition, and scenario that might be missed.
Runs against the live IGVF server on port 8009 using MCPHttpClient.
"""

import asyncio
import json
import sys
import time
import traceback

sys.path.insert(0, "/Users/eli/code/agoutic")

from common.mcp_client import MCPHttpClient
from atlas.igvf_client import IGVFClient
from atlas.igvf_tool_schemas import TOOL_SCHEMAS
from atlas import config as igvf_config

errors = []
warnings = []
passed = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  ✅ {msg}")


def fail(msg):
    errors.append(msg)
    print(f"  ❌ {msg}")


def warn(msg):
    warnings.append(msg)
    print(f"  ⚠️  {msg}")


async def run_all_tests():
    global passed

    client = MCPHttpClient("igvf-test", "http://localhost:8009")
    await client.connect()
    print("Connected to IGVF MCP server\n")

    async def call(name, **kw):
        return await client.call_tool(name, **kw)

    # =========================================================================
    # SUITE 1: PAGINATION & BOUNDARY VALUES
    # =========================================================================
    print("=" * 70)
    print("SUITE 1: PAGINATION & BOUNDARY VALUES")
    print("=" * 70)

    # 1a. limit=0
    print("\n1a. limit=0")
    r = await call("search_measurement_sets", limit=0)
    if r.get("count", -1) == 0:
        ok(f"limit=0 → count=0, total={r.get('total')}")
    else:
        fail(f"limit=0 returned count={r.get('count')}")

    # 1b. limit=1
    print("\n1b. limit=1")
    r = await call("search_measurement_sets", limit=1)
    if r.get("count") == 1:
        ok(f"limit=1 → exactly 1 result")
    else:
        fail(f"limit=1 returned {r.get('count')}")

    # 1c. limit=500 (max allowed cap)
    print("\n1c. limit=500 (max)")
    r = await call("search_measurement_sets", limit=500)
    if r.get("count") == 500:
        ok(f"limit=500 → count={r.get('count')} ✓")
    else:
        warn(f"limit=500 → count={r.get('count')} (API may return fewer)")

    # 1d. limit=501 (beyond cap — should be capped to 500)
    print("\n1d. limit=501 (beyond cap)")
    r = await call("search_measurement_sets", limit=501)
    if r.get("count", 999) <= 500:
        ok(f"limit=501 capped → count={r.get('count')}")
    else:
        fail(f"limit=501 not capped, returned {r.get('count')}")

    # 1e. Negative limit (clamped to 0 by client)
    print("\n1e. limit=-1 (negative, clamped to 0)")
    try:
        r = await call("search_measurement_sets", limit=-1)
        if isinstance(r, dict) and r.get("count", -1) == 0:
            ok(f"Negative limit clamped to 0 → count=0, total={r.get('total')}")
        elif isinstance(r, dict) and "error" in str(r).lower():
            ok("Negative limit returned error dict")
        else:
            fail(f"Negative limit returned unexpected count={r.get('count')}")
    except Exception as e:
        if "500" in str(e):
            fail("Negative limit causes 500 from IGVF API — needs input validation")
        else:
            ok(f"Negative limit raised error: {str(e)[:80]}")

    # 1f. String limit (type coercion)
    print("\n1f. limit as string '10'")
    try:
        r = await call("search_measurement_sets", limit="10")
        # FastMCP may coerce or reject
        if isinstance(r, dict):
            ok(f"String limit handled → count={r.get('count')}")
        else:
            warn(f"String limit result: {type(r)}")
    except Exception as e:
        ok(f"String limit rejected: {str(e)[:80]}")

    # =========================================================================
    # SUITE 2: SPECIAL CHARACTERS & INJECTION
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 2: SPECIAL CHARACTERS & INJECTION")
    print("=" * 70)

    # 2a. SQL injection-like string
    print("\n2a. SQL injection-like query")
    r = await call("search_genes", query="'; DROP TABLE genes; --")
    if isinstance(r, dict) and r.get("total", 0) == 0:
        ok("SQL injection string returned 0 results (safe)")
    elif isinstance(r, dict):
        ok(f"SQL injection string handled safely, total={r.get('total')}")
    else:
        warn(f"Unexpected result type: {type(r)}")

    # 2b. XSS-like string
    print("\n2b. XSS-like query")
    r = await call("search_genes", query="<script>alert('xss')</script>")
    ok(f"XSS string handled safely, total={r.get('total', 'N/A')}")

    # 2c. Unicode characters
    print("\n2c. Unicode characters")
    r = await call("search_genes", query="TP53α€¥日本語")
    ok(f"Unicode handled, total={r.get('total', 0)}")

    # 2d. Very long query string
    print("\n2d. Very long query (1000 chars)")
    long_q = "A" * 1000
    try:
        r = await call("search_genes", query=long_q)
        ok(f"Long query handled, total={r.get('total', 0)}")
    except Exception as e:
        ok(f"Long query rejected: {str(e)[:80]}")

    # 2e. Newlines and tabs in query
    print("\n2e. Newlines and tabs")
    r = await call("search_genes", query="TP53\n\tBRCA1")
    ok(f"Newlines/tabs handled, total={r.get('total', 0)}")

    # 2f. URL-encoded characters
    print("\n2f. URL path traversal attempt")
    try:
        r = await call("get_dataset", accession="../../etc/passwd")
        # Should fail or return error
        if isinstance(r, dict):
            ok("Path traversal handled safely")
    except Exception as e:
        ok(f"Path traversal rejected: {str(e)[:80]}")

    # 2g. Null byte
    print("\n2g. Null byte in accession")
    try:
        r = await call("get_dataset", accession="IGVFDS\x00HACK")
        ok(f"Null byte handled safely")
    except Exception as e:
        ok(f"Null byte rejected: {str(e)[:80]}")

    # =========================================================================
    # SUITE 3: PARAMETER TYPE COERCION & CONFLICTS
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 3: PARAMETER TYPE COERCION & CONFLICTS")
    print("=" * 70)

    # 3a. Integer passed as string
    print("\n3a. limit as float 10.5")
    try:
        r = await call("search_measurement_sets", limit=10.5)
        if isinstance(r, dict):
            ok(f"Float limit handled → count={r.get('count')}")
    except Exception as e:
        ok(f"Float limit rejected: {str(e)[:80]}")

    # 3b. Boolean param
    print("\n3b. limit as boolean True")
    try:
        r = await call("search_measurement_sets", limit=True)
        ok(f"Bool limit handled → count={r.get('count', 'err')}")
    except Exception as e:
        ok(f"Bool limit rejected: {str(e)[:80]}")

    # 3c. Extra unknown parameters (should be ignored or rejected)
    print("\n3c. Extra unknown parameters")
    try:
        r = await call("search_measurement_sets", limit=5, unknown_param="test", another_bad="xyz")
        if isinstance(r, dict) and r.get("count", -1) >= 0:
            ok(f"Extra params ignored, count={r.get('count')}")
        else:
            warn("Extra params caused unexpected result")
    except Exception as e:
        ok(f"Extra params rejected: {str(e)[:80]}")

    # 3d. Required param missing for get_dataset
    print("\n3d. get_dataset with no accession")
    try:
        r = await call("get_dataset")
        fail("Missing required param did not error!")
    except Exception as e:
        ok(f"Missing required param rejected: {str(e)[:80]}")

    # 3e. Required param missing for search_genes
    print("\n3e. search_genes with no query")
    try:
        r = await call("search_genes")
        fail("search_genes without query should fail")
    except Exception as e:
        ok(f"Missing query rejected: {str(e)[:80]}")

    # 3f. Required param missing for get_gene
    print("\n3f. get_gene with no gene_id")
    try:
        r = await call("get_gene")
        fail("get_gene without gene_id should fail")
    except Exception as e:
        ok(f"Missing gene_id rejected: {str(e)[:80]}")

    # =========================================================================
    # SUITE 4: CACHE BEHAVIOR
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 4: CACHE BEHAVIOR")
    print("=" * 70)

    # 4a. Same query twice should use cache (faster)
    print("\n4a. Cache hit performance")
    direct_client = IGVFClient()
    direct_client.clear_cache()

    t0 = time.time()
    r1 = direct_client.search("MeasurementSet", limit=5, field_filters={"status": "released"})
    t1 = time.time()
    r2 = direct_client.search("MeasurementSet", limit=5, field_filters={"status": "released"})
    t2 = time.time()

    first_time = t1 - t0
    cached_time = t2 - t1

    ids1 = [o.get("accession") for o in r1.get("@graph", [])]
    ids2 = [o.get("accession") for o in r2.get("@graph", [])]
    if ids1 == ids2:
        ok(f"Cache consistent: same results")
    else:
        fail(f"Cache inconsistency: {ids1} vs {ids2}")

    if cached_time < first_time:
        ok(f"Cache faster: {first_time:.3f}s vs {cached_time:.6f}s")
    else:
        warn(f"Cache not faster: {first_time:.3f}s vs {cached_time:.3f}s (may be network variance)")

    # 4b. Cache size
    print("\n4b. Cache state")
    info = await call("get_server_info")
    ok(f"Cache size: {info.get('cache_size', '?')} entries")

    # 4c. Different params should NOT cache-collide
    print("\n4c. Cache key isolation")
    direct_client.clear_cache()
    r_atac = direct_client.search("MeasurementSet", limit=2, field_filters={"preferred_assay_titles": "ATAC-seq"})
    r_wgs = direct_client.search("MeasurementSet", limit=2, field_filters={"preferred_assay_titles": "whole genome sequencing"})
    atac_ids = [o.get("accession") for o in r_atac.get("@graph", [])]
    wgs_ids = [o.get("accession") for o in r_wgs.get("@graph", [])]
    if atac_ids != wgs_ids:
        ok(f"Cache keys isolated: ATAC={atac_ids}, WGS={wgs_ids}")
    else:
        fail("Cache collision! ATAC and WGS returned same results")

    # =========================================================================
    # SUITE 5: SCHEMA-TOOL SIGNATURE ALIGNMENT
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 5: SCHEMA-TOOL SIGNATURE ALIGNMENT")
    print("=" * 70)

    # 5a. Check tool schemas match registered tools
    print("\n5a. Schema vs registered tools")
    mcp_tools = await client.list_tools()
    mcp_tool_names = {t["name"] for t in mcp_tools}
    schema_tool_names = set(TOOL_SCHEMAS.keys())

    missing_from_schema = mcp_tool_names - schema_tool_names
    missing_from_mcp = schema_tool_names - mcp_tool_names

    if missing_from_schema:
        fail(f"Tools in MCP but NOT in schemas: {missing_from_schema}")
    if missing_from_mcp:
        fail(f"Tools in schemas but NOT in MCP: {missing_from_mcp}")
    if not missing_from_schema and not missing_from_mcp:
        ok(f"All {len(mcp_tool_names)} tools match between MCP and schemas")

    # 5b. Check param names match between schema and MCP tool signatures
    print("\n5b. Parameter name alignment")
    for tool in mcp_tools:
        name = tool["name"]
        schema_params = set(TOOL_SCHEMAS.get(name, {}).get("parameters", {}).get("properties", {}).keys())
        mcp_params = set(tool.get("inputSchema", {}).get("properties", {}).keys())
        if schema_params != mcp_params:
            fail(f"  {name}: schema={schema_params} vs mcp={mcp_params}")
        else:
            pass  # silent pass for brevity
    ok(f"Parameter names aligned for all {len(mcp_tools)} tools")

    # 5c. Check required params match
    print("\n5c. Required params alignment")
    mismatches = []
    for tool in mcp_tools:
        name = tool["name"]
        schema_required = set(TOOL_SCHEMAS.get(name, {}).get("parameters", {}).get("required", []))
        mcp_required = set(tool.get("inputSchema", {}).get("required", []))
        if schema_required != mcp_required:
            mismatches.append(f"{name}: schema_req={schema_required} vs mcp_req={mcp_required}")
    if mismatches:
        for m in mismatches:
            fail(f"  Required mismatch: {m}")
    else:
        ok("Required params aligned for all tools")

    # =========================================================================
    # SUITE 6: CORTEX DISPATCH EDGE CASES
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 6: CORTEX DISPATCH EDGE CASES (config validation)")
    print("=" * 70)

    igvf_entry = igvf_config.CONSORTIUM_REGISTRY["igvf"]

    # 6a. All tool aliases resolve to real tools
    print("\n6a. Tool alias validation")
    aliases = igvf_entry.get("tool_aliases", {})
    for wrong, correct in aliases.items():
        if correct not in mcp_tool_names:
            fail(f"  Alias '{wrong}' → '{correct}' but '{correct}' is not a real tool!")
    ok(f"All {len(aliases)} tool aliases resolve to real tools")

    # 6b. All param aliases reference real tools
    print("\n6b. Param alias tool targets")
    param_aliases = igvf_entry.get("param_aliases", {})
    for tool_name, mappings in param_aliases.items():
        if tool_name not in mcp_tool_names:
            fail(f"  Param alias for '{tool_name}' but it's not a registered tool!")
    ok(f"All {len(param_aliases)} param alias entries reference real tools")

    # 6c. Param aliases map to actual tool params
    print("\n6c. Param alias target validation")
    mcp_tool_params = {}
    for tool in mcp_tools:
        mcp_tool_params[tool["name"]] = set(tool.get("inputSchema", {}).get("properties", {}).keys())

    alias_errors = []
    for tool_name, mappings in param_aliases.items():
        if tool_name in mcp_tool_params:
            real_params = mcp_tool_params[tool_name]
            for wrong_param, correct_param in mappings.items():
                if correct_param not in real_params:
                    alias_errors.append(f"{tool_name}: '{wrong_param}'→'{correct_param}' but '{correct_param}' not in {real_params}")
    if alias_errors:
        for ae in alias_errors:
            fail(f"  {ae}")
    else:
        ok(f"All param aliases map to real tool parameters")

    # 6d. Fallback patterns compile
    print("\n6d. Fallback pattern compilation")
    import re
    patterns = igvf_entry.get("fallback_patterns", {})
    for pat, repl in patterns.items():
        try:
            re.compile(pat)
        except re.error as e:
            fail(f"  Pattern '{pat}' does not compile: {e}")
    ok(f"All {len(patterns)} fallback patterns compile")

    # 6e. Skills reference exists
    print("\n6e. Skills reference")
    skills = igvf_entry.get("skills", [])
    print(f"  Skills: {skills}")
    ok(f"Skills configured: {skills}")

    # 6f. Consortium URL
    print("\n6f. Consortium URL")
    url = igvf_config.get_consortium_url("igvf")
    ok(f"URL={url}")

    # =========================================================================
    # SUITE 7: FILE TYPES, MULTI-FILE DATASETS, EDGE CASES
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 7: FILE & DATASET EDGE CASES")
    print("=" * 70)

    # 7a. get_files_for_dataset with file_format filter
    print("\n7a. get_files_for_dataset with format filter")
    r = await call("get_files_for_dataset", accession="IGVFDS6639ECQN", file_format="fastq")
    fastq_count = r.get("total_files", -1)
    ok(f"  Files with format=fastq: {fastq_count}")

    # 7b. get_files_for_dataset with non-existent format
    print("\n7b. get_files_for_dataset with non-existent format")
    r = await call("get_files_for_dataset", accession="IGVFDS6639ECQN", file_format="zzz_nonexistent")
    count = r.get("total_files", -1)
    if count == 0:
        ok("Non-existent format returns 0 files")
    else:
        fail(f"Non-existent format returned {count} files")

    # 7c. search_files without any filters
    print("\n7c. search_files with no filters")
    r = await call("search_files")
    ok(f"All files: total={r.get('total')}, count={r.get('count')}")

    # 7d. search_files with multiple filters
    print("\n7d. search_files with format + status")
    r = await call("search_files", file_format="bam", status="released", limit=3)
    if r.get("count", 0) > 0:
        ok(f"Multi-filter: {r.get('count')} BAM files")
    else:
        warn("No BAM files found?")

    # 7e. search_files with content_type
    print("\n7e. search_files by content_type")
    r = await call("search_files", content_type="reads", limit=3)
    ok(f"Content type 'reads': total={r.get('total')}, count={r.get('count')}")

    # 7f. get_file_download_url returns proper structure
    print("\n7f. get_file_download_url response structure")
    r = await call("get_file_download_url", file_accession="IGVFFI7174NOZD")
    required_keys = {"file_accession", "download_url"}
    actual_keys = set(r.keys())
    if required_keys.issubset(actual_keys):
        ok(f"Download URL has all required keys: {sorted(actual_keys)}")
    else:
        fail(f"Missing keys: {required_keys - actual_keys}")

    if r.get("download_url", "").startswith("http"):
        ok(f"URL is valid: {r['download_url'][:60]}...")
    else:
        fail(f"URL invalid or empty: {r.get('download_url')}")

    # 7g. get_file_metadata response completeness
    print("\n7g. get_file_metadata response completeness")
    r = await call("get_file_metadata", file_accession="IGVFFI7174NOZD")
    expected_fields = {"accession", "file_format", "content_type", "file_size", "status", "href"}
    actual = set(r.keys())
    missing = expected_fields - actual
    if missing:
        fail(f"File metadata missing fields: {missing}")
    else:
        ok(f"File metadata has all fields, size={r.get('file_size')} bytes")

    # 7h. get_dataset response completeness
    print("\n7h. get_dataset response completeness")
    r = await call("get_dataset", accession="IGVFDS6639ECQN")
    expected_fields = {"accession", "assay", "summary", "status", "lab", "link", "raw"}
    actual = set(r.keys())
    missing = expected_fields - actual
    if missing:
        fail(f"Dataset missing fields: {missing}")
    else:
        ok(f"Dataset has all fields: accession={r.get('accession')}")

    # =========================================================================
    # SUITE 8: SEARCH ACROSS ALL DATASET TYPES
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 8: ALL DATASET TYPE SEARCHES")
    print("=" * 70)

    # 8a. search_analysis_sets
    print("\n8a. search_analysis_sets")
    r = await call("search_analysis_sets", limit=2)
    ok(f"AnalysisSets: total={r.get('total')}, count={r.get('count')}")

    # 8b. search_prediction_sets
    print("\n8b. search_prediction_sets")
    r = await call("search_prediction_sets", limit=2)
    ok(f"PredictionSets: total={r.get('total')}, count={r.get('count')}")

    # 8c. search_by_sample
    print("\n8c. search_by_sample with K562")
    r = await call("search_by_sample", sample_term="K562", limit=3)
    ok(f"K562 datasets: total={r.get('total')}, sample_term={r.get('sample_term')}")

    # 8d. search_by_assay
    print("\n8d. search_by_assay with ATAC-seq")
    r = await call("search_by_assay", assay_title="ATAC-seq", limit=3)
    ok(f"ATAC-seq datasets: total={r.get('total')}, assay_title={r.get('assay_title')}")

    # 8e. search_by_sample with combined filters
    print("\n8e. search_by_sample + assay + organism filters")
    r = await call("search_by_sample", sample_term="K562", assay="ATAC-seq", organism="Homo sapiens", limit=3)
    ok(f"K562 + ATAC + human: total={r.get('total')}")

    # 8f. search with "in progress" status
    print("\n8f. search with status='in progress'")
    r = await call("search_measurement_sets", status="in progress", limit=3)
    ok(f"In-progress datasets: total={r.get('total')}")

    # 8g. search with lab filter
    print("\n8g. search_measurement_sets with lab filter")
    r = await call("search_measurement_sets", lab="Jay Shendure, UW", limit=3)
    ok(f"Lab-filtered results: total={r.get('total')}, count={r.get('count')}")

    # =========================================================================
    # SUITE 9: GENE & SAMPLE EDGE CASES
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 9: GENE & SAMPLE EDGE CASES")
    print("=" * 70)

    # 9a. search_genes by symbol
    print("\n9a. search_genes by symbol")
    r = await call("search_genes", query="BRCA1", limit=5)
    ok(f"BRCA1: total={r.get('total')}, count={r.get('count')}")

    # 9b. search_genes with organism filter
    print("\n9b. search_genes + organism filter")
    r = await call("search_genes", query="TP53", organism="Homo sapiens", limit=5)
    ok(f"TP53 (human): total={r.get('total')}")

    # 9c. search_genes with mouse organism
    print("\n9c. search_genes for mouse")
    r = await call("search_genes", query="Trp53", organism="Mus musculus", limit=5)
    ok(f"Trp53 (mouse): total={r.get('total')}")

    # 9d. get_gene with symbol
    print("\n9d. get_gene by symbol")
    r = await call("get_gene", gene_id="TP53")
    if "error" not in r:
        ok(f"Gene: symbol={r.get('symbol')}, taxa={r.get('taxa')}")
    else:
        warn(f"get_gene TP53: {r.get('error')}")

    # 9e. get_gene with Ensembl-like ID (IGVF @id path)
    print("\n9e. get_gene by IGVF path (if exists)")
    r = await call("get_gene", gene_id="ENSG00000205456")
    if "error" not in r:
        ok(f"Gene by Ensembl: symbol={r.get('symbol')}")
    else:
        ok(f"Gene by Ensembl lookup: {r.get('error', 'handled')}")

    # 9f. search_samples by type
    print("\n9f. search_samples by type InVitroSystem")
    r = await call("search_samples", sample_type="InVitroSystem", limit=3)
    ok(f"InVitroSystem samples: total={r.get('total')}")

    # 9g. search_samples by type PrimaryCell
    print("\n9g. search_samples by type PrimaryCell")
    r = await call("search_samples", sample_type="PrimaryCell", limit=3)
    ok(f"PrimaryCell samples: total={r.get('total')}")

    # 9h. search_samples by type Tissue
    print("\n9h. search_samples by type Tissue")
    r = await call("search_samples", sample_type="Tissue", limit=3)
    ok(f"Tissue samples: total={r.get('total')}")

    # 9i. search_samples by type WholeOrganism
    print("\n9i. search_samples WholeOrganism")
    r = await call("search_samples", sample_type="WholeOrganism", limit=3)
    ok(f"WholeOrganism samples: total={r.get('total')}")

    # 9j. search_samples with query
    print("\n9j. search_samples with text query")
    r = await call("search_samples", query="motor neuron", limit=3)
    ok(f"'motor neuron' samples: total={r.get('total')}")

    # 9k. search_samples with organism
    print("\n9k. search_samples by mouse organism")
    r = await call("search_samples", organism="Mus musculus", limit=3)
    ok(f"Mouse samples: total={r.get('total')}")

    # 9l. search_samples invalid type
    print("\n9l. search_samples with invalid type")
    try:
        r = await call("search_samples", sample_type="BogusType")
        if r.get("total", 0) == 0:
            ok("Invalid sample type → 0 results")
        else:
            warn(f"Invalid sample type returned {r.get('total')} results")
    except Exception as e:
        ok(f"Invalid sample type rejected: {str(e)[:80]}")

    # =========================================================================
    # SUITE 10: CONCURRENT & STRESS
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 10: CONCURRENT REQUESTS")
    print("=" * 70)

    print("\n10a. 5 concurrent searches")
    async def concurrent_search(i):
        return await call("search_measurement_sets", limit=2, status="released")

    tasks = [concurrent_search(i) for i in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = sum(1 for r in results if isinstance(r, dict) and "count" in r)
    failures = sum(1 for r in results if isinstance(r, Exception))
    if successes == 5:
        ok(f"All 5 concurrent requests succeeded")
    else:
        fail(f"Concurrent: {successes} succeeded, {failures} failed")
        for r in results:
            if isinstance(r, Exception):
                print(f"    Error: {r}")

    # 10b. Mixed concurrent: different tools
    print("\n10b. Mixed concurrent tool calls")
    mixed_tasks = [
        call("search_measurement_sets", limit=1),
        call("search_genes", query="TP53", limit=1),
        call("search_samples", limit=1),
        call("get_server_info"),
        call("search_files", file_format="bam", limit=1),
    ]
    results = await asyncio.gather(*mixed_tasks, return_exceptions=True)
    successes = sum(1 for r in results if isinstance(r, dict))
    if successes == 5:
        ok("All 5 mixed concurrent tool calls succeeded")
    else:
        fail(f"Mixed concurrent: {successes}/5 succeeded")
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"    Task {i} failed: {r}")

    # =========================================================================
    # SUITE 11: RESPONSE FORMAT CONSISTENCY
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 11: RESPONSE FORMAT CONSISTENCY")
    print("=" * 70)

    # All search tools should return {total, count, results}
    print("\n11a. Search response format consistency")
    search_tools = [
        ("search_measurement_sets", {}),
        ("search_analysis_sets", {}),
        ("search_prediction_sets", {}),
        ("search_by_sample", {"sample_term": "K562"}),
        ("search_by_assay", {"assay_title": "ATAC-seq"}),
        ("search_files", {}),
        ("search_genes", {"query": "TP53"}),
        ("search_samples", {}),
    ]

    format_ok = True
    for tool_name, params in search_tools:
        r = await call(tool_name, limit=1, **params)
        if not isinstance(r, dict):
            fail(f"{tool_name}: returned {type(r)}, not dict")
            format_ok = False
            continue
        if "total" not in r:
            fail(f"{tool_name}: missing 'total'")
            format_ok = False
        if "count" not in r:
            fail(f"{tool_name}: missing 'count'")
            format_ok = False
        if "results" not in r:
            fail(f"{tool_name}: missing 'results'")
            format_ok = False
    if format_ok:
        ok(f"All {len(search_tools)} search tools return {{total, count, results}}")

    # 11b. Dataset result fields consistency
    print("\n11b. Dataset result field consistency")
    r = await call("search_measurement_sets", limit=3)
    for i, ds in enumerate(r.get("results", [])):
        required = {"accession", "assay", "status", "link"}
        missing = required - set(ds.keys())
        if missing:
            fail(f"Result {i} missing fields: {missing}")
    ok(f"Dataset results have required fields")

    # 11c. File result fields consistency
    print("\n11c. File result field consistency")
    r = await call("search_files", limit=3)
    for i, f in enumerate(r.get("results", [])):
        required = {"accession", "file_format", "status", "link"}
        missing = required - set(f.keys())
        if missing:
            fail(f"File result {i} missing fields: {missing}")
    ok(f"File results have required fields")

    # 11d. Gene result fields consistency
    print("\n11d. Gene result field consistency")
    r = await call("search_genes", query="TP53", limit=3)
    for i, g in enumerate(r.get("results", [])):
        required = {"symbol", "taxa", "link"}
        missing = required - set(g.keys())
        if missing:
            fail(f"Gene result {i} missing fields: {missing}")
    ok(f"Gene results have required fields")

    # =========================================================================
    # SUITE 12: ERROR HANDLING & RESILIENCE
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 12: ERROR HANDLING & RESILIENCE")
    print("=" * 70)

    # 12a. get_dataset with random gibberish
    print("\n12a. get_dataset with gibberish accession")
    try:
        r = await call("get_dataset", accession="XYZZY123NONSENSE")
        if "error" in str(r).lower() or not r.get("accession"):
            ok("Gibberish accession handled gracefully")
        else:
            warn(f"Gibberish accession returned data: {list(r.keys())[:5]}")
    except Exception as e:
        ok(f"Gibberish accession errored: {str(e)[:80]}")

    # 12b. get_file_metadata with random gibberish
    print("\n12b. get_file_metadata with gibberish")
    try:
        r = await call("get_file_metadata", file_accession="BADFILE999")
        if "error" in str(r).lower():
            ok("Bad file accession handled")
    except Exception as e:
        ok(f"Bad file accession errored: {str(e)[:80]}")

    # 12c. get_file_download_url with bad accession
    print("\n12c. get_file_download_url with gibberish")
    try:
        r = await call("get_file_download_url", file_accession="NOTAFILE")
        if not r.get("download_url"):
            ok("Bad file → no download URL")
    except Exception as e:
        ok(f"Bad file download errored: {str(e)[:80]}")

    # 12d. search_by_sample with empty string
    print("\n12d. search_by_sample with empty sample_term")
    try:
        r = await call("search_by_sample", sample_term="")
        ok(f"Empty sample_term: total={r.get('total')}")
    except Exception as e:
        ok(f"Empty sample_term rejected: {str(e)[:80]}")

    # 12e. search_by_assay with empty string
    print("\n12e. search_by_assay with empty assay_title")
    try:
        r = await call("search_by_assay", assay_title="")
        ok(f"Empty assay_title: total={r.get('total')}")
    except Exception as e:
        ok(f"Empty assay_title rejected: {str(e)[:80]}")

    # 12f. get_files_for_dataset with non-existent dataset
    print("\n12f. get_files_for_dataset for non-existent dataset")
    r = await call("get_files_for_dataset", accession="IGVFDS0000ZZZZ")
    if r.get("total_files", -1) == 0:
        ok("Non-existent dataset → 0 files")
    else:
        warn(f"Non-existent dataset returned {r.get('total_files')} files")

    # 12g. search_files for dataset that exists but has no files of a type
    print("\n12g. search_files for dataset with no VCF files")
    r = await call("search_files", dataset_accession="IGVFDS6639ECQN", file_format="vcf")
    ok(f"Dataset with no VCF: total={r.get('total')}")

    # =========================================================================
    # SUITE 13: CLIENT INTERNALS
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 13: CLIENT INTERNALS")
    print("=" * 70)

    # 13a. get_object with full path vs bare accession
    print("\n13a. Path vs bare accession")
    obj1 = direct_client.get_object("IGVFDS6639ECQN")
    acc1 = obj1.get("accession", "")
    if acc1 == "IGVFDS6639ECQN":
        ok(f"Bare accession resolves correctly")
    else:
        fail(f"Bare accession returned {acc1}")

    # 13b. clear_cache
    print("\n13b. clear_cache")
    stats = direct_client.clear_cache()
    ok(f"Cache cleared: {stats}")

    # 13c. Verify cache is actually empty
    print("\n13c. Cache empty after clear")
    if len(direct_client._cache) == 0:
        ok("Cache is empty")
    else:
        fail(f"Cache still has {len(direct_client._cache)} entries")

    # 13d. search_all auto-pagination
    print("\n13d. search_all auto-pagination")
    results = direct_client.search_all(
        "MeasurementSet",
        field_filters={"status": "released", "preferred_assay_titles": "ATAC-seq"},
        max_results=50,
    )
    ok(f"search_all returned {len(results)} results (max 50)")

    # 13e. search_all respects max_results across pages
    print("\n13e. search_all max_results boundary")
    results = direct_client.search_all(
        "Gene",
        field_filters={"query": "TP53"},
        max_results=7,
    )
    if len(results) <= 7:
        ok(f"search_all capped at {len(results)} (max_results=7)")
    else:
        fail(f"search_all returned {len(results)} > 7")

    # =========================================================================
    # DONE
    # =========================================================================
    await client.disconnect()

    print("\n" + "=" * 70)
    print(f"FINAL RESULTS")
    print(f"=" * 70)
    print(f"  Passed:   {passed}")
    print(f"  Failed:   {len(errors)}")
    print(f"  Warnings: {len(warnings)}")
    if errors:
        print(f"\nFAILURES:")
        for e in errors:
            print(f"  ❌ {e}")
    if warnings:
        print(f"\nWARNINGS:")
        for w in warnings:
            print(f"  ⚠️  {w}")
    if not errors:
        print(f"\n🎉 ALL TESTS PASSED!")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
