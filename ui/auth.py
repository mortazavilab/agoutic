"""
Authentication helper for Streamlit UI.

Provides:
- Cookie extraction from Streamlit's private API
- Local-client bearer bootstrap from Cortex auth exchange
- Auth gate wrapper for pages
- User info display
"""

import streamlit as st
import requests
from typing import Optional, Dict
from urllib.parse import urlparse, urlencode


_LOCAL_API_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}


def _buffer_and_close_response(response: requests.Response) -> requests.Response:
    try:
        _ = response.content
    finally:
        response.close()
    return response


def _resolve_browser_login_base(api_url: str) -> str:
    """Return the API base URL the browser should use for login/logout links.

    Streamlit runs server-side on Watson, so its own default API URL can safely
    point at localhost. Browser clicks are different: remote users cannot follow
    links to 127.0.0.1. When the configured API URL is local-only, rewrite it to
    the current browser host while preserving the backend port.
    """
    parsed = urlparse(api_url)
    if parsed.hostname not in _LOCAL_API_HOSTS:
        return api_url.rstrip("/")

    try:
        headers = getattr(st.context, "headers", None)
        if headers is None:
            return api_url.rstrip("/")

        host = headers.get("X-Forwarded-Host") or headers.get("Host")
        if not host:
            return api_url.rstrip("/")

        public_host = host.split(",", 1)[0].strip().split(":", 1)[0]
        scheme = (headers.get("X-Forwarded-Proto") or parsed.scheme or "http").split(",", 1)[0].strip()
        port = parsed.port or 8000
        return f"{scheme}://{public_host}:{port}"
    except Exception:
        return api_url.rstrip("/")


def _normalized_origin(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _current_browser_origin() -> Optional[str]:
    try:
        headers = getattr(st.context, "headers", None)
        if headers is None:
            return None

        raw_host = headers.get("X-Forwarded-Host") or headers.get("Host")
        if not isinstance(raw_host, str) or not raw_host.strip():
            return None

        host = raw_host.split(",", 1)[0].strip()
        scheme_value = headers.get("X-Forwarded-Proto") or "http"
        scheme = scheme_value.split(",", 1)[0].strip() if isinstance(scheme_value, str) else "http"
        return f"{scheme}://{host}".rstrip("/")
    except Exception:
        return None


def _query_param_value(name: str) -> Optional[str]:
    query_params = getattr(st, "query_params", None)
    if query_params is None or not hasattr(query_params, "get"):
        return None

    raw_value = query_params.get(name)
    if isinstance(raw_value, (list, tuple)):
        raw_value = raw_value[0] if raw_value else None
    if not isinstance(raw_value, str):
        return None

    value = raw_value.strip()
    return value or None


def _clear_query_param(name: str) -> None:
    query_params = getattr(st, "query_params", None)
    if query_params is None:
        return
    try:
        del query_params[name]
    except Exception:
        pass


def get_bearer_token() -> Optional[str]:
    session_state = getattr(st, "session_state", None)
    if session_state is None or not hasattr(session_state, "get"):
        return None

    raw_value = session_state.get("_auth_bearer_token")
    if not isinstance(raw_value, str):
        return None

    token = raw_value.strip()
    return token or None


def build_auth_request_kwargs() -> dict:
    bearer_token = get_bearer_token()
    if bearer_token:
        return {"headers": {"Authorization": f"Bearer {bearer_token}"}}

    session_token = get_session_cookie()
    if session_token:
        return {"cookies": {"session": session_token}}

    return {}


def _merge_auth_request_kwargs(kwargs: dict, auth_kwargs: dict) -> dict:
    merged = dict(kwargs)

    auth_headers = auth_kwargs.get("headers") or {}
    if auth_headers:
        headers = dict(merged.get("headers") or {})
        for key, value in auth_headers.items():
            headers.setdefault(key, value)
        merged["headers"] = headers

    auth_cookies = auth_kwargs.get("cookies") or {}
    if auth_cookies:
        cookies = dict(merged.get("cookies") or {})
        for key, value in auth_cookies.items():
            cookies.setdefault(key, value)
        merged["cookies"] = cookies

    return merged


def _bootstrap_local_bearer_session(api_url: str) -> None:
    auth_code = _query_param_value("auth_code")
    if not auth_code:
        return

    if get_bearer_token():
        _clear_query_param("auth_code")
        return

    try:
        response = _buffer_and_close_response(
            requests.post(
                f"{api_url}/auth/exchange",
                json={"code": auth_code},
                timeout=10,
            )
        )
        if response.status_code == 200:
            payload = response.json() if hasattr(response, "json") else {}
            access_token = str(payload.get("access_token") or "").strip() if isinstance(payload, dict) else ""
            if not access_token:
                raise RuntimeError("Auth exchange succeeded without returning an access token")
            st.session_state["_auth_bearer_token"] = access_token
            st.session_state.pop("_auth_bootstrap_error", None)
            _clear_query_param("auth_code")
            st.rerun()

        detail = response.text
        try:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("detail"):
                detail = str(payload.get("detail"))
        except Exception:
            pass
        st.session_state["_auth_bootstrap_error"] = f"Failed to complete local login: {detail}"
        _clear_query_param("auth_code")
    except Exception as exc:
        st.session_state["_auth_bootstrap_error"] = f"Failed to complete local login: {exc}"
        _clear_query_param("auth_code")


def _build_login_url(api_url: str) -> str:
    login_base = _resolve_browser_login_base(api_url)
    browser_origin = _current_browser_origin()
    if browser_origin:
        browser_host = (urlparse(browser_origin).hostname or "").strip().lower()
        if browser_host in {"localhost", "127.0.0.1"}:
            return f"{login_base}/auth/login?{urlencode({'client_mode': 'local', 'return_to': browser_origin})}"
    return f"{login_base}/auth/login"


def get_session_cookie() -> Optional[str]:
    """
    Extract the session cookie from Streamlit's context headers.
    
    Uses st.context.headers API (Streamlit >= 1.45).
    
    Returns:
        str | None: The session token, or None if not found
    """
    try:
        import streamlit as st
        
        # Use the new st.context.headers API
        headers = st.context.headers
        if headers is None:
            return None
        
        cookies = headers.get("Cookie", "")
        for part in cookies.split(";"):
            if part.strip().startswith("session="):
                return part.strip().split("=", 1)[1]
        
        return None
    except Exception as e:
        st.error(f"Failed to extract session cookie: {e}")
        return None


def get_current_user_state(api_url: str) -> tuple:
    """
    Get the current user's information, distinguishing server errors from
    actual unauthenticated state.

    Returns:
        (user_dict | None, error_message | None, server_unavailable: bool)
    """
    _bootstrap_local_bearer_session(api_url)

    auth_kwargs = build_auth_request_kwargs()
    if not auth_kwargs:
        return None, None, False

    try:
        response = _buffer_and_close_response(
            requests.get(
                f"{api_url}/auth/me",
                timeout=5,
                **auth_kwargs,
            )
        )
        if response.status_code == 200:
            return response.json(), None, False
        elif response.status_code == 403:
            return response.json().get("user"), None, False
        else:
            return None, None, False
    except (requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ReadTimeout) as e:
        return None, str(e), True
    except Exception as e:
        return None, str(e), False


def get_current_user(api_url: str) -> Optional[Dict]:
    """
    Get the current user's information from the API.
    
    Args:
        api_url: Base URL for the API (e.g., "http://localhost:8000")
    
    Returns:
        dict | None: User info dict, or None if not authenticated
    """
    user, error_message, server_unavailable = get_current_user_state(api_url)
    if error_message and not server_unavailable:
        st.error(f"Failed to get user info: {error_message}")
    return user


def require_auth(api_url: str) -> Dict:
    """
    Require authentication for a Streamlit page.
    
    If the user is not authenticated, show a message and stop execution.
    If the user is authenticated but not active, show a pending approval message.
    
    Args:
        api_url: Base URL for the API (e.g., "http://localhost:8000")
    
    Returns:
        dict: User info dict
    
    Example:
        >>> user = require_auth("http://localhost:8000")
        >>> st.write(f"Hello, {user['display_name']}!")
    """
    user, error_message, server_unavailable = get_current_user_state(api_url)

    if server_unavailable:
        st.error("⚠️ AGOUTIC server is not responding")
        st.info(
            "The server may be busy with a heavy workload (e.g. reconcile). "
            "Your session is still valid — please wait and try again."
        )
        if error_message:
            st.caption(f"Detail: {error_message}")
        if st.button("🔄 Retry"):
            st.rerun()
        st.stop()
    
    if not user:
        st.error("🔒 You are not logged in")
        st.info("Click the button below to log in with your Google account")
        login_url = _build_login_url(api_url)
        login_base = _resolve_browser_login_base(api_url)
        bootstrap_error = st.session_state.get("_auth_bootstrap_error")
        if isinstance(bootstrap_error, str) and bootstrap_error.strip():
            st.warning(bootstrap_error)
        
        # Debug: show the API URL being used
        st.caption(f"Debug: Using API URL: {api_url}")
        if login_base != api_url.rstrip("/"):
            st.caption(f"Debug: Browser login URL: {login_base}")
        if "client_mode=local" in login_url:
            st.caption("Debug: Login will return to the local Streamlit client and exchange a bearer session.")
        
        # Use st.link_button for a more prominent clickable button
        st.link_button("🔐 Log in with Google", login_url, width="stretch")
        
        st.caption(f"You will be redirected to Google to authenticate, then back to this page.")
        st.stop()
    
    if not user.get("is_active", False):
        st.warning("⏳ Your account is pending admin approval")
        st.info(f"""
        **Account Details:**
        - Email: {user.get('email')}
        - Status: Waiting for approval
        
        Please contact an administrator to activate your account.
        """)
        st.stop()
    
    return user


def logout_button(api_url: str):
    """
    Display a logout button.
    
    Args:
        api_url: Base URL for the API (e.g., "http://localhost:8000")
    """
    if st.button("Logout"):
        try:
            make_authenticated_request("POST", f"{api_url}/auth/logout", timeout=5)
        except Exception:
            pass  # Ignore errors

        try:
            del st.session_state["_auth_bearer_token"]
        except Exception:
            pass
        st.session_state.pop("_auth_bootstrap_error", None)
        
        st.success("Logged out successfully")
        st.markdown(f"[Click here to log in again]({_build_login_url(api_url)})")
        st.stop()


def make_authenticated_request(method: str, url: str, auth_kwargs: Optional[Dict] = None, **kwargs) -> requests.Response:
    """
    Make an authenticated HTTP request using the current auth transport.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        url: Full URL to request
        **kwargs: Additional arguments to pass to requests
    
    Returns:
        requests.Response: The response object
    
    Example:
        >>> resp = make_authenticated_request("GET", f"{API_URL}/blocks?project_id=abc")
        >>> data = resp.json()
    """
    resolved_auth_kwargs = auth_kwargs if auth_kwargs is not None else build_auth_request_kwargs()
    request_kwargs = _merge_auth_request_kwargs(kwargs, resolved_auth_kwargs)
    response = requests.request(method, url, **request_kwargs)
    # Eagerly buffer the body so we can close the underlying socket before
    # handing the response object back to rerun-heavy Streamlit callers.
    return _buffer_and_close_response(response)
