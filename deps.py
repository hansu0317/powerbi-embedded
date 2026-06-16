"""라우트 공통 의존성: 세션 사용자 조회, CSRF 검증."""
import asyncio
import secrets

from fastapi.requests import Request

from database import db_get_user
from errors import AppError


async def current_user(request: Request):
    """세션 쿠키에서 username을 읽어 DB에서 사용자 정보 반환. 없으면 None."""
    username = request.session.get("username")
    if not username:
        return None
    return await asyncio.to_thread(db_get_user, username)


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str):
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, token):
        raise AppError.CSRF_INVALID.http()


def require_admin(user):
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    if not user["is_admin"]:
        raise AppError.FORBIDDEN_ADMIN.http()
