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


def get_current_user(api_url: str) -> Optional[Dict]:
    """
    Get the current user's information from the API.
    
    Args:
        api_url: Base URL for the API (e.g., "http://localhost:8000")
    
    Returns:
        dict | None: User info dict, or None if not authenticated
    """
    session_token = get_session_cookie()
    
    if not session_token:
        return None
    
    try:
        response = requests.get(
            f"{api_url}/auth/me",
            cookies={"session": session_token},
            timeout=5
        )
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 403:
            # User is authenticated but not active
            return response.json().get("user")
        else:
            return None
    except Exception as e:
        st.error(f"Failed to get user info: {e}")
        return None


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
    user = get_current_user(api_url)
    
    if not user:
        st.error("🔒 You are not logged in")
        st.info("Click the button below to log in with your Google account")
        
        login_url = f"{api_url}/auth/login"
        
        # Debug: show the API URL being used
        st.caption(f"Debug: Using API URL: {api_url}")
        
        # Use st.link_button for a more prominent clickable button
        st.link_button("🔐 Log in with Google", login_url, use_container_width=True)
        
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
                requests.post(
                    f"{api_url}/auth/logout",
                    cookies={"session": session_token},
                    timeout=5
                )
            except Exception:
                pass  # Ignore errors
        
        st.success("Logged out successfully")
        st.markdown(f"[Click here to log in again]({api_url}/auth/login)")
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
    
    return requests.request(method, url, **kwargs)
