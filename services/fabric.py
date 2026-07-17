"""PBI 동기화 루프 + 시작 시 복구 (Pro 전용)."""
import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

import config
from config import PBI_API, PBI_GROUPS, WORKSPACE_ID
from database import (
    db_get_synced_reports, db_mark_report_deleted, db_restore_report,
    db_get_pending_imports, db_get_recoverable_jobs,
    db_update_upload_job, db_register_report, db_fail_stale_publishing_jobs,
    db_get_freshness_targets, db_upsert_refresh_status, db_mark_refresh_retry,
)
from services.azure import get_access_token, get_fabric_token
from services.powerbi import rename_with_retry, pbi_refresh_dataset

logger = logging.getLogger("powerbi-gateway")

# 자가진단용 하트비트 — 각 백그라운드 작업의 마지막 실행 시각(Unix). 프로세스 메모리로 충분.
LOOP_HEARTBEAT: dict[str, float] = {}


# ── PBI 삭제 동기화 ───────────────────────────────────────────────────────────

async def sync_pbi_reports() -> dict:
    """PBI 워크스페이스와 DB를 대조해 삭제/복구를 반영한다."""
    rows = await asyncio.to_thread(db_get_synced_reports)
    if not rows:
        return {"checked": 0, "deleted": 0, "restored": 0}
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}"}

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


# ── v4: 데이터 신선도 관제 ────────────────────────────────────────────────────

def _parse_pbi_time(value: str | None):
    """PBI refresh 응답의 ISO 시각 문자열 → datetime (없으면 None)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def sync_dataset_freshness() -> dict:
    """active 보고서의 데이터셋별 refresh 이력을 수집해 DB에 반영한다.

    PBI는 refresh 실패를 아무에게도 알리지 않는다 — 여기서 수집한 상태가
    뷰어 '데이터 기준' 배지와 관리자 실패 현황의 원천이 된다.
    실패 데이터셋은 하루 한도(refresh_auto_retry_max) 내에서 자동 재시도한다.
    """
    targets = await asyncio.to_thread(db_get_freshness_targets)
    if not targets:
        return {"checked": 0, "failed": 0, "retried": 0}
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}"}

    checked = failed = retried = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for t in targets:
            ds_id, ws_id = t["pbi_dataset_id"], t["pbi_workspace_id"]
            try:
                resp = await client.get(
                    f"{PBI_GROUPS}/{ws_id}/datasets/{ds_id}/refreshes",
                    headers=headers, params={"$top": 1},
                )
                if resp.status_code != 200:
                    # DirectQuery·푸시 데이터셋 등은 refresh 이력이 없다 — 감시 제외로 표시
                    await asyncio.to_thread(
                        db_upsert_refresh_status, ds_id, ws_id, "NotRefreshable",
                        None, None, f"HTTP {resp.status_code}",
                    )
                    continue
                checked += 1
                history = resp.json().get("value", [])
                if not history:
                    await asyncio.to_thread(
                        db_upsert_refresh_status, ds_id, ws_id, "Unknown", None, None, "이력 없음")
                    continue
                last = history[0]
                status = last.get("status") or "Unknown"
                end_time = _parse_pbi_time(last.get("endTime"))
                if status == "Completed":
                    await asyncio.to_thread(
                        db_upsert_refresh_status, ds_id, ws_id, "Completed", end_time, end_time, None)
                elif status == "Failed":
                    failed += 1
                    reason = str(last.get("serviceExceptionJson") or "")[:500]
                    await asyncio.to_thread(
                        db_upsert_refresh_status, ds_id, ws_id, "Failed", None, end_time, reason)
                    # 자동 재시도 — 하루 한도 안에서만
                    if config.REFRESH_AUTO_RETRY_MAX > 0 and await asyncio.to_thread(db_mark_refresh_retry, ds_id):
                        try:
                            await pbi_refresh_dataset(ws_id, ds_id)
                            retried += 1
                            logger.info("FRESHNESS RETRY | dataset=%s (자동 재시도 접수)", ds_id)
                        except Exception as exc:
                            logger.warning("FRESHNESS RETRY FAIL | dataset=%s | %s", ds_id, exc)
                else:  # Unknown(진행 중)·Disabled 등
                    await asyncio.to_thread(
                        db_upsert_refresh_status, ds_id, ws_id, status, None, end_time, None)
            except Exception as exc:
                logger.warning("FRESHNESS SKIP | dataset=%s | %s", ds_id, exc)

    LOOP_HEARTBEAT["freshness"] = time.time()
    return {"checked": checked, "failed": failed, "retried": retried}


async def pbi_sync_loop():
    """서버 시작 시 1회 + pbi_sync_interval 주기로 PBI 동기화 + 신선도 수집을 반복한다.

    주기는 매 회 config에서 다시 읽으므로 관리자 설정 변경이 재시작 없이 반영된다.
    0(비활성)이어도 루프는 유지한다 — 나중에 다시 켤 수 있게."""
    try:
        logger.info("PBI SYNC (startup) | %s", await sync_pbi_reports())
        LOOP_HEARTBEAT["pbi_sync"] = time.time()
        logger.info("FRESHNESS (startup) | %s", await sync_dataset_freshness())
    except Exception:
        logger.exception("STARTUP PBI SYNC FAIL")
    while True:
        interval = config.PBI_SYNC_INTERVAL
        await asyncio.sleep(interval if interval > 0 else 60)
        if interval <= 0:
            continue
        try:
            summary = await sync_pbi_reports()
            LOOP_HEARTBEAT["pbi_sync"] = time.time()
            if summary["deleted"] or summary["restored"]:
                logger.info("PBI SYNC | %s", summary)
        except Exception:
            logger.exception("PBI SYNC FAIL")
        try:
            fresh = await sync_dataset_freshness()
            if fresh["failed"]:
                logger.warning("FRESHNESS | %s", fresh)
        except Exception:
            logger.exception("FRESHNESS FAIL")


# ── 시작 시 복구 ──────────────────────────────────────────────────────────────

def recover_db_jobs():
    """pbi_succeeded·db_failed 상태 업로드를 재시작 시 DB에 등록한다.

    그 전에 고아 'publishing' 잡을 실패 처리한다 — 방치하면 같은 이름
    재업로드가 부분 UNIQUE 인덱스에 걸려 계속 409가 난다."""
    import psycopg2
    stale = db_fail_stale_publishing_jobs()
    if stale:
        logger.warning("UPLOAD CLEANUP | 중단된 publishing 잡 %d건 실패 처리", stale)
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
                # 정상 업로드 경로와 동일하게 최대 5회 재시도(복구 상황일수록 PBI가
                # 아직 안정화 전일 가능성이 높음). 최종 실패해도 접수된 이름 그대로 진행.
                pbi_display_name, rename_warning = await rename_with_retry(
                    WORKSPACE_ID, pbi_report_id, dataset_ids,
                    pbi_display_name, job["report_name"], job["username"],
                    initial_delay=0,
                )
                await asyncio.to_thread(db_update_upload_job, job["id"], "pbi_succeeded", pbi_report_id=pbi_report_id, error_message=rename_warning)
                await asyncio.to_thread(
                    db_register_report, job["report_name"], pbi_report_id, job["user_id"],
                    dataset_ids[0] if dataset_ids else None, None, pbi_display_name,
                )
                await asyncio.to_thread(db_update_upload_job, job["id"], "completed")
                logger.info("UPLOAD IMPORT RECOVERED | job_id=%s", job["id"])
            except Exception:
                logger.exception("UPLOAD IMPORT RECOVERY FAIL | job_id=%s", job["id"])
