from types import SimpleNamespace

import ui.appui_sidebar as appui_sidebar
from ui.appui_sidebar import (
    _fetch_model_options,
    _render_token_usage_chart,
    _resolve_requested_project_id,
)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_model_options_reads_backend_payload():
    def request_fn(method, url, **kwargs):
        assert method == "GET"
        assert url == "http://api.test/config/llm-models"
        return _Response(
            200,
            {
                "models": [
                    {"key": "default", "model": "gemma4:31b"},
                    {"key": "fast", "model": "devstral-small-2:latest"},
                    {"key": "smart", "model": "devstral-2:latest"},
                ]
            },
        )

    _fetch_model_options.clear()
    assert _fetch_model_options("http://api.test", request_fn) == ["default", "fast", "smart"]


def test_fetch_model_options_falls_back_to_default_on_error():
    def request_fn(method, url, **kwargs):
        raise RuntimeError("boom")

    _fetch_model_options.clear()
    assert _fetch_model_options("http://api.test", request_fn) == ["default"]


def test_resolve_requested_project_id_accepts_existing_project():
    resolved, is_valid = _resolve_requested_project_id(
        "proj-2",
        "proj-1",
        [{"id": "proj-1"}, {"id": "proj-2"}],
    )

    assert resolved == "proj-2"
    assert is_valid is True


def test_resolve_requested_project_id_rejects_unknown_project():
    resolved, is_valid = _resolve_requested_project_id(
        "test703",
        "proj-1",
        [{"id": "proj-1"}, {"id": "proj-2"}],
    )

    assert resolved == "proj-1"
    assert is_valid is False


def test_render_token_usage_chart_falls_back_when_streamlit_chart_backend_errors(monkeypatch):
    fake_st = SimpleNamespace(
        line_chart=lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("pyarrow/brotli import failed")
        ),
        caption_calls=[],
    )

    def _capture_caption(message):
        fake_st.caption_calls.append(message)

    fake_st.caption = _capture_caption
    monkeypatch.setattr(appui_sidebar, "st", fake_st)

    _render_token_usage_chart(
        [
            {"date": "2026-07-05", "total_tokens": 100},
            {"date": "2026-07-06", "total_tokens": 200},
        ]
    )

    assert fake_st.caption_calls == ["Token history chart unavailable in this environment."]