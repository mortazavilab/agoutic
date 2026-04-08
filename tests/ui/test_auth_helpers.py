"""Tests for ui/auth.py — server-unavailable vs logged-out distinction."""

from unittest.mock import patch, MagicMock
import requests.exceptions

import pytest


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
