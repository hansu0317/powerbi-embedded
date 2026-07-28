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


async def require_admin_user(request: Request) -> dict:
    """관리자 세션 확인. `Depends(require_admin_user)`로 라우트에 주입해 보일러플레이트를 줄인다."""
    user = await current_user(request)
    require_admin(user)
    return user


async def require_admin_csrf(request: Request) -> dict:
    """CSRF(X-CSRF-Token 헤더) 검증 + 관리자 세션 확인을 함께 처리하는 Depends용 함수."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    return user


async def json_body(request: Request) -> dict:
    """요청 본문을 JSON dict로 파싱한다. 비어 있거나 형식이 틀리면 400.

    request.json()을 그대로 쓰면 본문이 없을 때 JSONDecodeError가 그대로 올라가
    500으로 나간다 — 사용자 입력 문제는 4xx로 돌려주는 것이 맞다."""
    try:
        body = await request.json()
    except Exception:
        raise AppError.BODY_INVALID.http()
    if not isinstance(body, dict):
        raise AppError.BODY_INVALID.http()
    return body


def get_client_ip(request: Request) -> str:
    """실제 클라이언트 IP 반환. 리버스 프록시 뒤에서는 X-Forwarded-For 첫 번째 값 사용."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host
