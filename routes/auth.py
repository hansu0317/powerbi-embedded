"""인증 라우트: /login, /logout."""
import asyncio
import logging

from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import db_check_and_get_user, db_verify_password, db_record_login
from deps import current_user, csrf_token, verify_csrf, get_client_ip, issue_tab_token
from errors import AppError

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
        "error": None, "csrf_token": csrf_token(request),
    })


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
