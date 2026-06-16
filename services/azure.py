"""Azure AD 서비스 주체 토큰 발급 (Power BI REST API용)."""
import time

import msal

from config import TENANT_ID, CLIENT_ID, CLIENT_SECRET
from errors import AppError

_token_cache = {"access_token": None, "expires_at": 0}


def get_access_token() -> str:
    """서비스 주체(Client Credentials)로 Azure AD 액세스 토큰 발급. 만료 5분 전 자동 갱신."""
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["access_token"]
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(
        scopes=["https://analysis.windows.net/powerbi/api/.default"]
    )
    if "access_token" not in result:
        raise AppError.TOKEN_FAILED.http(detail=result.get("error_description", "unknown"))
    _token_cache["access_token"] = result["access_token"]
    _token_cache["expires_at"]   = now + result.get("expires_in", 3600)
    return _token_cache["access_token"]
