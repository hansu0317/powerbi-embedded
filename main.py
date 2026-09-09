"""Power BI 게이트웨이 — 앱 초기화 진입점.

처음 이 코드를 공부한다면 이 파일보다 docs/00_학습_로드맵.md를 먼저 읽을 것 —
요청 하나가 끝까지 흐르는 경로, DB/인증/캐시/설정 구조를 순서대로 설명한다.

모듈 구조:
  config.py           환경변수 + 런타임 제한값 상수
  errors.py           AppError enum (중앙 에러 레지스트리)
  database/           커넥션 풀(pool.py) + 도메인별 DB 쿼리 함수(auth/reports/uploads/folders/admin/department_access)
  deps.py             세션 사용자 조회, CSRF 헬퍼, require_admin
  services/azure.py   Azure AD 토큰 발급
  services/fabric.py  PBI 동기화, 시작 복구
  services/powerbi.py Power BI Embed Token 발급, 보고서·데이터셋 이름 변경
  routes/auth.py      /login, /logout
  routes/report.py    /, /api/embed, /health, /docs (+ report_upload.py: /api/upload)
  routes/folders.py   /api/report-folders
  routes/admin*.py    /admin, /api/admin/* (도메인별로 분리)
"""
import asyncio
import logging
import sys

# Windows 콘솔 자체의 코드페이지(한글 Windows 기본 cp949)를 UTF-8(65001)로 강제한다.
# 아래 sys.stdout/stderr.reconfigure(encoding="utf-8")만으로는 부족하다 — 그건
# "파이썬이 UTF-8 바이트를 내보낸다"만 보장할 뿐, 그 바이트를 받는 콘솔 창이
# 여전히 cp949로 해석하면 화면엔 그대로 깨져 보인다(로그 파일 자체는 정상이어도).
# 콘솔에 붙어 있지 않은 경우(서비스 실행 등)엔 실패해도 무해하므로 조용히 무시.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

# Windows 콘솔/파일 리다이렉트 기본 인코딩(cp949)에서도 한글이 안 깨지도록 강제.
# Linux는 이미 UTF-8이라 no-op.
#
# utf-8-sig(BOM 포함)를 쓰는 이유 — server.ps1/server.sh는 이 프로세스의 표준출력을
# 그대로 로그 파일로 리다이렉트하는데, 그 파일을 나중에 PowerShell Get-Content 등으로
# 열어보면 파일 맨 앞에 "이거 UTF-8" 표시(BOM)가 없는 한 시스템 기본 코드페이지(cp949)로
# 잘못 짐작해 한글이 깨진다(위 SetConsoleOutputCP는 "지금 떠 있는 콘솔 화면"에만 효과가
# 있고, 나중에 파일을 열어보는 경우엔 적용 안 됨 — 별개 문제). BOM은 스트림 맨 앞에
# 한 번만 붙는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8-sig")
    except (AttributeError, ValueError):
        pass

# 로깅은 다른 모든 임포트보다 먼저 구성한다 — 아래에서 임포트하는 모듈(특히 config.py의
# SECRET_KEY 검증처럼 로드 중 실패할 수 있는 것들)이 죽어도 원인이 로그에 남도록.
#
# 표준출력 하나로만 내보낸다 — server.ps1/server.sh가 이 프로세스의 표준출력을
# logs/server.log 하나로 리다이렉트한다. 레벨별로 server.log(표준출력)/server.err.log
# (표준에러) 두 파일에 나눠 담던 예전 방식은 폐기했다 — 실제로 두 파일 다 이 로거
# 하나를 거쳐 나가던 같은 내용이라 나눠 담을 실익이 없었고(파일만 두 개, grep으로
# 각 파일을 따로 열어봐야 하는 불편함만 있었음), Windows Start-Process는 애초에 두
# 스트림을 같은 파일로 합치는 것 자체를 금지한다.
_log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setFormatter(_log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[_stdout_handler])
logger = logging.getLogger("powerbi-gateway")

# httpx가 API 호출마다 "HTTP Request: GET ... 200 OK" 한 줄씩 INFO로 찍어서, 정작
# 의미 있는 우리 로그(EMBED OK, LOGIN 등)가 그 사이에 묻힌다 — httpx 자체 로거만
# WARNING 이상으로 올려서 조용히 시킨다(2026-08-25, 로그 정리하며 발견).
logging.getLogger("httpx").setLevel(logging.WARNING)

# 앱 임포트 전체를 감싼다 — 여기서 나는 예외(설정값 누락 등 모듈 로드 중 실패)를
# 로그에 남기고 재발생시킨다. 위에서 로깅을 먼저 구성해둔 이유가 이것 — 감싸지
# 않으면 포맷 없는 원본 트레이스백이 콘솔에만 찍히고 로그 파일엔 안 남는다.
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
    from fastapi.requests import Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.sessions import SessionMiddleware
    from contextlib import asynccontextmanager

    import config
    from config import SECRET_KEY, COOKIE_SECURE
    from database import db_cleanup_login_attempts
    from errors import AppError, extract_code_message
    from services.fabric import pbi_sync_loop, recover_db_jobs, recover_pending_imports
    from services.backup import backup_loop
    from routes import (
        auth, report, report_upload,
        admin, admin_users, admin_reports, folders,
    )
except Exception:
    logger.exception("STARTUP IMPORT FAILURE")
    raise


async def _login_cleanup_loop():
    """오래된 로그인 시도 기록(30일 초과)을 하루 1회 정리한다.

    기존에는 db_record_login() 안에서 매 로그인마다 실행했다.
    로그인 응답 경로에서 분리해 서버 시작 시 1회 + 이후 24시간마다 실행한다.
    """
    try:
        deleted = await asyncio.to_thread(db_cleanup_login_attempts)
        logger.info("DAILY CLEANUP: 로그인 기록 %d건 정리 완료", deleted)
    except Exception:
        logger.exception("DAILY CLEANUP FAIL (startup)")
    while True:
        await asyncio.sleep(86400)  # 24시간
        try:
            await asyncio.to_thread(db_cleanup_login_attempts)
        except Exception:
            logger.exception("DAILY CLEANUP FAIL")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """서버 시작/종료 초기화.

    시작 순서:
      1. recover_db_jobs         — pbi_succeeded·db_failed 업로드를 DB에 등록
      2. recover_pending_imports — accepted 상태 import를 재조회해 이어서 처리
      3. pbi_sync_loop           — 백그라운드 PBI 삭제 동기화
      4. _login_cleanup_loop     — login_attempts 30일 초과 기록 일 1회 정리
      5. backup_loop             — 평일 17시, 서버가 켜져 있을 때만 DB 백업 1회(services/backup.py)
    """
    try:
        await asyncio.to_thread(recover_db_jobs)
        await recover_pending_imports()
    except Exception:
        logger.exception("STARTUP RECOVERY FAIL")
    sync_task    = asyncio.create_task(pbi_sync_loop())
    cleanup_task = asyncio.create_task(_login_cleanup_loop())
    backup_task  = asyncio.create_task(backup_loop())
    yield
    sync_task.cancel()
    cleanup_task.cancel()
    backup_task.cancel()


app = FastAPI(
    title="qualisoft BI 포털 API",
    version="7.0",
    description="App-Owns-Data Power BI 임베딩 게이트웨이. /openapi.json은 항상 노출되지만 "
                 "/docs·/redoc 대화형 UI는 보안상 비활성화돼 있다 — 로컬 개발 중 필요하면 "
                 "docs_url/redoc_url을 임시로 지정해서 켤 것.",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
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
app.include_router(report_upload.router)
app.include_router(folders.router)
app.include_router(admin.router)
app.include_router(admin_users.router)
app.include_router(admin_reports.router)


@app.exception_handler(HTTPException)
async def http_error_logger(request: Request, exc: HTTPException):
    """AppError.http()가 만든 5xx만 서버 로그에 남긴다.

    4xx는 정상적인 사용자 흐름의 일부(권한 없음·중복 등)라 노이즈만 쌓이므로 제외.
    응답 자체는 FastAPI 기본 핸들러에 그대로 위임한다.
    """
    if exc.status_code >= 500:
        code, msg = extract_code_message(exc.detail)
        logger.error("HTTP 5xx | path=%s | %s | %s", request.url.path, code, msg)
    return await default_http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_error_logger(request: Request, exc: Exception):
    """AppError로 감싸지 못한 예상 밖 예외 — 로깅 후 500 반환."""
    logger.exception("UNHANDLED EXCEPTION | path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "서버 오류가 발생했습니다."})


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/api/upload":
        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > config.MAX_PBIX_SIZE + 1024 * 1024:
                err = AppError.FILE_TOO_LARGE
                return HTMLResponse(
                    err.message.format(max_mb=config.MAX_PBIX_SIZE // (1024 * 1024)),
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
    elif not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response
