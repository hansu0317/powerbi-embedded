"""보고서 열람·업로드 라우트: /, /api/embed, /api/upload, /health, /docs."""
import asyncio
import io
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2
import psycopg2.errors
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import config
from config import PBI_API, WORKSPACE_ID
from database import (
    db_get_reports, db_get_all_active_reports, db_can_view_report, db_find_report,
    db_reserve_upload, db_update_upload_job, db_get_upload_job, db_register_report,
    db_health_check, db_get_report,
    db_get_user_favorites, db_set_favorite, db_get_user_recents, db_add_recent,
    db_fail_stuck_upload_job,
    db_log_activity, db_get_popular_report_ids, db_set_default_report, db_get_data_as_of,
    db_log_error, db_get_user_activity_log, db_reserve_update,
)
from deps import current_user, csrf_token, verify_csrf, get_client_ip
from errors import AppError, extract_code_message
from services.azure import get_access_token
from services.fabric_folders import get_or_create_folder, move_item_to_folder
from services.powerbi import (
    get_embed_token, rename_with_retry,
    pbi_download_pbix, pbi_start_export, pbi_poll_export, pbi_get_export_file,
    pbi_update_report_content, pbi_delete_report, pbi_delete_dataset,
)

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
        "marketing_portal_url": config.MARKETING_PORTAL_URL,
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
    # 데이터 기준 시각(마지막 refresh 성공) — 뷰어 신선도 배지용. 캐시된 토큰이어도 항상 최신을 읽는다
    fresh = await asyncio.to_thread(db_get_data_as_of, result.get("dataset_id"))
    result["data_as_of"] = fresh["last_success_at"].isoformat() if fresh and fresh["last_success_at"] else None
    result["refresh_status"] = fresh["last_status"] if fresh else None
    # 30분 dedupe: 토큰 자동 재발급·새로고침 탭 복원이 조회수를 부풀리지 않게 한다
    await asyncio.to_thread(
        db_log_activity, user["id"], user["username"], "report_view",
        report_id, result.get("report_name"), ip, 30,
    )
    return result


async def _report_pbi_ids(user: dict, report_id: int) -> tuple[str, str]:
    """다운로드/내보내기 공통: 열람 권한 확인 후 (workspace_id, pbi_report_id) 반환."""
    await _require_viewable_report(user, report_id)
    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["pbi_report_id"]:
        raise AppError.REPORT_NOT_FOUND.http()
    workspace_id = config.resolve_workspace_id(report_row["pbi_workspace_id"])
    return workspace_id, report_row["pbi_report_id"]


@router.get("/api/reports/{report_id}/download/pbix")
async def api_download_pbix(request: Request, report_id: int):
    """원본 .pbix 다운로드 (v6). 대시보드는 지원하지 않는다."""
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    workspace_id, pbi_report_id = await _report_pbi_ids(user, report_id)
    try:
        content = await pbi_download_pbix(workspace_id, pbi_report_id)
    except Exception as exc:
        raise AppError.PBIX_DOWNLOAD_FAILED.http(detail=str(exc))
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="report.pbix"'},
    )


@router.post("/api/reports/{report_id}/export/pptx")
async def api_export_pptx_start(request: Request, report_id: int):
    """PPTX 내보내기 시작 (v6). 전용 용량(Premium/Embedded/Fabric) 필요 — Pro는 503."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    workspace_id, pbi_report_id = await _report_pbi_ids(user, report_id)
    try:
        export_id = await pbi_start_export(workspace_id, pbi_report_id, "PPTX")
    except ValueError:
        raise AppError.PPTX_CAPACITY_REQUIRED.http()
    except Exception as exc:
        raise AppError.PPTX_EXPORT_FAILED.http(detail=str(exc))
    return {"export_id": export_id}


@router.get("/api/reports/{report_id}/export/pptx/{export_id}/status")
async def api_export_pptx_status(request: Request, report_id: int, export_id: str):
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    workspace_id, pbi_report_id = await _report_pbi_ids(user, report_id)
    try:
        status = await pbi_poll_export(workspace_id, pbi_report_id, export_id)
    except Exception as exc:
        raise AppError.PPTX_EXPORT_FAILED.http(detail=str(exc))
    return {"status": status.get("status", "Unknown")}


@router.get("/api/reports/{report_id}/export/pptx/{export_id}/file")
async def api_export_pptx_file(request: Request, report_id: int, export_id: str):
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    workspace_id, pbi_report_id = await _report_pbi_ids(user, report_id)
    try:
        content = await pbi_get_export_file(workspace_id, pbi_report_id, export_id)
    except Exception as exc:
        raise AppError.PPTX_EXPORT_FAILED.http(detail=str(exc))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": 'attachment; filename="report.pptx"'},
    )


@router.get("/api/user/activity")
async def api_user_activity(request: Request):
    """내 활동 로그 (v6) — 일반 사용자가 본인이 열람·업로드한 이력을 직접 확인.

    관리자 전용이던 활동 로그(v3)와 달리 인증만 요구하고 항상 본인 것만 반환한다."""
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()
    rows = await asyncio.to_thread(db_get_user_activity_log, user["id"], 200)
    return {"activity": rows}


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
    if not user.get("is_admin") and not user.get("can_upload"):
        logger.warning("UPLOAD DENY | user=%-12s | 업로드 권한 없음", user["username"])
        raise AppError.FORBIDDEN_UPLOAD.http()

    name, pbix_bytes, file_size = await _read_and_validate_pbix(file, report_name, user["id"])
    job_id = await asyncio.to_thread(db_reserve_upload, user["id"], name)
    logger.info("UPLOAD RESERVED | user=%-12s | report=%s | job_id=%s", user["username"], name, job_id)
    ip = get_client_ip(request)

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


async def _validate_pbix_file(file: UploadFile) -> tuple[bytes, int]:
    """확장자·크기·매직바이트만 검증(이름 중복 체크는 호출자 책임). (pbix_bytes, file_size) 반환.

    신규 업로드(_read_and_validate_pbix)와 v7 콘텐츠 업데이트(_validate_update_pbix)가
    공유하는 순수 파일 검증 — 업데이트는 '이미 존재하는 그 이름'을 업데이트하는 것이라
    이름 중복 체크를 하면 안 된다."""
    if not file.filename or not file.filename.lower().endswith(".pbix"):
        raise AppError.FILE_WRONG_TYPE.http()
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
    return await asyncio.to_thread(file.file.read), file_size


async def _read_and_validate_pbix(
    file: UploadFile, report_name: str, user_id: int
) -> tuple[str, bytes, int]:
    """신규 업로드용 파일 검증 후 (report_name, pbix_bytes, file_size) 반환."""
    if not file.filename or not file.filename.lower().endswith(".pbix"):
        raise AppError.FILE_WRONG_TYPE.http()
    name = report_name.strip() or Path(file.filename).stem.strip()
    if not name or len(name) > config.REPORT_NAME_MAX_LEN:
        raise AppError.NAME_INVALID.http(max=config.REPORT_NAME_MAX_LEN)
    if await asyncio.to_thread(db_find_report, user_id, name):
        raise AppError.REPORT_NAME_CONFLICT.http(name=name)
    pbix_bytes, file_size = await _validate_pbix_file(file)
    return name, pbix_bytes, file_size


async def _process_upload(user: dict, name: str, pbix_bytes: bytes, file_size: int, job_id: int, ip: str,
                          description: str | None = None):
    """백그라운드 태스크 진입점 — 어떤 예외도 잡을 '진행 중' 상태로 남기지 않는다.

    알려진 실패는 _run_upload 각 지점이 잡 상태(failed/conflict/unknown 등)를 기록한 뒤
    HTTPException으로 탈출한다. 그 밖의 예상 밖 예외가 새면 잡이 publishing/accepted로
    남아 서버 재시작 전까지 같은 이름 재업로드가 409로 막히므로, 여기서 failed 처리한다."""
    try:
        await _run_upload(user, name, pbix_bytes, file_size, job_id, ip, description)
    except HTTPException as exc:
        # 잡 상태는 발생 지점에서 이미 기록됨. 이 태스크는 asyncio.create_task로 요청
        # 컨텍스트 밖에서 도는 백그라운드라 main.py의 전역 예외 핸들러가 못 잡는다 —
        # 5xx(Azure/PBI 장애·DB 등록 실패 등)만 여기서 직접 error_log에 남긴다.
        if exc.status_code >= 500:
            code, msg = extract_code_message(exc.detail)
            try:
                await asyncio.to_thread(db_log_error, code, exc.status_code, msg, user["username"], "/api/upload", None)
            except Exception:
                logger.exception("ERROR LOG WRITE FAIL (upload) | job_id=%s", job_id)
    except Exception as exc:
        logger.exception("UPLOAD UNEXPECTED | user=%s | report=%s | job_id=%s", user["username"], name, job_id)
        try:
            await asyncio.to_thread(
                db_log_error, "UNHANDLED", 500, str(exc), user["username"], "/api/upload", traceback.format_exc(),
            )
        except Exception:
            logger.exception("ERROR LOG WRITE FAIL (upload) | job_id=%s", job_id)
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
    # 활동 기록은 실패한 업로드가 "업로드"로 남지 않도록 완료 시점에만 남긴다
    await asyncio.to_thread(db_log_activity, user["id"], user["username"], "report_upload", None, name, ip)
    logger.info("UPLOAD OK  | user=%-12s | ip=%s | report=%s", user["username"], ip, name)
    return {"report_name": name, "pbi_display_name": pbi_display_name, "new": True}


async def _pbi_import_pbix(
    user: dict, name: str, pbix_bytes: bytes, job_id: int, ip: str,
    workspace_id: str = WORKSPACE_ID, import_name: str | None = None, name_conflict: str = "CreateOrOverwrite",
) -> dict:
    """.pbix를 PBI에 게시하고 변환 완료까지 폴링한다. 성공 시 import 결과(JSON)를 반환.

    workspace_id/import_name/name_conflict는 v7 콘텐츠 업데이트의 스테이징 임포트가
    같은 폴링 로직을 재사용하기 위한 파라미터 — 기본값은 기존 신규 업로드 동작과 동일하다."""
    if import_name is None:
        import_name = f"{user['username']}__{name}"
    report_api = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
    try:
        access_token = await asyncio.to_thread(get_access_token)
    except Exception as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"token: {exc}")
        raise
    headers = {"Authorization": f"Bearer {access_token}"}

    import_params = {"datasetDisplayName": f"{import_name}.pbix", "nameConflict": name_conflict}

    async with httpx.AsyncClient(timeout=300) as client:
        try:
            resp = await client.post(
                f"{report_api}/imports", params=import_params, headers=headers,
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
        await asyncio.to_thread(db_update_upload_job, job_id, "accepted", import_id=import_id, pbi_workspace_id=workspace_id)
        logger.info("UPLOAD ACCEPT | user=%-12s | report=%s | import_id=%s", user["username"], name, import_id)

        # 변환 완료 대기
        for _ in range(config.IMPORT_POLL_MAX):
            await asyncio.sleep(config.IMPORT_POLL_INTERVAL)
            resp = await client.get(f"{report_api}/imports/{import_id}", headers=headers)
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


# ═══════════════════════════════════════════════════════════════════════════
# v7 — 보고서 콘텐츠 업데이트 (데이터셋은 그대로, 페이지·시각화만 교체)
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/reports/{report_id}/update-content")
async def api_update_report_content(request: Request, report_id: int, file: UploadFile = File(...)):
    """기존 보고서에 새 pbix를 올려 콘텐츠만 교체한다. 데이터셋(RLS·관계·DAX)은 유지된다.

    권한: 보고서 소유자 또는 admin만 — 열람 권한(can_view)과는 완전히 별개 체크다.
    can_view이 있어도 소유자·admin이 아니면 이 API는 쓸 수 없다."""
    verify_csrf(request, request.headers.get("X-CSRF-Token", ""))
    user = await current_user(request)
    if not user:
        raise AppError.NOT_AUTHENTICATED.http()

    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["pbi_report_id"]:
        raise AppError.REPORT_NOT_FOUND.http()
    if report_row["tab_type"] == "dashboard":
        raise AppError.UPDATE_NOT_SUPPORTED.http()
    if not (user.get("is_admin") or report_row["owner_id"] == user["id"]):
        raise AppError.FORBIDDEN_REPORT_EDIT.http()

    pbix_bytes, file_size = await _validate_pbix_file(file)
    job_id = await asyncio.to_thread(db_reserve_update, user["id"], report_id, report_row["name"])
    logger.info("UPDATE RESERVED | user=%-12s | report=%s | job_id=%s", user["username"], report_row["name"], job_id)

    asyncio.create_task(
        _process_report_update(user, report_row, pbix_bytes, file_size, job_id, get_client_ip(request))
    )
    return {"job_id": job_id, "report_name": report_row["name"], "status": "accepted"}


async def _process_report_update(user: dict, report_row: dict, pbix_bytes: bytes, file_size: int, job_id: int, ip: str):
    """백그라운드 진입점 — _process_upload와 동일한 예외 처리 철학을 따른다."""
    try:
        await _run_report_update(user, report_row, pbix_bytes, file_size, job_id, ip)
    except HTTPException as exc:
        if exc.status_code >= 500:
            code, msg = extract_code_message(exc.detail)
            try:
                await asyncio.to_thread(
                    db_log_error, code, exc.status_code, msg, user["username"],
                    "/api/reports/update-content", None,
                )
            except Exception:
                logger.exception("ERROR LOG WRITE FAIL (update) | job_id=%s", job_id)
    except Exception as exc:
        logger.exception(
            "UPDATE UNEXPECTED | user=%s | report=%s | job_id=%s", user["username"], report_row["name"], job_id,
        )
        try:
            await asyncio.to_thread(
                db_log_error, "UNHANDLED", 500, str(exc), user["username"],
                "/api/reports/update-content", traceback.format_exc(),
            )
        except Exception:
            logger.exception("ERROR LOG WRITE FAIL (update) | job_id=%s", job_id)
        try:
            marked = await asyncio.to_thread(db_fail_stuck_upload_job, job_id)
            if marked:
                logger.warning("UPDATE STUCK→FAILED | job_id=%s", job_id)
        except Exception:
            logger.exception("UPDATE STUCK MARK FAIL | job_id=%s", job_id)


async def _run_report_update(user: dict, report_row: dict, pbix_bytes: bytes, file_size: int, job_id: int, ip: str):
    """스테이징 임포트 → UpdateReportContent(대상 콘텐츠만 교체) → 스테이징 정리 → 완료.

    스테이징 보고서·데이터셋은 콘텐츠를 옮기는 매개체일 뿐이라 작업이 끝나면 삭제한다.
    삭제 실패는 치명적이지 않으므로 경고만 남기고 잡은 완료 처리한다(재사용 안 되는
    임시 항목이 워크스페이스에 남는 것뿐 — 다음 업데이트 때도 새 스테이징을 또 만듦)."""
    name = report_row["name"]
    logger.info("UPDATE START | user=%-12s | ip=%s | report=%s | bytes=%s", user["username"], ip, name, file_size)

    workspace_id = config.resolve_workspace_id(report_row["pbi_workspace_id"])
    staging_name = f"__update_staging__{report_row['id']}_{job_id}"

    result = await _pbi_import_pbix(
        user, name, pbix_bytes, job_id, ip,
        workspace_id=workspace_id, import_name=staging_name, name_conflict="Abort",
    )
    reports = result.get("reports", [])
    if not reports:
        await asyncio.to_thread(db_update_upload_job, job_id, "unknown", error_message="staging: no report in response")
        raise AppError.IMPORT_NO_REPORT.http()
    staging_report_id = reports[0]["id"]
    staging_dataset_ids = [d["id"] for d in result.get("datasets", [])]

    try:
        await pbi_update_report_content(workspace_id, report_row["pbi_report_id"], staging_report_id)
    except Exception as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=str(exc))
        raise AppError.UPLOAD_DB_FAILED.http(detail=f"UpdateReportContent 실패: {exc}") from exc

    # 스테이징 정리 (비치명적 — 실패해도 업데이트 자체는 이미 성공한 상태)
    try:
        await pbi_delete_report(workspace_id, staging_report_id)
        for ds_id in staging_dataset_ids:
            await pbi_delete_dataset(workspace_id, ds_id)
    except Exception as exc:
        logger.warning("UPDATE STAGING CLEANUP WARN | job_id=%s | error=%s", job_id, exc)

    await asyncio.to_thread(db_update_upload_job, job_id, "completed", report_id=report_row["id"], error_message=None)
    logger.info("UPDATE OK  | user=%-12s | ip=%s | report=%s", user["username"], ip, name)
