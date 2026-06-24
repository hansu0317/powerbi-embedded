"""관리자 포털 라우트: /admin, /api/admin/*"""
import asyncio
import logging

import bcrypt
import psycopg2.errors
from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import PASSWORD_MIN_LEN, WORKSPACE_ID
from database import (
    db_admin_get_stats, db_admin_get_users, db_admin_add_user,
    db_admin_toggle_user_active, db_admin_get_reports,
    db_admin_soft_delete_report, db_admin_get_upload_jobs,
    db_admin_set_category, db_import_managed_report,
    db_get_report, db_get_report_access, db_set_report_access,
    db_get_synced_reports, db_hard_delete_report,
)
from deps import current_user, csrf_token, verify_csrf, require_admin
from errors import AppError
from services.fabric import sync_pbi_reports, fetch_pbi_folders_and_reports
from services.powerbi import pbi_delete_report

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("powerbi-gateway")


@router.post("/api/admin/sync-pbi")
async def api_sync_pbi(request: Request):
    """관리자 수동 동기화: PBI에서 지운 보고서를 즉시 DB에 반영한다."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    summary = await sync_pbi_reports()
    logger.info("PBI SYNC (manual) | user=%s | %s", user["username"], summary)
    return summary


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = await current_user(request)
    require_admin(user)
    stats   = await asyncio.to_thread(db_admin_get_stats)
    users   = await asyncio.to_thread(db_admin_get_users)
    reports = await asyncio.to_thread(db_admin_get_reports)
    jobs    = await asyncio.to_thread(db_admin_get_upload_jobs)
    return templates.TemplateResponse(request, "admin.html", {
        "user": user, "stats": stats, "users": users,
        "reports": reports, "jobs": jobs,
        "csrf_token": csrf_token(request),
    })


@router.post("/api/admin/users/add")
async def api_admin_add_user(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    display_name: str = Form(),
    pbi_username: str = Form(""),
    roles: str = Form("도메인"),
    is_admin: bool = Form(False),
    csrf: str = Form(),
):
    verify_csrf(request, csrf)
    user = await current_user(request)
    require_admin(user)
    if len(password) < PASSWORD_MIN_LEN:
        raise AppError.PASSWORD_TOO_SHORT.http(min=PASSWORD_MIN_LEN)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        new_id = await asyncio.to_thread(
            db_admin_add_user, username, pw_hash, display_name,
            pbi_username or username, roles or "도메인", is_admin,
        )
    except psycopg2.errors.UniqueViolation:
        raise AppError.USER_ALREADY_EXISTS.http(username=username)
    logger.info("ADMIN ADD USER | admin=%s | new=%s | id=%s", user["username"], username, new_id)
    return {"id": new_id, "username": username}


@router.post("/api/admin/users/{user_id}/toggle-active")
async def api_admin_toggle_user(request: Request, user_id: int):
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    is_active = await asyncio.to_thread(db_admin_toggle_user_active, user_id)
    if is_active is None:
        raise AppError.USER_NOT_FOUND.http()
    return {"is_active": is_active}


@router.post("/api/admin/reports/{report_id}/delete")
async def api_admin_delete_report(request: Request, report_id: int):
    """PBI 워크스페이스에서 실제 삭제 후 DB 소프트 삭제."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)

    report = await asyncio.to_thread(db_get_report, report_id)

    pbi_warning = None
    if report and report.get("pbi_report_id"):
        ws_id = report.get("pbi_workspace_id") or WORKSPACE_ID
        try:
            await pbi_delete_report(ws_id, report["pbi_report_id"])
        except Exception as exc:
            pbi_warning = str(exc)
            logger.warning("PBI DELETE WARN | report_id=%s | error=%s", report_id, exc)

    deleted = await asyncio.to_thread(db_admin_soft_delete_report, report_id, user["id"])
    if not deleted:
        raise AppError.REPORT_ALREADY_DELETED.http()

    logger.info("ADMIN DELETE REPORT | admin=%s | report_id=%s", user["username"], report_id)
    result = {"deleted": True}
    if pbi_warning:
        result["pbi_warning"] = pbi_warning
    return result


@router.post("/api/admin/import-pbi")
async def api_admin_import_pbi(request: Request):
    """Fabric 폴더 구조를 읽어 새 공용 보고서를 DB에 등록한다.

    이미 등록된 보고서는 건너뛴다(pbi_report_id 중복 체크).
    권한은 부여하지 않으므로 등록 후 보고서 관리에서 별도 설정이 필요하다.
    """
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)

    reports = await fetch_pbi_folders_and_reports()
    fabric_ids = {r["pbi_report_id"] for r in reports}

    registered = skipped = deleted = 0

    # 신규 등록 + 기존 보고서 category 업데이트
    for r in reports:
        is_new = await asyncio.to_thread(
            db_import_managed_report,
            r["pbi_report_id"], r["name"], r["dataset_id"],
            WORKSPACE_ID, r["folder_id"], r["folder_name"],
            user["id"],
        )
        if is_new:
            registered += 1
            logger.info("ADMIN IMPORT PBI | admin=%s | report=%s | category=%s",
                        user["username"], r["name"], r["folder_name"])
        else:
            skipped += 1

    # Fabric에 없는 보고서는 DB에서 완전 삭제
    db_reports = await asyncio.to_thread(db_get_synced_reports)
    for row in db_reports:
        if row["pbi_report_id"] not in fabric_ids:
            did_delete = await asyncio.to_thread(db_hard_delete_report, row["id"])
            if did_delete:
                deleted += 1
                logger.info("ADMIN IMPORT PBI DELETE | admin=%s | report=%s",
                            user["username"], row["name"])

    logger.info("ADMIN IMPORT PBI DONE | admin=%s | registered=%d | skipped=%d | deleted=%d",
                user["username"], registered, skipped, deleted)
    return {"registered": registered, "skipped": skipped, "deleted": deleted, "total": len(reports)}


@router.post("/api/admin/reports/{report_id}/category")
async def api_admin_set_category(request: Request, report_id: int):
    """공용 보고서의 카테고리를 변경한다."""
    user = await current_user(request)
    require_admin(user)
    body = await request.json()
    category = (body.get("category") or "").strip() or None
    await asyncio.to_thread(db_admin_set_category, report_id, category, user["id"])
    logger.info("ADMIN CATEGORY | admin=%s | report_id=%s | category=%s",
                user["username"], report_id, category)
    return {"category": category}


@router.get("/api/admin/reports/{report_id}/access")
async def api_admin_get_access(request: Request, report_id: int):
    """보고서의 사용자별 열람 권한 현황 조회."""
    user = await current_user(request)
    require_admin(user)
    access = await asyncio.to_thread(db_get_report_access, report_id)
    return {"users": [dict(row) for row in access]}


@router.post("/api/admin/reports/{report_id}/access/{user_id}")
async def api_admin_set_access(request: Request, report_id: int, user_id: int):
    """보고서에 대한 특정 사용자의 열람 권한을 설정한다."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    body = await request.json()
    can_view = bool(body.get("can_view", False))
    await asyncio.to_thread(db_set_report_access, report_id, user_id, can_view, user["id"])
    logger.info("ADMIN ACCESS | admin=%s | report_id=%s | user_id=%s | can_view=%s",
                user["username"], report_id, user_id, can_view)
    return {"can_view": can_view}
