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
                "user_id": {
                    "type": "string",
                    "description": "Authenticated caller ID injected by Cortex for optional NCBI credentials.",
                },
            },
            "required": ["query"],
        },
    },
}
