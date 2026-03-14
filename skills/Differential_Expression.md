# Skill: Differential Expression (`differential_expression`)

## Description

This skill performs differential expression (DE) analysis using edgePython — a Python implementation of edgeR for RNA-seq count data. It supports bulk DE, single-cell DE (NEBULA-LN), differential transcript usage (DTU), ChIP-seq enrichment testing, and visualization.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Loading count matrices and sample metadata
- Filtering lowly-expressed genes
- Library-size normalization (TMM, RLE, etc.)
- Setting experimental designs (formula or matrix)
- Estimating NB dispersions
- Fitting QL-GLM and GLM models
- Testing contrasts and coefficients (QLF, TREAT, LRT, exact test)
- DTU analysis (diff_splice_dge, splice_variants)
- ChIP-seq normalization and enrichment testing
- Single-cell DE via NEBULA-LN
- Generating plots (MDS, BCV, volcano, MA/MD, heatmap)
- Saving results tables

**Example questions:**
- "Run differential expression on my count matrix"
- "Compare treated vs control samples"
- "What genes are differentially expressed?"
- "Run DE analysis on this RNA-seq data"
- "I have a counts table and want to find DE genes"
- "Do single-cell DE with NEBULA"
- "Generate a volcano plot of the DE results"
- "Test for differential transcript usage"

### ❌ This Skill Does NOT Handle:
- **Running pipelines / alignment** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE data search** → `[[SKILL_SWITCH_TO: ENCODE_Search]]`
- **Downloading files** → `[[SKILL_SWITCH_TO: download_files]]`
- **Checking pipeline job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
- **GO/pathway enrichment analysis** → `[[SKILL_SWITCH_TO: enrichment_analysis]]`

## Inputs

* `counts_path`: Path to count matrix (TSV/CSV) — genes as rows, samples as columns
* `sample_info_path`: Optional path to sample metadata file
* `group_column`: Column in sample info for the group factor
* `formula`: R-style design formula (e.g. `~ 0 + group`)
* `contrast`: Contrast string (e.g. `groupB - groupA`)

## Plan Logic

### Bulk RNA-seq DE Workflow

The standard workflow proceeds through these steps:

#### Step 1: Load Data

```
[[DATA_CALL: service=edgepython, tool=load_data, counts_path=<path>, sample_info_path=<path>, group_column=<col>]]
```

Gene symbols are automatically annotated when data is loaded, if reference files
are available in `data/reference/`. To manually annotate or re-annotate after loading:

```
[[DATA_CALL: service=edgepython, tool=annotate_genes]]
```

To translate specific gene IDs outside a DE pipeline:

```
[[DATA_CALL: service=edgepython, tool=translate_gene_ids, gene_ids=["ENSG00000141510", "ENSG00000157764"]]]
```

To look up genes by symbol or Ensembl ID (bidirectional):

```
[[DATA_CALL: service=edgepython, tool=lookup_gene, gene_symbols=["TP53", "BRCA1"]]]
[[DATA_CALL: service=edgepython, tool=lookup_gene, gene_ids=["ENSG00000141510"]]]
```

Or for flexible loading (kallisto, salmon, 10x, AnnData):

```
[[DATA_CALL: service=edgepython, tool=load_data_auto, data=<path>, source=<source>]]
```

#### Step 2: Filter Lowly-Expressed Genes

```
[[DATA_CALL: service=edgepython, tool=filter_genes, min_count=10, min_total_count=15]]
```

#### Step 3: Normalize

```
[[DATA_CALL: service=edgepython, tool=normalize, method=TMM]]
```

#### Step 4: Set Design

```
[[DATA_CALL: service=edgepython, tool=set_design, formula=~ 0 + group]]
```

#### Step 5: Estimate Dispersions

```
[[DATA_CALL: service=edgepython, tool=estimate_dispersion, robust=true]]
```

#### Step 6: Fit Model

```
[[DATA_CALL: service=edgepython, tool=fit_model, robust=true]]
```

#### Step 7: Test Contrast

```
[[DATA_CALL: service=edgepython, tool=test_contrast, contrast=groupB - groupA]]
```

#### Step 8: Get Results

```
[[DATA_CALL: service=edgepython, tool=get_top_genes, n=20, fdr_threshold=0.05]]
```

#### Step 9: Generate Plots

```
[[DATA_CALL: service=edgepython, tool=generate_plot, plot_type=volcano]]
[[DATA_CALL: service=edgepython, tool=generate_plot, plot_type=md]]
[[DATA_CALL: service=edgepython, tool=generate_plot, plot_type=heatmap]]
```

#### Step 10: Save Results

```
[[DATA_CALL: service=edgepython, tool=save_results, output_path=<path>, format=csv]]
```

### Two-Group Exact Test (Simple Case)

For simple two-group comparisons without covariates:

```
[[DATA_CALL: service=edgepython, tool=load_data, counts_path=<path>, sample_info_path=<path>, group_column=<col>]]
[[DATA_CALL: service=edgepython, tool=filter_genes]]
[[DATA_CALL: service=edgepython, tool=normalize]]
[[DATA_CALL: service=edgepython, tool=estimate_dispersion]]
[[DATA_CALL: service=edgepython, tool=exact_test, pair=["groupA", "groupB"]]]
[[DATA_CALL: service=edgepython, tool=get_top_genes]]
```

### Single-Cell DE Workflow (NEBULA-LN)

```
[[DATA_CALL: service=edgepython, tool=load_sc_data, h5ad_path=<path>, sample_col=orgID, cell_type_col=cell_type, cell_type_filter=["Neuron"]]]
[[DATA_CALL: service=edgepython, tool=set_sc_design, covariates={"Intercept": [1,...], "treatment": [0,1,...]}]]
[[DATA_CALL: service=edgepython, tool=fit_sc_model, norm_method=TMM]]
[[DATA_CALL: service=edgepython, tool=test_sc_coef, coef=treatment]]
[[DATA_CALL: service=edgepython, tool=get_sc_top_genes, n=20]]
[[DATA_CALL: service=edgepython, tool=save_sc_results, output_path=sc_de_results.csv]]
```

### DTU Workflow

For differential transcript usage with transcript-level counts:

```
[[DATA_CALL: service=edgepython, tool=load_data, counts_path=<path>]]
[[DATA_CALL: service=edgepython, tool=dtu_diff_splice_dge, gene_column=GeneID, exon_column=TranscriptID]]
```

### ChIP-seq Workflow

```
[[DATA_CALL: service=edgepython, tool=load_data, counts_path=chip_counts.csv]]
[[DATA_CALL: service=edgepython, tool=normalize_chip, input_csv=input_counts.csv]]
[[DATA_CALL: service=edgepython, tool=set_design, formula=~ 0 + group]]
[[DATA_CALL: service=edgepython, tool=fit_model]]
[[DATA_CALL: service=edgepython, tool=test_contrast, contrast=treated - control]]
```

### Utility Tools

Check pipeline state:
```
[[DATA_CALL: service=edgepython, tool=describe]]
[[DATA_CALL: service=edgepython, tool=describe_sc]]
```

Reset state for a new analysis:
```
[[DATA_CALL: service=edgepython, tool=reset_state]]
[[DATA_CALL: service=edgepython, tool=reset_sc_state]]
```

## Important Rules

1. **Always load data before any other step** — the pipeline is stateful and sequential.
2. **Filter before normalize** — filtering recalculates library sizes.
3. **Estimate dispersions before fitting** — the model needs dispersion estimates.
4. **Set design before fitting** — the design matrix must be set first.
5. **Use describe() to check state** if unsure where the pipeline is.
6. **Emit ALL pipeline DATA_CALLs in a single response.** The sequential dependency is handled server-side — each step automatically waits for the previous one. Do NOT ask "would you like me to proceed?" — execute all steps immediately.
7. **Do NOT skip steps** — the pipeline must proceed in order.
8. **For simple two-group comparisons**, exact_test is preferred over the GLM path.
9. **For complex designs** (batch effects, interaction terms), use the full GLM pipeline.
10. **Always generate at least a volcano plot** after testing to visualize results.
11. **When the user says "yes", "proceed", "go ahead", or "continue"**, emit the remaining pipeline DATA_CALLs. Do NOT ask clarifying questions — the user has already confirmed.
