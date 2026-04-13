"""
MCP Server for Analyzer (Analysis Server).
Exposes analysis tools via Model Context Protocol.
Supports both stdio and HTTP transports.
"""

import asyncio
import json
import sys
from typing import Any, Dict

from fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

from common.logging_config import setup_logging, get_logger
from analyzer.mcp_tools import TOOL_REGISTRY, TOOL_SCHEMAS
from analyzer.config import ANALYZER_MCP_PORT

# Configure logging
setup_logging("analyzer-mcp")
logger = get_logger(__name__)

# Create FastMCP server instance
mcp = FastMCP("AGOUTIC-Analyzer", version="0.4.0")


# --- Schema endpoint (fetched by Cortex at startup) ---
async def _tools_schema_endpoint(request):
    """Return machine-readable tool schemas for all Analyzer MCP tools."""
    return JSONResponse(TOOL_SCHEMAS)


# ==================== Register tools from TOOL_REGISTRY ====================

@mcp.tool()
async def list_job_files(work_dir: str | None = None, run_uuid: str | None = None, extensions: str | None = None, max_depth: int | None = None, name_pattern: str | None = None, allow_missing: bool = False) -> Dict[str, Any]:
    """List all files in a workflow directory with optional extension filtering."""
    return await TOOL_REGISTRY["list_job_files"](work_dir=work_dir, run_uuid=run_uuid, extensions=extensions, max_depth=max_depth, name_pattern=name_pattern, allow_missing=allow_missing)

@mcp.tool()
async def find_file(file_name: str, work_dir: str | None = None, run_uuid: str | None = None) -> Dict[str, Any]:
    """Find a specific file by name (exact or partial match) and return its relative path."""
    return await TOOL_REGISTRY["find_file"](file_name=file_name, work_dir=work_dir, run_uuid=run_uuid)

@mcp.tool()
async def read_file_content(file_path: str, work_dir: str | None = None, run_uuid: str | None = None, preview_lines: int | None = None) -> Dict[str, Any]:
    """Read content from a specific file in a workflow directory."""
    return await TOOL_REGISTRY["read_file_content"](file_path=file_path, work_dir=work_dir, run_uuid=run_uuid, preview_lines=preview_lines)

@mcp.tool()
async def parse_csv_file(file_path: str, work_dir: str | None = None, run_uuid: str | None = None, max_rows: int | None = 100) -> Dict[str, Any]:
    """Parse a CSV or TSV file and return structured tabular data."""
    return await TOOL_REGISTRY["parse_csv_file"](file_path=file_path, work_dir=work_dir, run_uuid=run_uuid, max_rows=max_rows)

@mcp.tool()
async def parse_bed_file(file_path: str, work_dir: str | None = None, run_uuid: str | None = None, max_records: int | None = 100) -> Dict[str, Any]:
    """Parse a BED format file and return structured genomic records."""
    return await TOOL_REGISTRY["parse_bed_file"](file_path=file_path, work_dir=work_dir, run_uuid=run_uuid, max_records=max_records)

@mcp.tool()
async def parse_xgenepy_outputs(output_dir: str, work_dir: str | None = None, run_uuid: str | None = None, max_rows: int | None = 200) -> Dict[str, Any]:
    """Parse canonical XgenePy output artifacts and return structured result payloads."""
    return await TOOL_REGISTRY["parse_xgenepy_outputs"](output_dir=output_dir, work_dir=work_dir, run_uuid=run_uuid, max_rows=max_rows)

@mcp.tool()
async def get_analysis_summary(run_uuid: str | None = None, work_dir: str | None = None) -> Dict[str, Any]:
    """Get comprehensive analysis summary for a completed job including file categorization and parsed reports."""
    return await TOOL_REGISTRY["get_analysis_summary"](run_uuid=run_uuid, work_dir=work_dir)

@mcp.tool()
async def categorize_job_files(work_dir: str | None = None, run_uuid: str | None = None) -> Dict[str, Any]:
    """Categorize all files in a workflow directory by type (txt, csv, bed, other)."""
    return await TOOL_REGISTRY["categorize_job_files"](work_dir=work_dir, run_uuid=run_uuid)


# ==================== Gene Annotation Tools ====================

@mcp.tool()
def translate_gene_ids(gene_ids: list | None = None, transcript_ids: list | None = None, organism: str | None = None) -> str:
    """Translate Ensembl gene or transcript IDs to gene symbols."""
    return TOOL_REGISTRY["translate_gene_ids"](gene_ids=gene_ids, transcript_ids=transcript_ids, organism=organism)

@mcp.tool()
def build_gene_cache(gtf_path: str, organism: str | None = None) -> str:
    """Build colocated gene/transcript cache TSVs for a specific GTF."""
    return TOOL_REGISTRY["build_gene_cache"](gtf_path=gtf_path, organism=organism)

@mcp.tool()
def lookup_gene(gene_symbols: list | None = None, gene_ids: list | None = None, transcript_ids: list | None = None, organism: str | None = None) -> str:
    """Look up genes or transcripts by symbol or Ensembl ID."""
    return TOOL_REGISTRY["lookup_gene"](gene_symbols=gene_symbols, gene_ids=gene_ids, transcript_ids=transcript_ids, organism=organism)


# ==================== Enrichment Analysis Tools ====================

@mcp.tool()
def run_go_enrichment(gene_list: str, species: str = "auto", sources: str = "GO:BP,GO:MF,GO:CC", name: str | None = None, conversation_id: str = "default") -> str:
    """Run Gene Ontology enrichment analysis on a gene list."""
    return TOOL_REGISTRY["run_go_enrichment"](gene_list=gene_list, species=species, sources=sources, name=name, conversation_id=conversation_id)

@mcp.tool()
def run_pathway_enrichment(gene_list: str, species: str = "auto", database: str = "KEGG", name: str | None = None, conversation_id: str = "default") -> str:
    """Run pathway enrichment analysis (KEGG or Reactome) on a gene list."""
    return TOOL_REGISTRY["run_pathway_enrichment"](gene_list=gene_list, species=species, database=database, name=name, conversation_id=conversation_id)

@mcp.tool()
def get_enrichment_results(name: str | None = None, max_terms: int = 30, pvalue_threshold: float = 0.05, source_filter: str | None = None, conversation_id: str = "default") -> str:
    """Retrieve stored enrichment results with optional filtering."""
    return TOOL_REGISTRY["get_enrichment_results"](name=name, max_terms=max_terms, pvalue_threshold=pvalue_threshold, source_filter=source_filter, conversation_id=conversation_id)

@mcp.tool()
def get_term_genes(term_id: str, name: str | None = None, conversation_id: str = "default") -> str:
    """Show genes contributing to a specific GO term or pathway."""
    return TOOL_REGISTRY["get_term_genes"](term_id=term_id, name=name, conversation_id=conversation_id)


# ==================== Server Entry Points ====================

async def main_stdio():
    """Run the MCP server in stdio mode (for subprocess spawning)."""
    logger.info("Starting Analyzer MCP Server (stdio mode)")
    logger.info(f"Registered tools: {list(TOOL_REGISTRY.keys())}")
    await mcp.run_async(transport="stdio")


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="AGOUTIC Analyzer MCP Server")
    parser.add_argument(
        "--mode",
        choices=["http", "stdio"],
        default="http",
        help="Transport mode: 'http' (default) or 'stdio'",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0 — all interfaces)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=ANALYZER_MCP_PORT,
        help=f"Port to bind to (default: {ANALYZER_MCP_PORT})",
    )

    args = parser.parse_args()

    if args.mode == "stdio":
        asyncio.run(main_stdio())
    else:
        logger.info("Analyzer MCP server starting", host=args.host, port=args.port,
                    tools=list(TOOL_REGISTRY.keys()))

        app = mcp.http_app()
        app.routes.insert(0, Route("/tools/schema", _tools_schema_endpoint, methods=["GET"]))
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
