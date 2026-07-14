"""관리자 포털 라우트: /admin, /api/admin/*"""
import asyncio
import logging

import bcrypt
import psycopg2.errors
from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import config
from config import WORKSPACE_ID
from database import (
    db_admin_get_stats, db_admin_get_users, db_admin_add_user,
    db_admin_toggle_user_active, db_admin_get_reports,
    db_admin_soft_delete_report, db_admin_get_upload_jobs,
    db_import_managed_report,
    db_get_report, db_get_report_access, db_set_report_access,
    db_get_synced_reports, db_hard_delete_report, db_get_pbi_report_map,
    db_count_other_reports_using_dataset, db_get_app_config, db_update_app_config,
    db_admin_get_groups, db_admin_create_group, db_admin_delete_group,
    db_get_group_members, db_set_group_member,
    db_get_report_group_access, db_set_report_group_access,
    db_get_user_report_list,
)
from deps import current_user, csrf_token, verify_csrf, require_admin
from errors import AppError
from services.fabric import sync_pbi_reports, fetch_pbi_folders_and_reports
from services.powerbi import (
    pbi_delete_report, pbi_delete_dataset, pbi_refresh_dataset, invalidate_embed_cache,
)

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


@router.get("/api/admin/users/{user_id}/reports")
async def api_admin_get_user_reports(request: Request, user_id: int):
    """사용자가 열람 가능한 보고서 목록 (직접/그룹 경로 포함) — '보고서 N' 클릭 팝업."""
    user = await current_user(request)
    require_admin(user)
    reports = await asyncio.to_thread(db_get_user_report_list, user_id)
    return {"reports": reports}


# ── 그룹 (팀/부서 단위 권한) ─────────────────────────────────────────────────

@router.get("/api/admin/groups")
async def api_admin_get_groups(request: Request):
    """그룹 목록 (멤버 수·부여 보고서 수 포함)."""
    user = await current_user(request)
    require_admin(user)
    return {"groups": await asyncio.to_thread(db_admin_get_groups)}


@router.post("/api/admin/groups")
async def api_admin_create_group(request: Request):
    """그룹 생성. body: {name, description?}"""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 50:
        raise AppError.GROUP_NAME_INVALID.http()
    try:
        group_id = await asyncio.to_thread(
            db_admin_create_group, name, str(body.get("description", "")).strip(), user["id"],
        )
    except psycopg2.errors.UniqueViolation:
        raise AppError.GROUP_ALREADY_EXISTS.http(name=name)
    logger.info("ADMIN ADD GROUP | admin=%s | group=%s | id=%s", user["username"], name, group_id)
    return {"id": group_id, "name": name}


@router.post("/api/admin/groups/{group_id}/delete")
async def api_admin_delete_group(request: Request, group_id: int):
    """그룹 삭제 — 멤버·보고서 부여도 함께 제거(개별 부여는 영향 없음)."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    deleted = await asyncio.to_thread(db_admin_delete_group, group_id)
    if not deleted:
        raise AppError.GROUP_NOT_FOUND.http()
    logger.info("ADMIN DEL GROUP | admin=%s | group_id=%s", user["username"], group_id)
    return {"deleted": True}


@router.get("/api/admin/groups/{group_id}/members")
async def api_admin_get_group_members(request: Request, group_id: int):
    """활성 사용자 전체 + 소속 여부 (멤버 편집 모달)."""
    user = await current_user(request)
    require_admin(user)
    return {"members": await asyncio.to_thread(db_get_group_members, group_id)}


@router.post("/api/admin/groups/{group_id}/members/{user_id}")
async def api_admin_set_group_member(request: Request, group_id: int, user_id: int):
    """그룹 멤버 추가/제거. body: {member: bool}"""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    body = await request.json()
    member = bool(body.get("member", False))
    try:
        await asyncio.to_thread(db_set_group_member, group_id, user_id, member, user["id"])
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.GROUP_NOT_FOUND.http()
    return {"group_id": group_id, "user_id": user_id, "member": member}


@router.get("/api/admin/reports/{report_id}/group-access")
async def api_admin_get_group_access(request: Request, report_id: int):
    """보고서에 부여된 그룹 현황 (권한 모달 '그룹' 탭)."""
    user = await current_user(request)
    require_admin(user)
    return {"groups": await asyncio.to_thread(db_get_report_group_access, report_id)}


@router.post("/api/admin/reports/{report_id}/group-access/{group_id}")
async def api_admin_set_group_access(request: Request, report_id: int, group_id: int):
    """보고서×그룹 열람 권한 부여/해제. body: {can_view: bool}"""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    body = await request.json()
    can_view = bool(body.get("can_view", False))
    try:
        await asyncio.to_thread(db_set_report_group_access, report_id, group_id, can_view, user["id"])
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.GROUP_NOT_FOUND.http()
    logger.info(
        "ADMIN GROUP ACCESS | admin=%s | report_id=%s | group_id=%s | can_view=%s",
        user["username"], report_id, group_id, can_view,
    )
    return {"report_id": report_id, "group_id": group_id, "can_view": can_view}


# 설정 키별 허용 범위 — 관리자 실수로 서비스를 마비시키는 값(0 한도, 폴링 폭주 등)을 차단한다.
# 키 추가 시 여기와 마이그레이션 시드에 함께 등록할 것.
CONFIG_LIMITS = {
    "max_pbix_size_mb":           (1, 1024),   # PBI Import API 자체 한도 1GB
    "max_uploads_per_day":        (1, 100),
    "max_personal_reports":       (1, 200),
    "report_name_max_len":        (10, 100),
    "password_min_len":           (4, 64),
    "pbi_sync_interval":          (0, 86400),  # 0 = 자동 동기화 끔
    "login_block_max_fail":       (1, 100),
    "login_block_minutes":        (1, 1440),
    "import_poll_max":            (10, 1000),
    "import_poll_interval_sec":   (1, 60),
    "embed_token_lifetime_min":   (5, 60),
    "pbi_token_cache_margin_sec": (0, 3600),
    "max_embed_rls_roles":        (1, 50),
}


@router.get("/api/admin/config")
async def api_admin_get_config(request: Request):
    """런타임 설정(app_config) 목록."""
    user = await current_user(request)
    require_admin(user)
    rows = await asyncio.to_thread(db_get_app_config)
    return {"config": rows}


@router.post("/api/admin/config")
async def api_admin_set_config(request: Request):
    """런타임 설정 변경 — 저장 즉시 재시작 없이 반영된다.

    키는 마이그레이션이 시드한 것만 허용하고, 값은 정수만 받는다(현재 키 전부 정수)."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    body = await request.json()
    key, value = str(body.get("key", "")), str(body.get("value", "")).strip()
    if key not in CONFIG_LIMITS:
        raise AppError.CONFIG_KEY_UNKNOWN.http(key=key)
    try:
        num = int(value)
    except ValueError:
        raise AppError.CONFIG_VALUE_INVALID.http(value=value)
    lo, hi = CONFIG_LIMITS[key]
    if not (lo <= num <= hi):
        raise AppError.CONFIG_VALUE_OUT_OF_RANGE.http(key=key, min=lo, max=hi)
    updated = await asyncio.to_thread(db_update_app_config, key, value)
    if not updated:
        raise AppError.CONFIG_KEY_UNKNOWN.http(key=key)
    config.reload_app_config()
    logger.info("ADMIN CONFIG | admin=%s | %s=%s", user["username"], key, value)
    return {"key": key, "value": value}


@router.get("/api/admin/reports")
async def api_admin_get_reports(request: Request):
    """보고서 목록 재조회 — 관리자 포털을 새로고침 없이 최신 상태로 유지한다.

    새 보고서는 직원 업로드·가져오기로 페이지 로드 이후에도 생기므로,
    부트스트랩 데이터만으로는 권한부여 화면이 낡은 상태로 남는다.
    """
    user = await current_user(request)
    require_admin(user)
    reports = await asyncio.to_thread(db_admin_get_reports)
    return {"reports": reports}


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
    if len(password) < config.PASSWORD_MIN_LEN:
        raise AppError.PASSWORD_TOO_SHORT.http(min=config.PASSWORD_MIN_LEN)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    # 폼은 콤마 구분 문자열로 받고 DB에는 TEXT[] 배열로 저장한다
    role_list = [r.strip() for r in roles.split(",") if r.strip()] or ["도메인"]
    try:
        new_id = await asyncio.to_thread(
            db_admin_add_user, username, pw_hash, display_name,
            pbi_username or username, role_list, is_admin,
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

        # 보고서만 지우면 데이터셋이 남아 용량이 누수된다.
        # 단, 같은 데이터셋을 쓰는 다른 활성 보고서가 있으면 절대 지우지 않는다.
        if pbi_warning is None and report.get("pbi_dataset_id"):
            shared = await asyncio.to_thread(
                db_count_other_reports_using_dataset, report["pbi_dataset_id"], report_id,
            )
            if shared == 0:
                try:
                    await pbi_delete_dataset(ws_id, report["pbi_dataset_id"])
                except Exception as exc:
                    pbi_warning = f"보고서는 삭제됐지만 데이터셋 삭제에 실패했습니다: {exc}"
                    logger.warning("PBI DATASET DELETE WARN | report_id=%s | error=%s", report_id, exc)
            else:
                logger.info("PBI DATASET KEEP | report_id=%s | 공유 보고서 %d건", report_id, shared)

    deleted = await asyncio.to_thread(db_admin_soft_delete_report, report_id, user["id"])
    if not deleted:
        raise AppError.REPORT_ALREADY_DELETED.http()

    invalidate_embed_cache(report_id)
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


@router.get("/api/admin/sync-status")
async def api_admin_sync_status(request: Request):
    """Fabric 현재 상태와 DB를 대조해 '가져오기 필요' 여부를 반환한다.

    신규(폴더 추가/직접 게시), 폴더 이동·이름변경(category 불일치),
    Fabric에서 사라진 보고서(삭제 대상)를 감지한다. import-pbi 실행 시 모두 정리된다.
    """
    user = await current_user(request)
    require_admin(user)
    try:
        fabric = await fetch_pbi_folders_and_reports()
        db_map = await asyncio.to_thread(db_get_pbi_report_map)
    except Exception as exc:
        logger.warning("SYNC STATUS FAIL | admin=%s | error=%s", user["username"], exc)
        return {"available": False, "drift": False}

    fabric_ids = {f["pbi_report_id"] for f in fabric}
    new, moved = [], []
    for f in fabric:
        rid = f["pbi_report_id"]
        if rid not in db_map:
            new.append(f["name"])
        elif (f["folder_name"] or None) != (db_map[rid]["category"] or None):
            moved.append({
                "name": f["name"],
                "from": db_map[rid]["category"],
                "to":   f["folder_name"],
            })
    removed = [v["name"] for rid, v in db_map.items() if rid not in fabric_ids]

    return {
        "available": True,
        "drift": bool(new or moved or removed),
        "new": new,
        "moved": moved,
        "removed": removed,
    }


@router.post("/api/admin/reports/{report_id}/refresh")
async def api_admin_refresh_dataset(request: Request, report_id: int):
    """보고서의 데이터셋 새로고침을 PBI에 요청한다."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    require_admin(user)
    report = await asyncio.to_thread(db_get_report, report_id)
    if not report:
        raise AppError.REPORT_NOT_FOUND.http()
    dataset_id   = report.get("pbi_dataset_id")
    workspace_id = report.get("pbi_workspace_id") or WORKSPACE_ID
    if not dataset_id:
        raise AppError.REPORT_NOT_FOUND.http()
    try:
        await pbi_refresh_dataset(workspace_id, dataset_id)
        logger.info("DATASET REFRESH | admin=%s | report_id=%d | dataset=%s", user["username"], report_id, dataset_id)
        return {"status": "accepted"}
    except Exception as exc:
        logger.warning("DATASET REFRESH FAIL | admin=%s | dataset=%s | error=%s", user["username"], dataset_id, exc)
        raise AppError.REPORT_FETCH_FAILED.http(detail=str(exc))


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
    if not can_view:
        invalidate_embed_cache(report_id)
    logger.info("ADMIN ACCESS | admin=%s | report_id=%s | user_id=%s | can_view=%s",
                user["username"], report_id, user_id, can_view)
    return {"can_view": can_view}
