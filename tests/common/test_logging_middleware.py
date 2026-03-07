"""Tests for common/logging_middleware.py."""

from uuid import UUID
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from common.logging_middleware import RequestLoggingMiddleware


FIXED_REQUEST_ID = "12345678-1234-5678-1234-567812345678"


def build_app():
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/ok")
    async def ok(request: Request):
        return JSONResponse({"request_id": request.state.request_id})

    @app.get("/health")
    async def health():
        return PlainTextResponse("healthy")

    @app.get("/missing")
    async def missing():
        return PlainTextResponse("missing", status_code=404)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    return app


class TestRequestLoggingMiddleware:
    def test_successful_request_sets_request_id_and_logs_info(self):
        app = build_app()

        with patch("common.logging_middleware.uuid.uuid4", return_value=UUID(FIXED_REQUEST_ID)), \
             patch("common.logging_middleware.structlog.contextvars.bind_contextvars") as bind_contextvars, \
             patch("common.logging_middleware.structlog.contextvars.unbind_contextvars") as unbind_contextvars, \
             patch("common.logging_middleware.logger.info") as log_info:
            client = TestClient(app)
            response = client.get("/ok")

        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == FIXED_REQUEST_ID
        assert response.json()["request_id"] == FIXED_REQUEST_ID
        bind_contextvars.assert_called_once_with(request_id=FIXED_REQUEST_ID)
        unbind_contextvars.assert_called_once_with("request_id")
        log_info.assert_called_once()
        _, kwargs = log_info.call_args
        assert kwargs["method"] == "GET"
        assert kwargs["path"] == "/ok"
        assert kwargs["status"] == 200
        assert kwargs["client"] == "testclient"
        assert kwargs["duration_ms"] >= 0

    def test_quiet_paths_log_at_debug(self):
        app = build_app()

        with patch("common.logging_middleware.uuid.uuid4", return_value=UUID(FIXED_REQUEST_ID)), \
             patch("common.logging_middleware.logger.debug") as log_debug, \
             patch("common.logging_middleware.logger.info") as log_info:
            client = TestClient(app)
            response = client.get("/health")

        assert response.status_code == 200
        log_debug.assert_called_once()
        log_info.assert_not_called()

    def test_client_errors_log_at_warning(self):
        app = build_app()

        with patch("common.logging_middleware.uuid.uuid4", return_value=UUID(FIXED_REQUEST_ID)), \
             patch("common.logging_middleware.logger.warning") as log_warning, \
             patch("common.logging_middleware.logger.error") as log_error:
            client = TestClient(app)
            response = client.get("/missing")

        assert response.status_code == 404
        log_warning.assert_called_once()
        _, kwargs = log_warning.call_args
        assert kwargs["path"] == "/missing"
        assert kwargs["status"] == 404
        log_error.assert_not_called()

    def test_server_errors_log_at_error_and_unbind_context(self):
        app = build_app()

        with patch("common.logging_middleware.uuid.uuid4", return_value=UUID(FIXED_REQUEST_ID)), \
             patch("common.logging_middleware.structlog.contextvars.unbind_contextvars") as unbind_contextvars, \
             patch("common.logging_middleware.logger.error") as log_error:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/boom")

        assert response.status_code == 500
        log_error.assert_called_once()
        _, kwargs = log_error.call_args
        assert kwargs["path"] == "/boom"
        assert kwargs["status"] == 500
        unbind_contextvars.assert_called_once_with("request_id")
