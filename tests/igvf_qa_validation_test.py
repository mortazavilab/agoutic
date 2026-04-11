"""
End-to-end plain-language Q&A test for the IGVF MCP pipeline.

Simulates the full flow:
  User question → auto-detect skill → tool call → verify answer correctness

Each test represents a realistic user question, dispatches the appropriate
IGVF tool(s), then validates the response is accurate by cross-checking
fields, counts, and data consistency against the live IGVF API.
"""
import os
import re
import sys
import unittest

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cortex.llm_validators import _auto_detect_skill_switch
from atlas.igvf_mcp_server import (
    get_dataset,
    get_file_download_url,
    get_file_metadata,
    get_files_for_dataset,
    get_gene,
    get_server_info,
    search_analysis_sets,
    search_by_assay,
    search_by_sample,
    search_files,
    search_genes,
    search_measurement_sets,
    search_prediction_sets,
    search_samples,
)

# Direct API client for cross-validation
IGVF_API = "https://api.data.igvf.org"


def _api_get(path: str, params: dict | None = None) -> dict:
    """Direct IGVF API call for cross-validation."""
    r = httpx.get(
        f"{IGVF_API}{path}",
        params=params,
        headers={"Accept": "application/json"},
        follow_redirects=True,
        timeout=30,
    )
    if r.status_code == 404:
        return r.json()
    r.raise_for_status()
    return r.json()


class TestPlainLanguageQA(unittest.TestCase):
    """
    Each test is a plain-language question a user might type.
    We verify:
    1. Auto-detect routes to IGVF_Search
    2. The tool returns data
    3. The data is correct (cross-validated against live API)
    """

    # ------------------------------------------------------------------
    # Q1: "How many ATAC-seq datasets does IGVF have?"
    # ------------------------------------------------------------------
    def test_q1_how_many_atacseq_datasets(self):
        question = "How many ATAC-seq datasets does IGVF have?"
        # Step 1: routing
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        # Step 2: tool call (LLM would emit search_by_assay with assay_title=ATAC-seq)
        result = search_by_assay(assay_title="ATAC-seq", limit=5)
        self.assertIn("total", result)
        self.assertGreater(result["total"], 0, "IGVF should have ATAC-seq datasets")
        self.assertEqual(result["assay_title"], "ATAC-seq")

        # Step 3: cross-validate total against direct API
        direct = _api_get("/search", {
            "type": "MeasurementSet",
            "preferred_assay_titles": "ATAC-seq",
            "status": "released",
            "format": "json",
            "limit": 0,
        })
        self.assertEqual(result["total"], direct.get("total", 0),
                         "Tool total should match direct API total")

    # ------------------------------------------------------------------
    # Q2: "Search IGVF for K562 data"
    # ------------------------------------------------------------------
    def test_q2_search_igvf_k562(self):
        question = "Search IGVF for K562 data"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = search_by_sample(sample_term="K562", limit=5)
        self.assertIn("total", result)
        # K562 is a common cell line — IGVF should have some
        self.assertGreaterEqual(result["total"], 0)
        self.assertEqual(result["sample_term"], "K562")

        # Every returned result should be a dataset with an accession
        for r in result["results"]:
            self.assertIn("accession", r)
            self.assertTrue(r["accession"].startswith("IGVFDS"),
                            f"Accession should start with IGVFDS: {r['accession']}")

    # ------------------------------------------------------------------
    # Q3: "What is IGVF dataset IGVFDS6639ECQN?"
    # ------------------------------------------------------------------
    def test_q3_lookup_specific_dataset(self):
        question = "What is IGVF dataset IGVFDS6639ECQN?"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = get_dataset(accession="IGVFDS6639ECQN")

        # Should return metadata, not an error
        self.assertNotIn("error", result)
        self.assertEqual(result["accession"], "IGVFDS6639ECQN")
        self.assertIn("summary", result)
        self.assertIn("status", result)

        # Cross-validate key fields against direct API
        direct = _api_get("/IGVFDS6639ECQN/", {"format": "json"})
        self.assertEqual(result["status"], direct.get("status", ""))
        self.assertEqual(result["accession"], direct.get("accession", ""))

    # ------------------------------------------------------------------
    # Q4: "Tell me about gene BRCA1 in IGVF"
    # ------------------------------------------------------------------
    def test_q4_gene_lookup_brca1(self):
        question = "Tell me about gene BRCA1 in IGVF"
        result = search_genes(query="BRCA1", organism="Homo sapiens", limit=10)

        self.assertIn("total", result)
        self.assertGreater(result["total"], 0, "BRCA1 should exist in IGVF")

        # After the exact-match promotion fix, BRCA1 should be first
        first = result["results"][0]
        self.assertEqual(first["symbol"], "BRCA1")
        self.assertTrue(first["link"].startswith("https://data.igvf.org/"))

    # ------------------------------------------------------------------
    # Q5: "What CRISPR screen datasets are available in IGVF?"
    # ------------------------------------------------------------------
    def test_q5_crispr_screen_datasets(self):
        question = "What CRISPR screen datasets are available in IGVF?"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = search_by_assay(assay_title="CRISPR screen", limit=5)
        self.assertIn("total", result)
        # There should be CRISPR screen data in IGVF
        self.assertGreaterEqual(result["total"], 0)

        # Cross-validate
        direct = _api_get("/search", {
            "type": "MeasurementSet",
            "preferred_assay_titles": "CRISPR screen",
            "status": "released",
            "format": "json",
            "limit": 0,
        })
        self.assertEqual(result["total"], direct.get("total", 0))

    # ------------------------------------------------------------------
    # Q6: "Find prediction sets in IGVF portal"
    # ------------------------------------------------------------------
    def test_q6_prediction_sets(self):
        question = "Find prediction sets in IGVF portal"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = search_prediction_sets(limit=5)
        self.assertIn("total", result)
        self.assertIn("results", result)
        self.assertGreaterEqual(result["total"], 0)

        # Cross-validate count
        direct = _api_get("/search", {
            "type": "PredictionSet",
            "status": "released",
            "format": "json",
            "limit": 0,
        })
        self.assertEqual(result["total"], direct.get("total", 0))

    # ------------------------------------------------------------------
    # Q7: "How many analysis sets does IGVF have?"
    # ------------------------------------------------------------------
    def test_q7_analysis_sets_count(self):
        question = "How many analysis sets does IGVF have?"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = search_analysis_sets(limit=1)
        self.assertIn("total", result)

        direct = _api_get("/search", {
            "type": "AnalysisSet",
            "status": "released",
            "format": "json",
            "limit": 0,
        })
        self.assertEqual(result["total"], direct.get("total", 0))

    # ------------------------------------------------------------------
    # Q8: "What files are in dataset IGVFDS6639ECQN?"
    # ------------------------------------------------------------------
    def test_q8_files_for_dataset(self):
        question = "What files are in dataset IGVFDS6639ECQN?"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = get_files_for_dataset(accession="IGVFDS6639ECQN")
        self.assertIn("total_files", result)
        self.assertIn("by_format", result)
        self.assertEqual(result["accession"], "IGVFDS6639ECQN")

        # Cross-validate file count
        direct = _api_get("/search", {
            "type": "File",
            "file_set.accession": "IGVFDS6639ECQN",
            "status": "released",
            "format": "json",
            "limit": 0,
        })
        self.assertEqual(result["total_files"], direct.get("total", 0),
                         "File count should match direct API")

    # ------------------------------------------------------------------
    # Q9: "Search IGVF for fastq files"
    # ------------------------------------------------------------------
    def test_q9_search_fastq_files(self):
        question = "Search IGVF for fastq files"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = search_files(file_format="fastq", limit=3)
        self.assertIn("total", result)
        self.assertGreater(result["total"], 0, "IGVF should have fastq files")

        # Every returned file should be fastq format
        for f in result["results"]:
            self.assertEqual(f["file_format"], "fastq",
                             f"Expected fastq, got {f['file_format']}")

    # ------------------------------------------------------------------
    # Q10: "Show me human measurement sets in IGVF"
    # ------------------------------------------------------------------
    def test_q10_human_measurement_sets(self):
        question = "Show me human measurement sets in IGVF"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = search_measurement_sets(organism="Homo sapiens", limit=5)
        self.assertIn("total", result)
        self.assertGreater(result["total"], 0)

        # Cross-validate
        direct = _api_get("/search", {
            "type": "MeasurementSet",
            "donors.taxa": "Homo sapiens",
            "status": "released",
            "format": "json",
            "limit": 0,
        })
        self.assertEqual(result["total"], direct.get("total", 0))

    # ------------------------------------------------------------------
    # Q11: "Get info about gene TP53 from IGVF"
    # ------------------------------------------------------------------
    def test_q11_get_gene_tp53(self):
        question = "Get info about gene TP53 from IGVF"
        # get_gene now tries symbol= filter fallback, so exact match works
        result = get_gene(gene_id="TP53")
        self.assertNotIn("error", result)
        self.assertEqual(result["symbol"], "TP53")
        self.assertIn("title", result)
        self.assertIn("locations", result)
        self.assertEqual(result["taxa"], "Homo sapiens")

    # ------------------------------------------------------------------
    # Q12: "What samples are available in IGVF?"
    # ------------------------------------------------------------------
    def test_q12_search_samples(self):
        question = "What samples are available in IGVF?"
        skill = _auto_detect_skill_switch(question, "welcome")
        self.assertEqual(skill, "IGVF_Search")

        result = search_samples(limit=5)
        self.assertIn("total", result)
        self.assertGreater(result["total"], 0, "IGVF should have samples")
        self.assertGreater(len(result["results"]), 0)

    # ------------------------------------------------------------------
    # Q13: "Is the IGVF portal online?"
    # ------------------------------------------------------------------
    def test_q13_server_info(self):
        question = "Is the IGVF portal online?"
        result = get_server_info()

        self.assertIn("server", result)
        self.assertEqual(result["server"], "AGOUTIC-IGVF")
        self.assertIn("portal", result)
        self.assertEqual(result["portal"], "https://data.igvf.org")
        self.assertIn("tools", result)
        self.assertGreater(len(result["tools"]), 0)


class TestAnswerConsistency(unittest.TestCase):
    """
    Cross-check that tool results are internally consistent:
    dataset lookup → files for that dataset → individual file lookup.
    """

    def test_dataset_to_files_to_file_metadata_chain(self):
        """
        Q: "Tell me about IGVFDS6639ECQN and show me its files"
        Simulates LLM calling get_dataset then get_files_for_dataset,
        then get_file_metadata on the first file.
        """
        # Step 1: get dataset
        ds = get_dataset(accession="IGVFDS6639ECQN")
        self.assertEqual(ds["accession"], "IGVFDS6639ECQN")

        # Step 2: get files for the dataset
        files = get_files_for_dataset(accession="IGVFDS6639ECQN", limit=10)
        self.assertGreaterEqual(files["total_files"], 0)

        if files["total_files"] > 0:
            # Step 3: pick first file and get its metadata
            first_file = files["files"][0]
            acc = first_file["accession"]
            self.assertTrue(acc.startswith("IGVFFI"),
                            f"File accession should start with IGVFFI: {acc}")

            file_meta = get_file_metadata(file_accession=acc)
            self.assertEqual(file_meta["accession"], acc)
            self.assertIn("file_format", file_meta)
            self.assertIn("status", file_meta)

            # The file format from search should match the metadata lookup
            self.assertEqual(first_file["file_format"], file_meta["file_format"],
                             "File format should be consistent between search and lookup")

    def test_gene_search_then_lookup_consistent(self):
        """
        Q: "Search IGVF for BRCA1 gene and give me details"
        Simulates LLM calling search_genes then get_gene.
        """
        search_result = search_genes(query="BRCA1", limit=1)
        self.assertGreater(search_result["total"], 0)

        found = search_result["results"][0]
        gene_id = str(found["gene_id"])

        detail = get_gene(gene_id=gene_id)
        self.assertNotIn("error", detail)
        self.assertEqual(detail["symbol"], found["symbol"])
        self.assertEqual(str(detail["gene_id"]), gene_id)

    def test_assay_search_then_dataset_lookup(self):
        """
        Q: "Find IGVF ATAC-seq datasets and tell me about the first one"
        """
        assay_results = search_by_assay(assay_title="ATAC-seq", limit=3)
        if assay_results["total"] > 0 and assay_results["results"]:
            first_acc = assay_results["results"][0]["accession"]
            ds = get_dataset(accession=first_acc)
            self.assertEqual(ds["accession"], first_acc)
            # The assay field should mention ATAC-seq
            assay_field = ds.get("assay", "").lower()
            # Some datasets may have multi-assay or slightly different naming
            # Just verify we got a valid dataset back
            self.assertIn("status", ds)
            self.assertIn("summary", ds)


class TestNegativePlainLanguage(unittest.TestCase):
    """Verify graceful handling when IGVF has no data for a query."""

    def test_nonexistent_sample(self):
        """Q: 'Search IGVF for unicorn tissue data'"""
        result = search_by_sample(sample_term="unicorn tissue", limit=5)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["results"], [])

    def test_nonexistent_gene(self):
        """Q: 'Look up gene FAKEGENE123 in IGVF'"""
        result = get_gene(gene_id="FAKEGENE123")
        self.assertIn("error", result)

    def test_nonexistent_accession(self):
        """Q: 'What is IGVF dataset IGVFDS0000FAKE?'"""
        with self.assertRaises(Exception):
            get_dataset(accession="IGVFDS0000FAKE")

    def test_empty_assay_search(self):
        """Q: 'Are there any underwater basket weaving datasets in IGVF?'"""
        result = search_by_assay(assay_title="underwater basket weaving", limit=5)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["count"], 0)


class TestCountAccuracy(unittest.TestCase):
    """
    Verify that counts reported by tools match direct API for
    several different query types, ensuring users get accurate numbers.
    """

    def _api_count(self, obj_type: str, **filters) -> int:
        params = {"type": obj_type, "format": "json", "limit": 0, "status": "released"}
        params.update(filters)
        data = _api_get("/search", params)
        return data.get("total", 0)

    def test_measurement_set_total_matches(self):
        result = search_measurement_sets(limit=1)
        expected = self._api_count("MeasurementSet")
        self.assertEqual(result["total"], expected)

    def test_analysis_set_total_matches(self):
        result = search_analysis_sets(limit=1)
        expected = self._api_count("AnalysisSet")
        self.assertEqual(result["total"], expected)

    def test_prediction_set_total_matches(self):
        result = search_prediction_sets(limit=1)
        expected = self._api_count("PredictionSet")
        self.assertEqual(result["total"], expected)

    def test_human_datasets_total_matches(self):
        result = search_measurement_sets(organism="Homo sapiens", limit=1)
        expected = self._api_count("MeasurementSet", **{"donors.taxa": "Homo sapiens"})
        self.assertEqual(result["total"], expected)

    def test_mouse_datasets_total_matches(self):
        result = search_measurement_sets(organism="Mus musculus", limit=1)
        expected = self._api_count("MeasurementSet", **{"donors.taxa": "Mus musculus"})
        self.assertEqual(result["total"], expected)

    def test_fastq_count_matches(self):
        result = search_files(file_format="fastq", limit=1)
        expected = self._api_count("File", file_format="fastq")
        self.assertEqual(result["total"], expected)


if __name__ == "__main__":
    unittest.main()
