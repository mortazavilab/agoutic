"""
Authentication helper for Streamlit UI.

Provides:
- Cookie extraction from Streamlit's private API
- Auth gate wrapper for pages
- User info display
"""

import streamlit as st
import requests
from typing import Optional, Dict
from urllib.parse import urlparse


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
    session_token = get_session_cookie()
    if not session_token:
        return None, None, False

    try:
        response = _buffer_and_close_response(
            requests.get(
                f"{api_url}/auth/me",
                cookies={"session": session_token},
                timeout=5,
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
        
        login_base = _resolve_browser_login_base(api_url)
        login_url = f"{login_base}/auth/login"
        
        # Debug: show the API URL being used
        st.caption(f"Debug: Using API URL: {api_url}")
        if login_base != api_url.rstrip("/"):
            st.caption(f"Debug: Browser login URL: {login_base}")
        
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
        session_token = get_session_cookie()
        if session_token:
            try:
                _buffer_and_close_response(
                    requests.post(
                        f"{api_url}/auth/logout",
                        cookies={"session": session_token},
                        timeout=5,
                    )
                )
            except Exception:
                pass  # Ignore errors
        
        st.success("Logged out successfully")
        login_base = _resolve_browser_login_base(api_url)
        st.markdown(f"[Click here to log in again]({login_base}/auth/login)")
        st.stop()


def make_authenticated_request(method: str, url: str, **kwargs) -> requests.Response:
    """
    Make an authenticated HTTP request using the session cookie.
    
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
    session_token = get_session_cookie()
    
    if "cookies" not in kwargs:
        kwargs["cookies"] = {}
    
    if session_token:
        kwargs["cookies"]["session"] = session_token

    response = requests.request(method, url, **kwargs)
    # Eagerly buffer the body so we can close the underlying socket before
    # handing the response object back to rerun-heavy Streamlit callers.
    return _buffer_and_close_response(response)
