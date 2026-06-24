"""Azure AD 서비스 주체 토큰 발급 (Power BI REST API용)."""
import threading
import time

import msal

from config import TENANT_ID, CLIENT_ID, CLIENT_SECRET
from errors import AppError

_token_cache         = {"access_token": None, "expires_at": 0}
_token_lock          = threading.Lock()
_fabric_token_cache  = {"access_token": None, "expires_at": 0}
_fabric_token_lock   = threading.Lock()

# ConfidentialClientApplication을 모듈 레벨에서 한 번만 생성한다.
# 인스턴스를 매 갱신마다 new하면 MSAL 내부 토큰 캐시가 초기화되어
# 항상 네트워크 요청이 발생하고, 불필요한 객체 생성 비용이 누적된다.
_msal_app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    client_credential=CLIENT_SECRET,
)


def get_access_token() -> str:
    """서비스 주체(Client Credentials)로 Azure AD 액세스 토큰 발급. 만료 5분 전 자동 갱신."""
    with _token_lock:
        now = time.time()
        if _token_cache["access_token"] and now < _token_cache["expires_at"] - 300:
            return _token_cache["access_token"]
        result = _msal_app.acquire_token_for_client(
            scopes=["https://analysis.windows.net/powerbi/api/.default"]
        )
        if "access_token" not in result:
            raise AppError.TOKEN_FAILED.http(detail=result.get("error_description", "unknown"))
        _token_cache["access_token"] = result["access_token"]
        _token_cache["expires_at"]   = now + result.get("expires_in", 3600)
        return _token_cache["access_token"]


def get_fabric_token() -> str:
    """Fabric REST API용 액세스 토큰 발급. 만료 5분 전 자동 갱신."""
    with _fabric_token_lock:
        now = time.time()
        if _fabric_token_cache["access_token"] and now < _fabric_token_cache["expires_at"] - 300:
            return _fabric_token_cache["access_token"]
        result = _msal_app.acquire_token_for_client(
            scopes=["https://api.fabric.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise AppError.TOKEN_FAILED.http(detail=result.get("error_description", "unknown"))
        _fabric_token_cache["access_token"] = result["access_token"]
        _fabric_token_cache["expires_at"]   = now + result.get("expires_in", 3600)
        return _fabric_token_cache["access_token"]
