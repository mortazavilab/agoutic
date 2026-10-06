"""Machine-readable contracts discovered by Cortex."""

TOOL_SCHEMAS = {
    "search_literature": {
        "description": (
            "Search PubMed and PubMed Central for scientific literature. "
            "Returns relevance-ranked, evidence-attributed records with PubMed/PMC links."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Scientific topic or PubMed-style search query.",
                },
                "result_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Number of ranked papers to return; defaults to 10.",
                },
                "year_from": {"type": "integer", "minimum": 1800, "maximum": 2100},
                "year_to": {"type": "integer", "minimum": 1800, "maximum": 2100},
                "organism": {
                    "type": "string",
                    "description": "Optional organism restriction; supports human, mouse, human and mouse, rat, and zebrafish.",
                },
                "article_type": {
                    "type": "string",
                    "enum": [
                        "journal article",
                        "comparative study",
                        "clinical trial",
                        "randomized controlled trial",
                        "review",
                        "systematic review",
                        "meta-analysis",
                        "case report",
                    ],
                },
                "study_design": {
                    "type": "string",
                    "enum": [
                        "functional study",
                        "functional genomics",
                        "comparative study",
                        "comparative genomics",
                        "systems genetics",
                        "population genetics",
                        "population genomics",
                        "genome-wide association study",
                        "quantitative trait loci study",
                        "cohort study",
                        "case-control study",
                        "cross-sectional study",
                        "single-cell study",
                        "animal study",
                    ],
                },
                "open_access_only": {
                    "type": "boolean",
                    "description": "Restrict to records with PMC open full text.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "recency"],
                    "description": "Order by topic relevance or publication date.",
                },
                "user_id": {
                    "type": "string",
                    "description": "Authenticated caller ID injected by Cortex for optional NCBI credentials.",
                },
            },
            "required": ["query"],
        },
    },
}
