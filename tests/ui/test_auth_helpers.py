"""Tests for ui/auth.py — server-unavailable vs logged-out distinction."""

from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import requests.exceptions

import pytest


class TestResolveBrowserLoginBase:
    def test_remote_api_url_is_left_alone(self):
        from ui.auth import _resolve_browser_login_base

        assert _resolve_browser_login_base("http://dhcp-7-223.bio.uci.edu:8000") == "http://dhcp-7-223.bio.uci.edu:8000"

    def test_local_api_url_uses_browser_host(self):
        from ui.auth import _resolve_browser_login_base
        import ui.auth as auth_mod

        mock_st = MagicMock()
        mock_st.context.headers = {"Host": "dhcp-7-223.bio.uci.edu:8501"}
        with patch.object(auth_mod, "st", mock_st):
            assert _resolve_browser_login_base("http://127.0.0.1:8000") == "http://dhcp-7-223.bio.uci.edu:8000"


class TestGetCurrentUserState:
    def test_timeout_is_server_unavailable(self):
        from ui.auth import get_current_user_state
        with patch("ui.auth.get_session_cookie", return_value="valid-token"), \
             patch("ui.auth.requests.get", side_effect=requests.exceptions.ReadTimeout("read timed out")):
            user, error, unavailable = get_current_user_state("http://localhost:8000")
        assert user is None
        assert unavailable is True
        assert "timed out" in error

    def test_connection_error_is_server_unavailable(self):
        from ui.auth import get_current_user_state
        with patch("ui.auth.get_session_cookie", return_value="valid-token"), \
             patch("ui.auth.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            user, error, unavailable = get_current_user_state("http://localhost:8000")
        assert user is None
        assert unavailable is True

    def test_success_returns_user(self):
        from ui.auth import get_current_user_state
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"username": "alice", "is_active": True}
        with patch("ui.auth.get_session_cookie", return_value="valid-token"), \
             patch("ui.auth.requests.get", return_value=mock_resp):
            user, error, unavailable = get_current_user_state("http://localhost:8000")
        assert user == {"username": "alice", "is_active": True}
        assert unavailable is False
        assert error is None

    def test_401_returns_no_user_not_unavailable(self):
        from ui.auth import get_current_user_state
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("ui.auth.get_session_cookie", return_value="valid-token"), \
             patch("ui.auth.requests.get", return_value=mock_resp):
            user, error, unavailable = get_current_user_state("http://localhost:8000")
        assert user is None
        assert unavailable is False

    def test_no_cookie_returns_no_user(self):
        from ui.auth import get_current_user_state
        with patch("ui.auth.get_session_cookie", return_value=None):
            user, error, unavailable = get_current_user_state("http://localhost:8000")
        assert user is None
        assert unavailable is False

    def test_bearer_token_is_used_when_present(self):
        from ui.auth import get_current_user_state
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"username": "alice", "is_active": True}
        with patch("ui.auth.get_bearer_token", return_value="bearer-123"), \
             patch("ui.auth.requests.get", return_value=mock_resp) as req_get:
            user, error, unavailable = get_current_user_state("http://localhost:8000")
        assert user == {"username": "alice", "is_active": True}
        assert unavailable is False
        assert error is None
        req_get.assert_called_once_with(
            "http://localhost:8000/auth/me",
            timeout=5,
            headers={"Authorization": "Bearer bearer-123"},
        )


class TestRequireAuth:
    def test_server_unavailable_shows_retry_not_login(self):
        from ui.auth import require_auth
        import ui.auth as auth_mod
        mock_st = MagicMock()
        mock_st.stop.side_effect = SystemExit
        with patch.object(auth_mod, "st", mock_st), \
             patch.object(
                 auth_mod, "get_current_user_state",
                 return_value=(None, "read timed out", True),
             ):
            with pytest.raises(SystemExit):
                require_auth("http://localhost:8000")

        error_calls = [str(c) for c in mock_st.error.call_args_list]
        assert any("not responding" in c for c in error_calls)
        assert not any("not logged in" in c.lower() for c in error_calls)

    def test_actual_logout_shows_login(self):
        from ui.auth import require_auth
        import ui.auth as auth_mod
        mock_st = MagicMock()
        mock_st.stop.side_effect = SystemExit
        with patch.object(auth_mod, "st", mock_st), \
             patch.object(
                 auth_mod, "get_current_user_state",
                 return_value=(None, None, False),
             ):
            with pytest.raises(SystemExit):
                require_auth("http://localhost:8000")

        error_calls = [str(c) for c in mock_st.error.call_args_list]
        assert any("not logged in" in c.lower() for c in error_calls)

    def test_actual_logout_rewrites_local_login_link_for_remote_browser(self):
        from ui.auth import require_auth
        import ui.auth as auth_mod

        mock_st = MagicMock()
        mock_st.stop.side_effect = SystemExit
        mock_st.context.headers = {"Host": "dhcp-7-223.bio.uci.edu:8501"}

        with patch.object(auth_mod, "st", mock_st), \
             patch.object(
                 auth_mod, "get_current_user_state",
                 return_value=(None, None, False),
             ):
            with pytest.raises(SystemExit):
                require_auth("http://127.0.0.1:8000")

        mock_st.link_button.assert_called_once_with(
            "🔐 Log in with Google",
            "http://dhcp-7-223.bio.uci.edu:8000/auth/login",
            width="stretch",
        )

    def test_local_browser_uses_local_client_login_link(self):
        from ui.auth import require_auth
        import ui.auth as auth_mod

        mock_st = MagicMock()
        mock_st.stop.side_effect = SystemExit
        mock_st.context.headers = {"Host": "localhost:8501"}
        mock_st.session_state = {}

        with patch.object(auth_mod, "st", mock_st), \
             patch.object(
                 auth_mod, "get_current_user_state",
                 return_value=(None, None, False),
             ):
            with pytest.raises(SystemExit):
                require_auth("http://cortex.example:8000")

        mock_st.link_button.assert_called_once_with(
            "🔐 Log in with Google",
            "http://cortex.example:8000/auth/login?client_mode=local&return_to=http%3A%2F%2Flocalhost%3A8501",
            width="stretch",
        )


class TestLocalAuthBootstrap:
    def test_bootstrap_local_auth_stores_bearer_token(self):
        from ui.auth import _bootstrap_local_bearer_session
        import ui.auth as auth_mod

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "bearer-abc"}

        fake_st = SimpleNamespace(
            query_params={"auth_code": "code-123"},
            session_state={},
        )
        fake_st.rerun = MagicMock(side_effect=SystemExit)

        with patch.object(auth_mod, "st", fake_st), \
             patch("ui.auth.requests.post", return_value=mock_response):
            with pytest.raises(SystemExit):
                _bootstrap_local_bearer_session("http://api.test")

        assert fake_st.session_state["_auth_bearer_token"] == "bearer-abc"
        assert "auth_code" not in fake_st.query_params


class TestMakeAuthenticatedRequest:
    def test_make_authenticated_request_uses_bearer_header(self):
        from ui.auth import make_authenticated_request

        response = MagicMock()
        with patch("ui.auth.get_bearer_token", return_value="bearer-xyz"), \
             patch("ui.auth.requests.request", return_value=response) as request_fn, \
             patch("ui.auth._buffer_and_close_response", side_effect=lambda r: r):
            result = make_authenticated_request("GET", "http://api.test/projects", timeout=5)

        assert result is response
        request_fn.assert_called_once_with(
            "GET",
            "http://api.test/projects",
            timeout=5,
            headers={"Authorization": "Bearer bearer-xyz"},
        )

    def test_make_authenticated_request_uses_supplied_auth_kwargs(self):
        from ui.auth import make_authenticated_request

        response = MagicMock()
        with patch("ui.auth.build_auth_request_kwargs", return_value={"headers": {"Authorization": "Bearer wrong-token"}}), \
             patch("ui.auth.requests.request", return_value=response) as request_fn, \
             patch("ui.auth._buffer_and_close_response", side_effect=lambda r: r):
            result = make_authenticated_request(
                "POST",
                "http://api.test/remote-profiles/p1/test",
                timeout=30,
                auth_kwargs={"headers": {"Authorization": "Bearer captured-token"}},
                json={"local_password": "secret"},
            )

        assert result is response
        request_fn.assert_called_once_with(
            "POST",
            "http://api.test/remote-profiles/p1/test",
            timeout=30,
            headers={"Authorization": "Bearer captured-token"},
            json={"local_password": "secret"},
        )
