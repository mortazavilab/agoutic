import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from analyzer.edgepython_adapter import (
    _build_project_output_path,
    _extract_saved_path,
    call_edgepython_tool,
    relocate_edgepython_artifact,
    reset_edgepython_session,
)
from analyzer.mcp_tools import EDGEPYTHON_PROXY_TOOL_REGISTRY, EDGEPYTHON_PROXY_TOOL_SCHEMAS


class TestEdgePythonArtifactHelpers(unittest.TestCase):
    def test_extract_saved_path(self):
        text = "Volcano plot saved to: /tmp/volcano.png"
        self.assertEqual(_extract_saved_path(text), "/tmp/volcano.png")

    def test_extract_saved_path_no_match(self):
        self.assertIsNone(_extract_saved_path("No output path here"))

    def test_build_project_output_path(self):
        path = _build_project_output_path("/data/users/eli/project-1", "plot.png")
        self.assertEqual(str(path), "/data/users/eli/project-1/de_results/plot.png")

    def test_relocate_edgepython_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "volcano.png"
            source.write_bytes(b"png-bytes")
            project_dir = Path(tmpdir) / "project-a"
            result_text = f"Volcano plot saved to: {source}"

            rewritten, target = relocate_edgepython_artifact(
                result_text,
                project_dir=project_dir,
            )

            self.assertIsNotNone(rewritten)
            self.assertEqual(target, project_dir / "de_results" / "volcano.png")
            self.assertTrue(target.exists())
            self.assertFalse(source.exists())
            self.assertIn(str(target), rewritten)


class TestEdgePythonSessionHelpers(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await reset_edgepython_session("conv-1")

    async def test_call_edgepython_tool_uses_cached_session_client(self):
        fake_client = AsyncMock()
        fake_client.call_tool = AsyncMock(return_value={"ok": True})
        fake_client.disconnect = AsyncMock()

        with patch("analyzer.edgepython_adapter._connect_new_client", new=AsyncMock(return_value=type("S", (), {
            "conversation_id": "conv-1",
            "client": fake_client,
            "created_at": None,
            "last_used_at": None,
        })())):
            result1 = await call_edgepython_tool("describe", conversation_id="conv-1")
            result2 = await call_edgepython_tool("describe", conversation_id="conv-1")

        self.assertEqual(result1, {"ok": True})
        self.assertEqual(result2, {"ok": True})
        self.assertEqual(fake_client.call_tool.await_count, 2)


class TestEdgePythonProxyRegistry(unittest.IsolatedAsyncioTestCase):
    def test_proxy_registry_contains_prefixed_tools(self):
        self.assertIn("edgepython_load_data", EDGEPYTHON_PROXY_TOOL_REGISTRY)
        self.assertIn("edgepython_generate_plot", EDGEPYTHON_PROXY_TOOL_REGISTRY)
        self.assertIn("edgepython_load_data", EDGEPYTHON_PROXY_TOOL_SCHEMAS)
        self.assertIn("conversation_id", EDGEPYTHON_PROXY_TOOL_SCHEMAS["edgepython_load_data"]["parameters"]["properties"])

    async def test_proxy_generate_plot_relocates_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "volcano.png"
            source.write_bytes(b"png-bytes")
            project_dir = Path(tmpdir) / "project-a"
            proxy = EDGEPYTHON_PROXY_TOOL_REGISTRY["edgepython_generate_plot"]

            with patch("analyzer.mcp_tools.call_edgepython_tool", new=AsyncMock(return_value=f"Volcano plot saved to: {source}")):
                result = await proxy(
                    conversation_id="conv-plot",
                    project_dir=str(project_dir),
                    plot_type="volcano",
                )

            self.assertIn(str(project_dir / "de_results" / "volcano.png"), result)
            self.assertTrue((project_dir / "de_results" / "volcano.png").exists())
