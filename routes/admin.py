"""관리자 포털 라우트: 대시보드(/admin, bootstrap) + Fabric↔DB 동기화·가져오기·자가진단.

사용자·보고서권한·설정·로그는 각각 routes/admin_users.py, admin_reports.py,
admin_config.py, admin_logs.py로 분리돼 있다(2026-08-27, database/ 패키지가 이미
도메인별로 나뉜 것과 결을 맞췄다 — 이 파일도 한때 사용자·보고서권한·설정·로그까지
전부 담아 600줄대 후반까지 커졌었다)."""
import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import WORKSPACE_ID
import config
from database import (
    db_admin_get_stats, db_admin_get_users, db_admin_get_reports, db_admin_get_upload_jobs,
    db_import_pbi_item, db_get_synced_reports, db_hard_delete_report, db_get_pbi_report_map,
)
from deps import csrf_token, require_admin_user, require_admin_csrf
from services.fabric import (
    sync_pbi_reports, fetch_pbi_folders_and_reports, fetch_pbi_folders_and_dashboards, LOOP_HEARTBEAT,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("powerbi-gateway")


@router.post("/api/admin/sync-pbi")
async def api_sync_pbi(user: dict = Depends(require_admin_csrf)):
    """관리자 수동 동기화: PBI에서 지운 보고서를 즉시 DB에 반영한다."""
    summary = await sync_pbi_reports()
    logger.info("PBI SYNC (manual) | user=%s | %s", user["username"], summary)
    return summary


async def _build_admin_context(user: dict) -> dict:
    """admin.html(SSR)과 /api/admin/bootstrap(탭 토큰 재조회)이 공유하는 데이터 조립.
    이유는 routes/report.py의 _build_report_context 주석 참고 — 같은 원리."""
    stats   = await asyncio.to_thread(db_admin_get_stats)
    users   = await asyncio.to_thread(db_admin_get_users)
    reports = await asyncio.to_thread(db_admin_get_reports)
    jobs    = await asyncio.to_thread(db_admin_get_upload_jobs)
    return {"user": user, "stats": stats, "users": users, "reports": reports, "jobs": jobs}


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: dict = Depends(require_admin_user)):
    ctx = await _build_admin_context(user)
    return templates.TemplateResponse(request, "admin.html", {
        **ctx,
        "csrf_token": csrf_token(request),
    })


@router.get("/api/admin/bootstrap")
async def api_admin_bootstrap(request: Request, user: dict = Depends(require_admin_user)):
    """탭 토큰 기준으로 admin.html의 부트스트랩 데이터를 다시 받는 JSON 버전."""
    ctx = await _build_admin_context(user)
    return {**ctx, "csrf_token": csrf_token(request)}


@router.post("/api/admin/import-pbi")
async def api_admin_import_pbi(user: dict = Depends(require_admin_csrf)):
    """Fabric 폴더 구조를 읽어 새 비공개 보고서 + 대시보드(v6)를 DB에 등록한다.

    이미 등록된 항목은 건너뛴다(pbi_report_id 중복 체크).
    권한은 부여하지 않으므로 등록 후 보고서 관리에서 별도 설정이 필요하다.
    """
    reports = await fetch_pbi_folders_and_reports()
    dashboards = await fetch_pbi_folders_and_dashboards()
    # 삭제 판정 기준 — 대시보드 ID도 반드시 포함해야 방금 등록한 대시보드가
    # 아래 "Fabric에 없는 항목 삭제" 단계에서 즉시 지워지지 않는다.
    fabric_ids = {r["pbi_report_id"] for r in reports} | {d["pbi_dashboard_id"] for d in dashboards}

    registered = skipped = deleted = 0
    # DB 커넥션 풀(기본 20개)을 다 쓰지 않도록 동시 실행 수를 제한한다.
    sem = asyncio.Semaphore(8)

    # 신규 등록 + 기존 보고서 category 업데이트 (동시 실행)
    async def _import_one(r):
        async with sem:
            return await asyncio.to_thread(
                db_import_pbi_item,
                r["pbi_report_id"], r["name"], WORKSPACE_ID,
                r["folder_id"], r["folder_name"], user["id"],
                r["dataset_id"], False,
            )

    async def _import_one_dashboard(d):
        async with sem:
            return await asyncio.to_thread(
                db_import_pbi_item,
                d["pbi_dashboard_id"], d["name"], WORKSPACE_ID,
                d["folder_id"], d["folder_name"], user["id"],
                None, True,
            )

    import_results = await asyncio.gather(*(_import_one(r) for r in reports))
    for r, is_new in zip(reports, import_results):
        if is_new:
            registered += 1
            logger.info("ADMIN IMPORT PBI | admin=%s | report=%s | category=%s",
                        user["username"], r["name"], r["folder_name"])
        else:
            skipped += 1

    dash_results = await asyncio.gather(*(_import_one_dashboard(d) for d in dashboards))
    for d, is_new in zip(dashboards, dash_results):
        if is_new:
            registered += 1
            logger.info("ADMIN IMPORT PBI DASHBOARD | admin=%s | dashboard=%s | category=%s",
                        user["username"], d["name"], d["folder_name"])
        else:
            skipped += 1

    # Fabric에 없는 보고서/대시보드는 DB에서 완전 삭제 (동시 실행)
    async def _delete_one(row):
        async with sem:
            return await asyncio.to_thread(db_hard_delete_report, row["id"])

    db_reports = await asyncio.to_thread(db_get_synced_reports)
    to_delete = [row for row in db_reports if row["pbi_report_id"] not in fabric_ids]
    delete_results = await asyncio.gather(*(_delete_one(row) for row in to_delete))
    for row, did_delete in zip(to_delete, delete_results):
        if did_delete:
            deleted += 1
            logger.info("ADMIN IMPORT PBI DELETE | admin=%s | report=%s",
                        user["username"], row["name"])

    total = len(reports) + len(dashboards)
    logger.info("ADMIN IMPORT PBI DONE | admin=%s | registered=%d | skipped=%d | deleted=%d",
                user["username"], registered, skipped, deleted)
    return {"registered": registered, "skipped": skipped, "deleted": deleted, "total": total}


@router.get("/api/admin/sync-status")
async def api_admin_sync_status(user: dict = Depends(require_admin_user)):
    """Fabric 현재 상태와 DB를 대조해 '가져오기 필요' 여부를 반환한다.

    신규(폴더 추가/직접 게시), 폴더 이동·이름변경(category 불일치),
    Fabric에서 사라진 보고서(삭제 대상)를 감지한다. import-pbi 실행 시 모두 정리된다.
    """
    try:
        fabric_reports = await fetch_pbi_folders_and_reports()
        fabric_dashboards = await fetch_pbi_folders_and_dashboards()
        db_map = await asyncio.to_thread(db_get_pbi_report_map)
    except Exception as exc:
        logger.warning("SYNC STATUS FAIL | admin=%s | error=%s", user["username"], exc)
        return {"available": False, "drift": False}

    # db_get_pbi_report_map은 report_type 무관하게 반환하므로(v6 대시보드 포함),
    # 대시보드를 report와 같은 모양으로 맞춰 합친다 — 안 그러면 대시보드가
    # 매번 "Fabric에서 사라짐(removed)"으로 오탐된다.
    fabric = fabric_reports + [
        {"pbi_report_id": d["pbi_dashboard_id"], "name": d["name"], "folder_name": d["folder_name"]}
        for d in fabric_dashboards
    ]

    fabric_ids = {f["pbi_report_id"] for f in fabric}
    new, moved = [], []
    for f in fabric:
        rid = f["pbi_report_id"]
        if rid not in db_map:
            new.append(f["name"])
        elif (f["folder_name"] or None) != (db_map[rid]["category"] or None):
            moved.append({
                "name": f["name"],
                "from": db_map[rid]["category"],
                "to":   f["folder_name"],
            })
    removed = [v["name"] for rid, v in db_map.items() if rid not in fabric_ids]

    return {
        "available": True,
        "drift": bool(new or moved or removed),
        "new": new,
        "moved": moved,
        "removed": removed,
    }


@router.get("/api/admin/system-status")
async def api_admin_system_status(user: dict = Depends(require_admin_user)):
    """자가진단: DB 응답시간·백그라운드 루프 하트비트·실패 잡 수."""
    import time as _time
    t0 = _time.perf_counter()
    stats = await asyncio.to_thread(db_admin_get_stats)
    db_ms = round((_time.perf_counter() - t0) * 1000)
    now = _time.time()
    heartbeats = {
        k: round(now - v) for k, v in LOOP_HEARTBEAT.items()  # 초 단위 경과
    }
    return {
        "db_latency_ms": db_ms,
        "loop_seconds_ago": heartbeats,      # {"pbi_sync": 132}
        "sync_interval_sec": config.PBI_SYNC_INTERVAL,
        **stats,
    }
