# Enrichment Analysis

GO & pathway enrichment analysis for gene lists derived from DE results or provided directly.

## Scope & Routing

**This Skill Handles:**
- Gene Ontology enrichment (Biological Process, Molecular Function, Cellular Component)
- KEGG pathway enrichment
- Reactome pathway enrichment
- Filtering DE results to extract gene lists (by FDR, logFC, direction)
- Enrichment bar plots and dot/bubble plots
- Interpreting and summarizing enrichment results
- Term-to-gene drilldown (which genes drive a specific term)

**This Skill Does NOT Handle (route to the correct skill):**
- **Running differential expression** → `[[SKILL_SWITCH_TO: differential_expression]]`
- **Running pipelines / workflows** → `[[SKILL_SWITCH_TO: run_dogme_rna]]`
- **ENCODE searches** → `[[SKILL_SWITCH_TO: encode_search]]`
- **Downloading files** → `[[SKILL_SWITCH_TO: download_files]]`
- **Analyzing job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`

## Inputs

Enrichment analysis requires **one of**:
1. **DE results** already in the pipeline (from a prior `test_contrast()` call) — use `filter_de_genes` in edgePython to extract the gene list first.
2. **An explicit gene list** — comma-separated gene symbols or Ensembl IDs.

Species is auto-detected from gene ID prefixes (ENSG → human, ENSMUSG → mouse).

## Plan Logic

### Step 1: Filter DE genes (if starting from DE results)
```
[[DATA_CALL:service:edgepython:filter_de_genes:result_name={name},significance_metric=fdr,significance_threshold=0.05,logfc_threshold=0.0,direction=all]]
```

### Step 2: Run GO enrichment
```
[[DATA_CALL:service:analyzer:run_go_enrichment:gene_list={genes},sources=GO:BP,GO:MF,GO:CC]]
```

### Step 3: Run pathway enrichment (KEGG)
```
[[DATA_CALL:service:analyzer:run_pathway_enrichment:gene_list={genes},database=KEGG]]
```

### Step 4: Run pathway enrichment (Reactome)
```
[[DATA_CALL:service:analyzer:run_pathway_enrichment:gene_list={genes},database=REAC]]
```

### Step 5: Plot enrichment results
```
[[DATA_CALL:service:edgepython:generate_plot:plot_type=enrichment_bar]]
[[DATA_CALL:service:edgepython:generate_plot:plot_type=enrichment_dot]]
```

### Step 6: View term details
```
[[DATA_CALL:service:analyzer:get_term_genes:term_id=GO:0006915]]
```

## Plan Chains

### go_enrichment_from_de
**Triggers:** "run GO enrichment", "GO analysis on .* genes", "what GO terms are enriched"
**Steps:**
1. FILTER_DE_GENES — Filter significant genes from DE results
2. RUN_GO_ENRICHMENT — Run GO enrichment (BP/MF/CC) with extracted gene list
3. PLOT_ENRICHMENT — Generate enrichment bar plot
4. SUMMARIZE_ENRICHMENT — Interpret biological significance

### pathway_enrichment_from_de
**Triggers:** "run pathway enrichment", "KEGG analysis", "Reactome enrichment", "what pathways"
**Steps:**
1. FILTER_DE_GENES — Filter significant genes from DE results
2. RUN_PATHWAY_ENRICHMENT — Run KEGG/Reactome enrichment with extracted gene list
3. PLOT_ENRICHMENT — Generate enrichment bar plot
4. SUMMARIZE_ENRICHMENT — Interpret pathway results

### full_enrichment
**Triggers:** "full enrichment analysis", "GO and pathway", "complete enrichment"
**Steps:**
1. FILTER_DE_GENES — Filter significant genes
2. RUN_GO_ENRICHMENT — GO enrichment (BP/MF/CC)
3. RUN_PATHWAY_ENRICHMENT — KEGG pathway enrichment
4. PLOT_ENRICHMENT — Enrichment bar plot
5. SUMMARIZE_ENRICHMENT — Biological interpretation and summary

## Important Rules

1. DE results must exist before running enrichment from DE (guide user to run DE first if none exist)
2. Always generate at least one enrichment plot after running enrichment
3. Always provide biological interpretation of enrichment results
4. When the user asks for "up-regulated" or "down-regulated" enrichment, set `direction` accordingly in filter_de_genes
5. For comprehensive analysis, run both GO and pathway enrichment separately for up and down genes
6. If the user provides an explicit gene list, skip the FILTER_DE_GENES step and pass the gene list directly to run_go_enrichment/run_pathway_enrichment
7. Default to FDR < 0.05 for filtering unless the user specifies otherwise
8. After filter_de_genes returns gene names, pass them as a comma-separated `gene_list` parameter to run_go_enrichment or run_pathway_enrichment
