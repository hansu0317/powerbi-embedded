"""Power BI 게이트웨이 — 앱 초기화 진입점.

모듈 구조:
  config.py           환경변수 + app_config DB 로더 + 런타임 상수
  errors.py           AppError enum (중앙 에러 레지스트리)
  database.py         커넥션 풀 + 모든 DB 쿼리 함수
  deps.py             세션 사용자 조회, CSRF 헬퍼, require_admin
  services/azure.py   Azure AD 토큰 발급
  services/fabric.py  PBI 동기화, 시작 복구
  services/powerbi.py Power BI Embed Token 발급, 보고서·데이터셋 이름 변경
  routes/auth.py      /login, /logout
  routes/report.py    /, /api/embed, /api/upload, /health, /docs
  routes/admin.py     /admin, /api/admin/*
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager

from config import SECRET_KEY, COOKIE_SECURE, MAX_PBIX_SIZE
from database import db_cleanup_login_attempts
from errors import AppError
from services.fabric import pbi_sync_loop, recover_db_jobs, recover_pending_imports
from routes import auth, report, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("powerbi-gateway")


async def _login_cleanup_loop():
    """login_attempts 30일 초과 기록을 하루 1회 정리한다.

    기존에는 db_record_login() 안에서 매 로그인마다 실행했다.
    로그인 응답 경로에서 분리해 서버 시작 시 1회 + 이후 24시간마다 실행한다.
    """
    try:
        await asyncio.to_thread(db_cleanup_login_attempts)
        logger.info("LOGIN CLEANUP: 30일 초과 기록 삭제 완료")
    except Exception:
        logger.exception("LOGIN CLEANUP FAIL (startup)")
    while True:
        await asyncio.sleep(86400)  # 24시간
        try:
            await asyncio.to_thread(db_cleanup_login_attempts)
        except Exception:
            logger.exception("LOGIN CLEANUP FAIL")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """서버 시작/종료 초기화.

    시작 순서:
      1. recover_db_jobs         — pbi_succeeded·db_failed 업로드를 DB에 등록
      2. recover_pending_imports — accepted 상태 import를 재조회해 이어서 처리
      3. pbi_sync_loop           — 백그라운드 PBI 삭제 동기화
      4. _login_cleanup_loop     — login_attempts 30일 초과 기록 일 1회 정리
    """
    try:
        await asyncio.to_thread(recover_db_jobs)
        await recover_pending_imports()
    except Exception:
        logger.exception("STARTUP RECOVERY FAIL")
    sync_task    = asyncio.create_task(pbi_sync_loop())
    cleanup_task = asyncio.create_task(_login_cleanup_loop())
    yield
    sync_task.cancel()
    cleanup_task.cancel()


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=28800,
    same_site="lax",
    https_only=COOKIE_SECURE,
)

app.include_router(auth.router)
app.include_router(report.router)
app.include_router(admin.router)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/api/upload":
        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > MAX_PBIX_SIZE + 1024 * 1024:
                err = AppError.FILE_TOO_LARGE
                return HTMLResponse(
                    err.message.format(max_mb=MAX_PBIX_SIZE // (1024 * 1024)),
                    status_code=err.status,
                )
        except ValueError:
            return HTMLResponse("잘못된 Content-Length입니다.", status_code=400)
    return await call_next(request)


@app.middleware("http")
async def static_no_cache(request: Request, call_next):
    """프론트 번들(app.js/app.css)은 파일명이 고정이라 브라우저가 옛 버전을 캐시할 수 있다.
    no-cache로 매 요청 재검증(변경 없으면 304)하게 해 빌드 후 항상 최신을 받게 한다."""
    response = await call_next(request)
    if request.url.path.startswith("/static/dist/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
