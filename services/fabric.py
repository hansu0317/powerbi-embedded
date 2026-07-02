"""PBI 동기화 루프 + 시작 시 복구 (Pro 전용)."""
import asyncio
import logging

import httpx

from config import PBI_API, PBI_GROUPS, PBI_SYNC_INTERVAL, WORKSPACE_ID
from database import (
    db_get_synced_reports, db_mark_report_deleted, db_restore_report,
    db_get_pending_imports, db_get_recoverable_jobs,
    db_update_upload_job, db_register_report,
)
from services.azure import get_access_token, get_fabric_token

logger = logging.getLogger("powerbi-gateway")


# ── PBI 삭제 동기화 ───────────────────────────────────────────────────────────

async def sync_pbi_reports() -> dict:
    """PBI 워크스페이스와 DB를 대조해 삭제/복구를 반영한다."""
    rows = await asyncio.to_thread(db_get_synced_reports)
    if not rows:
        return {"checked": 0, "deleted": 0, "restored": 0}
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}"}

    from config import PBI_GROUPS
    workspace_reports: dict[str, set[str] | None] = {}
    async with httpx.AsyncClient(timeout=60) as client:
        for ws_id in {row["pbi_workspace_id"] for row in rows}:
            try:
                resp = await client.get(f"{PBI_GROUPS}/{ws_id}/reports", headers=headers)
                resp.raise_for_status()
                workspace_reports[ws_id] = {item["id"] for item in resp.json().get("value", [])}
            except Exception as exc:
                workspace_reports[ws_id] = None
                logger.warning("PBI SYNC SKIP | workspace=%s | error=%s", ws_id, exc)

    deleted = restored = checked = 0
    for row in rows:
        existing = workspace_reports.get(row["pbi_workspace_id"])
        if existing is None:
            continue
        checked += 1
        in_pbi = row["pbi_report_id"] in existing
        if row["status"] == "active" and not in_pbi:
            if await asyncio.to_thread(db_mark_report_deleted, row["id"], row["pbi_report_id"], "workspace sync"):
                deleted += 1
                logger.info("PBI SYNC DELETE  | report_id=%s | name=%s", row["id"], row["name"])
        elif row["status"] == "deleted" and in_pbi:
            if await asyncio.to_thread(db_restore_report, row["id"], row["pbi_report_id"]):
                restored += 1
                logger.info("PBI SYNC RESTORE | report_id=%s | name=%s", row["id"], row["name"])
    return {"checked": checked, "deleted": deleted, "restored": restored}


async def pbi_sync_loop():
    """서버 시작 시 1회 + PBI_SYNC_INTERVAL 주기로 PBI 동기화를 반복한다."""
    try:
        logger.info("PBI SYNC (startup) | %s", await sync_pbi_reports())
    except Exception:
        logger.exception("STARTUP PBI SYNC FAIL")
    if PBI_SYNC_INTERVAL <= 0:
        return
    while True:
        await asyncio.sleep(PBI_SYNC_INTERVAL)
        try:
            summary = await sync_pbi_reports()
            if summary["deleted"] or summary["restored"]:
                logger.info("PBI SYNC | %s", summary)
        except Exception:
            logger.exception("PBI SYNC FAIL")


# ── 시작 시 복구 ──────────────────────────────────────────────────────────────

def recover_db_jobs():
    """pbi_succeeded·db_failed 상태 업로드를 재시작 시 DB에 등록한다."""
    import psycopg2
    jobs = db_get_recoverable_jobs()
    for job in jobs:
        try:
            pbi_display_name = f"{job['username']}__{job['report_name']}"
            db_register_report(
                job["report_name"], job["pbi_report_id"], job["user_id"],
                pbi_workspace_id=job["pbi_workspace_id"],
                pbi_display_name=pbi_display_name,
            )
            db_update_upload_job(job["id"], "completed", error_message=None)
            logger.info("UPLOAD RECOVERED | job_id=%s | report=%s", job["id"], job["report_name"])
        except psycopg2.Error:
            logger.exception("UPLOAD RECOVERY FAIL | job_id=%s", job["id"])


async def fetch_pbi_folders_and_reports() -> list[dict]:
    """Fabric 폴더 목록 + PBI 보고서 목록을 조합해 반환한다.

    반환: [{"folder_name": str|None, "folder_id": str|None,
             "pbi_report_id": str, "name": str, "dataset_id": str|None}]

    폴더 목록은 Fabric API(다른 스코프), 보고서 목록은 PBI API로 각각 조회한다.
    folder_id로 매핑해 각 보고서가 어느 폴더에 속하는지 결정한다.
    폴더 없는 보고서(루트)는 folder_name=None으로 반환한다.
    folder_name은 하위 폴더까지 포함한 전체 경로("본부/팀")다.
    """
    fabric_token = await asyncio.to_thread(get_fabric_token)
    pbi_token    = await asyncio.to_thread(get_access_token)

    fabric_headers = {"Authorization": f"Bearer {fabric_token}"}
    pbi_headers    = {"Authorization": f"Bearer {pbi_token}"}

    fabric_api = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Fabric 폴더 목록 (folderId → 전체 경로 "본부/팀")
        folder_nodes: dict[str, dict] = {}
        params: dict = {"recursive": "true"}
        while True:
            resp = await client.get(f"{fabric_api}/folders", headers=fabric_headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("value", []):
                folder_nodes[item["id"]] = {
                    "name":   item["displayName"],
                    "parent": item.get("parentFolderId"),
                }
            ct = data.get("continuationToken")
            if not ct:
                break
            params = {"continuationToken": ct}

        # parentFolderId를 따라 올라가며 경로를 조합 (seen은 순환 참조 방어)
        folder_map: dict[str, str] = {}
        for fid in folder_nodes:
            parts: list[str] = []
            cur: str | None = fid
            seen: set[str] = set()
            while cur and cur in folder_nodes and cur not in seen:
                seen.add(cur)
                parts.append(folder_nodes[cur]["name"])
                cur = folder_nodes[cur]["parent"]
            folder_map[fid] = "/".join(reversed(parts))

        # 2. Fabric /items → 보고서별 folderId 매핑
        #    PBI REST API(/reports)는 folderId를 반환하지 않으므로 Fabric /items를 사용한다.
        report_folder_map: dict[str, str | None] = {}
        params = {}
        while True:
            resp = await client.get(f"{fabric_api}/items", headers=fabric_headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("value", []):
                if item.get("type") == "Report":
                    report_folder_map[item["id"]] = item.get("folderId")
            ct = data.get("continuationToken")
            if not ct:
                break
            params = {"continuationToken": ct}

        # 3. PBI /reports → datasetId 조회 (Fabric /items에는 datasetId 없음)
        resp = await client.get(f"{PBI_GROUPS}/{WORKSPACE_ID}/reports", headers=pbi_headers)
        resp.raise_for_status()
        pbi_reports = resp.json().get("value", [])

    result = []
    for r in pbi_reports:
        report_id = r["id"]
        folder_id = report_folder_map.get(report_id)
        result.append({
            "folder_name":   folder_map.get(folder_id) if folder_id else None,
            "folder_id":     folder_id,
            "pbi_report_id": report_id,
            "name":          r["name"],
            "dataset_id":    r.get("datasetId"),
        })
    return result


async def recover_pending_imports():
    """accepted·unknown 상태 업로드의 PBI Import를 재조회해 이어서 처리한다."""
    jobs = await asyncio.to_thread(db_get_pending_imports)
    if not jobs:
        return
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        for job in jobs:
            try:
                resp = await client.get(f"{PBI_API}/imports/{job['import_id']}", headers=headers)
                if resp.status_code != 200:
                    logger.warning("UPLOAD RECOVERY WAIT | job_id=%s | http=%s", job["id"], resp.status_code)
                    continue
                result = resp.json()
                state = result.get("importState")
                if state == "Failed":
                    await asyncio.to_thread(db_update_upload_job, job["id"], "failed", error_message=str(result.get("error")))
                    continue
                if state != "Succeeded" or not result.get("reports"):
                    continue
                pbi_report_id = result["reports"][0]["id"]
                dataset_ids = [d["id"] for d in result.get("datasets", [])]
                pbi_display_name = f"{job['username']}__{job['report_name']}"
                from services.powerbi import pbi_rename_report, pbi_rename_dataset
                from config import WORKSPACE_ID
                try:
                    await pbi_rename_report(WORKSPACE_ID, pbi_report_id, pbi_display_name)
                    for ds_id in dataset_ids:
                        await pbi_rename_dataset(WORKSPACE_ID, ds_id, pbi_display_name)
                except Exception as exc:
                    logger.warning("IMPORT RECOVERY RENAME WARN | job_id=%s | error=%s", job["id"], exc)
                await asyncio.to_thread(db_update_upload_job, job["id"], "pbi_succeeded", pbi_report_id=pbi_report_id)
                await asyncio.to_thread(
                    db_register_report, job["report_name"], pbi_report_id, job["user_id"],
                    dataset_ids[0] if dataset_ids else None, None, pbi_display_name,
                )
                await asyncio.to_thread(db_update_upload_job, job["id"], "completed")
                logger.info("UPLOAD IMPORT RECOVERED | job_id=%s", job["id"])
            except Exception:
                logger.exception("UPLOAD IMPORT RECOVERY FAIL | job_id=%s", job["id"])
