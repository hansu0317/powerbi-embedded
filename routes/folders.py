"""포털 보고서 폴더 조회 API.

임의 폴더 생성·이름변경·삭제·이동은 이 앱(API/사용자)이 다루지 않는다(2026-08-12~,
2026-08-27 이동 API까지 완전히 정리) — 포털이 Fabric과 별개로 자기만의 폴더 구조를
만들고 관리하는 부담을 지지 않기로 했다. 폴더 구조 자체는 Power BI/Fabric에서만
바뀌고, 이 앱은 그걸 그대로 따라가기만 한다(반대 방향, 즉 포털이 Fabric 쪽 폴더를
바꾸는 동작은 두지 않는다).

"따라간다"는 2026-08-19부터 자동이다 — 가져오기(database/admin.py::
db_import_pbi_item → db_ensure_folder_path)가 매 항목의 Fabric 폴더 경로를 보고
report_folders를 자동으로 미러링한다. 즉 report_folders는 여기서 API로 새로 만드는 게
아니라 가져오기가 채워두고, 이 라우터는 그 결과를 조회해서 보여주기만 한다."""
import asyncio

from fastapi.requests import Request
from fastapi import APIRouter

from database import db_get_report_folders, db_get_writable_folders
from deps import require_user

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
