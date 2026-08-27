"""관리자 포털 라우트: 활동/감사 로그 조회 + CSV 내보내기.

routes/admin.py에서 2026-08-27에 분리했다(분리 배경은 admin_users.py 상단 주석 참고)."""
import asyncio
import csv
import io
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import Response

import config
from database import db_get_activity_log, db_get_audit_log
from deps import require_admin_user

router = APIRouter()
logger = logging.getLogger("powerbi-gateway")


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
    # limit도 같이 내려줘야 화면의 "최근 N건까지만 표시" 안내가 실제 조회 상한과
    # 어긋나지 않는다(관리자가 설정에서 activity_log_max_rows를 바꿀 수 있으므로).
    return {"rows": rows, "limit": config.ACTIVITY_LOG_MAX_ROWS}


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
        content="﻿" + buf.getvalue(),  # BOM: Excel 한글 인코딩 인식용
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={type}_log.csv"},
    )
