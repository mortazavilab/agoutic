"""
FastAPI REST API for Server4 (Analysis Server).
Provides HTTP endpoints for file discovery, reading, and parsing.
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import Optional

from common.logging_config import setup_logging, get_logger
from common.logging_middleware import RequestLoggingMiddleware
from server4.config import SERVER4_HOST, SERVER4_PORT
from server4.analysis_engine import (
    discover_files,
    categorize_files,
    read_file_content,
    parse_csv_file,
    parse_bed_file,
    generate_analysis_summary,
    get_job_work_dir
)
from server4.schemas import (
    FileListing,
    FileContentRequest,
    FileContentResponse,
    ParsedTableData,
    ParsedBedData,
    JobFileSummary,
    AnalysisSummary,
    ErrorResponse
)

# Configure logging
setup_logging("server4-rest")
logger = get_logger(__name__)

# Create FastAPI app
app = FastAPI(
    title="AGOUTIC Server4 - Analysis Server",
    description="File discovery, parsing, and analysis for Dogme job results",
    version="0.1.0"
)

# Add request logging middleware FIRST (outermost)
app.add_middleware(RequestLoggingMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Health Check ====================

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "AGOUTIC Server4 - Analysis Server",
        "version": "0.1.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


# ==================== File Discovery Endpoints ====================

@app.get("/analysis/jobs/{run_uuid}/files", response_model=FileListing)
async def list_files(
    run_uuid: str,
    extensions: Optional[str] = Query(None, description="Comma-separated extensions (e.g., .txt,.csv)")
):
    """
    List all files in a job's work directory.
    
    Args:
        run_uuid: Job UUID
        extensions: Optional extension filter
    
    Returns:
        FileListing with all discovered files
    """
    try:
        # Parse extensions
        ext_list = None
        if extensions:
            ext_list = [ext.strip() for ext in extensions.split(',')]
        
        # Discover files
        file_listing = discover_files(run_uuid, ext_list)
        return file_listing
    
    except FileNotFoundError as e:
        logger.error(f"Job not found: {run_uuid}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error listing files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analysis/jobs/{run_uuid}/files/categorize", response_model=JobFileSummary)
async def categorize_files_endpoint(run_uuid: str):
    """
    Categorize files by type (txt, csv, bed, other).
    
    Args:
        run_uuid: Job UUID
    
    Returns:
        JobFileSummary with categorized file lists
    """
    try:
        file_summary = categorize_files(run_uuid)
        return file_summary
    
    except FileNotFoundError as e:
        logger.error(f"Job not found: {run_uuid}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error categorizing files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== File Content Endpoints ====================

@app.get("/analysis/files/content", response_model=FileContentResponse)
async def get_file_content(
    run_uuid: str = Query(..., description="Job UUID"),
    file_path: str = Query(..., description="Relative file path"),
    preview_lines: Optional[int] = Query(None, description="Line limit for preview")
):
    """
    Read content from a file.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to file
        preview_lines: Optional line limit
    
    Returns:
        FileContentResponse with file content
    """
    try:
        content_response = read_file_content(run_uuid, file_path, preview_lines)
        return content_response
    
    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}")
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        logger.error(f"Invalid file or path: {file_path}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error reading file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analysis/files/download")
async def download_file(
    run_uuid: str = Query(..., description="Job UUID"),
    file_path: str = Query(..., description="Relative file path")
):
    """
    Download a file from a job's work directory.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to file
    
    Returns:
        FileResponse for download
    """
    try:
        work_dir = get_job_work_dir(run_uuid)
        if not work_dir:
            raise HTTPException(status_code=404, detail=f"Job not found: {run_uuid}")
        
        full_path = (work_dir / file_path).resolve()
        
        # Security check
        if not str(full_path).startswith(str(work_dir.resolve())):
            raise HTTPException(status_code=400, detail="Invalid file path")
        
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        return FileResponse(
            path=full_path,
            filename=full_path.name,
            media_type="application/octet-stream"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Parsing Endpoints ====================

@app.get("/analysis/files/parse/csv", response_model=ParsedTableData)
async def parse_csv_endpoint(
    run_uuid: str = Query(..., description="Job UUID"),
    file_path: str = Query(..., description="Relative file path to CSV/TSV"),
    max_rows: Optional[int] = Query(100, description="Maximum rows to return")
):
    """
    Parse a CSV/TSV file.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to CSV/TSV file
        max_rows: Maximum rows to return
    
    Returns:
        ParsedTableData with structured data
    """
    try:
        parsed_data = parse_csv_file(run_uuid, file_path, max_rows)
        return parsed_data
    
    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}")
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        logger.error(f"Invalid CSV file: {file_path}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error parsing CSV: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analysis/files/parse/bed", response_model=ParsedBedData)
async def parse_bed_endpoint(
    run_uuid: str = Query(..., description="Job UUID"),
    file_path: str = Query(..., description="Relative file path to BED"),
    max_records: Optional[int] = Query(100, description="Maximum records to return")
):
    """
    Parse a BED format file.
    
    Args:
        run_uuid: Job UUID
        file_path: Relative path to BED file
        max_records: Maximum records to return
    
    Returns:
        ParsedBedData with structured records
    """
    try:
        parsed_data = parse_bed_file(run_uuid, file_path, max_records)
        return parsed_data
    
    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}")
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        logger.error(f"Invalid BED file: {file_path}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error parsing BED: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Analysis Summary Endpoint ====================

@app.get("/analysis/summary/{run_uuid}", response_model=AnalysisSummary)
async def get_summary(run_uuid: str):
    """
    Get comprehensive analysis summary for a job.
    
    Args:
        run_uuid: Job UUID
    
    Returns:
        AnalysisSummary with complete analysis
    """
    try:
        summary = generate_analysis_summary(run_uuid)
        return summary
    
    except ValueError as e:
        logger.error(f"Job not found: {run_uuid}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Server Entry Point ====================

if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting Server4 REST API on {SERVER4_HOST}:{SERVER4_PORT}")
    uvicorn.run(
        app,
        host=SERVER4_HOST,
        port=SERVER4_PORT,
        log_level="info"
    )
