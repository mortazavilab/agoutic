"""Focused helper coverage for edgePython plot labeling and legend layout."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from edgepython_mcp import edgepython_server as server


class TestEdgePythonPlotHelpers(unittest.TestCase):
    def _write_table(self, tmpdir: str, name: str, frame: pd.DataFrame) -> Path:
        path = Path(tmpdir) / name
        sep = "," if path.suffix == ".csv" else "\t"
        frame.to_csv(path, sep=sep, index=False)
        return path

    def test_format_feature_label_uses_gene_name_by_default(self):
        row = pd.Series({"Symbol": "TP53", "TranscriptID": "ENST00000269305"}, name="ENST00000269305")
        self.assertEqual(server._format_feature_label(row), "TP53")

    def test_format_feature_label_can_include_transcript(self):
        row = pd.Series({"Symbol": "TP53", "TranscriptID": "ENST00000269305"}, name="ENST00000269305")
        self.assertEqual(
            server._format_feature_label(row, include_transcript=True),
            "TP53\nENST00000269305",
        )

    def test_format_feature_label_uses_row_index_for_transcript_level_rows(self):
        original_feature_level = server._state.get("feature_level")
        try:
            server._state["feature_level"] = "transcript"
            row = pd.Series({"Symbol": "TP53"}, name="ENST00000269305")
            self.assertEqual(
                server._format_feature_label(row, include_transcript=True),
                "TP53\nENST00000269305",
            )
        finally:
            server._state["feature_level"] = original_feature_level

    def test_place_legend_outside_anchors_beyond_axes(self):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1], label="Example")
        legend = server._place_legend_outside(ax)
        try:
            self.assertIsNotNone(legend)
            self.assertGreater(legend.get_bbox_to_anchor()._bbox.x0, 1.0)
        finally:
            plt.close(fig)

    def test_infer_heatmap_mode_prefers_correlation_for_square_matching_labels(self):
        matrix = pd.DataFrame(
            [[1.0, 0.4], [0.4, 1.0]],
            index=["sampleA", "sampleB"],
            columns=["sampleA", "sampleB"],
        )
        self.assertEqual(server._infer_heatmap_mode(matrix), "correlation")

    def test_infer_heatmap_mode_prefers_raw_for_rectangular_tables(self):
        matrix = pd.DataFrame(
            [[5.0, 7.0, 9.0], [4.0, 6.0, 8.0]],
            index=["gene1", "gene2"],
            columns=["sampleA", "sampleB", "sampleC"],
        )
        self.assertEqual(server._infer_heatmap_mode(matrix), "raw")

    def test_infer_bar_stat_columns_finds_error_and_n_fields(self):
        frame = pd.DataFrame({"condition": ["A"], "mean_expr": [2.4], "sem": [0.3], "n": [4]})
        self.assertEqual(server._infer_bar_stat_columns(frame, y_column="mean_expr"), ("sem", "n"))

    def test_generate_plot_can_render_pca_from_input_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            table_path = self._write_table(
                tmpdir,
                "pca_input.tsv",
                pd.DataFrame(
                    {
                        "sample": ["S1", "S2", "S3", "S4"],
                        "group": ["ctrl", "ctrl", "treated", "treated"],
                        "geneA": [1.0, 1.2, 4.8, 5.1],
                        "geneB": [2.0, 2.1, 5.9, 6.2],
                        "geneC": [1.5, 1.4, 5.2, 5.4],
                    }
                ),
            )
            output_path = Path(tmpdir) / "pca.png"

            result = server.generate_plot(
                plot_type="pca",
                input_path=str(table_path),
                output_path=str(output_path),
                color="group",
            )

            self.assertIn(str(output_path), result)
            self.assertTrue(output_path.exists())
            self.assertTrue(output_path.with_suffix(".svg").exists())

    def test_generate_plot_can_render_heatmap_from_input_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            table_path = self._write_table(
                tmpdir,
                "heatmap_input.tsv",
                pd.DataFrame(
                    {
                        "gene": ["G1", "G2", "G3"],
                        "sampleA": [10, 4, 8],
                        "sampleB": [7, 3, 5],
                        "sampleC": [12, 6, 9],
                    }
                ),
            )
            output_path = Path(tmpdir) / "heatmap.png"

            result = server.generate_plot(
                plot_type="heatmap",
                input_path=str(table_path),
                output_path=str(output_path),
            )

            self.assertIn(str(output_path), result)
            self.assertTrue(output_path.exists())
            self.assertTrue(output_path.with_suffix(".svg").exists())


    def test_generate_plot_can_render_stacked_bar_from_wide_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            table_path = self._write_table(
                tmpdir,
                "stacked_bar.tsv",
                pd.DataFrame(
                    {
                        "modification": ["KNOWN", "NIC"],
                        "sampleA": [10, 5],
                        "sampleB": [12, 6],
                        "sampleC": [11, 4],
                    }
                ),
            )
            output_path = Path(tmpdir) / "stacked_bar.png"

            result = server.generate_plot(
                plot_type="stacked_bar",
                input_path=str(table_path),
                output_path=str(output_path),
                x="modification",
            )

            self.assertIn(str(output_path), result)
            self.assertTrue(output_path.exists())
            self.assertTrue(output_path.with_suffix(".svg").exists())

    def test_generate_plot_can_render_bar_with_errorbars_and_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            table_path = self._write_table(
                tmpdir,
                "bar_input.csv",
                pd.DataFrame(
                    {
                        "condition": ["ctrl", "treated"],
                        "mean_expr": [2.4, 4.9],
                        "sem": [0.3, 0.4],
                        "n": [4, 4],
                    }
                ),
            )
            output_path = Path(tmpdir) / "bar.png"

            result = server.generate_plot(
                plot_type="bar",
                input_path=str(table_path),
                output_path=str(output_path),
                x="condition",
                y="mean_expr",
            )

            self.assertIn(str(output_path), result)
            self.assertTrue(output_path.exists())
            self.assertTrue(output_path.with_suffix(".svg").exists())