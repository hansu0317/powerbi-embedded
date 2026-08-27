"""v6 — 보고서 다운로드/내보내기: 원본 .pbix 다운로드, PPTX Export To File.

routes/report.py에서 2026-08-27에 분리했다(분리 배경은 그 파일 상단 주석 참고)."""
import asyncio

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import Response

import config
from database import db_get_report
from deps import require_user, require_user_csrf
from errors import AppError
from routes.report import _require_viewable_report
from services.powerbi import (
    pbi_download_pbix, pbi_start_export, pbi_poll_export, pbi_get_export_file,
)

router = APIRouter()


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
    user = await require_user(request)
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
    user = await require_user_csrf(request)
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
    user = await require_user(request)
    workspace_id, pbi_report_id = await _report_pbi_ids(user, report_id)
    try:
        status = await pbi_poll_export(workspace_id, pbi_report_id, export_id)
    except Exception as exc:
        raise AppError.PPTX_EXPORT_FAILED.http(detail=str(exc))
    return {"status": status.get("status", "Unknown")}


@router.get("/api/reports/{report_id}/export/pptx/{export_id}/file")
async def api_export_pptx_file(request: Request, report_id: int, export_id: str):
    user = await require_user(request)
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
