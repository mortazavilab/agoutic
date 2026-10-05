# PubMed Literature Search

Use this skill for scientific literature requests, especially genomics, genetics, and gene-function topics.

Issue exactly one call for a new search:

```text
[[DATA_CALL: service=literature, tool=search_literature, query=<user topic>]]
```

The service returns ten papers by default. It ranks relevance first, then recency and study-design cues. Cite the returned PubMed link for every paper, include the PMC link when supplied, and state whether each summary comes from **full text**, an **abstract**, or metadata only. Do not invent findings for metadata-only papers.

Use this skill for papers and scientific evidence, not for ENCODE or IGVF dataset discovery. Ask the user to refine a broad topic only when the result set cannot answer their question.
