"""Tests for DE skill routing and parameter extraction."""
import json
import tempfile
import unittest
from pathlib import Path

from cortex.llm_validators import _auto_detect_skill_switch
from cortex.data_call_generator import _validate_edgepython_params
from cortex.plot_routing import infer_plot_route
from cortex.tool_dispatch import _inject_edgepython_output_path, _resolve_edgepython_plot_source_params


class _DummyBlock:
    def __init__(self, payload):
        self.payload_json = json.dumps(payload)


class TestDERouting(unittest.TestCase):

    def test_de_counts_csv_from_dogme(self):
        result = _auto_detect_skill_switch(
            "i want to DE for /media/backup_disk/agoutic_root/test-edgepy/sample_counts.csv",
            "run_dogme_rna",
        )
        self.assertEqual(result, "differential_expression")

    def test_de_counts_csv_from_welcome(self):
        result = _auto_detect_skill_switch(
            "i want to DE for /media/backup_disk/agoutic_root/test-edgepy/sample_counts.csv",
            "welcome",
        )
        self.assertEqual(result, "differential_expression")

    def test_deg_counts_csv_from_welcome(self):
        result = _auto_detect_skill_switch(
            "i want to DEG /media/backup_disk/agoutic_root/test-edgepy/sample_counts.csv",
            "welcome",
        )
        self.assertEqual(result, "differential_expression")

    def test_decode_not_routed_to_de(self):
        result = _auto_detect_skill_switch(
            "decode the file at /tmp/data.csv",
            "welcome",
        )
        self.assertNotEqual(result, "differential_expression")

    def test_already_in_de_no_switch(self):
        result = _auto_detect_skill_switch(
            "i want to DE for /media/backup_disk/test/counts.csv",
            "differential_expression",
        )
        self.assertIsNone(result)

    def test_local_bam_still_routes_to_local_sample(self):
        result = _auto_detect_skill_switch(
            "analyze the sample at /media/data/my_reads.bam",
            "welcome",
        )
        self.assertEqual(result, "analyze_local_sample")

    def test_de_count_csv_from_encode(self):
        result = _auto_detect_skill_switch(
            "run de on /home/user/gene_count.csv",
            "ENCODE_Search",
        )
        self.assertEqual(result, "differential_expression")

    def test_csv_without_de_signal_not_routed_to_de(self):
        result = _auto_detect_skill_switch(
            "load the sample_counts.csv file",
            "welcome",
        )
        self.assertNotEqual(result, "differential_expression")


class TestEdgepythonParamExtraction(unittest.TestCase):
    """Tests for _validate_edgepython_params fallback extraction."""

    FULL_MSG = (
        "i want to DE for /media/backup_disk/agoutic_root/test-edgepy/sample_counts.csv "
        "metadata: /media/backup_disk/agoutic_root/test-edgepy/sample_info.csv "
        "group column: condition contrast: treated - control"
    )

    def test_load_data_extracts_counts_path(self):
        result = _validate_edgepython_params("load_data", {}, self.FULL_MSG)
        self.assertEqual(
            result["counts_path"],
            "/media/backup_disk/agoutic_root/test-edgepy/sample_counts.csv",
        )

    def test_load_data_extracts_sample_info(self):
        result = _validate_edgepython_params("load_data", {}, self.FULL_MSG)
        self.assertEqual(
            result["sample_info_path"],
            "/media/backup_disk/agoutic_root/test-edgepy/sample_info.csv",
        )

    def test_load_data_extracts_group_column(self):
        result = _validate_edgepython_params("load_data", {}, self.FULL_MSG)
        self.assertEqual(result["group_column"], "condition")

    def test_load_data_does_not_overwrite_existing(self):
        result = _validate_edgepython_params(
            "load_data",
            {"counts_path": "/already/set.csv"},
            self.FULL_MSG,
        )
        self.assertEqual(result["counts_path"], "/already/set.csv")

    def test_test_contrast_extracts_contrast(self):
        result = _validate_edgepython_params("test_contrast", {}, self.FULL_MSG)
        self.assertEqual(result["contrast"], "treated - control")

    def test_set_design_default_formula(self):
        result = _validate_edgepython_params("set_design", {}, self.FULL_MSG)
        self.assertEqual(result["formula"], "~ 0 + group")

    def test_filter_genes_passthrough(self):
        """Tools without special params should pass through unchanged."""
        result = _validate_edgepython_params("filter_genes", {}, self.FULL_MSG)
        self.assertEqual(result, {})

    def test_counts_and_info_are_different_paths(self):
        result = _validate_edgepython_params("load_data", {}, self.FULL_MSG)
        self.assertNotEqual(result.get("counts_path"), result.get("sample_info_path"))

    def test_generate_plot_extracts_named_dpi(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"plot_type": "volcano"},
            "make a publication volcano plot",
        )
        self.assertEqual(result["dpi"], 600)

    def test_generate_plot_extracts_high_res_dpi(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"plot_type": "volcano"},
            "make a high res volcano plot",
        )
        self.assertEqual(result["dpi"], 900)

    def test_generate_plot_builds_svg_companion_for_explicit_output(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"plot_type": "volcano", "output_path": "/tmp/volcano.png", "dpi": "1200"},
            "volcano plot at 1200 dpi",
        )
        self.assertEqual(result["dpi"], 1200)
        self.assertEqual(result["svg_output_path"], "/tmp/volcano.svg")

    def test_generate_plot_extracts_transcript_label_request(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"plot_type": "volcano"},
            "make a volcano plot with transcript labels on the plot",
        )
        self.assertTrue(result["label_transcripts"])

    def test_generate_plot_normalizes_plot_type_and_df_ref(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"plot_type": "stacked bar", "df": "DF2"},
            "make a stacked bar chart",
        )
        self.assertEqual(result["plot_type"], "stacked_bar")
        self.assertEqual(result["df_id"], 2)

    def test_generate_plot_infers_plot_type_from_text_when_missing(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"df": "DF3"},
            "make a PCA plot of DF3",
        )
        self.assertEqual(result["plot_type"], "pca")
        self.assertEqual(result["df_id"], 3)

    def test_generate_plot_promotes_bar_to_stacked_bar_from_text(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"plot_type": "bar", "df": "DF2"},
            "make a stacked bar chart of DF2",
        )
        self.assertEqual(result["plot_type"], "stacked_bar")
        self.assertEqual(result["mode"], "stack")

    def test_generate_plot_extracts_percent_bar_mode(self):
        result = _validate_edgepython_params(
            "generate_plot",
            {"plot_type": "bar", "df": "DF2"},
            "make a normalized stacked bar chart of DF2",
        )
        self.assertEqual(result["plot_type"], "stacked_bar")
        self.assertEqual(result["mode"], "percent")


class TestPlotRouting(unittest.TestCase):
    def test_simple_bar_stays_declarative(self):
        route = infer_plot_route("bar", user_message="bar chart of read counts per sample")
        self.assertEqual(route, "declarative")

    def test_publication_deg_bar_uses_edgepython(self):
        route = infer_plot_route(
            "bar",
            user_message="bar chart of DEG counts per contrast for the dissertation",
        )
        self.assertEqual(route, "edgepython")

    def test_heatmap_uses_edgepython(self):
        route = infer_plot_route("heatmap", user_message="heatmap of the top genes")
        self.assertEqual(route, "edgepython")


class TestEdgepythonToolSchemas(unittest.TestCase):
    """Verify tool_schemas.py uses JSON Schema format compatible with validate_against_schema."""

    def test_all_schemas_have_properties_key(self):
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        for tool_name, schema in TOOL_SCHEMAS.items():
            params_obj = schema.get("parameters", {})
            self.assertIn("properties", params_obj,
                          f"{tool_name}: missing 'properties' key in parameters")
            self.assertIn("required", params_obj,
                          f"{tool_name}: missing 'required' key in parameters")

    def test_load_data_params_not_stripped(self):
        """Ensure validate_against_schema doesn't strip edgepython params."""
        from edgepython_mcp.tool_schemas import TOOL_SCHEMAS
        schema = TOOL_SCHEMAS["load_data"]
        params_obj = schema.get("parameters", {})
        properties = params_obj.get("properties", {})
        known_keys = set(properties.keys())
        test_params = {"counts_path": "/test.csv", "sample_info_path": "/meta.csv", "group_column": "cond"}
        cleaned = {k: v for k, v in test_params.items() if k in known_keys}
        self.assertEqual(cleaned, test_params, "Schema format causes params to be stripped!")


class TestMistralToolCallConversion(unittest.TestCase):
    """Verify [TOOL_CALLS]DATA_CALL[ARGS]{json} is converted to [[DATA_CALL:...]]."""

    def _convert(self, text):
        import json
        import re
        _mistral_tc_pattern = r'\[TOOL_CALLS\]\s*DATA_CALL\s*\[ARGS\]\s*(\{[^}]+\})'
        def _convert_mistral_tool_call(m):
            try:
                payload = json.loads(m.group(1))
                source = payload.pop("service", payload.pop("consortium", "edgepython"))
                tool = payload.pop("tool", "")
                if not tool:
                    return m.group(0)
                source_type = "consortium" if source == "encode" else "service"
                param_str = ", ".join(f"{k}={v}" for k, v in payload.items())
                tag = f"[[DATA_CALL: {source_type}={source}, tool={tool}"
                if param_str:
                    tag += f", {param_str}"
                tag += "]]"
                return tag
            except (json.JSONDecodeError, TypeError):
                return m.group(0)
        return re.sub(_mistral_tc_pattern, _convert_mistral_tool_call, text)

    def test_edgepython_load_data(self):
        text = '[TOOL_CALLS]DATA_CALL[ARGS]{"service": "edgepython", "tool": "load_data", "counts_path": "/data/counts.csv", "sample_info_path": "/data/info.csv", "group_column": "condition"}'
        result = self._convert(text)
        self.assertIn("[[DATA_CALL: service=edgepython, tool=load_data", result)
        self.assertIn("counts_path=/data/counts.csv", result)
        self.assertIn("sample_info_path=/data/info.csv", result)
        self.assertIn("group_column=condition", result)

    def test_encode_consortium(self):
        text = '[TOOL_CALLS]DATA_CALL[ARGS]{"consortium": "encode", "tool": "search_by_biosample", "search_term": "K562"}'
        result = self._convert(text)
        self.assertIn("[[DATA_CALL: consortium=encode, tool=search_by_biosample", result)
        self.assertIn("search_term=K562", result)

    def test_no_match_passthrough(self):
        text = "This is normal text without tool calls."
        result = self._convert(text)
        self.assertEqual(result, text)


class TestEdgepythonOutputPathInjection(unittest.TestCase):
    """Verify that edgepython output tools get redirected to the project folder."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_dir = f"{self._tmpdir.name}/project"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _inject(self, tool_name, params, project_dir):
        """Use the real output path injection logic from cortex.tool_dispatch."""
        from pathlib import Path
        injected = dict(params)
        Path(project_dir).mkdir(parents=True, exist_ok=True)
        _inject_edgepython_output_path(tool_name, injected, Path(project_dir))
        return injected

    def test_volcano_plot_redirected(self):
        params = {"plot_type": "volcano"}
        result = self._inject("generate_plot", params, self.project_dir)
        self.assertEqual(result["output_path"], f"{self.project_dir}/de_results/volcano.png")
        self.assertEqual(result["svg_output_path"], f"{self.project_dir}/de_results/volcano.svg")

    def test_md_plot_redirected(self):
        params = {"plot_type": "md", "result_name": "treated-control"}
        result = self._inject("generate_plot", params, self.project_dir)
        self.assertEqual(result["output_path"], f"{self.project_dir}/de_results/md_treated-control.png")
        self.assertEqual(result["svg_output_path"], f"{self.project_dir}/de_results/md_treated-control.svg")

    def test_named_dpi_is_normalized(self):
        params = {"plot_type": "volcano", "dpi": "publication"}
        result = self._inject("generate_plot", params, self.project_dir)
        self.assertEqual(result["dpi"], 600)

    def test_save_results_redirected(self):
        params = {"output_path": "/media/test-edgepy/de_results.csv", "format": "csv"}
        result = self._inject("save_results", params, self.project_dir)
        self.assertEqual(result["output_path"], f"{self.project_dir}/de_results/de_results.csv")

    def test_save_results_default_name(self):
        params = {"format": "tsv"}
        result = self._inject("save_results", params, self.project_dir)
        self.assertEqual(result["output_path"], f"{self.project_dir}/de_results/de_results.tsv")

    def test_load_data_not_affected(self):
        params = {"counts_path": "/media/test/counts.csv"}
        result = self._inject("load_data", params, self.project_dir)
        self.assertNotIn("output_path", result)
        self.assertEqual(result["counts_path"], "/media/test/counts.csv")

    def test_plot_with_explicit_output_path_not_overridden(self):
        params = {"plot_type": "volcano", "output_path": "/custom/path/volcano.png"}
        result = self._inject("generate_plot", params, self.project_dir)
        self.assertEqual(result["output_path"], "/custom/path/volcano.png")
        self.assertEqual(result["svg_output_path"], "/custom/path/volcano.svg")


class TestEdgepythonPlotSourceResolution(unittest.TestCase):
    def test_resolve_edgepython_plot_source_rehydrates_truncated_dataframe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "counts.csv"
            csv_path.write_text("sample\tcount\nA\t1\nB\t2\n", encoding="utf-8")

            block = _DummyBlock({
                "_dataframes": {
                    "counts.csv": {
                        "columns": ["sample", "count"],
                        "data": [{"sample": "A", "count": 1}],
                        "row_count": 2,
                        "metadata": {
                            "df_id": 1,
                            "visible": True,
                            "label": "counts.csv",
                            "row_count": 2,
                            "is_truncated": True,
                        },
                    }
                },
                "_provenance": [
                    {
                        "success": True,
                        "source": "analyzer",
                        "tool": "parse_csv_file",
                        "params": {"file_path": "counts.csv", "work_dir": tmpdir},
                    }
                ],
            })
            params = {"plot_type": "bar", "df": "DF1"}

            _resolve_edgepython_plot_source_params(
                "generate_plot",
                params,
                history_blocks=[block],
                project_dir_path=Path(tmpdir),
            )

            self.assertEqual(Path(params["input_path"]).resolve(), csv_path.resolve())
            self.assertEqual(params["df_label"], "counts.csv")
            self.assertNotIn("df", params)
            self.assertNotIn("df_id", params)

    def test_resolve_edgepython_plot_source_errors_when_full_data_unavailable(self):
        block = _DummyBlock({
            "_dataframes": {
                "preview_only.csv": {
                    "columns": ["sample", "count"],
                    "data": [{"sample": "A", "count": 1}],
                    "row_count": 2,
                    "metadata": {
                        "df_id": 1,
                        "visible": True,
                        "label": "preview_only.csv",
                        "row_count": 2,
                        "is_truncated": True,
                    },
                }
            }
        })
        params = {"plot_type": "bar", "df": "DF1"}

        _resolve_edgepython_plot_source_params(
            "generate_plot",
            params,
            history_blocks=[block],
            project_dir_path=None,
        )

        self.assertIn("__routing_error__", params)


if __name__ == "__main__":
    unittest.main()
