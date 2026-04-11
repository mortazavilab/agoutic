"""
Tool schemas for the edgePython MCP server.

Cortex fetches these at startup via GET /tools/schema to know what
tools are available and how to call them.

Uses JSON Schema format (type/properties/required) to match
validate_against_schema() in cortex/tool_contracts.py.
"""

TOOL_SCHEMAS = {
    # =================================================================
    # Bulk RNA-seq tools
    # =================================================================
    "load_data": {
        "description": "Load a count matrix (and optional sample metadata) to create a DGEList.",
        "parameters": {
            "type": "object",
            "properties": {
                "counts_path": {"type": "string", "description": "Path to count matrix file (TSV/CSV). Genes as rows, samples as columns."},
                "sample_info_path": {"type": "string", "description": "Optional path to sample metadata file."},
                "group_column": {"type": "string", "description": "Column in sample info to use as group factor."},
                "separator": {"type": "string", "description": "Column separator. Auto-detected from file extension."},
            },
            "required": ["counts_path"],
        },
    },
    "load_data_auto": {
        "description": "Load data using edgePython's flexible read_data() loader (kallisto, salmon, 10x, AnnData, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Path to file/dir or other supported value."},
                "data_list": {"type": "array", "description": "List of paths (e.g. quantification dirs)."},
                "source": {"type": "string", "description": "Data source (kallisto, salmon, oarfish, rsem, 10x, table, anndata, rds, sparse, matrix, dataframe)."},
                "format": {"type": "string", "description": "Source-specific format (e.g. 'h5' or 'tsv')."},
                "path": {"type": "string", "description": "Base path prefix for relative paths."},
                "group": {"type": "array", "description": "Group assignments."},
                "labels": {"type": "array", "description": "Sample labels."},
                "columns": {"type": "array", "description": "For table format: (gene_id_col, count_col) 0-based indices."},
                "sep": {"type": "string", "description": "Field separator."},
                "obs_col": {"type": "string", "description": "AnnData: column in .obs to use as group factor."},
                "layer": {"type": "string", "description": "AnnData: layer name to use instead of .X."},
                "ngibbs": {"type": "integer", "description": "RSEM: number of Gibbs samples per sample."},
                "verbose": {"type": "boolean", "description": "Print progress messages."},
            },
            "required": [],
        },
    },
    "describe": {
        "description": "Report the current state of the analysis pipeline.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "reset_state": {
        "description": "Reset all pipeline state.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "filter_genes": {
        "description": "Filter out lowly-expressed genes using edgePython's filterByExpr-compatible logic.",
        "parameters": {
            "type": "object",
            "properties": {
                "min_count": {"type": "integer", "description": "Minimum count threshold (default: 10)."},
                "min_total_count": {"type": "integer", "description": "Minimum total count across all samples (default: 15)."},
            },
            "required": [],
        },
    },
    "normalize": {
        "description": "Apply library-size normalization (TMM, TMMwsp, RLE, upperquartile, none).",
        "parameters": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "Normalization method. One of TMM, TMMwsp, RLE, upperquartile, none."},
            },
            "required": [],
        },
    },
    "normalize_chip": {
        "description": "Normalize ChIP-Seq counts to input control and set offsets for downstream GLM.",
        "parameters": {
            "type": "object",
            "properties": {
                "input_csv": {"type": "string", "description": "Path to input control counts (CSV/TSV)."},
                "dispersion": {"type": "number", "description": "Negative binomial dispersion (default: 0.01)."},
                "niter": {"type": "integer", "description": "Number of iterations for scaling-factor estimation."},
                "loss": {"type": "string", "description": "Loss function — 'p' or 'z'."},
            },
            "required": ["input_csv"],
        },
    },
    "chip_enrichment_test": {
        "description": "Test for ChIP-Seq enrichment relative to input for one sample.",
        "parameters": {
            "type": "object",
            "properties": {
                "input_csv": {"type": "string", "description": "Path to input control counts (CSV/TSV)."},
                "sample": {"type": "integer", "description": "Sample index (0-based) to test."},
                "dispersion": {"type": "number", "description": "Negative binomial dispersion."},
                "niter": {"type": "integer", "description": "Number of iterations."},
                "n_top": {"type": "integer", "description": "Number of top enriched features to report."},
            },
            "required": ["input_csv"],
        },
    },
    "set_design": {
        "description": "Set the experimental design using an R-style formula (e.g. '~ 0 + group').",
        "parameters": {
            "type": "object",
            "properties": {
                "formula": {"type": "string", "description": "R-style formula. Variables must match sample metadata columns."},
            },
            "required": ["formula"],
        },
    },
    "set_design_matrix": {
        "description": "Set the design matrix directly as a 2D numeric array.",
        "parameters": {
            "type": "object",
            "properties": {
                "matrix": {"type": "array", "description": "2D list of floats with shape (samples, coefficients)."},
                "columns": {"type": "array", "description": "Column names for the design matrix."},
            },
            "required": ["matrix", "columns"],
        },
    },
    "estimate_dispersion": {
        "description": "Estimate NB dispersions (common, trended, tagwise).",
        "parameters": {
            "type": "object",
            "properties": {
                "robust": {"type": "boolean", "description": "Use robust empirical Bayes (default: True)."},
            },
            "required": [],
        },
    },
    "estimate_glm_robust_dispersion": {
        "description": "Estimate robust GLM dispersions with iterative Huber reweighting.",
        "parameters": {
            "type": "object",
            "properties": {
                "prior_df": {"type": "number", "description": "Prior df (default: 10.0)."},
                "update_trend": {"type": "boolean", "description": "Update trend (default: True)."},
                "maxit": {"type": "integer", "description": "Max iterations (default: 6)."},
                "k": {"type": "number", "description": "Huber tuning constant (default: 1.345)."},
                "residual_type": {"type": "string", "description": "Residual type — 'pearson' or 'deviance'."},
                "verbose": {"type": "boolean", "description": "Print progress."},
            },
            "required": [],
        },
    },
    "voom_transform": {
        "description": "Run voom/voomLmFit-style mean-variance weighting.",
        "parameters": {
            "type": "object",
            "properties": {
                "span": {"type": "number", "description": "Loess span (default: 0.5)."},
                "adaptive_span": {"type": "boolean", "description": "Use adaptive span."},
                "sample_weights": {"type": "boolean", "description": "Estimate sample-level weights."},
                "block_column": {"type": "string", "description": "Column in sample metadata for blocking variable."},
            },
            "required": [],
        },
    },
    "fit_model": {
        "description": "Fit a quasi-likelihood negative binomial GLM.",
        "parameters": {
            "type": "object",
            "properties": {
                "robust": {"type": "boolean", "description": "Use robust empirical Bayes for QL dispersion (default: True)."},
            },
            "required": [],
        },
    },
    "fit_glm": {
        "description": "Fit a negative binomial GLM (non-QL).",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "test_contrast": {
        "description": "Test a contrast for differential expression (e.g. 'groupB - groupA').",
        "parameters": {
            "type": "object",
            "properties": {
                "contrast": {"type": "string", "description": "Contrast string, e.g. 'groupB - groupA'."},
                "name": {"type": "string", "description": "Label for this result set."},
                "method": {"type": "string", "description": "'qlf' (default) or 'treat'."},
                "lfc": {"type": "number", "description": "Log2-FC threshold for TREAT method (default: 0.585)."},
            },
            "required": ["contrast"],
        },
    },
    "test_coef": {
        "description": "Test a single coefficient by index.",
        "parameters": {
            "type": "object",
            "properties": {
                "coef": {"type": "integer", "description": "0-based coefficient index."},
                "name": {"type": "string", "description": "Label for this result set."},
                "method": {"type": "string", "description": "'qlf' (default) or 'treat'."},
                "lfc": {"type": "number", "description": "Log2-FC threshold for TREAT method."},
            },
            "required": ["coef"],
        },
    },
    "glm_lrt_test": {
        "description": "Run a likelihood-ratio test (LRT) from a GLM fit.",
        "parameters": {
            "type": "object",
            "properties": {
                "coef": {"type": "integer", "description": "0-based coefficient index."},
                "contrast": {"type": "array", "description": "Contrast vector (same length as coefficients)."},
                "name": {"type": "string", "description": "Label for this result set."},
                "use_ql_fit": {"type": "boolean", "description": "Use the QL fit from fit_model() instead of fit_glm()."},
            },
            "required": [],
        },
    },
    "exact_test": {
        "description": "Run edgePython exact test for two-group DE.",
        "parameters": {
            "type": "object",
            "properties": {
                "pair": {"type": "array", "description": "Two group labels to compare."},
                "dispersion": {"type": "string", "description": "'auto', 'common', 'trended', 'tagwise', or numeric."},
                "rejection_region": {"type": "string", "description": "'doubletail', 'deviance', or 'smallp'."},
                "big_count": {"type": "integer", "description": "Threshold for beta approximation."},
                "prior_count": {"type": "number", "description": "Prior count for logFC calculation."},
                "name": {"type": "string", "description": "Label for this result set."},
            },
            "required": [],
        },
    },
    "dtu_diff_splice_dge": {
        "description": "Run DTU testing with diff_splice_dge on the current DGEList.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_column": {"type": "string", "description": "Column name for gene IDs in gene annotation."},
                "exon_column": {"type": "string", "description": "Column name for exon/transcript IDs."},
                "prior_count": {"type": "number", "description": "Prior count (default: 0.125)."},
                "name": {"type": "string", "description": "Label for this result set."},
            },
            "required": [],
        },
    },
    "dtu_splice_variants": {
        "description": "Run splice_variants to flag genes with isoform heterogeneity.",
        "parameters": {
            "type": "object",
            "properties": {
                "gene_column": {"type": "string", "description": "Column name for gene IDs."},
                "name": {"type": "string", "description": "Label for this result set."},
            },
            "required": [],
        },
    },
    "get_top_genes": {
        "description": "Get the top differentially expressed genes from a test result.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Which result set to query. Default: most recent."},
                "n": {"type": "integer", "description": "Number of top genes (default: 20)."},
                "fdr_threshold": {"type": "number", "description": "FDR cutoff (default: 0.05)."},
            },
            "required": [],
        },
    },
    "get_result_table": {
        "description": "Return a full result table as TSV/CSV/JSON text.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Which result set to query."},
                "format": {"type": "string", "description": "'tsv', 'csv', or 'json'."},
                "max_rows": {"type": "integer", "description": "Optional row cap."},
            },
            "required": [],
        },
    },
    "save_results": {
        "description": "Save a full result table to disk.",
        "parameters": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "Destination file path."},
                "name": {"type": "string", "description": "Which result set to save."},
                "format": {"type": "string", "description": "'tsv', 'csv', or 'json'."},
            },
            "required": ["output_path"],
        },
    },
    "generate_plot": {
        "description": "Generate and save a visualization (mds, bcv, ql_dispersion, md, volcano, heatmap).",
        "parameters": {
            "type": "object",
            "properties": {
                "plot_type": {"type": "string", "description": "Type of plot: mds, bcv, ql_dispersion, md, volcano, heatmap."},
                "result_name": {"type": "string", "description": "Which result to plot (for md/volcano/heatmap)."},
                "output_path": {"type": "string", "description": "Path to save the PNG."},
            },
            "required": ["plot_type"],
        },
    },

    # =================================================================
    # Single-cell tools
    # =================================================================
    "load_sc_data": {
        "description": "Load single-cell count data from an h5ad file.",
        "parameters": {
            "type": "object",
            "properties": {
                "h5ad_path": {"type": "string", "description": "Path to .h5ad file."},
                "sample_col": {"type": "string", "description": "Column in obs for sample/subject IDs."},
                "cell_type_col": {"type": "string", "description": "Column in obs for cell type annotations."},
                "cell_type_filter": {"type": "array", "description": "List of cell type values to keep."},
                "layer": {"type": "string", "description": "Count layer: 'raw', 'X', or named layer."},
                "log1p_transform": {"type": "boolean", "description": "Reverse log1p transformation (default: True)."},
            },
            "required": ["h5ad_path", "sample_col"],
        },
    },
    "set_sc_design": {
        "description": "Set the design matrix for single-cell analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "covariates": {"type": "object", "description": "Dict mapping column names to per-cell values. Must include 'Intercept'."},
            },
            "required": ["covariates"],
        },
    },
    "fit_sc_model": {
        "description": "Fit NEBULA-LN single-cell mixed model.",
        "parameters": {
            "type": "object",
            "properties": {
                "norm_method": {"type": "string", "description": "Normalization method: 'TMM' or 'none'."},
                "verbose": {"type": "boolean", "description": "Print progress."},
            },
            "required": [],
        },
    },
    "test_sc_coef": {
        "description": "Run a Wald test on a coefficient from the single-cell model.",
        "parameters": {
            "type": "object",
            "properties": {
                "coef": {"type": "string", "description": "Coefficient name to test."},
                "coef_index": {"type": "integer", "description": "0-based coefficient index."},
                "name": {"type": "string", "description": "Label for this result."},
            },
            "required": [],
        },
    },
    "get_sc_top_genes": {
        "description": "Get top DE genes from the single-cell model.",
        "parameters": {
            "type": "object",
            "properties": {
                "coef": {"type": "string", "description": "Coefficient name."},
                "coef_index": {"type": "integer", "description": "0-based coefficient index."},
                "n": {"type": "integer", "description": "Number of top genes (default: 20)."},
                "fdr_threshold": {"type": "number", "description": "FDR cutoff (default: 0.05)."},
            },
            "required": [],
        },
    },
    "describe_sc": {
        "description": "Report the current state of the single-cell analysis pipeline.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "save_sc_results": {
        "description": "Save full single-cell DE results to disk.",
        "parameters": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "Destination file path."},
                "coef": {"type": "string", "description": "Coefficient name."},
                "coef_index": {"type": "integer", "description": "0-based coefficient index."},
                "format": {"type": "string", "description": "'csv', 'tsv', or 'json'."},
            },
            "required": ["output_path"],
        },
    },
    "reset_sc_state": {
        "description": "Reset all single-cell pipeline state.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # =================================================================
    # Gene annotation tools
    # =================================================================
    "annotate_genes": {
        "description": "Add gene symbol annotations to the currently loaded dataset. Auto-detects organism from gene ID prefixes.",
        "parameters": {
            "type": "object",
            "properties": {
                "organism": {"type": "string", "description": "'human' or 'mouse'. Auto-detected if not provided."},
            },
            "required": [],
        },
    },
    "filter_de_genes": {
        "description": "Filter DE results to extract gene lists for enrichment analysis. Splits by direction (up/down/all) with FDR and logFC thresholds.",
        "parameters": {
            "type": "object",
            "properties": {
                "result_name": {"type": "string", "description": "Name of the DE result to filter. Default: most recent."},
                "fdr_threshold": {"type": "number", "description": "FDR cutoff. Default: 0.05."},
                "logfc_threshold": {"type": "number", "description": "Minimum |log2FC|. Default: 0 (no filter)."},
                "direction": {"type": "string", "description": "'all', 'up', or 'down'. Default: 'all'."},
            },
            "required": [],
        },
    },
}
