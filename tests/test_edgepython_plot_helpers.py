"""Focused helper coverage for edgePython plot labeling and legend layout."""

import unittest

import pandas as pd

from edgepython_mcp import edgepython_server as server


class TestEdgePythonPlotHelpers(unittest.TestCase):
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