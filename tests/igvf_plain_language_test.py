"""
Tests for IGVF plain-language routing:
1. Auto-detect skill switch via _auto_detect_skill_switch()
2. Welcome skill SKILL.md contains IGVF routing entry
3. Full tag parse → alias → dispatch simulation
"""
import sys
import os
import re
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cortex.llm_validators import _auto_detect_skill_switch


class TestAutoDetectIGVF(unittest.TestCase):
    """Verify _auto_detect_skill_switch routes IGVF queries correctly."""

    # ── Positive cases: should return "IGVF_Search" ──

    def test_search_igvf_for_k562(self):
        result = _auto_detect_skill_switch("search IGVF for K562 data", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_what_datasets_in_igvf(self):
        result = _auto_detect_skill_switch("what datasets are in IGVF?", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_measurement_sets(self):
        result = _auto_detect_skill_switch("show me measurement sets from IGVF", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_prediction_sets(self):
        result = _auto_detect_skill_switch("find prediction sets in IGVF portal", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_analysis_sets(self):
        result = _auto_detect_skill_switch("how many analysis sets does IGVF have?", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_accession_igvfds(self):
        result = _auto_detect_skill_switch("look up IGVFDS6639ECQN", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_accession_igvffi(self):
        result = _auto_detect_skill_switch("get file IGVFFI1234ABCD", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_portal_search(self):
        result = _auto_detect_skill_switch("search the IGVF portal for ATAC-seq", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_data_search(self):
        result = _auto_detect_skill_switch("search IGVF data for RNA-seq", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_how_many_igvf_datasets(self):
        result = _auto_detect_skill_switch("how many IGVF datasets are there?", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvf_dataset_accession(self):
        result = _auto_detect_skill_switch("what is IGVF dataset IGVFDS0001ABCD?", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_from_encode_context(self):
        """Should switch away from ENCODE_Search to IGVF_Search."""
        result = _auto_detect_skill_switch("now search IGVF for the same datasets", "ENCODE_Search")
        self.assertEqual(result, "IGVF_Search")

    def test_from_de_context(self):
        """Should switch from DE to IGVF_Search."""
        result = _auto_detect_skill_switch("search IGVF for K562 datasets", "differential_expression")
        self.assertEqual(result, "IGVF_Search")

    # ── Negative cases: should NOT return "IGVF_Search" ──

    def test_already_in_igvf(self):
        """Should not re-switch if already in IGVF_Search."""
        result = _auto_detect_skill_switch("search IGVF for K562 data", "IGVF_Search")
        self.assertIsNone(result)

    def test_encode_query_not_igvf(self):
        """Plain ENCODE query should route to ENCODE, not IGVF."""
        result = _auto_detect_skill_switch("search ENCODE for K562 experiments", "welcome")
        self.assertEqual(result, "ENCODE_Search")

    def test_generic_unrelated(self):
        """Unrelated query should not route to IGVF."""
        result = _auto_detect_skill_switch("run my pipeline on the cluster", "welcome")
        self.assertNotEqual(result, "IGVF_Search")

    def test_igvf_alone_no_search(self):
        """Just 'IGVF' without a search intent should not trigger."""
        result = _auto_detect_skill_switch("tell me about IGVF", "welcome")
        self.assertIsNone(result)

    # ── Accession-only cases (no other keywords needed) ──

    def test_igvfds_accession_only(self):
        """Bare IGVFDS accession should trigger even without 'search'."""
        result = _auto_detect_skill_switch("IGVFDS6639ECQN", "welcome")
        self.assertEqual(result, "IGVF_Search")

    def test_igvffi_accession_only(self):
        """Bare IGVFFI accession should trigger."""
        result = _auto_detect_skill_switch("IGVFFI9999ZZZZ", "welcome")
        self.assertEqual(result, "IGVF_Search")


class TestWelcomeSkillRouting(unittest.TestCase):
    """Verify welcome SKILL.md contains IGVF routing."""

    @classmethod
    def setUpClass(cls):
        skill_path = os.path.join(
            os.path.dirname(__file__), "..", "skills", "welcome", "SKILL.md"
        )
        with open(skill_path) as f:
            cls.skill_content = f.read()

    def test_igvf_search_routing_present(self):
        self.assertIn("[[SKILL_SWITCH_TO: IGVF_Search]]", self.skill_content)

    def test_igvf_keywords_mentioned(self):
        self.assertIn("IGVF", self.skill_content)
        self.assertIn("measurement sets", self.skill_content)
        self.assertIn("IGVFDS", self.skill_content)

    def test_igvf_routing_after_encode(self):
        """IGVF routing should appear after ENCODE routing."""
        encode_pos = self.skill_content.index("ENCODE_Search")
        igvf_pos = self.skill_content.index("IGVF_Search")
        self.assertGreater(igvf_pos, encode_pos)


class TestTagParsingForIGVF(unittest.TestCase):
    """Verify DATA_CALL tags for IGVF tools are correctly parsed."""

    # The pattern from cortex/tag_parser.py
    DATA_CALL_PATTERN = re.compile(
        r'\[\[DATA_CALL:\s*(?:(consortium|service)=(\w+)),\s*tool=(\w+)(?:,\s*(.+))?\]\]'
    )

    def _parse(self, tag: str):
        m = self.DATA_CALL_PATTERN.search(tag)
        self.assertIsNotNone(m, f"Tag did not match: {tag}")
        return {
            "scope_type": m.group(1),
            "scope_val": m.group(2),
            "tool": m.group(3),
            "params": m.group(4),
        }

    def test_search_measurement_sets(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, assay_title=ATAC-seq, limit=5]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["scope_val"], "igvf")
        self.assertEqual(parsed["tool"], "search_measurement_sets")
        self.assertIn("assay_title=ATAC-seq", parsed["params"])

    def test_search_by_sample(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=search_by_sample, sample_term=K562, limit=10]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "search_by_sample")

    def test_get_dataset(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=get_dataset, accession=IGVFDS6639ECQN]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "get_dataset")
        self.assertIn("accession=IGVFDS6639ECQN", parsed["params"])

    def test_search_genes(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=search_genes, query=BRCA1]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "search_genes")

    def test_get_gene(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=get_gene, gene_id=ENSG00000012048]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "get_gene")

    def test_search_files(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=search_files, file_format=fastq, limit=5]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "search_files")

    def test_get_file_download_url(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=get_file_download_url, file_accession=IGVFFI1234ABCD]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "get_file_download_url")

    def test_search_samples(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=search_samples, sample_term=brain, limit=5]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "search_samples")

    def test_get_server_info(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=get_server_info]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "get_server_info")
        self.assertIsNone(parsed["params"])

    def test_search_by_assay(self):
        tag = '[[DATA_CALL: consortium=igvf, tool=search_by_assay, assay_title=ATAC-seq, sample_term=K562]]'
        parsed = self._parse(tag)
        self.assertEqual(parsed["tool"], "search_by_assay")
        self.assertIn("assay_title=ATAC-seq", parsed["params"])


if __name__ == "__main__":
    unittest.main()
