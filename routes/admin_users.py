"""관리자 포털 라우트: 사용자 관리 (/api/admin/users/*, /api/admin/users/{id}/reports).

routes/admin.py에서 2026-08-27에 분리했다 — 원래 파일이 admin 도메인 전체(대시보드·
사용자·보고서권한·설정·로그)를 한 파일에 담아 600줄대 후반까지 커졌고, database/
패키지가 이미 도메인별로 나뉜 것과 결이 안 맞았다. 라우트 등록은 main.py에서
routes.admin_users.router를 별도로 include한다."""
import csv
import io
import asyncio
import logging

import bcrypt
import psycopg2.errors
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.requests import Request

import config
from database import (
    db_admin_add_user, db_admin_update_user, db_admin_toggle_user_active,
    db_admin_toggle_user_upload, db_get_user_report_list,
)
from deps import verify_csrf, require_admin_user, require_admin_csrf, json_body
from errors import AppError

router = APIRouter()
logger = logging.getLogger("powerbi-gateway")


@router.get("/api/admin/users/{user_id}/reports")
async def api_admin_get_user_reports(user_id: int, user: dict = Depends(require_admin_user)):
    """사용자가 열람 가능한 보고서 목록 (직접/부서 경로 포함) — '보고서 N' 클릭 팝업."""
    reports = await asyncio.to_thread(db_get_user_report_list, user_id)
    return {"reports": reports}


@router.post("/api/admin/users/add")
async def api_admin_add_user(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    display_name: str = Form(),
    pbi_username: str = Form(""),
    is_admin: bool = Form(False),
    can_upload: bool = Form(True),
    department: str = Form(""),
    email: str = Form(""),
    csrf: str = Form(),
    user: dict = Depends(require_admin_user),
):
    verify_csrf(request, csrf)
    if len(password) < config.PASSWORD_MIN_LEN:
        raise AppError.PASSWORD_TOO_SHORT.http(min=config.PASSWORD_MIN_LEN)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    email_clean = email.strip() or None
    try:
        new_id = await asyncio.to_thread(
            db_admin_add_user, username, pw_hash, display_name,
            pbi_username or username, is_admin, can_upload,
            department.strip() or None, email_clean,
        )
    except psycopg2.errors.UniqueViolation as exc:
        if "users_email_unique_idx" in str(exc):
            raise AppError.EMAIL_ALREADY_EXISTS.http(email=email_clean)
        raise AppError.USER_ALREADY_EXISTS.http(username=username)
    logger.info(
        "ADMIN ADD USER | admin=%s | new=%s | id=%s | department=%s",
        user["username"], username, new_id, department.strip() or None,
    )
    return {"id": new_id, "username": username}


@router.post("/api/admin/users/{user_id}/edit")
async def api_admin_edit_user(
    request: Request, user_id: int, user: dict = Depends(require_admin_csrf),
):
    """표시 이름·GET 필터 속성(pbi_username, department) 수정.

    비밀번호·아이디·관리자 권한·업로드 권한은 각각 별도 경로(add 시 지정, toggle-*)에서
    다룬다.
    body: {display_name, pbi_username, department, email}"""
    body = await json_body(request)
    display_name = str(body.get("display_name", "")).strip()
    pbi_username = str(body.get("pbi_username", "")).strip()
    department = str(body.get("department", "")).strip() or None
    email = str(body.get("email", "")).strip() or None
    if not display_name or not pbi_username:
        raise AppError.BODY_INVALID.http()

    try:
        updated = await asyncio.to_thread(
            db_admin_update_user, user_id, display_name, pbi_username, department, email,
        )
    except psycopg2.errors.UniqueViolation:
        raise AppError.EMAIL_ALREADY_EXISTS.http(email=email)
    if not updated:
        raise AppError.USER_NOT_FOUND.http()
    logger.info("ADMIN EDIT USER | admin=%s | user_id=%s", user["username"], user_id)
    return {
        "user_id": user_id, "display_name": display_name, "pbi_username": pbi_username,
        "department": department, "email": email,
    }


def _parse_csv_bool(value: str, default: bool) -> bool:
    v = value.strip().lower()
    if not v:
        return default
    if v in ("true", "1", "y", "yes"):
        return True
    if v in ("false", "0", "n", "no"):
        return False
    raise ValueError(f"불리언 값은 true/false만 허용합니다: '{value}'")


def _bulk_add_one(row: dict) -> tuple[str, str | None]:
    """CSV 한 행을 사용자 1명으로 등록. 반환: (상태, 오류메시지|None)."""
    username = (row.get("username") or "").strip()
    password = row.get("password") or ""
    display_name = (row.get("display_name") or "").strip()
    if not username or not password or not display_name:
        return "error", "username/password/display_name은 필수입니다."
    if len(password) < config.PASSWORD_MIN_LEN:
        return "error", f"비밀번호는 {config.PASSWORD_MIN_LEN}자 이상이어야 합니다."

    try:
        is_admin = _parse_csv_bool(row.get("is_admin") or "", False)
        can_upload = _parse_csv_bool(row.get("can_upload") or "", True)
    except ValueError as exc:
        return "error", str(exc)
    pbi_username = (row.get("pbi_username") or "").strip() or username
    department = (row.get("department") or "").strip() or None
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    try:
        db_admin_add_user(
            username, pw_hash, display_name, pbi_username,
            is_admin, can_upload,
            department,
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

    헤더: username,password,display_name,pbi_username,is_admin,can_upload,department
    department를 채우면 그 부서에 이미 부여된 보고서 열람권한과 GET 필터가 즉시
    적용된다 — 별도로 소속을 추가할 필요가 없다(관리자 포털 '보고서' 탭 → 권한 →
    부서에서 미리 부여해두면 됨).
    """
    verify_csrf(request, csrf)
    raw = await file.read(2 * 1024 * 1024 + 1)
    if not raw:
        raise AppError.CSV_EMPTY.http()
    if len(raw) > 2 * 1024 * 1024:
        raise AppError.CSV_TOO_LARGE.http(max_mb=2)
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"username", "password", "display_name"} <= set(reader.fieldnames):
        raise AppError.CSV_HEADER_INVALID.http()
    rows = list(reader)
    if len(rows) > MAX_BULK_USER_ROWS:
        raise AppError.CSV_TOO_MANY_ROWS.http(max=MAX_BULK_USER_ROWS)

    results = []
    created = 0
    for i, row in enumerate(rows, start=2):  # 헤더가 1행이니 데이터는 2행부터
        status, message = await asyncio.to_thread(_bulk_add_one, row)
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


@router.post("/api/admin/users/{user_id}/toggle-upload")
async def api_admin_toggle_upload(user_id: int, user: dict = Depends(require_admin_csrf)):
    """사용자의 보고서 업로드 권한을 켜고 끈다."""
    can_upload = await asyncio.to_thread(db_admin_toggle_user_upload, user_id)
    if can_upload is None:
        raise AppError.USER_NOT_FOUND.http()
    logger.info("ADMIN UPLOAD PERM | admin=%s | user_id=%s | can_upload=%s",
                user["username"], user_id, can_upload)
    return {"can_upload": can_upload}
