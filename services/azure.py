"""Azure AD 서비스 주체 토큰 발급 (Power BI REST API용)."""
import threading
import time

import msal

import config
from config import TENANT_ID, CLIENT_ID, CLIENT_SECRET
from errors import AppError

_token_cache         = {"access_token": None, "expires_at": 0}
_token_lock          = threading.Lock()
_fabric_token_cache  = {"access_token": None, "expires_at": 0}
_fabric_token_lock   = threading.Lock()

# import만으로 네트워크에 접속하지 않는다. 실제 토큰이 필요한 첫 요청에서 생성한다.
# 덕분에 오프라인 코드 검사와 단위 테스트가 Microsoft 로그인 상태와 분리된다.
_msal_app = None
_msal_app_lock = threading.Lock()

def _get_msal_app():
    """MSAL 애플리케이션을 최초 사용 시 한 번만 생성한다."""
    global _msal_app
    if _msal_app is None:
        with _msal_app_lock:
            if _msal_app is None:
                _msal_app = msal.ConfidentialClientApplication(
                    CLIENT_ID,
                    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
                    client_credential=CLIENT_SECRET,
                )
    return _msal_app


def get_access_token() -> str:
    """서비스 주체(Client Credentials)로 Azure AD 액세스 토큰 발급. 만료 5분 전 자동 갱신."""
    with _token_lock:
        now = time.time()
        if _token_cache["access_token"] and now < _token_cache["expires_at"] - config.PBI_TOKEN_CACHE_MARGIN_SEC:
            return _token_cache["access_token"]
        result = _get_msal_app().acquire_token_for_client(
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
        if _fabric_token_cache["access_token"] and now < _fabric_token_cache["expires_at"] - config.PBI_TOKEN_CACHE_MARGIN_SEC:
            return _fabric_token_cache["access_token"]
        result = _get_msal_app().acquire_token_for_client(
            scopes=["https://api.fabric.microsoft.com/.default"]
        )
        if "access_token" not in result:
            raise AppError.TOKEN_FAILED.http(detail=result.get("error_description", "unknown"))
        _fabric_token_cache["access_token"] = result["access_token"]
        _fabric_token_cache["expires_at"]   = now + result.get("expires_in", 3600)
        return _fabric_token_cache["access_token"]
