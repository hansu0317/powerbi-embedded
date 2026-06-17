"""인증 라우트: /login, /logout."""
import asyncio
import logging

from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import db_login_allowed, db_authenticate, db_record_login
from deps import current_user, csrf_token, verify_csrf
from errors import AppError

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("powerbi-gateway")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if await current_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {
        "error": None, "csrf_token": csrf_token(request),
    })


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(), password: str = Form(), csrf: str = Form()):
    verify_csrf(request, csrf)
    ip = request.client.host
    if not await asyncio.to_thread(db_login_allowed, username, ip):
        logger.warning("LOGIN BLOCK | user=%-12s | ip=%s", username, ip)
        return templates.TemplateResponse(request, "login.html", {
            "error": AppError.LOGIN_RATE_LIMIT.message,
            "csrf_token": csrf_token(request),
        }, status_code=AppError.LOGIN_RATE_LIMIT.status)
    user = await asyncio.to_thread(db_authenticate, username, password)
    await asyncio.to_thread(db_record_login, username, ip, isinstance(user, dict))
    if user == "inactive":
        logger.warning("LOGIN INACTIVE | user=%-12s | ip=%s", username, ip)
        return templates.TemplateResponse(request, "login.html", {
            "error": "계정이 비활성화되었습니다. 관리자에게 문의하세요.",
            "csrf_token": csrf_token(request),
        })
    if not user:
        logger.warning("LOGIN FAIL | user=%-12s | ip=%s", username, ip)
        return templates.TemplateResponse(request, "login.html", {
            "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
            "csrf_token": csrf_token(request),
        })
    request.session["username"] = user["username"]
    logger.info("LOGIN OK   | user=%-12s | ip=%s | name=%s", user["username"], ip, user["display_name"])
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    user = await current_user(request)
    if user:
        logger.info("LOGOUT     | user=%-12s | ip=%s", user["username"], request.client.host)
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
