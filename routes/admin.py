"""관리자 포털 라우트: /admin, /api/admin/*"""
import asyncio
import csv
import io
import logging

import bcrypt
import psycopg2.errors
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

import config
from config import WORKSPACE_ID
from database import (
    db_admin_get_stats, db_admin_get_users, db_admin_add_user,
    db_admin_toggle_user_active, db_admin_get_reports,
    db_admin_soft_delete_report, db_admin_get_upload_jobs,
    db_import_pbi_item,
    db_get_report, db_get_report_access, db_set_report_access,
    db_get_synced_reports, db_hard_delete_report, db_get_pbi_report_map,
    db_count_other_reports_using_dataset, db_get_app_config, db_update_app_config,
    db_admin_get_groups, db_admin_create_group, db_admin_delete_group,
    db_get_group_members, db_set_group_member,
    db_get_report_group_access, db_set_report_group_access,
    db_get_user_report_list,
    db_get_activity_log, db_get_audit_log,
    db_admin_toggle_user_upload,
)
from deps import csrf_token, verify_csrf, require_admin_user, require_admin_csrf, json_body
from errors import AppError
from services.fabric import (
    sync_pbi_reports, fetch_pbi_folders_and_reports, fetch_pbi_folders_and_dashboards, LOOP_HEARTBEAT,
)
from services.powerbi import (
    pbi_delete_report, pbi_delete_dataset, invalidate_embed_cache,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("powerbi-gateway")


@router.post("/api/admin/sync-pbi")
async def api_sync_pbi(user: dict = Depends(require_admin_csrf)):
    """관리자 수동 동기화: PBI에서 지운 보고서를 즉시 DB에 반영한다."""
    summary = await sync_pbi_reports()
    logger.info("PBI SYNC (manual) | user=%s | %s", user["username"], summary)
    return summary


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: dict = Depends(require_admin_user)):
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
async def api_admin_get_user_reports(user_id: int, user: dict = Depends(require_admin_user)):
    """사용자가 열람 가능한 보고서 목록 (직접/그룹 경로 포함) — '보고서 N' 클릭 팝업."""
    reports = await asyncio.to_thread(db_get_user_report_list, user_id)
    return {"reports": reports}


# ── 그룹 (팀/부서 단위 권한) ─────────────────────────────────────────────────

@router.get("/api/admin/groups")
async def api_admin_get_groups(user: dict = Depends(require_admin_user)):
    """그룹 목록 (멤버 수·부여 보고서 수 포함)."""
    return {"groups": await asyncio.to_thread(db_admin_get_groups)}


@router.post("/api/admin/groups")
async def api_admin_create_group(request: Request, user: dict = Depends(require_admin_csrf)):
    """그룹 생성. body: {name, description?}"""
    body = await json_body(request)
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
async def api_admin_delete_group(group_id: int, user: dict = Depends(require_admin_csrf)):
    """그룹 삭제 — 멤버·보고서 부여도 함께 제거(개별 부여는 영향 없음)."""
    deleted = await asyncio.to_thread(db_admin_delete_group, group_id)
    if not deleted:
        raise AppError.GROUP_NOT_FOUND.http()
    logger.info("ADMIN DEL GROUP | admin=%s | group_id=%s", user["username"], group_id)
    return {"deleted": True}


@router.get("/api/admin/groups/{group_id}/members")
async def api_admin_get_group_members(group_id: int, user: dict = Depends(require_admin_user)):
    """활성 사용자 전체 + 소속 여부 (멤버 편집 모달)."""
    return {"members": await asyncio.to_thread(db_get_group_members, group_id)}


@router.post("/api/admin/groups/{group_id}/members/{user_id}")
async def api_admin_set_group_member(
    request: Request, group_id: int, user_id: int, user: dict = Depends(require_admin_csrf),
):
    """그룹 멤버 추가/제거. body: {member: bool}"""
    body = await json_body(request)
    member = bool(body.get("member", False))
    try:
        await asyncio.to_thread(db_set_group_member, group_id, user_id, member, user["id"])
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.GROUP_NOT_FOUND.http()
    return {"group_id": group_id, "user_id": user_id, "member": member}


@router.get("/api/admin/reports/{report_id}/group-access")
async def api_admin_get_group_access(report_id: int, user: dict = Depends(require_admin_user)):
    """보고서에 부여된 그룹 현황 (권한 모달 '그룹' 탭)."""
    return {"groups": await asyncio.to_thread(db_get_report_group_access, report_id)}


@router.post("/api/admin/reports/{report_id}/group-access/{group_id}")
async def api_admin_set_group_access(
    request: Request, report_id: int, group_id: int, user: dict = Depends(require_admin_csrf),
):
    """보고서×그룹 열람 권한 부여/해제. body: {can_view: bool}"""
    body = await json_body(request)
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
    "activity_log_retention_days": (7, 3650),
    "error_log_retention_days":  (7, 3650),
}


@router.get("/api/admin/config")
async def api_admin_get_config(user: dict = Depends(require_admin_user)):
    """런타임 설정(app_config) 목록."""
    rows = await asyncio.to_thread(db_get_app_config)
    return {"config": rows}


@router.post("/api/admin/config")
async def api_admin_set_config(request: Request, user: dict = Depends(require_admin_csrf)):
    """런타임 설정 변경 — 저장 즉시 재시작 없이 반영된다.

    키는 마이그레이션이 시드한 것만 허용하고, 값은 정수만 받는다(현재 키 전부 정수)."""
    body = await json_body(request)
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
async def api_admin_get_reports(user: dict = Depends(require_admin_user)):
    """보고서 목록 재조회 — 관리자 포털을 새로고침 없이 최신 상태로 유지한다.

    새 보고서는 직원 업로드·가져오기로 페이지 로드 이후에도 생기므로,
    부트스트랩 데이터만으로는 권한부여 화면이 낡은 상태로 남는다.
    """
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
    can_upload: bool = Form(True),
    group_ids: str = Form(""),
    csrf: str = Form(),
    user: dict = Depends(require_admin_user),
):
    verify_csrf(request, csrf)
    if len(password) < config.PASSWORD_MIN_LEN:
        raise AppError.PASSWORD_TOO_SHORT.http(min=config.PASSWORD_MIN_LEN)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    # 폼은 콤마 구분 문자열로 받고 DB에는 TEXT[] 배열로 저장한다
    role_list = [r.strip() for r in roles.split(",") if r.strip()] or ["도메인"]
    group_id_list = [int(g) for g in group_ids.split(",") if g.strip()]
    try:
        new_id = await asyncio.to_thread(
            db_admin_add_user, username, pw_hash, display_name,
            pbi_username or username, role_list, is_admin, can_upload, group_id_list,
        )
    except psycopg2.errors.UniqueViolation:
        raise AppError.USER_ALREADY_EXISTS.http(username=username)
    logger.info(
        "ADMIN ADD USER | admin=%s | new=%s | id=%s | groups=%s",
        user["username"], username, new_id, group_id_list,
    )
    return {"id": new_id, "username": username}


def _parse_csv_bool(value: str, default: bool) -> bool:
    v = value.strip().lower()
    if not v:
        return default
    return v in ("true", "1", "y", "yes")


def _bulk_add_one(row: dict, group_map: dict[str, int]) -> tuple[str, str | None]:
    """CSV 한 행을 사용자 1명으로 등록. 반환: (상태, 오류메시지|None)."""
    username = (row.get("username") or "").strip()
    password = row.get("password") or ""
    display_name = (row.get("display_name") or "").strip()
    if not username or not password or not display_name:
        return "error", "username/password/display_name은 필수입니다."
    if len(password) < config.PASSWORD_MIN_LEN:
        return "error", f"비밀번호는 {config.PASSWORD_MIN_LEN}자 이상이어야 합니다."

    group_names = [g.strip() for g in (row.get("groups") or "").split(";") if g.strip()]
    missing = [g for g in group_names if g not in group_map]
    if missing:
        return "error", f"존재하지 않는 그룹: {', '.join(missing)}"
    group_id_list = [group_map[g] for g in group_names]

    role_list = [r.strip() for r in (row.get("roles") or "").split(";") if r.strip()] or ["도메인"]
    is_admin = _parse_csv_bool(row.get("is_admin") or "", False)
    can_upload = _parse_csv_bool(row.get("can_upload") or "", True)
    pbi_username = (row.get("pbi_username") or "").strip() or username
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    try:
        db_admin_add_user(
            username, pw_hash, display_name, pbi_username,
            role_list, is_admin, can_upload, group_id_list,
        )
    except psycopg2.errors.UniqueViolation:
        return "error", f"'{username}' 아이디가 이미 존재합니다."
    return "ok", None


MAX_BULK_USER_ROWS = 500


@router.post("/api/admin/users/bulk-import")
async def api_admin_bulk_add_users(
    request: Request,
    file: UploadFile = File(...),
    csrf: str = Form(),
    user: dict = Depends(require_admin_user),
):
    """CSV로 사용자 여러 명을 한 번에 등록한다.

    헤더: username,password,display_name,pbi_username,roles,groups,is_admin,can_upload
    roles/groups는 세미콜론(;)으로 여러 값 구분. groups는 미리 존재하는 그룹 이름만 허용—
    그룹×보고서 권한은 그룹 쪽에서 한 번만 설정해두면, 이 경로로 늘어나는 인원은
    그룹 멤버십만으로 자동으로 동일한 열람 권한을 받는다.
    """
    verify_csrf(request, csrf)
    raw = await file.read()
    if not raw:
        raise AppError.CSV_EMPTY.http()
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"username", "password", "display_name"} <= set(reader.fieldnames):
        raise AppError.CSV_HEADER_INVALID.http()
    rows = list(reader)
    if len(rows) > MAX_BULK_USER_ROWS:
        raise AppError.CSV_TOO_MANY_ROWS.http(max=MAX_BULK_USER_ROWS)

    groups = await asyncio.to_thread(db_admin_get_groups)
    group_map = {g["name"]: g["id"] for g in groups}

    results = []
    created = 0
    for i, row in enumerate(rows, start=2):  # 헤더가 1행이니 데이터는 2행부터
        status, message = await asyncio.to_thread(_bulk_add_one, row, group_map)
        if status == "ok":
            created += 1
        results.append({"row": i, "username": row.get("username", ""), "status": status, "message": message})

    logger.info(
        "ADMIN BULK ADD USERS | admin=%s | created=%d | failed=%d",
        user["username"], created, len(rows) - created,
    )
    return {"created": created, "failed": len(rows) - created, "results": results}


@router.post("/api/admin/users/{user_id}/toggle-active")
async def api_admin_toggle_user(user_id: int, user: dict = Depends(require_admin_csrf)):
    is_active = await asyncio.to_thread(db_admin_toggle_user_active, user_id)
    if is_active is None:
        raise AppError.USER_NOT_FOUND.http()
    return {"is_active": is_active}


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
async def api_admin_import_pbi(user: dict = Depends(require_admin_csrf)):
    """Fabric 폴더 구조를 읽어 새 공용 보고서 + 대시보드(v6)를 DB에 등록한다.

    이미 등록된 항목은 건너뛴다(pbi_report_id 중복 체크).
    권한은 부여하지 않으므로 등록 후 보고서 관리에서 별도 설정이 필요하다.
    """
    reports = await fetch_pbi_folders_and_reports()
    dashboards = await fetch_pbi_folders_and_dashboards()
    # 삭제 판정 기준 — 대시보드 ID도 반드시 포함해야 방금 등록한 대시보드가
    # 아래 "Fabric에 없는 항목 삭제" 단계에서 즉시 지워지지 않는다.
    fabric_ids = {r["pbi_report_id"] for r in reports} | {d["pbi_dashboard_id"] for d in dashboards}

    registered = skipped = deleted = 0
    # DB 커넥션 풀(기본 20개)을 다 쓰지 않도록 동시 실행 수를 제한한다.
    sem = asyncio.Semaphore(8)

    # 신규 등록 + 기존 보고서 category 업데이트 (동시 실행)
    async def _import_one(r):
        async with sem:
            return await asyncio.to_thread(
                db_import_pbi_item,
                r["pbi_report_id"], r["name"], WORKSPACE_ID,
                r["folder_id"], r["folder_name"], user["id"],
                r["dataset_id"], False,
            )

    async def _import_one_dashboard(d):
        async with sem:
            return await asyncio.to_thread(
                db_import_pbi_item,
                d["pbi_dashboard_id"], d["name"], WORKSPACE_ID,
                d["folder_id"], d["folder_name"], user["id"],
                None, True,
            )

    import_results = await asyncio.gather(*(_import_one(r) for r in reports))
    for r, is_new in zip(reports, import_results):
        if is_new:
            registered += 1
            logger.info("ADMIN IMPORT PBI | admin=%s | report=%s | category=%s",
                        user["username"], r["name"], r["folder_name"])
        else:
            skipped += 1

    dash_results = await asyncio.gather(*(_import_one_dashboard(d) for d in dashboards))
    for d, is_new in zip(dashboards, dash_results):
        if is_new:
            registered += 1
            logger.info("ADMIN IMPORT PBI DASHBOARD | admin=%s | dashboard=%s | category=%s",
                        user["username"], d["name"], d["folder_name"])
        else:
            skipped += 1

    # Fabric에 없는 보고서/대시보드는 DB에서 완전 삭제 (동시 실행)
    async def _delete_one(row):
        async with sem:
            return await asyncio.to_thread(db_hard_delete_report, row["id"])

    db_reports = await asyncio.to_thread(db_get_synced_reports)
    to_delete = [row for row in db_reports if row["pbi_report_id"] not in fabric_ids]
    delete_results = await asyncio.gather(*(_delete_one(row) for row in to_delete))
    for row, did_delete in zip(to_delete, delete_results):
        if did_delete:
            deleted += 1
            logger.info("ADMIN IMPORT PBI DELETE | admin=%s | report=%s",
                        user["username"], row["name"])

    total = len(reports) + len(dashboards)
    logger.info("ADMIN IMPORT PBI DONE | admin=%s | registered=%d | skipped=%d | deleted=%d",
                user["username"], registered, skipped, deleted)
    return {"registered": registered, "skipped": skipped, "deleted": deleted, "total": total}


@router.get("/api/admin/sync-status")
async def api_admin_sync_status(user: dict = Depends(require_admin_user)):
    """Fabric 현재 상태와 DB를 대조해 '가져오기 필요' 여부를 반환한다.

    신규(폴더 추가/직접 게시), 폴더 이동·이름변경(category 불일치),
    Fabric에서 사라진 보고서(삭제 대상)를 감지한다. import-pbi 실행 시 모두 정리된다.
    """
    try:
        fabric_reports = await fetch_pbi_folders_and_reports()
        fabric_dashboards = await fetch_pbi_folders_and_dashboards()
        db_map = await asyncio.to_thread(db_get_pbi_report_map)
    except Exception as exc:
        logger.warning("SYNC STATUS FAIL | admin=%s | error=%s", user["username"], exc)
        return {"available": False, "drift": False}

    # db_get_pbi_report_map은 report_type 무관하게 반환하므로(v6 대시보드 포함),
    # 대시보드를 report와 같은 모양으로 맞춰 합친다 — 안 그러면 대시보드가
    # 매번 "Fabric에서 사라짐(removed)"으로 오탐된다.
    fabric = fabric_reports + [
        {"pbi_report_id": d["pbi_dashboard_id"], "name": d["name"], "folder_name": d["folder_name"]}
        for d in fabric_dashboards
    ]

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
    can_view = bool(body.get("can_view", False))
    await asyncio.to_thread(db_set_report_access, report_id, user_id, can_view, user["id"])
    if not can_view:
        invalidate_embed_cache(report_id)
    logger.info("ADMIN ACCESS | admin=%s | report_id=%s | user_id=%s | can_view=%s",
                user["username"], report_id, user_id, can_view)
    return {"can_view": can_view}


# ── 권한 매트릭스 / 로그 / 편의 (v3) ─────────────────────────────────────────

@router.post("/api/admin/users/{user_id}/toggle-upload")
async def api_admin_toggle_upload(user_id: int, user: dict = Depends(require_admin_csrf)):
    """사용자의 보고서 업로드 권한을 켜고 끈다."""
    can_upload = await asyncio.to_thread(db_admin_toggle_user_upload, user_id)
    if can_upload is None:
        raise AppError.USER_NOT_FOUND.http()
    logger.info("ADMIN UPLOAD PERM | admin=%s | user_id=%s | can_upload=%s",
                user["username"], user_id, can_upload)
    return {"can_upload": can_upload}


def _fetch_logs(log_type: str, username: str, event: str, date_from: str, date_to: str):
    """로그 화면·CSV가 공유하는 조회. log_type: activity(사용자 활동) | audit(관리 감사)."""
    if log_type == "audit":
        return db_get_audit_log(date_from or None, date_to or None)
    return db_get_activity_log(username or None, event or None,
                               date_from or None, date_to or None)


@router.get("/api/admin/logs")
async def api_admin_logs(
    type: str = "activity", username: str = "", event: str = "",
    date_from: str = "", date_to: str = "",
    user: dict = Depends(require_admin_user),
):
    """활동/감사 로그 조회 (관리자 로그 탭)."""
    rows = await asyncio.to_thread(_fetch_logs, type, username, event, date_from, date_to)
    return {"rows": rows}


@router.get("/api/admin/logs/export")
async def api_admin_logs_export(
    type: str = "activity", username: str = "", event: str = "",
    date_from: str = "", date_to: str = "",
    user: dict = Depends(require_admin_user),
):
    """로그 CSV 다운로드. BOM을 붙여 Excel에서 한글이 깨지지 않게 한다."""
    rows = await asyncio.to_thread(_fetch_logs, type, username, event, date_from, date_to)
    buf = io.StringIO()
    writer = csv.writer(buf)
    if type == "audit":
        writer.writerow(["일시", "행위자", "행위", "보고서", "상세"])
        for r in rows:
            writer.writerow([r["created_at"], r["actor"] or "", r["action"],
                             r["report_name"] or "", str(r["details"])])
    else:
        writer.writerow(["일시", "사용자", "이벤트", "보고서", "IP"])
        for r in rows:
            writer.writerow([r["created_at"], r["username"], r["event"],
                             r["report_name"] or "", r["ip"] or ""])
    logger.info("ADMIN LOG EXPORT | admin=%s | type=%s | rows=%d", user["username"], type, len(rows))
    return Response(
        content="\ufeff" + buf.getvalue(),  # BOM: Excel 한글 인코딩 인식용
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={type}_log.csv"},
    )


# ── v4: 신선도·자가진단 ──────────────────────────────────────────────────────

@router.get("/api/admin/system-status")
async def api_admin_system_status(user: dict = Depends(require_admin_user)):
    """자가진단: DB 응답시간·백그라운드 루프 하트비트·실패 잡 수."""
    import time as _time
    t0 = _time.perf_counter()
    stats = await asyncio.to_thread(db_admin_get_stats)
    db_ms = round((_time.perf_counter() - t0) * 1000)
    now = _time.time()
    heartbeats = {
        k: round(now - v) for k, v in LOOP_HEARTBEAT.items()  # 초 단위 경과
    }
    return {
        "db_latency_ms": db_ms,
        "loop_seconds_ago": heartbeats,      # {"pbi_sync": 132}
        "sync_interval_sec": config.PBI_SYNC_INTERVAL,
        **stats,
    }


