"""관리자 포털 라우트: 보고서 목록·삭제·열람권한(개인/부서)·공개범위.

routes/admin.py에서 2026-08-27에 분리했다(분리 배경은 admin_users.py 상단 주석 참고)."""
import asyncio
import logging

import psycopg2.errors
from fastapi import APIRouter, Depends
from fastapi.requests import Request

import config
from database import (
    db_get_report, db_get_report_access, db_set_report_access,
    db_get_report_department_access, db_set_report_department_access,
    db_admin_set_report_visibility, db_admin_get_reports, db_admin_soft_delete_report,
)
from deps import require_admin_user, require_admin_csrf, json_body
from errors import AppError
from services.powerbi import pbi_delete_report, invalidate_embed_cache

router = APIRouter()
logger = logging.getLogger("powerbi-gateway")


@router.get("/api/admin/reports")
async def api_admin_get_reports(user: dict = Depends(require_admin_user)):
    """보고서 목록 재조회 — 관리자 포털을 새로고침 없이 최신 상태로 유지한다.

    새 보고서는 직원 업로드·가져오기로 페이지 로드 이후에도 생기므로,
    부트스트랩 데이터만으로는 권한부여 화면이 낡은 상태로 남는다.
    """
    reports = await asyncio.to_thread(db_admin_get_reports)
    return {"reports": reports}


@router.post("/api/admin/reports/{report_id}/delete")
async def api_admin_delete_report(report_id: int, user: dict = Depends(require_admin_csrf)):
    """PBI 워크스페이스에서 실제 삭제 후 DB 소프트 삭제."""
    report = await asyncio.to_thread(db_get_report, report_id)

    pbi_warning = None
    if report and report.get("pbi_report_id"):
        ws_id = config.resolve_workspace_id(report.get("pbi_workspace_id"))
        try:
            await pbi_delete_report(ws_id, report["pbi_report_id"])
        except Exception as exc:
            pbi_warning = str(exc)
            logger.warning("PBI DELETE WARN | report_id=%s | error=%s", report_id, exc)

        # 포털에 등록되지 않은 보고서·타일도 이 모델을 참조할 수 있다.
        # 로컬 DB 참조 수만으로 데이터셋을 자동 삭제하지 않는다.

    deleted = await asyncio.to_thread(db_admin_soft_delete_report, report_id, user["id"])
    if not deleted:
        raise AppError.REPORT_ALREADY_DELETED.http()

    invalidate_embed_cache(report_id)
    logger.info("ADMIN DELETE REPORT | admin=%s | report_id=%s", user["username"], report_id)
    result = {"deleted": True}
    if pbi_warning:
        result["pbi_warning"] = pbi_warning
    return result


@router.post("/api/admin/reports/{report_id}/visibility")
async def api_admin_set_report_visibility(
    request: Request, report_id: int, user: dict = Depends(require_admin_csrf),
):
    visibility = str((await json_body(request)).get("visibility", "personal"))
    if visibility not in ("personal", "shared"):
        raise AppError.BODY_INVALID.http()
    changed = await asyncio.to_thread(
        db_admin_set_report_visibility, report_id, visibility, user["id"],
    )
    if not changed:
        raise AppError.REPORT_NOT_FOUND.http()
    logger.info("ADMIN REPORT VISIBILITY | admin=%s | report_id=%s | visibility=%s",
                user["username"], report_id, visibility)
    return {"report_id": report_id, "visibility": visibility}


@router.get("/api/admin/reports/{report_id}/access")
async def api_admin_get_access(report_id: int, user: dict = Depends(require_admin_user)):
    """보고서의 사용자별 열람 권한 현황 조회."""
    access = await asyncio.to_thread(db_get_report_access, report_id)
    return {"users": [dict(row) for row in access]}


@router.post("/api/admin/reports/{report_id}/access/{user_id}")
async def api_admin_set_access(
    request: Request, report_id: int, user_id: int, user: dict = Depends(require_admin_csrf),
):
    """보고서에 대한 특정 사용자의 열람 권한을 설정한다."""
    body = await json_body(request)
    can_view = body.get("can_view")
    if not isinstance(can_view, bool):
        raise AppError.BODY_INVALID.http()
    await asyncio.to_thread(db_set_report_access, report_id, user_id, can_view, user["id"])
    if not can_view:
        invalidate_embed_cache(report_id)
    logger.info("ADMIN ACCESS | admin=%s | report_id=%s | user_id=%s | can_view=%s",
                user["username"], report_id, user_id, can_view)
    return {"can_view": can_view}


# ── 부서 단위 보고서 접근(1층 열람권한) ──────────────────────────────────────
# 부여 UI의 부서 선택지는 별도 엔드포인트 없이 프론트가 이미 로드한 사용자 목록에서
# 뽑아 쓴다(frontend/src/pages/AdminPage.tsx::departmentOptions) — 2026-08-27,
# 전용 엔드포인트(db_list_departments)가 한 번도 안 불려서 정리.

@router.get("/api/admin/reports/{report_id}/department-access")
async def api_admin_get_department_access(report_id: int, user: dict = Depends(require_admin_user)):
    """보고서에 부여된 부서 현황 (권한 모달 '부서' 탭)."""
    return {"departments": await asyncio.to_thread(db_get_report_department_access, report_id)}


@router.post("/api/admin/reports/{report_id}/department-access")
async def api_admin_set_department_access(
    request: Request, report_id: int, user: dict = Depends(require_admin_csrf),
):
    """보고서×부서 열람 권한 부여/해제. body: {department: str, can_view: bool}"""
    body = await json_body(request)
    department = str(body.get("department", "")).strip()
    can_view = body.get("can_view")
    if not isinstance(can_view, bool):
        raise AppError.BODY_INVALID.http()
    if not department:
        raise AppError.BODY_INVALID.http()
    try:
        await asyncio.to_thread(db_set_report_department_access, report_id, department, can_view, user["id"])
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.REPORT_NOT_FOUND.http()
    logger.info(
        "ADMIN DEPARTMENT ACCESS | admin=%s | report_id=%s | department=%s | can_view=%s",
        user["username"], report_id, department, can_view,
    )
    return {"report_id": report_id, "department": department, "can_view": can_view}
