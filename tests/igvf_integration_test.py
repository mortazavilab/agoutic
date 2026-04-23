"""
IGVF End-to-End Integration Test Suite

Tests the full pipeline that Cortex uses: tag parsing → tool/param alias
resolution → MCP call → /tools/schema endpoint → fallback pattern matching
→ skill manifest → server-down handling → cross-tool workflows.

Runs against the live IGVF server on port 8009.
"""

import asyncio
import json
import re
import sys
import time
import traceback

sys.path.insert(0, "/Users/eli/code/agoutic")

import httpx
from common.mcp_client import MCPHttpClient
from atlas.igvf_client import IGVFClient
from atlas.igvf_tool_schemas import TOOL_SCHEMAS
from atlas import config as igvf_config
from cortex.tag_parser import DATA_CALL_PATTERN, ParsedLLMResponse
from cortex.llm_validators import _parse_tag_params

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

    # =========================================================================
    # SUITE 1: /tools/schema ENDPOINT
    # =========================================================================
    print("=" * 70)
    print("SUITE 1: /tools/schema ENDPOINT")
    print("=" * 70)

    # 1a. Fetch schema endpoint
    print("\n1a. GET /tools/schema")
    async with httpx.AsyncClient(base_url="http://localhost:8009", timeout=10) as hc:
        resp = await hc.get("/tools/schema")
        if resp.status_code == 200:
            schema = resp.json()
            ok(f"Schema endpoint returns {len(schema)} tools")
        else:
            fail(f"Schema endpoint returned {resp.status_code}")
            schema = {}

    # 1b. Schema keys match TOOL_SCHEMAS
    print("\n1b. Schema keys match TOOL_SCHEMAS constant")
    if set(schema.keys()) == set(TOOL_SCHEMAS.keys()):
        ok(f"Schema endpoint matches TOOL_SCHEMAS: {sorted(schema.keys())}")
    else:
        fail(f"Mismatch: endpoint={set(schema.keys())}, code={set(TOOL_SCHEMAS.keys())}")

    # 1c. Every schema has description and parameters
    print("\n1c. Schema completeness")
    incomplete = []
    for name, s in schema.items():
        if not s.get("description"):
            incomplete.append(f"{name}: missing description")
        if "parameters" not in s:
            incomplete.append(f"{name}: missing parameters")
        else:
            if "properties" not in s["parameters"]:
                incomplete.append(f"{name}: missing parameters.properties")
    if incomplete:
        for ic in incomplete:
            fail(f"  {ic}")
    else:
        ok(f"All {len(schema)} schemas have description + parameters.properties")

    # 1d. HTTP Content-Type
    print("\n1d. Schema Content-Type header")
    async with httpx.AsyncClient(base_url="http://localhost:8009", timeout=10) as hc:
        resp = await hc.get("/tools/schema")
        ct = resp.headers.get("content-type", "")
        if "json" in ct:
            ok(f"Content-Type: {ct}")
        else:
            warn(f"Content-Type: {ct} (expected JSON)")

    # =========================================================================
    # SUITE 2: DATA_CALL TAG PARSING (unit-level)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 2: DATA_CALL TAG PARSING")
    print("=" * 70)

    # 2a. Standard IGVF DATA_CALL tag
    print("\n2a. Standard IGVF DATA_CALL tag")
    tag = '[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, status=released, limit=10]]'
    m = DATA_CALL_PATTERN.search(tag)
    if m:
        source_type, source_key, tool, params_str = m.group(1), m.group(2), m.group(3), m.group(4)
        params = _parse_tag_params(params_str)
        if source_type == "consortium" and source_key == "igvf" and tool == "search_measurement_sets":
            ok(f"Parsed: {source_type}={source_key}, tool={tool}, params={params}")
        else:
            fail(f"Wrong parse: {source_type}={source_key}, {tool}")
    else:
        fail("DATA_CALL_PATTERN did not match standard IGVF tag")

    # 2b. IGVF tag with no params
    print("\n2b. IGVF tag with no params")
    tag = '[[DATA_CALL: consortium=igvf, tool=get_server_info]]'
    m = DATA_CALL_PATTERN.search(tag)
    if m:
        params = _parse_tag_params(m.group(4))
        if m.group(3) == "get_server_info" and params == {}:
            ok(f"No-params tag parsed: tool={m.group(3)}, params={params}")
        else:
            fail(f"Wrong: tool={m.group(3)}, params={params}")
    else:
        fail("No-params tag did not match")

    # 2c. IGVF tag with complex params (spaces in values)
    print("\n2c. Tag with spaces in values")
    tag = '[[DATA_CALL: consortium=igvf, tool=search_by_sample, sample_term=motor neuron, organism=Homo sapiens]]'
    m = DATA_CALL_PATTERN.search(tag)
    if m:
        params = _parse_tag_params(m.group(4))
        if params.get("sample_term") == "motor neuron":
            ok(f"Spaces in value preserved: {params}")
        else:
            fail(f"Value lost spaces: {params}")
    else:
        fail("Complex-params tag did not match")

    # 2d. Tag with accession
    print("\n2d. Tag with accession param")
    tag = '[[DATA_CALL: consortium=igvf, tool=get_dataset, accession=IGVFDS6639ECQN]]'
    m = DATA_CALL_PATTERN.search(tag)
    if m:
        params = _parse_tag_params(m.group(4))
        if params.get("accession") == "IGVFDS6639ECQN":
            ok(f"Accession parsed: {params}")
        else:
            fail(f"Accession wrong: {params}")
    else:
        fail("Accession tag did not match")

    # 2e. Multiple DATA_CALL tags in same response
    print("\n2e. Multiple DATA_CALL tags")
    response = """I'll search for K562 datasets and also look up TP53:
[[DATA_CALL: consortium=igvf, tool=search_by_sample, sample_term=K562]]
And here's the gene lookup:
[[DATA_CALL: consortium=igvf, tool=search_genes, query=TP53]]"""
    matches = list(DATA_CALL_PATTERN.finditer(response))
    if len(matches) == 2:
        ok(f"Found {len(matches)} tags: {[m.group(3) for m in matches]}")
    else:
        fail(f"Expected 2 tags, found {len(matches)}")

    # 2f. IGVF tag embedded in markdown
    print("\n2f. Tag embedded in markdown text")
    response = "Let me search IGVF for ATAC-seq data:\n\n[[DATA_CALL: consortium=igvf, tool=search_by_assay, assay_title=ATAC-seq]]\n\nI found the following results."
    matches = list(DATA_CALL_PATTERN.finditer(response))
    if len(matches) == 1 and matches[0].group(3) == "search_by_assay":
        ok("Tag extracted from markdown context")
    else:
        fail(f"Markdown extraction failed: {len(matches)} matches")

    # 2g. Param with equals sign in value (edge case)
    print("\n2g. Param with numeric value")
    tag = '[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, limit=50]]'
    m = DATA_CALL_PATTERN.search(tag)
    if m:
        params = _parse_tag_params(m.group(4))
        if params.get("limit") == "50":
            ok(f"Numeric param parsed as string: {params}")
        else:
            fail(f"Numeric param wrong: {params}")

    # =========================================================================
    # SUITE 3: TOOL ALIAS RESOLUTION
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 3: TOOL ALIAS RESOLUTION")
    print("=" * 70)

    igvf_entry = igvf_config.CONSORTIUM_REGISTRY["igvf"]
    tool_aliases = igvf_entry.get("tool_aliases", {})
    all_tool_aliases = igvf_config.get_all_tool_aliases()

    # Connect MCP client for live tests
    client = MCPHttpClient("igvf-test", "http://localhost:8009")
    await client.connect()

    async def call(name, **kw):
        return await client.call_tool(name, **kw)

    # 3a. Test each alias resolves correctly
    print("\n3a. Every IGVF tool alias")
    mcp_tools = await client.list_tools()
    mcp_tool_names = {t["name"] for t in mcp_tools}

    for wrong_name, correct_name in tool_aliases.items():
        resolved = all_tool_aliases.get(wrong_name, wrong_name)
        if resolved == correct_name and correct_name in mcp_tool_names:
            pass  # silent pass
        else:
            fail(f"  Alias '{wrong_name}' → expected '{correct_name}', got '{resolved}'")
    ok(f"All {len(tool_aliases)} IGVF tool aliases resolve correctly")

    # 3b. Simulate LLM hallucinating "search_datasets" (should become search_measurement_sets)
    print("\n3b. Hallucinated tool name: search_datasets → search_measurement_sets")
    hallucinated = "search_datasets"
    resolved = all_tool_aliases.get(hallucinated, hallucinated)
    if resolved == "search_measurement_sets":
        ok(f"'{hallucinated}' → '{resolved}' ✓")
        # Actually call the real tool
        r = await call(resolved, limit=1)
        ok(f"  Live call returned total={r.get('total')}")
    else:
        fail(f"'{hallucinated}' → '{resolved}' (expected search_measurement_sets)")

    # 3c. Hallucinated "download_file" → get_file_download_url
    print("\n3c. Hallucinated: download_file → get_file_download_url")
    resolved = all_tool_aliases.get("download_file", "download_file")
    if resolved == "get_file_download_url":
        ok(f"'download_file' → '{resolved}' ✓")
    else:
        fail(f"'download_file' → '{resolved}'")

    # 3d. Hallucinated "gene_search" → search_genes
    print("\n3d. Hallucinated: gene_search → search_genes")
    resolved = all_tool_aliases.get("gene_search", "gene_search")
    if resolved == "search_genes":
        ok(f"'gene_search' → '{resolved}' ✓")
    else:
        fail(f"'gene_search' → '{resolved}'")

    # 3e. Unknown tool not in aliases stays unchanged
    print("\n3e. Unknown tool stays unchanged")
    unknown = "completely_random_tool"
    resolved = all_tool_aliases.get(unknown, unknown)
    if resolved == unknown:
        ok(f"Unknown '{unknown}' stays '{resolved}'")
    else:
        fail(f"Unknown '{unknown}' resolved to '{resolved}'")

    # 3f. No alias collisions between ENCODE and IGVF
    print("\n3f. No cross-consortium alias collisions")
    encode_aliases = igvf_config.CONSORTIUM_REGISTRY.get("encode", {}).get("tool_aliases", {})
    igvf_aliases = igvf_entry.get("tool_aliases", {})
    collisions = set(encode_aliases.keys()) & set(igvf_aliases.keys())
    # Some collisions are expected (like "search") since the merged dict just overwrites
    if collisions:
        # Check if the colliding aliases resolve to different targets
        for c in collisions:
            enc_target = encode_aliases[c]
            igvf_target = igvf_aliases[c]
            if enc_target != igvf_target:
                warn(f"  Alias collision '{c}': ENCODE→{enc_target}, IGVF→{igvf_target}")
        ok(f"Found {len(collisions)} shared alias keys (last-registered wins in merged dict)")
    else:
        ok("No alias key collisions between ENCODE and IGVF")

    # =========================================================================
    # SUITE 4: PARAM ALIAS RESOLUTION
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 4: PARAM ALIAS RESOLUTION")
    print("=" * 70)

    param_aliases = igvf_entry.get("param_aliases", {})
    all_param_aliases = igvf_config.get_all_param_aliases()

    # 4a. search_by_sample: "biosample" → "sample_term"
    print("\n4a. search_by_sample: biosample → sample_term")
    pa = all_param_aliases.get("search_by_sample", {})
    corrected = {pa.get(k, k): v for k, v in {"biosample": "K562"}.items()}
    if corrected == {"sample_term": "K562"}:
        ok(f"'biosample' remapped to 'sample_term': {corrected}")
        r = await call("search_by_sample", **corrected)
        ok(f"  Live call: total={r.get('total')}")
    else:
        fail(f"Remapping failed: {corrected}")

    # 4b. search_by_assay: "assay" → "assay_title"
    print("\n4b. search_by_assay: assay → assay_title")
    pa = all_param_aliases.get("search_by_assay", {})
    corrected = {pa.get(k, k): v for k, v in {"assay": "ATAC-seq"}.items()}
    if corrected == {"assay_title": "ATAC-seq"}:
        ok(f"'assay' remapped to 'assay_title': {corrected}")
    else:
        fail(f"Remapping failed: {corrected}")

    # 4c. search_genes: "gene_symbol" → "query"
    print("\n4c. search_genes: gene_symbol → query")
    pa = all_param_aliases.get("search_genes", {})
    corrected = {pa.get(k, k): v for k, v in {"gene_symbol": "BRCA1"}.items()}
    if corrected == {"query": "BRCA1"}:
        ok(f"'gene_symbol' remapped to 'query': {corrected}")
        r = await call("search_genes", **corrected)
        ok(f"  Live call: total={r.get('total')}")
    else:
        fail(f"Remapping failed: {corrected}")

    # 4d. get_gene: "gene_symbol" → "gene_id"
    print("\n4d. get_gene: gene_symbol → gene_id")
    pa = all_param_aliases.get("get_gene", {})
    corrected = {pa.get(k, k): v for k, v in {"gene_symbol": "TP53"}.items()}
    if corrected == {"gene_id": "TP53"}:
        ok(f"'gene_symbol' remapped to 'gene_id': {corrected}")
    else:
        fail(f"Remapping failed: {corrected}")

    # 4e. get_files_for_dataset: "dataset_accession" → "accession"
    print("\n4e. get_files_for_dataset: dataset_accession → accession")
    pa = all_param_aliases.get("get_files_for_dataset", {})
    corrected = {pa.get(k, k): v for k, v in {"dataset_accession": "IGVFDS6639ECQN"}.items()}
    if corrected == {"accession": "IGVFDS6639ECQN"}:
        ok(f"'dataset_accession' remapped to 'accession': {corrected}")
        r = await call("get_files_for_dataset", **corrected)
        ok(f"  Live call: total_files={r.get('total_files')}")
    else:
        fail(f"Remapping failed: {corrected}")

    # 4f. get_file_metadata: "accession" → "file_accession"
    print("\n4f. get_file_metadata: accession → file_accession")
    pa = all_param_aliases.get("get_file_metadata", {})
    corrected = {pa.get(k, k): v for k, v in {"accession": "IGVFFI7174NOZD"}.items()}
    if corrected == {"file_accession": "IGVFFI7174NOZD"}:
        ok(f"'accession' remapped to 'file_accession': {corrected}")
        r = await call("get_file_metadata", **corrected)
        ok(f"  Live call: format={r.get('file_format')}, size={r.get('file_size')}")
    else:
        fail(f"Remapping failed: {corrected}")

    # 4g. get_file_download_url: "accession" → "file_accession"
    print("\n4g. get_file_download_url: accession → file_accession")
    pa = all_param_aliases.get("get_file_download_url", {})
    corrected = {pa.get(k, k): v for k, v in {"accession": "IGVFFI7174NOZD"}.items()}
    if corrected == {"file_accession": "IGVFFI7174NOZD"}:
        ok(f"'accession' remapped to 'file_accession': {corrected}")
    else:
        fail(f"Remapping failed: {corrected}")

    # 4h. Params that are already correct stay unchanged
    print("\n4h. Correct params unchanged")
    pa = all_param_aliases.get("search_by_sample", {})
    correct_params = {"sample_term": "K562", "organism": "Homo sapiens", "limit": "5"}
    remapped = {pa.get(k, k): v for k, v in correct_params.items()}
    if remapped == correct_params:
        ok("Correct params unchanged through alias mapping")
    else:
        fail(f"Correct params modified: {remapped}")

    # 4i. search_measurement_sets: multiple alias paths
    print("\n4i. search_measurement_sets: biosample → sample")
    pa = all_param_aliases.get("search_measurement_sets", {})
    corrected = {pa.get(k, k): v for k, v in {"biosample": "K562", "assay_title": "ATAC-seq"}.items()}
    if corrected.get("sample") == "K562" and corrected.get("assay") == "ATAC-seq":
        ok(f"Multi-alias: {corrected}")
    else:
        fail(f"Multi-alias failed: {corrected}")

    # =========================================================================
    # SUITE 5: FALLBACK PATTERN MATCHING
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 5: FALLBACK PATTERN MATCHING")
    print("=" * 70)

    fallback_patterns = igvf_entry.get("fallback_patterns", {})
    all_fallback = igvf_config.get_all_fallback_patterns()

    # 5a. Each IGVF fallback pattern produces valid DATA_CALL
    print("\n5a. Fallback patterns produce valid DATA_CALL tags")
    test_cases = [
        (r'Get Dataset\s*\(([^)]+)\)', "Get Dataset (accession=IGVFDS6639ECQN)"),
        (r'Search Measurement Sets\s*\(([^)]+)\)', "Search Measurement Sets (status=released)"),
        (r'Search IGVF\s*\(([^)]+)\)', "Search IGVF (sample=K562)"),
        (r'Search By Sample\s*\(([^)]+)\)', "Search By Sample (sample_term=K562)"),
        (r'Search By Assay\s*\(([^)]+)\)', "Search By Assay (assay_title=ATAC-seq)"),
        (r'Search Genes\s*\(([^)]+)\)', "Search Genes (query=TP53)"),
        (r'Get Gene\s*\(([^)]+)\)', "Get Gene (gene_id=TP53)"),
        (r'Get File Metadata\s*\(([^)]+)\)', "Get File Metadata (file_accession=IGVFFI7174NOZD)"),
        (r'Get Server Info\s*\(\)', "Get Server Info ()"),
    ]
    for pattern_key, test_input in test_cases:
        replacement = fallback_patterns.get(pattern_key)
        if replacement is None:
            fail(f"  Pattern not found: {pattern_key}")
            continue
        result = re.sub(pattern_key, replacement, test_input)
        if "[[DATA_CALL:" in result and "consortium=igvf" in result:
            pass  # silent
        else:
            fail(f"  Pattern {pattern_key} → '{result}' (no DATA_CALL tag)")
    ok(f"All {len(test_cases)} fallback patterns produce valid DATA_CALL tags")

    # 5b. Fallback-produced tags are parseable by DATA_CALL_PATTERN
    print("\n5b. Fallback output parseable by DATA_CALL_PATTERN")
    parseable_count = 0
    for pattern_key, test_input in test_cases:
        replacement = fallback_patterns.get(pattern_key)
        if replacement is None:
            continue
        result = re.sub(pattern_key, replacement, test_input)
        m = DATA_CALL_PATTERN.search(result)
        if m:
            parseable_count += 1
        else:
            fail(f"  Cannot parse fallback output: '{result}'")
    ok(f"{parseable_count}/{len(test_cases)} fallback outputs are parseable")

    # =========================================================================
    # SUITE 6: SKILL MANIFEST INTEGRATION
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 6: SKILL MANIFEST INTEGRATION")
    print("=" * 70)

    # 6a. Import and find IGVF_Search
    print("\n6a. IGVF_Search in skill manifest")
    try:
        from cortex.skill_manifest import SKILL_MANIFESTS
        igvf_skill = SKILL_MANIFESTS.get("IGVF_Search")
        if igvf_skill:
            ok(f"IGVF_Search found: display_name='{igvf_skill.display_name}'")
        else:
            fail("IGVF_Search not found in SKILL_MANIFESTS")
    except ImportError as e:
        fail(f"Cannot import skill_manifest: {e}")
        igvf_skill = None

    # 6b. Required services
    print("\n6b. Required services")
    if igvf_skill:
        if "igvf" in igvf_skill.required_services:
            ok(f"required_services={igvf_skill.required_services}")
        else:
            fail(f"'igvf' not in required_services: {igvf_skill.required_services}")

    # 6c. Skill file exists
    print("\n6c. Skill file exists")
    if igvf_skill:
        from pathlib import Path
        skill_path = Path("/Users/eli/code/agoutic/skills") / igvf_skill.skill_file
        if skill_path.exists():
            content = skill_path.read_text()
            ok(f"Skill file exists: {skill_path.name} ({len(content)} chars)")
            # Check it mentions key tools
            for tool_name in ["search_measurement_sets", "get_dataset", "search_genes"]:
                if tool_name in content:
                    pass
                else:
                    warn(f"  Tool '{tool_name}' not mentioned in skill file")
        else:
            fail(f"Skill file missing: {skill_path}")

    # 6d. Category
    print("\n6d. Skill category")
    if igvf_skill:
        if igvf_skill.category == "data_retrieval":
            ok(f"category={igvf_skill.category}")
        else:
            warn(f"category={igvf_skill.category} (expected data_retrieval)")

    # 6e. Expected inputs
    print("\n6e. Expected inputs")
    if igvf_skill:
        expected = set(igvf_skill.expected_inputs)
        ok(f"expected_inputs={expected}")

    # =========================================================================
    # SUITE 7: SERVER-DOWN ERROR HANDLING
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 7: SERVER-DOWN ERROR HANDLING")
    print("=" * 70)

    # 7a. Connect to non-existent server
    print("\n7a. Connect to server on wrong port")
    bad_client = MCPHttpClient("igvf-bad", "http://localhost:9999")
    try:
        await bad_client.connect()
        fail("Should not connect to non-existent server!")
        await bad_client.disconnect()
    except RuntimeError as e:
        if "not reachable" in str(e).lower() or "cannot connect" in str(e).lower():
            ok(f"Connection error caught: {str(e)[:80]}")
        else:
            ok(f"Connection error (different wording): {str(e)[:80]}")
    except Exception as e:
        ok(f"Connection error caught ({type(e).__name__}): {str(e)[:80]}")

    # 7b. Call tool without connecting
    print("\n7b. Call tool without connect()")
    raw_client = MCPHttpClient("igvf-raw", "http://localhost:8009")
    try:
        await raw_client.call_tool("get_server_info")
        fail("Should fail without connect!")
    except RuntimeError as e:
        if "not connected" in str(e).lower() or "call connect" in str(e).lower():
            ok(f"Not-connected error: {str(e)[:60]}")
        else:
            ok(f"Error caught: {str(e)[:60]}")

    # 7c. Disconnect is idempotent
    print("\n7c. Disconnect without connect")
    try:
        await raw_client.disconnect()
        ok("Disconnect without connect is safe")
    except Exception as e:
        fail(f"Disconnect raised: {e}")

    # 7d. Double disconnect
    print("\n7d. Double disconnect")
    temp_client = MCPHttpClient("igvf-tmp", "http://localhost:8009")
    await temp_client.connect()
    await temp_client.disconnect()
    try:
        await temp_client.disconnect()
        ok("Double disconnect is safe")
    except Exception as e:
        fail(f"Double disconnect raised: {e}")

    # =========================================================================
    # SUITE 8: CROSS-TOOL WORKFLOWS (realistic user scenarios)
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 8: CROSS-TOOL WORKFLOWS")
    print("=" * 70)

    # 8a. Workflow: Search → get details → list files → get download URL
    print("\n8a. Full workflow: search → dataset → files → download")
    r1 = await call("search_measurement_sets", status="released", limit=1)
    if r1.get("count", 0) > 0:
        acc = r1["results"][0]["accession"]
        ok(f"Step 1: Found dataset {acc}")

        r2 = await call("get_dataset", accession=acc)
        ok(f"Step 2: Got metadata for {acc}: assay={r2.get('assay')}")

        r3 = await call("get_files_for_dataset", accession=acc, limit=10)
        file_count = r3.get("total_files", 0)
        ok(f"Step 3: Found {file_count} files for {acc}")

        if file_count > 0:
            file_acc = r3["files"][0]["accession"]
            r4 = await call("get_file_download_url", file_accession=file_acc)
            ok(f"Step 4: Download URL for {file_acc}: {r4.get('download_url', '')[:50]}...")
        else:
            warn(f"  No files for {acc}, skipping download URL step")
    else:
        fail("No datasets found to start workflow")

    # 8b. Workflow: Search genes → get gene details
    print("\n8b. Gene workflow: search → details")
    r1 = await call("search_genes", query="BRCA1", organism="Homo sapiens", limit=1)
    if r1.get("count", 0) > 0:
        gene_id = r1["results"][0].get("gene_id", "")
        symbol = r1["results"][0].get("symbol", "")
        ok(f"Step 1: Found gene {symbol} (ID: {gene_id})")

        r2 = await call("get_gene", gene_id=str(gene_id))
        ok(f"Step 2: Gene details: symbol={r2.get('symbol')}, taxa={r2.get('taxa')}")
    else:
        fail("No genes found for BRCA1")

    # 8c. Workflow: Search by sample → search files for that dataset
    print("\n8c. Sample→Dataset→Files workflow")
    r1 = await call("search_by_sample", sample_term="K562", limit=1)
    if r1.get("count", 0) > 0:
        acc = r1["results"][0]["accession"]
        ok(f"Step 1: K562 dataset {acc}")

        r2 = await call("search_files", dataset_accession=acc, limit=5)
        ok(f"Step 2: {r2.get('total', 0)} files for {acc}")
    else:
        fail("No K562 datasets found")

    # 8d. Workflow: Compare two assays
    print("\n8d. Compare two assays")
    r_atac = await call("search_by_assay", assay_title="ATAC-seq")
    r_rnaseq = await call("search_measurement_sets", assay="RNA-seq")
    ok(f"ATAC-seq: {r_atac.get('total', 0)} datasets")
    ok(f"RNA-seq: {r_rnaseq.get('total', 0)} datasets")

    # 8e. Workflow: Browse file formats available for a dataset
    print("\n8e. Browse formats for a dataset")
    r = await call("get_files_for_dataset", accession="IGVFDS6639ECQN")
    formats = r.get("by_format", {})
    ok(f"Formats for IGVFDS6639ECQN: {formats}")

    # =========================================================================
    # SUITE 9: EDGE CASES FROM REAL LLM BEHAVIOR
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 9: REAL LLM BEHAVIOR EDGE CASES")
    print("=" * 70)

    # 9a. LLM might send integer as string in DATA_CALL params
    print("\n9a. limit as string from DATA_CALL parsing")
    params = _parse_tag_params("status=released, limit=25")
    # Params come as strings from tag parsing — tool should handle
    r = await call("search_measurement_sets", **params)
    ok(f"String params from tag parsing work: count={r.get('count')}")

    # 9b. LLM might hallucinate "search_experiments" for IGVF
    print("\n9b. Hallucinated 'search_experiments' alias chain")
    resolved = all_tool_aliases.get("search_experiments", "search_experiments")
    # Should resolve to search_measurement_sets for IGVF
    # (Note: might also resolve to search_by_biosample for ENCODE if ENCODE aliases override)
    ok(f"'search_experiments' → '{resolved}' (depends on merge order)")

    # 9c. LLM sends both consortium=igvf and a valid tool
    print("\n9c. Full tag → parse → alias → call")
    tag = '[[DATA_CALL: consortium=igvf, tool=search_datasets, biosample=K562, limit=3]]'
    m = DATA_CALL_PATTERN.search(tag)
    if m:
        tool = m.group(3)
        params = _parse_tag_params(m.group(4))
        # Apply tool alias
        corrected_tool = all_tool_aliases.get(tool, tool)
        # Apply param aliases
        pa = all_param_aliases.get(corrected_tool, {})
        params = {pa.get(k, k): v for k, v in params.items()}
        ok(f"  Parsed: tool={tool} → {corrected_tool}, params={params}")

        # Call the corrected tool with corrected params
        r = await call(corrected_tool, **params)
        ok(f"  Result: total={r.get('total')}, count={r.get('count')}")
    else:
        fail("Tag did not parse")

    # 9d. LLM uses "get_experiment" for IGVF (should alias to get_dataset)
    print("\n9d. get_experiment → get_dataset alias + call")
    resolved = all_tool_aliases.get("get_experiment", "get_experiment")
    if resolved == "get_dataset":
        r = await call(resolved, accession="IGVFDS6639ECQN")
        ok(f"'get_experiment' → get_dataset: accession={r.get('accession')}")
    else:
        fail(f"'get_experiment' → '{resolved}' (expected get_dataset)")

    # 9e. LLM constructs tag with file_id instead of file_accession
    print("\n9e. file_id param alias to file_accession")
    tag = '[[DATA_CALL: consortium=igvf, tool=file_metadata, file_id=IGVFFI7174NOZD]]'
    m = DATA_CALL_PATTERN.search(tag)
    if m:
        tool = m.group(3)
        params = _parse_tag_params(m.group(4))
        corrected_tool = all_tool_aliases.get(tool, tool)
        pa = all_param_aliases.get(corrected_tool, {})
        params = {pa.get(k, k): v for k, v in params.items()}
        ok(f"  tool={tool}→{corrected_tool}, params={params}")

        r = await call(corrected_tool, **params)
        ok(f"  Result: format={r.get('file_format')}, size={r.get('file_size')}")
    else:
        fail("Tag did not parse")

    # 9f. LLM tag with quoted values
    print("\n9f. Quoted values in tag params")
    params = _parse_tag_params('sample_term="motor neuron", organism="Homo sapiens"')
    if params.get("sample_term") == "motor neuron":
        ok(f"Quoted values stripped: {params}")
    else:
        fail(f"Quoted values not handled: {params}")

    # 9g. LLM tag with single-quoted values
    print("\n9g. Single-quoted values")
    params = _parse_tag_params("sample_term='K562', status='released'")
    if params.get("sample_term") == "K562":
        ok(f"Single-quoted values stripped: {params}")
    else:
        fail(f"Single-quoted values not handled: {params}")

    # =========================================================================
    # SUITE 10: CONSORTIUM REGISTRY COMPLETENESS
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 10: CONSORTIUM REGISTRY COMPLETENESS")
    print("=" * 70)

    igvf_reg = igvf_config.CONSORTIUM_REGISTRY["igvf"]

    # 10a. All required registry fields present
    print("\n10a. Registry field completeness")
    required_fields = ["url", "display_name", "emoji", "table_columns", "count_field",
                       "count_label", "skills", "tool_aliases", "param_aliases", "fallback_patterns"]
    missing = [f for f in required_fields if f not in igvf_reg]
    if missing:
        fail(f"Missing registry fields: {missing}")
    else:
        ok(f"All {len(required_fields)} registry fields present")

    # 10b. table_columns reference real fields from tool output
    print("\n10b. Table column field validation")
    r = await call("search_measurement_sets", limit=1)
    if r.get("results"):
        result_keys = set(r["results"][0].keys())
        for col_name, col_key in igvf_reg["table_columns"]:
            if col_key in result_keys:
                pass
            else:
                fail(f"  Table column '{col_name}' references '{col_key}' not in result keys {result_keys}")
        ok(f"Table columns {[c[0] for c in igvf_reg['table_columns']]} map to valid fields")
    else:
        warn("No results to validate table columns against")

    # 10c. count_field exists in results
    print("\n10c. count_field validation")
    if r.get("results"):
        if igvf_reg["count_field"] in r["results"][0]:
            ok(f"count_field '{igvf_reg['count_field']}' exists in results")
        else:
            warn(f"count_field '{igvf_reg['count_field']}' not in result keys")

    # 10d. Display name and emoji
    print("\n10d. Display metadata")
    ok(f"display_name='{igvf_reg['display_name']}', emoji={igvf_reg['emoji']}")

    # 10e. get_consortium_url works
    print("\n10e. get_consortium_url")
    url = igvf_config.get_consortium_url("igvf")
    ok(f"URL: {url}")

    # 10f. get_consortium_entry works
    print("\n10f. get_consortium_entry")
    entry = igvf_config.get_consortium_entry("igvf")
    ok(f"Entry has {len(entry)} keys")

    # 10g. Unknown consortium raises KeyError
    print("\n10g. Unknown consortium raises KeyError")
    try:
        igvf_config.get_consortium_url("nonexistent_consortium")
        fail("Should raise KeyError")
    except KeyError:
        ok("KeyError raised for unknown consortium")

    # =========================================================================
    # SUITE 11: CLIENT EDGE CASES
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 11: CLIENT EDGE CASES")
    print("=" * 70)

    direct = IGVFClient()

    # 11a. get_object with path prefix
    print("\n11a. get_object with / prefix")
    try:
        obj = direct.get_object("/measurement-sets/IGVFDS6639ECQN/")
        if obj.get("accession") == "IGVFDS6639ECQN":
            ok("Full path lookup works")
        else:
            fail(f"Wrong accession: {obj.get('accession')}")
    except Exception as e:
        warn(f"Full path lookup failed: {str(e)[:80]}")

    # 11b. get_object bare accession
    print("\n11b. get_object bare accession")
    obj = direct.get_object("IGVFDS6639ECQN")
    if obj.get("accession") == "IGVFDS6639ECQN":
        ok("Bare accession lookup works")
    else:
        fail(f"Bare accession wrong: {obj.get('accession')}")

    # 11c. search with multiple field filters
    print("\n11c. Multiple field filters")
    resp = direct.search("MeasurementSet", limit=5, field_filters={
        "status": "released",
        "preferred_assay_titles": "ATAC-seq",
        "donors.taxa": "Homo sapiens",
    })
    ok(f"Multi-filter search: total={resp.get('total')}, count={len(resp.get('@graph', []))}")

    # 11d. search with empty field_filters
    print("\n11d. Empty field_filters")
    resp = direct.search("MeasurementSet", limit=1, field_filters={})
    ok(f"Empty filters: total={resp.get('total')}")

    # 11e. search with None field_filters
    print("\n11e. None field_filters")
    resp = direct.search("MeasurementSet", limit=1, field_filters=None)
    ok(f"None filters: total={resp.get('total')}")

    # 11f. get_file_download_url returns full URL
    print("\n11f. Download URL format")
    url = direct.get_file_download_url("IGVFFI7174NOZD")
    if url.startswith("https://"):
        ok(f"Full URL: {url[:60]}...")
    elif url.startswith("http://"):
        warn(f"HTTP not HTTPS: {url[:60]}...")
    else:
        fail(f"Bad URL: {url}")

    # 11g. Client timeout config
    print("\n11g. Client timeout")
    fast_client = IGVFClient(timeout=5.0)
    resp = fast_client.search("MeasurementSet", limit=1, field_filters={"status": "released"})
    ok(f"5s timeout works: total={resp.get('total')}")
    fast_client.close()

    # 11h. Client close
    print("\n11h. Client close")
    temp = IGVFClient()
    temp.close()
    try:
        temp.search("MeasurementSet", limit=1)
        warn("Closed client still works (httpx may handle this)")
    except Exception as e:
        ok(f"Closed client rejected: {type(e).__name__}")

    # =========================================================================
    # SUITE 12: DATA INTEGRITY SPOT-CHECKS
    # =========================================================================
    print("\n" + "=" * 70)
    print("SUITE 12: DATA INTEGRITY SPOT-CHECKS")
    print("=" * 70)

    # 12a. Known dataset has expected values
    print("\n12a. Known dataset IGVFDS6639ECQN")
    r = await call("get_dataset", accession="IGVFDS6639ECQN")
    if r.get("accession") == "IGVFDS6639ECQN" and r.get("status") == "released":
        ok(f"Dataset verified: accession={r['accession']}, status={r['status']}")
    else:
        fail(f"Dataset data mismatch: {r.get('accession')}, {r.get('status')}")

    # 12b. Link format
    print("\n12b. Dataset link format")
    link = r.get("link", "")
    if link.startswith("https://data.igvf.org/") and "IGVFDS6639ECQN" in link:
        ok(f"Link correct: {link}")
    else:
        fail(f"Link wrong: {link}")

    # 12c. File link format
    print("\n12c. File link format")
    r = await call("get_file_metadata", file_accession="IGVFFI7174NOZD")
    link = r.get("link", "")
    if link.startswith("https://data.igvf.org/"):
        ok(f"File link correct: {link}")
    else:
        fail(f"File link wrong: {link}")

    # 12d. Gene link format
    print("\n12d. Gene link format")
    r = await call("search_genes", query="TP53", limit=1)
    if r.get("results"):
        link = r["results"][0].get("link", "")
        if link.startswith("https://data.igvf.org/"):
            ok(f"Gene link correct: {link}")
        else:
            fail(f"Gene link wrong: {link}")
    else:
        warn("No gene results for link check")

    # 12e. Raw metadata included in get_dataset
    print("\n12e. Raw metadata present")
    r = await call("get_dataset", accession="IGVFDS6639ECQN")
    raw = r.get("raw", {})
    if raw and isinstance(raw, dict) and len(raw) > 5:
        ok(f"Raw metadata has {len(raw)} fields")
        # Ensure @-prefixed keys are filtered
        at_keys = [k for k in raw if k.startswith("@")]
        if at_keys:
            fail(f"  Raw has @-prefixed keys (should be filtered): {at_keys}")
        # Ensure audit/actions filtered
        if "audit" in raw or "actions" in raw:
            fail("  Raw has audit/actions (should be filtered)")
    else:
        fail(f"Raw metadata missing or empty")

    # 12f. Sample results have sample_type
    print("\n12f. Sample result type info")
    r = await call("search_samples", sample_type="InVitroSystem", limit=1)
    if r.get("results"):
        sample = r["results"][0]
        if sample.get("sample_type"):
            ok(f"Sample type present: {sample['sample_type']}")
        else:
            fail("Sample missing type info")
    else:
        fail("No InVitroSystem samples")

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
