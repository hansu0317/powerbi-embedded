"""보고서 열람·업로드 라우트: /, /api/embed, /api/upload, /health, /docs."""
import asyncio
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2
import psycopg2.errors
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from config import PBI_API, WORKSPACE_ID
from database import (
    db_get_reports, db_get_all_active_reports, db_can_view_report, db_find_report,
    db_reserve_upload, db_update_upload_job, db_get_upload_job, db_register_report,
    db_health_check,
    db_get_user_favorites, db_set_favorite, db_get_user_recents, db_add_recent,
    db_fail_stuck_upload_job,
    db_log_activity, db_get_popular_report_ids, db_set_default_report,
)
from deps import current_user, csrf_token, verify_csrf, get_client_ip
from errors import AppError
from services.azure import get_access_token
from services.fabric_folders import get_or_create_folder, move_item_to_folder
from services.powerbi import get_embed_token, rename_with_retry

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

    # 인기 보고서: 전체 순위를 뽑은 뒤 이 사용자가 볼 수 있는 것과 교집합 → 상위 5개.
    # 권한 없는 보고서가 인기 목록으로 존재를 노출하지 않도록 필터링이 필수다.
    visible_ids = {r["id"] for r in report_list}
    ranking = await asyncio.to_thread(db_get_popular_report_ids, 30, 20)
    popular = [
        {"report_id": row["report_id"], "views": row["views"]}
        for row in ranking if row["report_id"] in visible_ids
    ][:5]

    return templates.TemplateResponse(request, "report.html", {
        "user":      user,
        "reports":   report_list,
        "favorites": favorites,
        "recents":   recents,
        "popular":   popular,
        "csrf_token": csrf_token(request),
    })


async def _require_viewable_report(user: dict, report_id: int, error: AppError = AppError.REPORT_NOT_FOUND):
    """열람 권한이 없거나 존재하지 않는 보고서 ID를 걸러낸다 (임베드와 동일 정책).

    기본(error=REPORT_NOT_FOUND)은 권한 없음과 존재하지 않음을 같은 404로 응답해
    보고서 존재 여부를 노출하지 않는다. api_embed는 403(FORBIDDEN_REPORT)을 넘겨 쓴다.
    관리자는 권한 확인을 건너뛰지만 없는 ID는 FK 위반을 404로 변환해 걸러진다."""
    if not user.get("is_admin") and not await asyncio.to_thread(
        db_can_view_report, user["username"], report_id
    ):
        raise error.http()


@router.post("/api/favorites/{report_id}")
async def api_set_favorite(request: Request, report_id: int):
    """즐겨찾기 추가/해제. body: {"favorite": true|false}"""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    await _require_viewable_report(user, report_id)
    body = await request.json()
    on = bool(body.get("favorite", False))
    try:
        await asyncio.to_thread(db_set_favorite, user["id"], report_id, on)
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.REPORT_NOT_FOUND.http()
    return {"report_id": report_id, "favorite": on}


@router.post("/api/recents/{report_id}")
async def api_add_recent(request: Request, report_id: int):
    """최근 본 보고서 기록."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    await _require_viewable_report(user, report_id)
    try:
        await asyncio.to_thread(db_add_recent, user["id"], report_id)
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.REPORT_NOT_FOUND.http()
    return {"report_id": report_id, "status": "ok"}


@router.post("/api/user/default-report")
async def api_set_default_report(request: Request):
    """기본 보고서 설정/해제. body: {"report_id": int|null}

    설정된 보고서는 뷰어 진입 시 자동으로 열린다. null이면 해제."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    body = await request.json()
    report_id = body.get("report_id")
    if report_id is not None:
        report_id = int(report_id)
        await _require_viewable_report(user, report_id)
    try:
        await asyncio.to_thread(db_set_default_report, user["id"], report_id)
    except psycopg2.errors.ForeignKeyViolation:
        raise AppError.REPORT_NOT_FOUND.http()
    return {"default_report_id": report_id}


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
    result = await get_embed_token(report_id, user["pbi_username"], user["roles"])
    await asyncio.to_thread(
        db_log_activity, user["id"], user["username"], "report_view",
        report_id, result.get("report_name"), ip,
    )
    return result


@router.post("/api/upload")
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
    report_name: str = Form(""),
    report_description: str = Form(""),
):
    """파일 수신 후 즉시 job_id 반환. 실제 PBI 게시는 백그라운드에서 진행."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    if not user.get("can_upload"):
        logger.warning("UPLOAD DENY | user=%-12s | 업로드 권한 없음", user["username"])
        raise AppError.FORBIDDEN_UPLOAD.http()

    name, pbix_bytes, file_size = await _read_and_validate_pbix(file, report_name, user["id"])
    job_id = await asyncio.to_thread(db_reserve_upload, user["id"], name)
    logger.info("UPLOAD RESERVED | user=%-12s | report=%s | job_id=%s", user["username"], name, job_id)
    ip = get_client_ip(request)
    await asyncio.to_thread(db_log_activity, user["id"], user["username"], "report_upload", None, name, ip)

    asyncio.create_task(_process_upload(user, name, pbix_bytes, file_size, job_id, ip,
                                        report_description.strip()[:500] or None))
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
        "error":       job["error_message"] if job["status"] not in ("completed", "accepted", "publishing", "pbi_succeeded") else None,
    }


async def _read_and_validate_pbix(
    file: UploadFile, report_name: str, user_id: int
) -> tuple[str, bytes, int]:
    """파일 검증 후 (report_name, pbix_bytes, file_size) 반환."""
    if not file.filename or not file.filename.lower().endswith(".pbix"):
        raise AppError.FILE_WRONG_TYPE.http()

    name = report_name.strip() or Path(file.filename).stem.strip()
    if not name or len(name) > config.REPORT_NAME_MAX_LEN:
        raise AppError.NAME_INVALID.http(max=config.REPORT_NAME_MAX_LEN)

    if await asyncio.to_thread(db_find_report, user_id, name):
        raise AppError.REPORT_NAME_CONFLICT.http(name=name)

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if not file_size:
        raise AppError.FILE_EMPTY.http()
    if file_size > config.MAX_PBIX_SIZE:
        raise AppError.FILE_TOO_LARGE.http(max_mb=config.MAX_PBIX_SIZE // (1024 * 1024))
    if file.file.read(4)[:2] != b"PK":
        raise AppError.FILE_INVALID_CONTENT.http()
    file.file.seek(0)

    return name, await asyncio.to_thread(file.file.read), file_size


async def _process_upload(user: dict, name: str, pbix_bytes: bytes, file_size: int, job_id: int, ip: str,
                          description: str | None = None):
    """백그라운드 태스크 진입점 — 어떤 예외도 잡을 '진행 중' 상태로 남기지 않는다.

    알려진 실패는 _run_upload 각 지점이 잡 상태(failed/conflict/unknown 등)를 기록한 뒤
    HTTPException으로 탈출한다. 그 밖의 예상 밖 예외가 새면 잡이 publishing/accepted로
    남아 서버 재시작 전까지 같은 이름 재업로드가 409로 막히므로, 여기서 failed 처리한다."""
    try:
        await _run_upload(user, name, pbix_bytes, file_size, job_id, ip, description)
    except HTTPException:
        pass  # 알려진 실패 — 잡 상태는 발생 지점에서 이미 기록됨
    except Exception:
        logger.exception("UPLOAD UNEXPECTED | user=%s | report=%s | job_id=%s", user["username"], name, job_id)
        try:
            marked = await asyncio.to_thread(db_fail_stuck_upload_job, job_id)
            if marked:
                logger.warning("UPLOAD STUCK→FAILED | job_id=%s", job_id)
        except Exception:
            logger.exception("UPLOAD STUCK MARK FAIL | job_id=%s", job_id)


async def _run_upload(user: dict, name: str, pbix_bytes: bytes, file_size: int, job_id: int, ip: str,
                      description: str | None = None):
    """실제 게시 파이프라인 오케스트레이터: 파일 검증·예약은 호출자(api_upload)에서 완료된 상태로 진입.

    각 단계는 실패 시 잡 상태(failed/conflict/unknown 등)를 스스로 기록한 뒤 HTTPException으로 탈출한다.
    """
    logger.info("UPLOAD START | user=%-12s | ip=%s | report=%s | bytes=%s", user["username"], ip, name, file_size)

    # 1~2) .pbix 게시 + 변환 완료 대기
    result = await _pbi_import_pbix(user, name, pbix_bytes, job_id, ip)

    reports = result.get("reports", [])
    if not reports:
        await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message="no report in response")
        raise AppError.IMPORT_NO_REPORT.http()
    pbi_report_id = reports[0]["id"]
    dataset_ids   = [d["id"] for d in result.get("datasets", [])]

    # 3) 이름 변경 (username__보고서명, 최대 5회 재시도 — 실패해도 경고만 남기고 진행)
    import_name = f"{user['username']}__{name}"
    pbi_display_name, rename_warning = await rename_with_retry(
        WORKSPACE_ID, pbi_report_id, dataset_ids, import_name, import_name, user["username"]
    )
    await asyncio.to_thread(db_update_upload_job, job_id, "pbi_succeeded",
                            pbi_report_id=pbi_report_id, error_message=rename_warning)
    logger.info("UPLOAD PBI OK | user=%-12s | report=%s | pbi_report_id=%s", user["username"], name, pbi_report_id)

    # 4) Fabric 사용자 폴더로 이동 (비치명적)
    await _move_to_user_folder(user, pbi_report_id, dataset_ids)

    # 5) 게이트웨이 DB 등록 + 완료 처리
    await _register_uploaded_report(user, name, pbi_report_id, dataset_ids, pbi_display_name, job_id, description)
    logger.info("UPLOAD OK  | user=%-12s | ip=%s | report=%s", user["username"], ip, name)
    return {"report_name": name, "pbi_display_name": pbi_display_name, "new": True}


async def _pbi_import_pbix(user: dict, name: str, pbix_bytes: bytes, job_id: int, ip: str) -> dict:
    """.pbix를 PBI에 게시하고 변환 완료까지 폴링한다. 성공 시 import 결과(JSON)를 반환."""
    try:
        access_token = await asyncio.to_thread(get_access_token)
    except Exception as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"token: {exc}")
        raise
    headers = {"Authorization": f"Bearer {access_token}"}

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

        # 변환 완료 대기
        for _ in range(config.IMPORT_POLL_MAX):
            await asyncio.sleep(config.IMPORT_POLL_INTERVAL)
            resp = await client.get(f"{PBI_API}/imports/{import_id}", headers=headers)
            if resp.status_code != 200:
                await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message=f"poll HTTP {resp.status_code}")
                raise AppError.IMPORT_POLL_FAILED.http(detail=resp.text)
            result = resp.json()
            state  = result.get("importState")
            if state == "Succeeded":
                return result
            if state == "Failed":
                await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=str(result.get("error")))
                raise AppError.IMPORT_FAILED.http(detail=str(result.get("error")))

        await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message="poll timeout")
        raise AppError.IMPORT_TIMEOUT.http()


async def _move_to_user_folder(user: dict, pbi_report_id: str, dataset_ids: list[str]):
    """/* fabric */ 사용자 폴더 생성 후 보고서·데이터셋 이동. 실패해도 보고서 기능에 영향 없음."""
    folder_id = await get_or_create_folder(WORKSPACE_ID, user["username"])
    if not folder_id:
        logger.warning("FOLDER UNAVAILABLE | user=%s | report will stay at workspace root", user["username"])
        return
    items = [pbi_report_id] + dataset_ids
    results = await asyncio.gather(
        *[move_item_to_folder(WORKSPACE_ID, iid, folder_id) for iid in items],
        return_exceptions=True,
    )
    moved = sum(1 for r in results if r is True)
    logger.info("FOLDER MOVE | user=%-12s | moved=%d/%d", user["username"], moved, len(items))


async def _register_uploaded_report(
    user: dict, name: str, pbi_report_id: str, dataset_ids: list[str],
    pbi_display_name: str, job_id: int, description: str | None = None,
):
    """게이트웨이 DB에 보고서를 등록하고 잡을 completed로 마감한다. DB 실패는 db_failed로 기록."""
    try:
        gateway_report_id = await asyncio.to_thread(
            db_register_report, name, pbi_report_id, user["id"],
            dataset_ids[0] if dataset_ids else None,
            WORKSPACE_ID, pbi_display_name,
            user["username"],  # Fabric 폴더명 = username → 사이드바 폴더 트리에 반영
            description,
        )
    except psycopg2.Error as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "db_failed", error_message=str(exc))
        logger.exception("UPLOAD DB FAIL | user=%s | report=%s | pbi_report_id=%s", user["username"], name, pbi_report_id)
        raise AppError.UPLOAD_DB_FAILED.http() from exc

    await asyncio.to_thread(db_update_upload_job, job_id, "completed", report_id=gateway_report_id, error_message=None)
