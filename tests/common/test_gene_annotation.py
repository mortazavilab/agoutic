"""Tests for common.gene_annotation — GeneAnnotator."""

import pandas as pd
import pytest

from common.gene_annotation import GeneAnnotator


# -----------------------------------------------------------------------
# strip_version
# -----------------------------------------------------------------------

class TestStripVersion:
    def test_versioned_human(self):
        assert GeneAnnotator.strip_version("ENSG00000141510.17") == "ENSG00000141510"

    def test_versioned_mouse(self):
        assert GeneAnnotator.strip_version("ENSMUSG00000059552.14") == "ENSMUSG00000059552"

    def test_unversioned(self):
        assert GeneAnnotator.strip_version("ENSG00000141510") == "ENSG00000141510"

    def test_gene_symbol_unchanged(self):
        # Gene symbols with dots that aren't version suffixes
        assert GeneAnnotator.strip_version("TP53") == "TP53"

    def test_empty_string(self):
        assert GeneAnnotator.strip_version("") == ""

    def test_dot_but_not_version(self):
        # Dots followed by non-digits should be kept
        assert GeneAnnotator.strip_version("GENE.abc") == "GENE.abc"


# -----------------------------------------------------------------------
# detect_organism
# -----------------------------------------------------------------------

class TestDetectOrganism:
    def test_human_gene(self):
        assert GeneAnnotator.detect_organism("ENSG00000141510") == "human"

    def test_human_versioned(self):
        assert GeneAnnotator.detect_organism("ENSG00000141510.17") == "human"

    def test_mouse_gene(self):
        assert GeneAnnotator.detect_organism("ENSMUSG00000059552") == "mouse"

    def test_mouse_versioned(self):
        assert GeneAnnotator.detect_organism("ENSMUSG00000059552.14") == "mouse"

    def test_human_transcript(self):
        assert GeneAnnotator.detect_organism("ENST00000269305") == "human"

    def test_mouse_transcript(self):
        assert GeneAnnotator.detect_organism("ENSMUST00000070533") == "mouse"

    def test_unknown_symbol(self):
        assert GeneAnnotator.detect_organism("TP53") is None

    def test_unknown_numeric(self):
        assert GeneAnnotator.detect_organism("12345") is None

    def test_empty(self):
        assert GeneAnnotator.detect_organism("") is None

    def test_whitespace_stripped(self):
        assert GeneAnnotator.detect_organism("  ENSG00000141510  ") == "human"


# -----------------------------------------------------------------------
# Fixtures — annotator with test data
# -----------------------------------------------------------------------

@pytest.fixture
def ref_dir(tmp_path):
    """Create a tmp reference directory with small TSV files."""
    d = tmp_path / "reference"
    d.mkdir()

    (d / "human_genes.tsv").write_text(
        "gene_id\tgene_symbol\tgene_name\tbiotype\n"
        "ENSG00000141510\tTP53\ttumor protein p53\tprotein_coding\n"
        "ENSG00000157764\tBRAF\tB-Raf proto-oncogene\tprotein_coding\n"
        "ENSG00000111640\tGAPDH\tglyceraldehyde-3-phosphate dehydrogenase\tprotein_coding\n"
        "ENSG00000251562\tMALAT1\tmetastasis associated lung adenocarcinoma transcript 1\tlncRNA\n"
    )
    (d / "mouse_genes.tsv").write_text(
        "gene_id\tgene_symbol\tgene_name\tbiotype\n"
        "ENSMUSG00000059552\tTrp53\ttransformation related protein 53\tprotein_coding\n"
        "ENSMUSG00000002413\tBraf\tBraf transforming gene\tprotein_coding\n"
        "ENSMUSG00000057666\tGapdh\tglyceraldehyde-3-phosphate dehydrogenase\tprotein_coding\n"
    )
    return tmp_path


@pytest.fixture
def annotator(ref_dir):
    return GeneAnnotator(data_dir=ref_dir)


# -----------------------------------------------------------------------
# Annotator init / introspection
# -----------------------------------------------------------------------

class TestAnnotatorInit:
    def test_loads_both_organisms(self, annotator):
        assert annotator.is_available
        assert set(annotator.available_organisms) == {"human", "mouse"}

    def test_stats(self, annotator):
        s = annotator.stats()
        assert s["human"] == 4
        assert s["mouse"] == 3

    def test_no_data_dir(self, tmp_path):
        """Empty dir -> available=False, no crash."""
        a = GeneAnnotator(data_dir=tmp_path / "nonexistent")
        assert not a.is_available
        assert a.available_organisms == []
        assert a.stats() == {}

    def test_empty_reference_dir(self, tmp_path):
        """Reference dir exists but no TSV files."""
        (tmp_path / "reference").mkdir()
        a = GeneAnnotator(data_dir=tmp_path)
        assert not a.is_available


# -----------------------------------------------------------------------
# translate (single gene)
# -----------------------------------------------------------------------

class TestTranslate:
    def test_human_gene(self, annotator):
        assert annotator.translate("ENSG00000141510") == "TP53"

    def test_versioned_id(self, annotator):
        assert annotator.translate("ENSG00000141510.17") == "TP53"

    def test_mouse_gene(self, annotator):
        assert annotator.translate("ENSMUSG00000059552") == "Trp53"

    def test_explicit_organism(self, annotator):
        assert annotator.translate("ENSG00000157764", organism="human") == "BRAF"

    def test_missing_gene(self, annotator):
        assert annotator.translate("ENSG99999999999") is None

    def test_no_data_returns_none(self, tmp_path):
        a = GeneAnnotator(data_dir=tmp_path / "empty")
        assert a.translate("ENSG00000141510") is None

    def test_cross_organism_fallback(self, annotator):
        """When organism is wrong, still find via fallback."""
        result = annotator.translate("ENSMUSG00000059552", organism="human")
        assert result == "Trp53"

    def test_whitespace(self, annotator):
        assert annotator.translate("  ENSG00000141510  ") == "TP53"

    def test_lncrna(self, annotator):
        assert annotator.translate("ENSG00000251562") == "MALAT1"


# -----------------------------------------------------------------------
# translate_batch
# -----------------------------------------------------------------------

class TestTranslateBatch:
    def test_batch_human(self, annotator):
        ids = ["ENSG00000141510", "ENSG00000157764", "ENSG99999999999"]
        result = annotator.translate_batch(ids)
        assert result == {
            "ENSG00000141510": "TP53",
            "ENSG00000157764": "BRAF",
            "ENSG99999999999": None,
        }

    def test_batch_mouse(self, annotator):
        ids = ["ENSMUSG00000059552", "ENSMUSG00000002413"]
        result = annotator.translate_batch(ids)
        assert result == {
            "ENSMUSG00000059552": "Trp53",
            "ENSMUSG00000002413": "Braf",
        }

    def test_empty_list(self, annotator):
        assert annotator.translate_batch([]) == {}

    def test_versioned_batch(self, annotator):
        ids = ["ENSG00000141510.17", "ENSG00000111640.5"]
        result = annotator.translate_batch(ids)
        assert result["ENSG00000141510.17"] == "TP53"
        assert result["ENSG00000111640.5"] == "GAPDH"


# -----------------------------------------------------------------------
# annotate_dataframe
# -----------------------------------------------------------------------

class TestAnnotateDataframe:
    def test_adds_symbol_column(self, annotator):
        df = pd.DataFrame({
            "GeneID": ["ENSG00000141510", "ENSG00000157764", "ENSG99999999999"],
        })
        result = annotator.annotate_dataframe(df)
        assert "Symbol" in result.columns
        assert result["Symbol"].tolist() == ["TP53", "BRAF", None]

    def test_custom_column_names(self, annotator):
        df = pd.DataFrame({
            "my_id": ["ENSG00000141510"],
        })
        result = annotator.annotate_dataframe(df, id_column="my_id", symbol_column="gene_sym")
        assert result["gene_sym"].tolist() == ["TP53"]

    def test_missing_id_column_noop(self, annotator):
        df = pd.DataFrame({"other": [1, 2]})
        result = annotator.annotate_dataframe(df)
        assert "Symbol" not in result.columns

    def test_no_data_noop(self, tmp_path):
        a = GeneAnnotator(data_dir=tmp_path / "empty")
        df = pd.DataFrame({"GeneID": ["ENSG00000141510"]})
        result = a.annotate_dataframe(df)
        assert "Symbol" not in result.columns

    def test_modifies_in_place(self, annotator):
        df = pd.DataFrame({"GeneID": ["ENSG00000141510"]})
        result = annotator.annotate_dataframe(df)
        assert result is df  # same object

    def test_versioned_ids_in_dataframe(self, annotator):
        df = pd.DataFrame({
            "GeneID": ["ENSG00000141510.17", "ENSG00000111640.5"],
        })
        annotator.annotate_dataframe(df)
        assert df["Symbol"].tolist() == ["TP53", "GAPDH"]

    def test_mouse_dataframe(self, annotator):
        df = pd.DataFrame({
            "GeneID": ["ENSMUSG00000059552", "ENSMUSG00000002413"],
        })
        annotator.annotate_dataframe(df)
        assert df["Symbol"].tolist() == ["Trp53", "Braf"]
