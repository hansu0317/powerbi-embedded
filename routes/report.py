"""보고서 열람 라우트: /, /api/bootstrap, /api/embed, 즐겨찾기/최근본, /health, /docs.

업로드·콘텐츠교체·다운로드/내보내기는 각각 routes/report_upload.py,
report_update.py, report_export.py로 분리돼 있다(2026-08-27, routes/admin.py를
나눌 때와 같은 이유 — 이 파일도 한때 업로드 파이프라인까지 전부 담아 684줄까지
커졌었다)."""
import asyncio
import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
from fastapi import APIRouter, HTTPException
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database import (
    db_get_reports, db_get_all_active_reports, db_can_view_report, db_get_report,
    db_get_user_favorites, db_set_favorite, db_get_user_recents, db_add_recent,
    db_health_check, db_log_activity, db_get_popular_report_ids, db_get_user_activity_log,
)
from deps import (
    current_user, csrf_token, get_client_ip, json_body,
    require_user, require_user_csrf,
)
from errors import AppError
from services.powerbi import get_embed_token

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("powerbi-gateway")

# GET 필터(_build_get_filter)에서 "이 부서는 전체를 다 본다"는 의미로 예약해 둔 department
# 값 — 실제 PBIX 컬럼에 있을 리 없는 값이라 부서명과 절대 안 겹친다는 전제.
GET_FILTER_ALL_VALUE = "ALL"


async def _build_report_context(user: dict) -> dict:
    """report.html(SSR)과 /api/bootstrap(탭 토큰 재조회)이 공유하는 데이터 조립.

    /api/bootstrap이 왜 필요한가 — 같은 브라우저의 다른 탭에서 다른 계정으로 로그인하면
    공유 쿠키가 덮어써진다. 이 탭이 새로고침되면 SSR은 그 순간의(잘못된) 쿠키를 읽어
    엉뚱한 사람 데이터로 렌더링하므로, 프론트가 자기 탭 토큰과 SSR 결과가 다른 걸
    감지하면 이 함수와 동일한 데이터를 이 엔드포인트로 다시 받아 뒤엎는다(main.tsx)."""
    if user.get("is_admin"):
        report_list = await asyncio.to_thread(db_get_all_active_reports)
    else:
        report_list = await asyncio.to_thread(db_get_reports, user["username"])
    favorites = await asyncio.to_thread(db_get_user_favorites, user["id"])
    recents   = await asyncio.to_thread(db_get_user_recents, user["id"])

    # 인기 보고서: 전체 순위를 뽑은 뒤 이 사용자가 볼 수 있는 것과 교집합 → 상위 5개.
    # 권한 없는 보고서가 인기 목록으로 존재를 노출하지 않도록 필터링이 필수다.
    visible_ids = {r["id"] for r in report_list}
    ranking = await asyncio.to_thread(db_get_popular_report_ids, 7, 20)
    popular = [
        {"report_id": row["report_id"], "views": row["views"]}
        for row in ranking if row["report_id"] in visible_ids
    ][:5]

    return {
        "user":      user,
        "reports":   report_list,
        "favorites": favorites,
        "recents":   recents,
        "popular":   popular,
        "marketing_portal_url": config.MARKETING_PORTAL_URL,
        # 프론트(useRecents의 MAX)가 db_get_user_recents와 같은 상한을 쓰도록 값 자체를
        # 내려준다 — 프론트에 따로 하드코딩하면 관리자가 설정을 바꿔도 화면은 예전
        # 숫자로 계속 자르는 불일치가 생긴다(2026-08-12).
        "recents_limit": config.RECENTS_LIMIT,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    ctx = await _build_report_context(user)
    return templates.TemplateResponse(request, "report.html", {
        **ctx,
        "csrf_token": csrf_token(request),
    })


@router.get("/api/bootstrap")
async def api_bootstrap(request: Request):
    """탭 토큰 기준으로 report.html의 부트스트랩 데이터를 다시 받는 JSON 버전.

    main.tsx가 SSR 결과(window.__BOOTSTRAP__)와 이 탭이 들고 있는 토큰의 소유자가
    다를 때만 호출한다 — 정상 상황(불일치 없음)에서는 아예 호출되지 않는다."""
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    ctx = await _build_report_context(user)
    return {**ctx, "csrf_token": csrf_token(request)}


async def _require_viewable_report(user: dict, report_id: int, error: AppError = AppError.REPORT_NOT_FOUND):
    """열람 권한이 없거나 존재하지 않는 보고서 ID를 걸러낸다 (임베드와 동일 정책).

    기본(error=REPORT_NOT_FOUND)은 권한 없음과 존재하지 않음을 같은 404로 응답해
    보고서 존재 여부를 노출하지 않는다. api_embed는 403(FORBIDDEN_REPORT)을 넘겨 쓴다.
    관리자는 권한 확인을 건너뛰지만 없는 ID는 FK 위반을 404로 변환해 걸러진다.

    routes/report_export.py의 다운로드/내보내기도 이 함수를 그대로 가져다 쓴다."""
    if not user.get("is_admin") and not await asyncio.to_thread(
        db_can_view_report, user["username"], report_id
    ):
        raise error.http()


@router.post("/api/favorites/{report_id}")
async def api_set_favorite(request: Request, report_id: int):
    """즐겨찾기 추가/해제. body: {"favorite": true|false}"""
    user = await require_user_csrf(request)
    await _require_viewable_report(user, report_id)
    body = await json_body(request)
    on = bool(body.get("favorite", False))
    try:
        await asyncio.to_thread(db_set_favorite, user["id"], report_id, on)
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.REPORT_NOT_FOUND.http()
    return {"report_id": report_id, "favorite": on}


@router.post("/api/recents/{report_id}")
async def api_add_recent(request: Request, report_id: int):
    """최근 본 보고서 기록."""
    user = await require_user_csrf(request)
    await _require_viewable_report(user, report_id)
    try:
        await asyncio.to_thread(db_add_recent, user["id"], report_id)
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.REPORT_NOT_FOUND.http()
    return {"report_id": report_id, "status": "ok"}


@router.get("/health")
async def health():
    try:
        await asyncio.to_thread(db_health_check)
    except psycopg2.Error:
        raise AppError.DB_UNAVAILABLE.http()
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/docs", response_class=HTMLResponse)
async def docs(request: Request):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not user["is_admin"]:
        raise AppError.FORBIDDEN_ADMIN.http()
    return templates.TemplateResponse(request, "docs.html", {})


@router.get("/api/embed/{report_id}")
async def api_embed(request: Request, report_id: int):
    ip = get_client_ip(request)
    user = await current_user(request)
    if not user:
        logger.warning("EMBED DENY | user=미로그인       | ip=%s | report_id=%s", ip, report_id)
        raise AppError.NOT_AUTHENTICATED.http()
    try:
        await _require_viewable_report(user, report_id, error=AppError.FORBIDDEN_REPORT)
    except HTTPException:
        logger.warning("EMBED DENY | user=%-12s | ip=%s | report_id=%s (권한없음)", user["username"], ip, report_id)
        raise
    logger.info("EMBED OK   | user=%-12s | ip=%s | report_id=%s", user["username"], ip, report_id)
    result = await get_embed_token(report_id, user["pbi_username"])
    get_filter = await _build_get_filter(user, report_id)
    if get_filter:
        result["get_filter"] = get_filter
        logger.info(
            "GET_FILTER APPLY | user=%-12s | report_id=%s | %s/%s eq %s",
            user["username"], report_id, get_filter["table"], get_filter["column"], get_filter["value"],
        )
    # 30분 dedupe: 토큰 자동 재발급·새로고침 탭 복원이 조회수를 부풀리지 않게 한다
    await asyncio.to_thread(
        db_log_activity, user["id"], user["username"], "report_view",
        report_id, result.get("report_name"), ip, 30,
    )
    return result


async def _build_get_filter(user: dict, report_id: int) -> dict | None:
    """GET 필터(부서, 2026-08-24) 조립 — 진짜 RLS 아님, config.PBI_RLS_ROLE_NAME 기반
    동적 RLS와 별개의 표시용 필터. reports.filter_table/filter_column이 둘 다 설정된
    보고서에서만, 사용자의 users.department 값을 그대로 embed 필터로 건다.

    PBIX에 역할(Role)을 아예 안 만든 보고서용 — 역할이 있는 데이터셋은 이미
    get_embed_token이 identity/roles를 붙여 진짜 RLS로 처리하므로, 이 함수는 둘 다
    적용해도 서로 충돌하지 않는다(그냥 화면 필터 하나가 얹히는 것뿐).

    브라우저 devtools로 SDK를 직접 호출하면 우회 가능 — 보안 경계로 쓰지 말 것
    (docs/01_RLS_적용가이드.md "GET 필터" 절 참고).

    ── 값 채우는 방법 (스크립트 없음 — SQL 직접 실행) ──────────────────────────
        UPDATE reports SET filter_table = 'DimOrg', filter_column = '부서'
                            WHERE name = '영업정보 시장현황';
    filter_table/filter_column은 그 PBIX를 만든 사람만 아는 실제 데이터 모델
    값이라 관리자 포털에 입력창을 안 둔다 — 잘못 넣으면 조용히 "조회 결과 0건"이
    될 뿐이라(안전한 실패) 매 보고서 등록 시 값을 넣은 사람이 직접 화면으로
    확인할 것.

    관리자는 건너뛴다 — is_admin 계정도 department가 '관리자' 등 실제 필터 값과
    무관한 문자열을 갖고 있어서, 그대로 걸면 관리자가 자기 보고서에서 빈 화면을
    보게 된다(2026-08-24 발견). 1층 보고서 열람 권한(_require_viewable_report)과
    같은 원칙 — 관리자는 이 판정 전체를 우회한다.

    department가 GET_FILTER_ALL_VALUE("ALL", 대소문자 무관)이면 전체를 다 보는
    "부서"로 취급해 필터를 안 건다 — 관리자가 아니어도 전사 데이터를 봐야 하는
    직책(임원 등)을 위한 값(2026-08-24 추가). department가 아예 비어있는 것과
    결과는 같지만("미배정"과 "의도적으로 전체" 둘 다 필터 없음), 의미가 다르므로
    분리해 둔다 — "ALL"은 실제 부서값으로 명시적으로 입력한 것."""
    if user.get("is_admin") or not user.get("department"):
        return None
    if user["department"].strip().upper() == GET_FILTER_ALL_VALUE:
        return None
    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["filter_table"] or not report_row["filter_column"]:
        return None
    return {
        "table":  report_row["filter_table"],
        "column": report_row["filter_column"],
        "value":  user["department"],
    }


@router.get("/api/user/activity")
async def api_user_activity(request: Request):
    """내 활동 로그 (v6) — 일반 사용자가 본인이 열람·업로드한 이력을 직접 확인.

    관리자 전용이던 활동 로그(v3)와 달리 인증만 요구하고 항상 본인 것만 반환한다."""
    user = await require_user(request)
    rows = await asyncio.to_thread(db_get_user_activity_log, user["id"], 200)
    return {"activity": rows}
