# Skill: Enrichment Analysis (`enrichment_analysis`)

## Description

This skill performs Gene Ontology (GO) and pathway enrichment analysis on gene lists, typically derived from differential expression results. It supports GO Biological Process, Molecular Function, and Cellular Component analysis, as well as KEGG and Reactome pathway enrichment, using the g:Profiler web service via edgePython.

## Skill Scope & Routing

### This Skill Handles:
- Filtering DE results to extract significant gene lists (by FDR, logFC, direction)
- GO enrichment analysis (BP, MF, CC)
- KEGG pathway enrichment
- Reactome pathway enrichment
- Enrichment result visualization (bar plots, dot plots)
- Exploring genes in specific GO terms or pathways
- Interpreting enrichment results biologically

**Example questions:**
- "Run GO enrichment on the upregulated genes"
- "What pathways are enriched in my DE results?"
- "Do KEGG pathway analysis on the significant genes"
- "Show me the enriched biological processes"
- "What genes contribute to the apoptosis GO term?"
- "Run enrichment analysis on these genes: TP53, BRCA1, MDM2"
- "Plot the enrichment results"
- "Run GO on downregulated genes only"
- "Do Reactome enrichment"
- "Compare GO enrichment for up vs down genes"

### This Skill Does NOT Handle:
- **Running DE analysis** (load data, normalize, fit, test) -> `[[SKILL_SWITCH_TO: differential_expression]]`
- **Running pipelines / alignment** -> `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE data search** -> `[[SKILL_SWITCH_TO: ENCODE_Search]]`
- **Downloading files** -> `[[SKILL_SWITCH_TO: download_files]]`
- **Checking pipeline job results** -> `[[SKILL_SWITCH_TO: analyze_job_results]]`

## Inputs

Two modes of operation:

**Mode 1: From DE results** (most common)
- A DE test must have been completed in the current session (test_contrast, exact_test, etc.)
- The tool reads directly from the DE pipeline state

**Mode 2: Explicit gene list**
- Provide comma-separated gene symbols or Ensembl IDs
- No prior DE analysis needed

## Plan Logic

### Standard Enrichment Workflow (from DE results)

#### Step 1: Filter DE Genes

Filter to extract the significant gene set. Specify direction to separate up/down.

```
[[DATA_CALL: service=edgepython, tool=filter_de_genes, fdr_threshold=0.05, logfc_threshold=1.0, direction=all]]
```

For upregulated only:
```
[[DATA_CALL: service=edgepython, tool=filter_de_genes, direction=up]]
```

#### Step 2: Run GO Enrichment

Run GO enrichment on the filtered genes:

```
[[DATA_CALL: service=edgepython, tool=run_go_enrichment, direction=up, name=go_up]]
```

For downregulated genes separately:
```
[[DATA_CALL: service=edgepython, tool=run_go_enrichment, direction=down, name=go_down]]
```

For all significant genes:
```
[[DATA_CALL: service=edgepython, tool=run_go_enrichment]]
```

#### Step 3: Run Pathway Enrichment (optional but recommended)

```
[[DATA_CALL: service=edgepython, tool=run_pathway_enrichment, database=KEGG]]
```

For Reactome:
```
[[DATA_CALL: service=edgepython, tool=run_pathway_enrichment, database=REAC]]
```

#### Step 4: Plot Results

```
[[DATA_CALL: service=edgepython, tool=generate_plot, plot_type=enrichment_bar]]
[[DATA_CALL: service=edgepython, tool=generate_plot, plot_type=enrichment_dot]]
```

#### Step 5: Explore Specific Terms

```
[[DATA_CALL: service=edgepython, tool=get_term_genes, term_id=GO:0006915]]
```

#### Step 6: Get Full Results Table

```
[[DATA_CALL: service=edgepython, tool=get_enrichment_results, source_filter=GO:BP, max_terms=30]]
```

### From Explicit Gene List

When the user provides genes directly:

```
[[DATA_CALL: service=edgepython, tool=run_go_enrichment, gene_list=TP53,BRCA1,MDM2,ATM,CHEK2, species=Hs]]
[[DATA_CALL: service=edgepython, tool=run_pathway_enrichment, gene_list=TP53,BRCA1,MDM2,ATM,CHEK2, database=KEGG]]
```

### Comparing Up vs Down Enrichment

When the user asks to compare enrichment between up and downregulated genes:

```
[[DATA_CALL: service=edgepython, tool=run_go_enrichment, direction=up, name=go_up]]
[[DATA_CALL: service=edgepython, tool=run_go_enrichment, direction=down, name=go_down]]
[[DATA_CALL: service=edgepython, tool=generate_plot, plot_type=enrichment_bar, result_name=go_up]]
[[DATA_CALL: service=edgepython, tool=generate_plot, plot_type=enrichment_bar, result_name=go_down]]
```

## Important Rules

1. **DE results must exist before enrichment from DE** — the enrichment tools read from the DE pipeline state. If no DE results exist, prompt the user to run DE first or provide an explicit gene list.
2. **Emit ALL DATA_CALLs in a single response** — do not pause between steps.
3. **Always run separate enrichments for up and down genes** when the user asks for directional analysis or when the user asks "what pathways are enriched?" without specifying direction — run both directions.
4. **Always generate at least one enrichment plot** (enrichment_bar or enrichment_dot) after running enrichment.
5. **Interpret the biological meaning** of the top enriched terms after plotting. Identify dominant biological themes: immune, neuronal, cell-cycle, chromatin, metabolic, stress, etc.
6. When the user says "proceed", "go ahead", or similar — emit all remaining DATA_CALLs.
7. Species is auto-detected from gene IDs. Only specify manually if auto-detection fails.

## Plan Chains

### go_enrichment_from_de
- description: Filter DE genes and run GO enrichment analysis
- trigger: GO|gene ontology|enrichment|biological process|molecular function|cellular component + run|do|perform|analyze|analysis
- steps:
  1. FILTER_DE_GENES: Filter significant DE genes
  2. RUN_GO_ENRICHMENT: Run GO enrichment on filtered genes
  3. PLOT_ENRICHMENT: Generate enrichment bar plot
  4. SUMMARIZE_ENRICHMENT: Interpret enrichment results
- auto_approve: true

### pathway_enrichment_from_de
- description: Filter DE genes and run pathway enrichment
- trigger: pathway|KEGG|Reactome|signaling + run|do|perform|analyze|analysis|enrichment
- steps:
  1. FILTER_DE_GENES: Filter significant DE genes
  2. RUN_PATHWAY_ENRICHMENT: Run pathway enrichment on filtered genes
  3. PLOT_ENRICHMENT: Generate enrichment dot plot
  4. SUMMARIZE_ENRICHMENT: Interpret pathway enrichment results
- auto_approve: true

### full_enrichment
- description: Comprehensive enrichment analysis (GO + pathways)
- trigger: full|comprehensive|complete + enrichment|GO|pathway
- steps:
  1. FILTER_DE_GENES: Filter significant DE genes
  2. RUN_GO_ENRICHMENT: Run GO enrichment (all ontologies)
  3. RUN_PATHWAY_ENRICHMENT: Run KEGG pathway enrichment
  4. PLOT_ENRICHMENT: Generate enrichment plots
  5. SUMMARIZE_ENRICHMENT: Comprehensive enrichment summary
- auto_approve: true
