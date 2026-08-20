"""MS 계정 로그인(SSO) — Azure AD 인증 코드 흐름(Authorization Code Flow, MSAL).

services/azure.py의 서비스 주체(Client Credentials) 인증과는 완전히 다른 용도다 —
그쪽은 "서버가 자기 자신으로" Power BI API를 부르고, 여기는 "실제 사람이 자기 자신을
증명"하기 위해 브라우저를 Microsoft 로그인 화면으로 보냈다가 돌려받는다. 같은 Azure AD
앱 등록(CLIENT_ID/TENANT_ID/CLIENT_SECRET)을 재사용하지만 흐름 자체가 별개라서 파일도
분리했다(2026-08-20 도입).

흐름:
  1. build_auth_flow()      — Microsoft 로그인 URL 생성. 반환된 flow dict를 세션에
                              저장해뒀다가(routes/auth.py) 2번에 그대로 넘겨야 한다
                              (PKCE code_verifier·state·nonce가 여기 들어있음).
  2. complete_auth_flow()   — Microsoft가 돌려준 쿼리 파라미터 + 1번의 flow로 토큰 교환.
"""
import msal

from config import TENANT_ID, CLIENT_ID, CLIENT_SECRET, SSO_REDIRECT_URI

# openid/profile/email은 인증 코드 흐름에서 MSAL이 자동으로 얹어준다 — 여기 스코프는
# 로그인 후 추가로 필요한 델리게이트 권한만 적는다. 지금은 이름·이메일만 있으면
# 충분해서(Graph 호출 없이 ID 토큰 클레임만 씀) User.Read 하나로 족하다.
_SCOPES = ["User.Read"]

_sso_app = None


def _get_sso_app():
    """SSO 전용 MSAL 앱. services/azure.py의 _msal_app과 별개 인스턴스 —
    용도(그랜트 타입)가 달라 캐시를 같이 쓰면 안 된다."""
    global _sso_app
    if _sso_app is None:
        _sso_app = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{TENANT_ID}",
            client_credential=CLIENT_SECRET,
        )
    return _sso_app


def sso_enabled() -> bool:
    return bool(SSO_REDIRECT_URI)


def build_auth_flow() -> dict:
    """Microsoft 로그인으로 보낼 flow를 만든다. flow["auth_uri"]로 리디렉션하고,
    flow 전체는 세션에 저장해뒀다가 complete_auth_flow에 그대로 넘겨야 한다.

    prompt="select_account" — 브라우저에 이미 로그인된 Microsoft 계정이 있으면
    Microsoft가 그걸 물어보지도 않고 조용히 그대로 써버린다. 회사 PC엔 관리/서비스용
    계정이 이미 로그인돼 있는 경우가 흔해서, 본인 계정으로 로그인하려 했는데 엉뚱한
    계정으로 시도돼 NO MATCH가 뜨는 혼란이 생긴다(2026-08-20 발견). 매번 계정 선택
    화면을 띄워서 실수로 다른 계정이 쓰이는 걸 막는다."""
    return _get_sso_app().initiate_auth_code_flow(
        _SCOPES, redirect_uri=SSO_REDIRECT_URI, prompt="select_account",
    )


def complete_auth_flow(flow: dict, auth_response: dict) -> dict:
    """Microsoft가 콜백으로 돌려준 쿼리 파라미터(auth_response)를 flow와 맞춰 토큰 교환.

    반환값에 "id_token_claims"가 있으면 성공 — claims["preferred_username"]이 로그인한
    사람의 이메일(UPN)이다. 실패(state 불일치, 사용자가 취소 등)하면 MSAL이 "error" 키를
    담아 반환한다(예외를 던지지 않음 — 호출자가 직접 확인해야 함)."""
    return _get_sso_app().acquire_token_by_auth_code_flow(flow, auth_response)
