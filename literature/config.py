"""Runtime configuration for the literature MCP service."""
from __future__ import annotations

import os

LITERATURE_MCP_PORT = int(os.getenv("LITERATURE_MCP_PORT", "8010"))
NCBI_EUTILS_URL = os.getenv(
    "NCBI_EUTILS_URL", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
).rstrip("/")
NCBI_PMC_OA_URL = os.getenv(
    "NCBI_PMC_OA_URL", "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
)
NCBI_TIMEOUT_SECONDS = float(os.getenv("NCBI_TIMEOUT_SECONDS", "20"))
NCBI_PUBLIC_REQUESTS_PER_SECOND = float(
    os.getenv("NCBI_PUBLIC_REQUESTS_PER_SECOND", "3")
)
NCBI_API_KEY_REQUESTS_PER_SECOND = float(
    os.getenv("NCBI_API_KEY_REQUESTS_PER_SECOND", "10")
)
DEFAULT_RESULT_COUNT = 10
MAX_RESULT_COUNT = 20
MAX_QUERY_LENGTH = 500
