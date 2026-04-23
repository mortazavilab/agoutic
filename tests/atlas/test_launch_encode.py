"""Tests for atlas/launch_encode.py."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import atlas.launch_encode as launch_encode


def _stub_module(*, server, schema_endpoint):
    module = ModuleType("atlas.mcp_server")
    module.server = server
    module._tools_schema_endpoint = schema_endpoint
    return module


class TestMain:
    def test_missing_encodelib_path_exits(self, tmp_path, monkeypatch):
        missing_path = tmp_path / "missing-encodelib"
        monkeypatch.setenv("ENCODELIB_PATH", str(missing_path))
        monkeypatch.setattr(sys, "argv", ["launch_encode.py"])

        with patch.object(launch_encode.logger, "error") as log_error:
            with pytest.raises(SystemExit) as exc:
                launch_encode.main()

        assert exc.value.code == 1
        assert log_error.call_args_list[0].args[0] == "ENCODELIB not found"
        assert log_error.call_args_list[0].kwargs["path"] == str(missing_path)

    def test_import_failure_exits(self, tmp_path, monkeypatch):
        encodelib_path = tmp_path / "ENCODELIB"
        encodelib_path.mkdir()
        monkeypatch.setenv("ENCODELIB_PATH", str(encodelib_path))
        monkeypatch.setattr(sys, "argv", ["launch_encode.py"])

        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "atlas.mcp_server":
                raise ImportError("mock import failure")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import), \
             patch.object(launch_encode.logger, "error") as log_error:
            with pytest.raises(SystemExit) as exc:
                launch_encode.main()

        assert exc.value.code == 1
        assert log_error.call_args_list[0].args[0] == "Failed to import atlas.mcp_server"
        assert log_error.call_args_list[0].kwargs["error"] == "mock import failure"

    def test_uses_http_app_when_available_and_registers_schema_route(self, tmp_path, monkeypatch):
        encodelib_path = tmp_path / "ENCODELIB"
        encodelib_path.mkdir()
        monkeypatch.setenv("ENCODELIB_PATH", str(encodelib_path))
        monkeypatch.setenv("ENCODE_MCP_PORT", "8123")
        monkeypatch.setattr(sys, "argv", ["launch_encode.py", "--host", "127.0.0.1"])

        http_app = SimpleNamespace(routes=[])
        server = SimpleNamespace(http_app=MagicMock(return_value=http_app))
        schema_endpoint = object()
        fake_uvicorn = SimpleNamespace(run=MagicMock())

        with patch.dict(
            sys.modules,
            {
                "atlas.mcp_server": _stub_module(server=server, schema_endpoint=schema_endpoint),
                "uvicorn": fake_uvicorn,
            },
        ), patch.object(launch_encode.logger, "info") as log_info:
            launch_encode.main()

        server.http_app.assert_called_once_with()
        assert http_app.routes[0].path == "/tools/schema"
        assert http_app.routes[0].endpoint is schema_endpoint
        fake_uvicorn.run.assert_called_once_with(
            http_app,
            host="127.0.0.1",
            port=8123,
            log_level="info",
        )
        assert sys.path[0] == str(encodelib_path)
        assert log_info.call_args_list[0].args[0] == "Imported extended MCP server from atlas.mcp_server"

    def test_falls_back_to_server_as_asgi_app(self, tmp_path, monkeypatch):
        encodelib_path = tmp_path / "ENCODELIB"
        encodelib_path.mkdir()
        monkeypatch.setenv("ENCODELIB_PATH", str(encodelib_path))
        monkeypatch.setattr(sys, "argv", ["launch_encode.py", "--port", "8124"])

        app = SimpleNamespace(routes=[])
        schema_endpoint = object()
        fake_uvicorn = SimpleNamespace(run=MagicMock())

        with patch.dict(
            sys.modules,
            {
                "atlas.mcp_server": _stub_module(server=app, schema_endpoint=schema_endpoint),
                "uvicorn": fake_uvicorn,
            },
        ):
            launch_encode.main()

        assert app.routes[0].path == "/tools/schema"
        assert app.routes[0].endpoint is schema_endpoint
        fake_uvicorn.run.assert_called_once_with(
            app,
            host="0.0.0.0",
            port=8124,
            log_level="info",
        )
