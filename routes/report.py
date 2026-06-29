"""보고서 열람·업로드 라우트: /, /api/embed, /api/upload, /health, /docs."""
import asyncio
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import (
    PBI_API, WORKSPACE_ID,
    MAX_PBIX_SIZE, REPORT_NAME_MAX_LEN,
    IMPORT_POLL_MAX, IMPORT_POLL_INTERVAL,
)
from database import (
    db_get_reports, db_get_all_active_reports, db_can_view_report, db_find_report,
    db_reserve_upload, db_update_upload_job, db_get_upload_job, db_register_report,
    db_health_check,
    db_get_user_favorites, db_set_favorite, db_get_user_recents, db_add_recent,
)
from deps import current_user, csrf_token, verify_csrf, get_client_ip
from errors import AppError
from services.azure import get_access_token
from services.fabric_folders import get_or_create_folder, move_item_to_folder
from services.powerbi import get_embed_token, pbi_rename_report, pbi_rename_dataset

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("powerbi-gateway")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = await current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user.get("is_admin"):
        report_list = await asyncio.to_thread(db_get_all_active_reports)
    else:
        report_list = await asyncio.to_thread(db_get_reports, user["username"])
    favorites = await asyncio.to_thread(db_get_user_favorites, user["id"])
    recents   = await asyncio.to_thread(db_get_user_recents, user["id"])
    return templates.TemplateResponse(request, "report.html", {
        "user":      user,
        "reports":   report_list,
        "favorites": favorites,
        "recents":   recents,
        "csrf_token": csrf_token(request),
    })


@router.post("/api/favorites/{report_id}")
async def api_set_favorite(request: Request, report_id: int):
    """즐겨찾기 추가/해제. body: {"favorite": true|false}"""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    body = await request.json()
    on = bool(body.get("favorite", False))
    await asyncio.to_thread(db_set_favorite, user["id"], report_id, on)
    return {"report_id": report_id, "favorite": on}


@router.post("/api/recents/{report_id}")
async def api_add_recent(request: Request, report_id: int):
    """최근 본 보고서 기록."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    await asyncio.to_thread(db_add_recent, user["id"], report_id)
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
    if not user.get("is_admin") and not await asyncio.to_thread(db_can_view_report, user["username"], report_id):
        logger.warning("EMBED DENY | user=%-12s | ip=%s | report_id=%s (권한없음)", user["username"], ip, report_id)
        raise AppError.FORBIDDEN_REPORT.http()
    logger.info("EMBED OK   | user=%-12s | ip=%s | report_id=%s", user["username"], ip, report_id)
    return await get_embed_token(report_id, user["pbi_username"], user["roles"])


@router.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = File(...), report_name: str = Form("")):
    """파일 수신 후 즉시 job_id 반환. 실제 PBI 게시는 백그라운드에서 진행."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()

    name, pbix_bytes, file_size = await _read_and_validate_pbix(file, report_name, user["id"])
    job_id = await asyncio.to_thread(db_reserve_upload, user["id"], name)
    logger.info("UPLOAD RESERVED | user=%-12s | report=%s | job_id=%s", user["username"], name, job_id)

    asyncio.create_task(_process_upload(user, name, pbix_bytes, file_size, job_id, request.client.host))
    return {"job_id": job_id, "report_name": name, "status": "accepted"}


@router.get("/api/upload/status/{job_id}")
async def api_upload_status(request: Request, job_id: int):
    """업로드 잡 상태 폴링 엔드포인트."""
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    job = await asyncio.to_thread(db_get_upload_job, job_id, user["id"])
    if not job:
        raise AppError.REPORT_NOT_FOUND.http()
    return {
        "job_id":      job["id"],
        "status":      job["status"],
        "report_name": job["report_name"],
        "report_id":   job["report_id"],
        "error":       job["error_message"] if job["status"] not in ("completed", "accepted", "publishing", "accepted", "pbi_succeeded") else None,
    }


async def _read_and_validate_pbix(
    file: UploadFile, report_name: str, user_id: int
) -> tuple[str, bytes, int]:
    """파일 검증 후 (report_name, pbix_bytes, file_size) 반환."""
    if not file.filename or not file.filename.lower().endswith(".pbix"):
        raise AppError.FILE_WRONG_TYPE.http()

    name = report_name.strip() or Path(file.filename).stem.strip()
    if not name or len(name) > REPORT_NAME_MAX_LEN:
        raise AppError.NAME_INVALID.http(max=REPORT_NAME_MAX_LEN)

    if await asyncio.to_thread(db_find_report, user_id, name):
        raise AppError.REPORT_NAME_CONFLICT.http(name=name)

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if not file_size:
        raise AppError.FILE_EMPTY.http()
    if file_size > MAX_PBIX_SIZE:
        raise AppError.FILE_TOO_LARGE.http(max_mb=MAX_PBIX_SIZE // (1024 * 1024))
    if file.file.read(4)[:2] != b"PK":
        raise AppError.FILE_INVALID_CONTENT.http()
    file.file.seek(0)

    return name, await asyncio.to_thread(file.file.read), file_size


async def _rename_with_retry(
    workspace_id: str, pbi_report_id: str, dataset_ids: list[str],
    display_name: str, fallback_name: str, username: str,
) -> tuple[str, str | None]:
    """보고서·데이터셋 이름 변경. 최대 5회 시도, 최종 실패 시 (fallback_name, warning) 반환."""
    await asyncio.sleep(8)  # PBI가 import 직후 보고서를 활성화하는 데 시간이 걸림
    final_name = display_name
    warning    = None
    for attempt in range(5):
        if attempt > 0:
            await asyncio.sleep(5)
        try:
            await pbi_rename_report(workspace_id, pbi_report_id, display_name)
            for ds_id in dataset_ids:
                await pbi_rename_dataset(workspace_id, ds_id, display_name)
            logger.info("UPLOAD RENAME OK | user=%-12s | display=%s (attempt=%s)", username, display_name, attempt + 1)
            return display_name, None
        except Exception as exc:
            if attempt == 4:
                logger.warning("UPLOAD RENAME WARN | user=%s | display=%s | error=%s", username, display_name, exc)
                return fallback_name, f"rename failed after 5 attempts: {exc}"
    return final_name, warning


async def _process_upload(user: dict, name: str, pbix_bytes: bytes, file_size: int, job_id: int, ip: str):
    """백그라운드 태스크: 파일 검증·예약은 호출자(api_upload)에서 완료된 상태로 진입."""
    logger.info("UPLOAD START | user=%-12s | ip=%s | report=%s | bytes=%s", user["username"], ip, name, file_size)

    try:
        access_token = await asyncio.to_thread(get_access_token)
    except Exception as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"token: {exc}")
        raise
    headers = {"Authorization": f"Bearer {access_token}"}

    # 1) .pbix 신규 게시
    import_name   = f"{user['username']}__{name}"
    import_params = {"datasetDisplayName": f"{import_name}.pbix", "nameConflict": "CreateOrOverwrite"}

    async with httpx.AsyncClient(timeout=300) as client:
        try:
            resp = await client.post(
                f"{PBI_API}/imports", params=import_params, headers=headers,
                files={"file": (f"{import_name}.pbix", io.BytesIO(pbix_bytes), "application/octet-stream")},
            )
        except httpx.RequestError as exc:
            await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message=f"import request: {exc}")
            raise AppError.IMPORT_UNCONFIRMED.http() from exc

        if resp.status_code == 409:
            await asyncio.to_thread(db_update_upload_job, job_id, "conflict", error_message="name conflict")
            raise AppError.IMPORT_NAME_CONFLICT.http()
        if resp.status_code not in (200, 202):
            await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"import HTTP {resp.status_code}")
            logger.warning("UPLOAD FAIL| user=%-12s | ip=%s | report=%s (%s)", user["username"], ip, name, resp.status_code)
            raise AppError.IMPORT_REQUEST_FAILED.http(detail=resp.text)

        import_id = resp.json()["id"]
        await asyncio.to_thread(db_update_upload_job, job_id, "accepted", import_id=import_id, pbi_workspace_id=WORKSPACE_ID)
        logger.info("UPLOAD ACCEPT | user=%-12s | report=%s | import_id=%s", user["username"], name, import_id)

        # 2) 변환 완료 대기
        for _ in range(IMPORT_POLL_MAX):
            await asyncio.sleep(IMPORT_POLL_INTERVAL)
            resp = await client.get(f"{PBI_API}/imports/{import_id}", headers=headers)
            if resp.status_code != 200:
                await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message=f"poll HTTP {resp.status_code}")
                raise AppError.IMPORT_POLL_FAILED.http(detail=resp.text)
            result = resp.json()
            state  = result.get("importState")
            if state == "Succeeded":
                break
            if state == "Failed":
                await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=str(result.get("error")))
                raise AppError.IMPORT_FAILED.http(detail=str(result.get("error")))
        else:
            await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message="poll timeout")
            raise AppError.IMPORT_TIMEOUT.http()

    reports = result.get("reports", [])
    if not reports:
        await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message="no report in response")
        raise AppError.IMPORT_NO_REPORT.http()

    pbi_report_id    = reports[0]["id"]
    dataset_ids      = [d["id"] for d in result.get("datasets", [])]
    pbi_display_name = f"{user['username']}__{name}"
    pbi_display_name, rename_warning = await _rename_with_retry(
        WORKSPACE_ID, pbi_report_id, dataset_ids, pbi_display_name, import_name, user["username"]
    )

    await asyncio.to_thread(db_update_upload_job, job_id, "pbi_succeeded",
                            pbi_report_id=pbi_report_id, error_message=rename_warning)
    logger.info("UPLOAD PBI OK | user=%-12s | report=%s | pbi_report_id=%s", user["username"], name, pbi_report_id)

    # /* fabric */
    # 3) Fabric 사용자 폴더로 이동 (비치명적 — 실패해도 보고서 기능에 영향 없음)
    folder_id = await get_or_create_folder(WORKSPACE_ID, user["username"])
    if folder_id:
        items = [pbi_report_id] + dataset_ids
        results = await asyncio.gather(
            *[move_item_to_folder(WORKSPACE_ID, iid, folder_id) for iid in items],
            return_exceptions=True,
        )
        moved = sum(1 for r in results if r is True)
        logger.info("FOLDER MOVE | user=%-12s | moved=%d/%d", user["username"], moved, len(items))
    else:
        logger.warning("FOLDER UNAVAILABLE | user=%s | report will stay at workspace root", user["username"])

    # 4) 게이트웨이 DB 등록
    try:
        gateway_report_id = await asyncio.to_thread(
            db_register_report, name, pbi_report_id, user["id"],
            dataset_ids[0] if dataset_ids else None,
            WORKSPACE_ID, pbi_display_name,
            user["username"],  # Fabric 폴더명 = username → 사이드바 폴더 트리에 반영
        )
    except psycopg2.Error as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "db_failed", error_message=str(exc))
        logger.exception("UPLOAD DB FAIL | user=%s | report=%s | pbi_report_id=%s", user["username"], name, pbi_report_id)
        raise AppError.UPLOAD_DB_FAILED.http() from exc

    await asyncio.to_thread(db_update_upload_job, job_id, "completed", report_id=gateway_report_id, error_message=None)
    logger.info("UPLOAD OK  | user=%-12s | ip=%s | report=%s", user["username"], ip, name)
    return {"report_name": name, "pbi_display_name": pbi_display_name, "new": True}
