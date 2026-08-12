"""라우트 공통 의존성: 세션 사용자 조회, CSRF 검증."""
import asyncio
import secrets

from fastapi.requests import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import SECRET_KEY
from database import db_get_user
from errors import AppError

# 탭별 로그인 토큰(Authorization: Bearer) — 세션 쿠키는 브라우저 전체가 공유해서
# 같은 브라우저의 다른 탭에서 다른 계정으로 로그인하면 기존 탭까지 그 계정으로
# 덮어써진다. 로그인 시 이 토큰을 발급해 프론트가 탭 전용 sessionStorage에 저장하고
# 매 요청마다 헤더로 실어 보내면, 탭마다 독립된 로그인 상태를 유지할 수 있다.
# 세션 쿠키(max_age=28800, main.py)와 동일하게 8시간으로 맞춘다.
_TAB_TOKEN_SERIALIZER = URLSafeTimedSerializer(SECRET_KEY, salt="tab-auth-token")
TAB_TOKEN_MAX_AGE = 28800


def issue_tab_token(username: str) -> str:
    return _TAB_TOKEN_SERIALIZER.dumps({"username": username})


def _verify_tab_token(token: str) -> str | None:
    try:
        data = _TAB_TOKEN_SERIALIZER.loads(token, max_age=TAB_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("username")


async def current_user(request: Request):
    """Authorization 헤더(탭 토큰)를 우선 확인하고, 없으면 세션 쿠키를 본다.

    헤더 인증이 성공하면 request.state.auth_via_token = True를 세팅한다 — 이걸로
    verify_csrf가 "브라우저가 자동으로 붙인 자격증명(쿠키)에 의존하지 않는 요청"임을
    알고 CSRF 검증을 건너뛴다(공격자가 Authorization 헤더는 위조할 수 없으므로)."""
    request.state.auth_via_token = False
    auth_header = request.headers.get("Authorization", "")
    username = None
    if auth_header.startswith("Bearer "):
        username = _verify_tab_token(auth_header[7:])
        if username:
            request.state.auth_via_token = True
    if not username:
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
    # 탭 토큰(Authorization 헤더)으로 인증된 요청은 CSRF 공격이 성립하지 않는다 —
    # 공격 페이지가 다른 탭의 sessionStorage나 Authorization 헤더를 위조할 수 없기 때문.
    # current_user()가 먼저 호출돼 request.state.auth_via_token이 세팅된 경우에만 적용된다.
    if getattr(request.state, "auth_via_token", False):
        return
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, token):
        raise AppError.CSRF_INVALID.http()


def require_admin(user):
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    if not user["is_admin"]:
        raise AppError.FORBIDDEN_ADMIN.http()


async def require_user(request: Request) -> dict:
    """인증된 사용자 반환. 일반 사용자 API의 반복 인증 검사를 한곳에 둔다."""
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    return user


async def require_user_csrf(request: Request) -> dict:
    """사용자 인증 후 상태 변경 요청의 CSRF를 검증한다."""
    user = await require_user(request)
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    return user


async def require_admin_user(request: Request) -> dict:
    """관리자 세션 확인. `Depends(require_admin_user)`로 라우트에 주입해 보일러플레이트를 줄인다."""
    user = await require_user(request)
    require_admin(user)
    return user


async def require_admin_csrf(request: Request) -> dict:
    """CSRF(X-CSRF-Token 헤더) 검증 + 관리자 세션 확인을 함께 처리하는 Depends용 함수.

    current_user()를 먼저 호출해야 request.state.auth_via_token이 세팅되고,
    verify_csrf()가 탭 토큰 인증 여부를 알 수 있다 — 순서를 바꾸면 안 된다."""
    user = await require_user_csrf(request)
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
