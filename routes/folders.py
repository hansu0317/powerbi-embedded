"""포털 보고서 폴더 조회와 Fabric Item 이동 API.

임의 폴더 생성·이름변경·삭제는 이 앱(API/사용자)이 다루지 않는다(2026-08-12~) — 포털이
Fabric과 별개로 자기만의 폴더 구조를 만들고 관리하는 부담을 지지 않기로 했다. 폴더 구조
자체는 Power BI/Fabric에서 관리하고, 이 앱은 그걸 그대로 따라간다.

단, "따라간다"가 2026-08-19부터는 자동이다 — 가져오기(database/admin.py::
db_import_pbi_item → db_ensure_folder_path)가 매 항목의 Fabric 폴더 경로를 보고
report_folders를 자동으로 미러링한다. 즉 report_folders는 여기서 API로 새로 만드는 게
아니라 가져오기가 채워두고, 이 라우터는 그 결과를 조회해서 보여주고 기존 보고서를 그
안으로 옮기는 것까지만 한다."""
import asyncio

from fastapi import APIRouter
from fastapi.requests import Request

import config
from database import (
    db_get_folder, db_get_report_folders, db_get_writable_folders, db_move_report_to_folder,
)
from deps import json_body, require_user, require_user_csrf
from errors import AppError
from services.fabric_folders import move_item_to_folder

router = APIRouter(prefix="/api")


@router.get("/report-folders")
async def list_folders(request: Request):
    """탐색 트리(사이드바)용 — 실제로 열람 가능한 보고서가 있는 폴더만 내려준다."""
    user = await require_user(request)
    folders = await asyncio.to_thread(db_get_report_folders, user["id"], user["is_admin"])
    return {"folders": folders}


@router.get("/report-folders/writable")
async def list_writable_folders(request: Request):
    """업로드 대상 폴더 선택기용 — 쓸 수 있는 폴더 전부(빈 공용 폴더 포함). 위
    /report-folders와 의도적으로 다른 규칙(database/folders.py 상단 주석 참고)."""
    user = await require_user(request)
    folders = await asyncio.to_thread(db_get_writable_folders, user["id"], user["is_admin"])
    return {"folders": folders}


@router.post("/reports/{report_id}/folder")
async def move_report(request: Request, report_id: int):
    user = await require_user_csrf(request)
    folder_id = int((await json_body(request)).get("folder_id", 0))
    report = await asyncio.to_thread(
        db_move_report_to_folder, report_id, folder_id, user["id"], user["is_admin"],
    )
    if not report:
        raise AppError.BODY_INVALID.http()
    folder = await asyncio.to_thread(db_get_folder, folder_id)
    if report.get("pbi_report_id") and folder and folder.get("fabric_folder_id"):
        workspace_id = config.resolve_workspace_id(report.get("pbi_workspace_id"))
        await move_item_to_folder(workspace_id, report["pbi_report_id"], folder["fabric_folder_id"])
    return {"moved": True, "folder_id": folder_id}
