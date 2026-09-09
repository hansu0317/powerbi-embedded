"""신규 보고서 업로드 파이프라인: POST /api/upload, GET /api/upload/status/{job_id}.

routes/report.py에서 2026-08-27에 분리했다(분리 배경은 그 파일 상단 주석 참고)."""
import asyncio
import io
import logging

import httpx
import psycopg2
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.requests import Request

import config
from config import WORKSPACE_ID
from database import (
    db_find_report, db_reserve_upload, db_update_upload_job, db_get_upload_job,
    db_register_report, db_fail_stuck_upload_job, db_get_folder, db_can_write_folder,
)
from deps import get_client_ip, require_user, require_user_csrf
from errors import AppError, extract_code_message
from services.azure import get_access_token
from services.fabric_folders import get_or_create_folder, move_item_to_folder
from services.powerbi import rename_with_retry

router = APIRouter()
logger = logging.getLogger("powerbi-gateway")


@router.post("/api/upload")
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
    report_description: str = Form(""),
    folder_id: int | None = Form(None),
    visibility: str = Form("personal"),
):
    """신규 PBIX 업로드. 검증·예약 후 작업 ID를 반환하고 비공개 보고서로 게시한다.

    같은 이름은 거부하며 현재 콘텐츠 업데이트 API는 제공하지 않는다."""
    user = await require_user_csrf(request)
    if not user.get("is_admin") and not user.get("can_upload"):
        logger.warning("UPLOAD DENY | user=%-12s | 업로드 권한 없음", user["username"])
        raise AppError.FORBIDDEN_UPLOAD.http()

    if visibility not in ("personal", "shared"):
        raise AppError.BODY_INVALID.http()
    folder = await asyncio.to_thread(db_get_folder, folder_id) if folder_id else None
    if folder_id and not await asyncio.to_thread(db_can_write_folder, folder_id, user["id"], user["is_admin"]):
        raise AppError.FORBIDDEN_UPLOAD.http()
    # 신규 업로드는 항상 비공개로 시작한다. 부서·공용 공개는 업로드 이후
    # 관리자 포털의 권한 관리에서만 명시적으로 수행한다.
    visibility = "personal"
    name, pbix_bytes, file_size = await _read_and_validate_pbix(file, user["id"])
    job_id = await asyncio.to_thread(db_reserve_upload, user["id"], name)
    logger.info("UPLOAD RESERVED | user=%-12s | report=%s | job_id=%s", user["username"], name, job_id)
    ip = get_client_ip(request)

    asyncio.create_task(_process_upload(user, name, pbix_bytes, file_size, job_id, ip,
                                        report_description.strip()[:500] or None,
                                        folder["name"] if folder else user["username"], folder_id, visibility))
    return {"job_id": job_id, "report_name": name, "status": "accepted"}


@router.get("/api/upload/status/{job_id}")
async def api_upload_status(request: Request, job_id: int):
    """업로드 잡 상태 폴링 엔드포인트."""
    user = await require_user(request)
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
    """확장자·크기·매직바이트 검사 후 파일 내용과 크기를 반환한다."""
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
    file: UploadFile, user_id: int
) -> tuple[str, bytes, int]:
    """파일명으로 보고서 이름을 정하고 중복·파일 형식을 검사한다.

    기존 보고서와 같은 이름이면 덮어쓰지 않고 거부한다."""
    if not file.filename or not file.filename.lower().endswith(".pbix"):
        raise AppError.FILE_WRONG_TYPE.http()
    name = file.filename[:-len(".pbix")].strip()
    if not name or len(name) > config.REPORT_NAME_MAX_LEN:
        raise AppError.NAME_INVALID.http(max=config.REPORT_NAME_MAX_LEN)
    existing = await asyncio.to_thread(db_find_report, user_id, name)
    if existing:
        raise AppError.REPORT_NAME_TAKEN.http(name=name)
    pbix_bytes, file_size = await _validate_pbix_file(file)
    return name, pbix_bytes, file_size


async def _process_upload(user: dict, name: str, pbix_bytes: bytes, file_size: int, job_id: int, ip: str,
                          description: str | None = None, category: str | None = None,
                          portal_folder_id: int | None = None, visibility: str = "personal"):
    """백그라운드 태스크 진입점 — 어떤 예외도 잡을 '진행 중' 상태로 남기지 않는다.

    알려진 실패는 _run_upload 각 지점이 잡 상태(failed/conflict/unknown 등)를 기록한 뒤
    HTTPException으로 탈출한다. 그 밖의 예상 밖 예외가 새면 잡이 publishing/accepted로
    남아 서버 재시작 전까지 같은 이름 재업로드가 409로 막히므로, 여기서 failed 처리한다."""
    try:
        await _run_upload(user, name, pbix_bytes, file_size, job_id, ip, description, category,
                          portal_folder_id, visibility)
    except HTTPException as exc:
        # 잡 상태는 발생 지점에서 이미 기록됨. 이 태스크는 백그라운드라 main.py의
        # 전역 예외 핸들러가 못 잡으므로 5xx만 서버 로그에 남긴다.
        if exc.status_code >= 500:
            code, msg = extract_code_message(exc.detail)
            logger.error("UPLOAD 5xx | job_id=%s | %s | %s", job_id, code, msg)
    except Exception:
        logger.exception("UPLOAD UNEXPECTED | user=%s | report=%s | job_id=%s", user["username"], name, job_id)
        try:
            marked = await asyncio.to_thread(db_fail_stuck_upload_job, job_id)
            if marked:
                logger.warning("UPLOAD STUCK→FAILED | job_id=%s", job_id)
        except Exception:
            logger.exception("UPLOAD STUCK MARK FAIL | job_id=%s", job_id)


async def _run_upload(user: dict, name: str, pbix_bytes: bytes, file_size: int, job_id: int, ip: str,
                      description: str | None = None, category: str | None = None,
                      portal_folder_id: int | None = None, visibility: str = "personal"):
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
    await _move_to_user_folder(user, pbi_report_id, dataset_ids, category)

    # 5) 게이트웨이 DB 등록 + 완료 처리
    await _register_uploaded_report(user, name, pbi_report_id, dataset_ids, pbi_display_name, job_id,
                                    description, category, portal_folder_id, visibility)
    logger.info("UPLOAD OK  | user=%-12s | ip=%s | report=%s", user["username"], ip, name)
    return {"report_name": name, "pbi_display_name": pbi_display_name, "new": True}


async def _pbi_import_pbix(user: dict, name: str, pbix_bytes: bytes, job_id: int, ip: str) -> dict:
    """.pbix를 PBI에 게시하고 변환 완료까지 폴링한다. 성공 시 import 결과(JSON)를 반환."""
    import_name = f"{user['username']}__{name}"
    report_api = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"
    try:
        access_token = await asyncio.to_thread(get_access_token)
    except Exception as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "failed", error_message=f"token: {exc}")
        raise
    headers = {"Authorization": f"Bearer {access_token}"}

    import_params = {"datasetDisplayName": f"{import_name}.pbix", "nameConflict": "CreateOrOverwrite"}

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
        await asyncio.to_thread(db_update_upload_job, job_id, "accepted", import_id=import_id, pbi_workspace_id=WORKSPACE_ID)
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


async def _move_to_user_folder(user: dict, pbi_report_id: str, dataset_ids: list[str], folder_name: str | None = None):
    """/* fabric */ 사용자 폴더 생성 후 보고서·데이터셋 이동. 실패해도 보고서 기능에 영향 없음."""
    folder_id = await get_or_create_folder(WORKSPACE_ID, folder_name or user["username"])
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
    category: str | None = None, portal_folder_id: int | None = None,
    visibility: str = "personal",
):
    """게이트웨이 DB에 보고서를 등록하고 잡을 completed로 마감한다. DB 실패는 db_failed로 기록."""
    try:
        gateway_report_id = await asyncio.to_thread(
            db_register_report, name, pbi_report_id, user["id"],
            dataset_ids[0] if dataset_ids else None,
            WORKSPACE_ID, pbi_display_name,
            category or user["username"],  # 사이드바 폴더 트리 경로 (기본: 내 계정)
            description, portal_folder_id, visibility,
        )
    except psycopg2.Error as exc:
        await asyncio.to_thread(db_update_upload_job, job_id, "db_failed", error_message=str(exc))
        logger.exception("UPLOAD DB FAIL | user=%s | report=%s | pbi_report_id=%s", user["username"], name, pbi_report_id)
        raise AppError.UPLOAD_DB_FAILED.http() from exc

    await asyncio.to_thread(db_update_upload_job, job_id, "completed", report_id=gateway_report_id, error_message=None)
