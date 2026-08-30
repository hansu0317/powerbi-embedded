"""관리자 포털 라우트: 사용자 관리 (/api/admin/users/*, /api/admin/users/{id}/reports).

routes/admin.py에서 2026-08-27에 분리했다 — 원래 파일이 admin 도메인 전체(대시보드·
사용자·보고서권한·설정·로그)를 한 파일에 담아 600줄대 후반까지 커졌고, database/
패키지가 이미 도메인별로 나뉜 것과 결이 안 맞았다. 라우트 등록은 main.py에서
routes.admin_users.router를 별도로 include한다."""
import asyncio
import logging

import bcrypt
import psycopg2.errors
from fastapi import APIRouter, Depends, Form
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
