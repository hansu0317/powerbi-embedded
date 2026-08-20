"""인증 라우트: /login, /logout, /login/sso, /auth/callback."""
import asyncio
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    db_check_and_get_user, db_verify_password, db_record_login,
    db_get_user_by_email, db_sso_record_login,
)
from deps import current_user, csrf_token, verify_csrf, get_client_ip, issue_tab_token
from errors import AppError
from services.sso import sso_enabled, build_auth_flow, complete_auth_flow

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("powerbi-gateway")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """항상 로그인 폼을 보여준다 — "이미 로그인돼 있으면 /로 리다이렉트"를 여기서
    쿠키 기준으로 하면, 같은 브라우저의 다른 탭이 이미 다른 계정으로 로그인돼 있을 때
    이 탭에서 로그인 폼 자체를 볼 수 없게 된다(공유 쿠키만 보고 "이미 로그인됨"으로
    착각). 그 판단은 이 탭 전용 토큰을 아는 프론트(LoginPage.tsx)에서 한다."""
    return templates.TemplateResponse(request, "login.html", {
        "error": None, "csrf_token": csrf_token(request), "sso_enabled": sso_enabled(),
    })


@router.get("/login/sso")
async def login_sso(request: Request):
    """"Microsoft 계정으로 로그인" 버튼 대상. Microsoft 로그인 화면으로 리디렉션한다.

    flow(state·PKCE code_verifier·nonce)는 세션 쿠키에 잠깐 담아뒀다가 /auth/callback에서
    그대로 꺼내 써야 한다 — SessionMiddleware의 same_site=lax 덕분에 Microsoft에서
    돌아오는 최상위 GET 리디렉션에도 이 쿠키가 그대로 실려 온다."""
    if not sso_enabled():
        return RedirectResponse("/login")
    flow = build_auth_flow()
    request.session["sso_flow"] = flow
    return RedirectResponse(flow["auth_uri"])


@router.get("/auth/callback")
async def auth_callback(request: Request):
    """Microsoft 로그인 완료 후 돌아오는 지점. 이메일로 기존 계정을 찾아 로그인 처리한다
    (관리자가 미리 계정에 이메일을 등록해둔 경우만 — 자동 가입은 하지 않는다).

    성공하면 프론트가 sessionStorage에 저장할 탭 토큰을 쿼리 파라미터로 실어 "/"로
    보낸다 — 이건 fetch가 아니라 브라우저 전체 리디렉션이라 응답 바디를 JS가 가로챌
    수 없다(/login의 fetch 로그인과 다른 점). main.tsx가 이 쿼리 파라미터를 읽어
    sessionStorage에 옮기고 즉시 주소에서 지운다."""
    ip = get_client_ip(request)
    flow = request.session.pop("sso_flow", None)
    if not flow:
        return RedirectResponse("/login?" + urlencode({"sso_error": "flow_missing"}))

    try:
        result = await asyncio.to_thread(complete_auth_flow, flow, dict(request.query_params))
    except ValueError:
        logger.warning("SSO LOGIN FAIL | reason=state_mismatch | ip=%s", ip)
        return RedirectResponse("/login?" + urlencode({"sso_error": "denied"}))

    if "id_token_claims" not in result:
        logger.warning("SSO LOGIN FAIL | reason=%s | ip=%s", result.get("error", "unknown"), ip)
        return RedirectResponse("/login?" + urlencode({"sso_error": "denied"}))

    claims = result["id_token_claims"]
    email = claims.get("preferred_username") or claims.get("email") or ""
    if not email:
        logger.warning("SSO LOGIN FAIL | reason=no_email | ip=%s", ip)
        return RedirectResponse("/login?" + urlencode({"sso_error": "no_email"}))

    row = await asyncio.to_thread(db_get_user_by_email, email)
    if not row:
        logger.warning("SSO LOGIN NO MATCH | email=%s | ip=%s", email, ip)
        return RedirectResponse("/login?" + urlencode({"sso_error": "nomatch"}))
    if not row["is_active"]:
        logger.warning("SSO LOGIN INACTIVE | user=%-12s | ip=%s", row["username"], ip)
        return RedirectResponse("/login?" + urlencode({"sso_error": "inactive"}))

    display_name = claims.get("name") or row["display_name"]
    await asyncio.to_thread(db_sso_record_login, row["username"], display_name, ip)
    request.session["username"] = row["username"]
    token = issue_tab_token(row["username"])
    logger.info("SSO LOGIN OK | user=%-12s | ip=%s | name=%s", row["username"], ip, display_name)
    return RedirectResponse("/?" + urlencode({"sso_token": token, "sso_user": row["username"]}))


@router.post("/login")
async def login(request: Request, username: str = Form(), password: str = Form(), csrf: str = Form()):
    """로그인. 세션 쿠키(기존 방식, 새 탭 첫 로드용)와 탭 전용 토큰(신규, sessionStorage
    보관용)을 함께 발급한다 — 왜 두 개나 필요한지는 issue_tab_token 주석 참고."""
    verify_csrf(request, csrf)
    ip = get_client_ip(request)

    # 차단 확인 + 사용자 SELECT를 한 DB 커넥션에서 처리 (기존: 두 번 별도 호출)
    status, row = await asyncio.to_thread(db_check_and_get_user, username, ip)
    if status == "blocked":
        logger.warning("LOGIN BLOCK | user=%-12s | ip=%s", username, ip)
        return JSONResponse(
            {"ok": False, "error": AppError.LOGIN_RATE_LIMIT.message},
            status_code=AppError.LOGIN_RATE_LIMIT.status,
        )

    # bcrypt는 CPU 집약적이므로 DB 커넥션 반환 후 별도 스레드에서 실행
    user = await asyncio.to_thread(db_verify_password, row, password)
    await asyncio.to_thread(db_record_login, username, ip, isinstance(user, dict))

    if user == "inactive":
        logger.warning("LOGIN INACTIVE | user=%-12s | ip=%s", username, ip)
        return JSONResponse({"ok": False, "error": "계정이 비활성화되었습니다. 관리자에게 문의하세요."})
    if not user:
        logger.warning("LOGIN FAIL | user=%-12s | ip=%s", username, ip)
        return JSONResponse({"ok": False, "error": "아이디 또는 비밀번호가 올바르지 않습니다."})

    request.session["username"] = user["username"]
    token = issue_tab_token(user["username"])
    logger.info("LOGIN OK   | user=%-12s | ip=%s | name=%s", user["username"], ip, user["display_name"])
    return JSONResponse({"ok": True, "token": token, "username": user["username"]})


@router.post("/logout")
async def logout(request: Request):
    user = await current_user(request)
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    if user:
        logger.info("LOGOUT     | user=%-12s | ip=%s", user["username"], get_client_ip(request))
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
