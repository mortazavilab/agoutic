from ui.appui_sidebar import _fetch_model_options, _resolve_requested_project_id


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