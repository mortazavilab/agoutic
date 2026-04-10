from __future__ import annotations

import pandas as pd
import pytest

from cortex.de_prep import prepare_de_inputs


def _abundance_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "gene_ID": "GENE1",
                "transcript_ID": "TX1",
                "annot_gene_name": "Gene One",
                "gko": 10,
                "jbh": 30,
                "lwf": 20,
                "exc": 40,
            },
            {
                "gene_ID": "GENE1",
                "transcript_ID": "TX2",
                "annot_gene_name": "Gene One",
                "gko": 1,
                "jbh": 3,
                "lwf": 2,
                "exc": 4,
            },
            {
                "gene_ID": "GENE2",
                "transcript_ID": "TX3",
                "annot_gene_name": "Gene Two",
                "gko": 7,
                "jbh": 9,
                "lwf": 8,
                "exc": 11,
            },
        ]
    )


def test_prepare_de_inputs_gene_level_sums_counts(tmp_path):
    abundance_path = tmp_path / "reconciled_abundance.tsv"
    _abundance_frame().to_csv(abundance_path, sep="\t", index=False)

    result = prepare_de_inputs(
        str(abundance_path),
        output_dir=str(tmp_path / "de_inputs"),
        group_a_label="AD",
        group_a_samples=["exc", "jbh"],
        group_b_label="control",
        group_b_samples=["gko", "lwf"],
        level="gene",
    )

    counts = pd.read_csv(result["counts_path"], sep="\t")
    sample_info = pd.read_csv(result["sample_info_path"])

    assert result["pair"] == ["AD", "control"]
    assert result["contrast"] == "AD - control"
    assert result["feature_column"] == "gene_ID"
    assert list(counts.columns) == ["gene_ID", "exc", "jbh", "gko", "lwf"]
    assert counts.loc[counts["gene_ID"] == "GENE1", "exc"].iloc[0] == 44
    assert counts.loc[counts["gene_ID"] == "GENE1", "jbh"].iloc[0] == 33
    assert counts.loc[counts["gene_ID"] == "GENE1", "gko"].iloc[0] == 11
    assert counts.loc[counts["gene_ID"] == "GENE1", "lwf"].iloc[0] == 22
    assert sample_info.to_dict("records") == [
        {"sample": "exc", "group": "AD"},
        {"sample": "jbh", "group": "AD"},
        {"sample": "gko", "group": "control"},
        {"sample": "lwf", "group": "control"},
    ]


def test_prepare_de_inputs_transcript_level_from_dataframe_payload(tmp_path):
    frame = _abundance_frame()
    payload = {
        "columns": list(frame.columns),
        "data": frame.to_dict("records"),
        "metadata": {"label": "DF1: reconciled_abundance.tsv"},
    }

    result = prepare_de_inputs(
        payload,
        output_dir=str(tmp_path / "de_inputs"),
        group_a_label="AD",
        group_a_samples=["exc"],
        group_b_label="control",
        group_b_samples=["gko"],
        level="transcript",
    )

    counts = pd.read_csv(result["counts_path"], sep="\t")
    assert result["feature_column"] == "transcript_ID"
    assert result["source_label"] == "DF1: reconciled_abundance.tsv"
    assert set(counts["transcript_ID"]) == {"TX1", "TX2", "TX3"}


def test_prepare_de_inputs_raises_for_missing_sample_column(tmp_path):
    abundance_path = tmp_path / "reconciled_abundance.tsv"
    _abundance_frame().to_csv(abundance_path, sep="\t", index=False)

    with pytest.raises(ValueError, match="were not found"):
        prepare_de_inputs(
            str(abundance_path),
            output_dir=str(tmp_path / "de_inputs"),
            group_a_label="AD",
            group_a_samples=["exc", "missing_sample"],
            group_b_label="control",
            group_b_samples=["gko"],
        )
