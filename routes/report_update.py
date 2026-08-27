"""v7 — 보고서 콘텐츠 업데이트: POST /api/reports/{report_id}/update-content.

데이터셋(RLS·관계·DAX)은 그대로 두고 페이지·시각화만 교체한다. routes/report.py에서
2026-08-27에 분리했다(분리 배경은 그 파일 상단 주석 참고) — 파일 검증·PBI Import
폴링은 신규 업로드(routes/report_upload.py)와 완전히 같은 로직이라 그대로 가져다 쓴다."""
import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.requests import Request

import config
from database import db_get_report, db_reserve_update, db_update_upload_job, db_fail_stuck_upload_job
from deps import get_client_ip, require_user_csrf
from errors import AppError, extract_code_message
from routes.report_upload import _pbi_import_pbix, _validate_pbix_file
from services.powerbi import pbi_update_report_content, pbi_delete_report, pbi_delete_dataset

router = APIRouter()
logger = logging.getLogger("powerbi-gateway")


@router.post("/api/reports/{report_id}/update-content")
async def api_update_report_content(request: Request, report_id: int, file: UploadFile = File(...)):
    """기존 보고서에 새 pbix를 올려 콘텐츠만 교체한다. 데이터셋(RLS·관계·DAX)은 유지된다.

    권한: 보고서 소유자 또는 admin만 — 열람 권한(can_view)과는 완전히 별개 체크다.
    can_view이 있어도 소유자·admin이 아니면 이 API는 쓸 수 없다."""
    user = await require_user_csrf(request)

    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["pbi_report_id"]:
        raise AppError.REPORT_NOT_FOUND.http()
    if report_row["tab_type"] == "dashboard":
        raise AppError.UPDATE_NOT_SUPPORTED.http()
    if not (user.get("is_admin") or report_row["owner_id"] == user["id"]):
        raise AppError.FORBIDDEN_REPORT_EDIT.http()

    # 업데이트는 "같은 보고서의 수정본"만 받는다 — 파일명이 다르면 엉뚱한 pbix를 잘못
    # 골랐을 가능성이 높다(관계없는 페이지·시각화가 기존 데이터셋 위에 그대로 얹힘).
    uploaded_stem = (file.filename or "").rsplit(".", 1)[0].strip()
    if uploaded_stem.lower() != report_row["name"].strip().lower():
        raise AppError.UPDATE_FILENAME_MISMATCH.http(expected=report_row["name"], got=file.filename or "")

    pbix_bytes, file_size = await _validate_pbix_file(file)
    job_id = await asyncio.to_thread(db_reserve_update, user["id"], report_id, report_row["name"])
    logger.info("UPDATE RESERVED | user=%-12s | report=%s | job_id=%s", user["username"], report_row["name"], job_id)

    asyncio.create_task(
        _process_report_update(user, report_row, pbix_bytes, file_size, job_id, get_client_ip(request))
    )
    return {"job_id": job_id, "report_name": report_row["name"], "status": "accepted"}


async def _process_report_update(user: dict, report_row: dict, pbix_bytes: bytes, file_size: int, job_id: int, ip: str):
    """백그라운드 진입점 — routes/report_upload.py::_process_upload와 동일한 예외 처리 철학을 따른다."""
    try:
        await _run_report_update(user, report_row, pbix_bytes, file_size, job_id, ip)
    except HTTPException as exc:
        if exc.status_code >= 500:
            code, msg = extract_code_message(exc.detail)
            logger.error("UPDATE 5xx | job_id=%s | %s | %s", job_id, code, msg)
    except Exception:
        logger.exception(
            "UPDATE UNEXPECTED | user=%s | report=%s | job_id=%s", user["username"], report_row["name"], job_id,
        )
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
