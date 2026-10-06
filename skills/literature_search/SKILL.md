# PubMed Literature Search

Use this skill for scientific literature requests, especially genomics, genetics, and gene-function topics.

Issue exactly one call for a new search:

```text
[[DATA_CALL: service=literature, tool=search_literature, query=<user topic>]]
```

The service returns ten papers by default and does not restrict publication type or study design unless the user explicitly asks. Preserve broad retrieval for basic, functional, and translational research. When a user requests a specific filter, pass the corresponding optional argument: `year_from`, `year_to`, `organism`, `article_type`, `study_design`, `open_access_only`, `sort_by` (`relevance` or `recency`), and `result_count`.

Search coverage should prioritize functional and comparative biology, systems genetics, population genetics/genomics, and human–mouse studies where relevant. The organism filter supports `human and mouse` when both species are requested. Optional article-type filters include broad journal/comparative articles as well as clinical trials, reviews, meta-analyses, and case reports. Optional study-design filters include functional studies/genomics, comparative studies/genomics, systems genetics, population genetics/genomics, GWAS, QTL, single-cell, animal, and population/clinical designs. These are convenience filters, not a complete taxonomy; do not apply them unless requested, and pass domain-specific study concepts in the search query.

It ranks relevance first by default, then uses recency and research-design cues. Cite the returned PubMed link for every paper, include the PMC link when supplied, and state whether each summary comes from **full text**, an **abstract**, or metadata only. Use structured methods/results/conclusion/limitations only when explicitly available in labeled evidence, and do not invent findings for metadata-only papers. Chat renders a compact comparison table plus paper-level citations and evidence.

Pass the scientific topic, not request boilerplate such as “find me papers on.” The service normalizes common species and tissue variants, preserves comparative intent, searches a larger candidate set, and ranks candidates against the topic before returning the requested number of papers.

Use this skill for papers and scientific evidence, not for ENCODE or IGVF dataset discovery. Ask the user to refine a broad topic only when the result set cannot answer their question.
