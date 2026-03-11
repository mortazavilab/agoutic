"""Tests for DE skill routing and parameter extraction."""
import unittest
from cortex.llm_validators import _auto_detect_skill_switch
from cortex.data_call_generator import _validate_edgepython_params


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

    def _inject(self, tool_name, params, project_dir):
        """Simulate the output path injection logic from cortex/app.py."""
        from pathlib import Path
        source_key = "edgepython"
        _project_dir_path = Path(project_dir)
        if source_key == "edgepython" and _project_dir_path:
            _ep_output_dir = _project_dir_path / "de_results"
            if tool_name == "generate_plot" and not params.get("output_path"):
                _plot_type = params.get("plot_type", "plot")
                _result_name = params.get("result_name", "")
                _suffix = f"_{_result_name}" if _result_name else ""
                params["output_path"] = str(_ep_output_dir / f"{_plot_type}{_suffix}.png")
            elif tool_name == "save_results":
                _fmt = params.get("format", "csv")
                _orig = params.get("output_path", "")
                _fname = Path(_orig).name if _orig else f"de_results.{_fmt}"
                params["output_path"] = str(_ep_output_dir / _fname)
        return params

    def test_volcano_plot_redirected(self):
        params = {"plot_type": "volcano"}
        result = self._inject("generate_plot", params, "/data/users/eli/my-project")
        self.assertEqual(result["output_path"], "/data/users/eli/my-project/de_results/volcano.png")

    def test_md_plot_redirected(self):
        params = {"plot_type": "md", "result_name": "treated-control"}
        result = self._inject("generate_plot", params, "/data/users/eli/my-project")
        self.assertEqual(result["output_path"], "/data/users/eli/my-project/de_results/md_treated-control.png")

    def test_save_results_redirected(self):
        params = {"output_path": "/media/test-edgepy/de_results.csv", "format": "csv"}
        result = self._inject("save_results", params, "/data/users/eli/my-project")
        self.assertEqual(result["output_path"], "/data/users/eli/my-project/de_results/de_results.csv")

    def test_save_results_default_name(self):
        params = {"format": "tsv"}
        result = self._inject("save_results", params, "/data/users/eli/my-project")
        self.assertEqual(result["output_path"], "/data/users/eli/my-project/de_results/de_results.tsv")

    def test_load_data_not_affected(self):
        params = {"counts_path": "/media/test/counts.csv"}
        result = self._inject("load_data", params, "/data/users/eli/my-project")
        self.assertNotIn("output_path", result)
        self.assertEqual(result["counts_path"], "/media/test/counts.csv")

    def test_plot_with_explicit_output_path_not_overridden(self):
        params = {"plot_type": "volcano", "output_path": "/custom/path/volcano.png"}
        result = self._inject("generate_plot", params, "/data/users/eli/my-project")
        self.assertEqual(result["output_path"], "/custom/path/volcano.png")


if __name__ == "__main__":
    unittest.main()
