"""Power BI Embed Token 발급 + 표준 PBI REST API 헬퍼."""
import asyncio

import httpx

from config import WORKSPACE_ID
from database import db_get_report, db_mark_report_deleted
from errors import AppError
from services.azure import get_access_token


async def get_embed_token(report_id: int, pbi_username: str, roles: str) -> dict:
    """Power BI Embed Token 발급. roles는 DB에 저장된 콤마 구분 문자열."""
    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["pbi_report_id"]:
        raise AppError.REPORT_NOT_FOUND.http()

    pbi_report_id = report_row["pbi_report_id"]
    workspace_id  = report_row["pbi_workspace_id"] or WORKSPACE_ID
    report_api    = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"

    access_token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{report_api}/reports/{pbi_report_id}", headers=headers)
        if resp.status_code == 404:
            await asyncio.to_thread(db_mark_report_deleted, report_row["id"], pbi_report_id, "embed 404")
            raise AppError.REPORT_DELETED.http()
        if resp.status_code != 200:
            raise AppError.REPORT_FETCH_FAILED.http(detail=resp.text)
        report_info = resp.json()

        dataset_id   = report_info.get("datasetId", "")
        dataset_info = None
        if dataset_id:
            resp = await client.get(f"{report_api}/datasets/{dataset_id}", headers=headers)
            if resp.status_code == 200:
                dataset_info = resp.json()

        body = {"accessLevel": "view"}
        identity_required = (
            report_row["rls_enabled"]
            or dataset_info is None
            or dataset_info.get("isEffectiveIdentityRequired")
        )
        if identity_required:
            identity = {"username": pbi_username, "datasets": [dataset_id]}
            roles_required = (
                report_row["rls_enabled"]
                or dataset_info is None
                or dataset_info.get("isEffectiveIdentityRolesRequired")
            )
            if roles_required:
                configured_roles = report_row["rls_role_names"]
                identity["roles"] = configured_roles or [r.strip() for r in roles.split(",") if r.strip()]
            body["identities"] = [identity]

        resp = await client.post(f"{report_api}/reports/{pbi_report_id}/GenerateToken", headers=headers, json=body)
        if resp.status_code != 200:
            raise AppError.EMBED_TOKEN_FAILED.http(detail=resp.text)
        token_data = resp.json()

    return {
        "embed_token": token_data["token"],
        "embed_url":   report_info["embedUrl"],
        "report_id":   pbi_report_id,
        "report_name": report_row["name"],
        "settings": {
            "default_page":    report_row["default_page"],
            "enable_filter":   report_row["enable_filter"],
            "enable_page_nav": report_row["enable_page_nav"],
            "use_data_bot":    report_row["use_data_bot"],
            "tab_type":        report_row["tab_type"],
        },
    }


# ── 표준 PBI API — 이름 변경 / 삭제 ─────────────────────────────────────────

async def _pbi_patch_name(resource_url: str, new_name: str) -> None:
    """PBI REST API PATCH로 이름을 변경한다. 409 시 ValueError('name_conflict:...') 발생."""
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(resource_url, headers=headers, json={"name": new_name})
    if resp.status_code == 409:
        raise ValueError(f"name_conflict:{new_name}")
    if resp.status_code not in (200, 204):
        resp.raise_for_status()


async def pbi_rename_report(workspace_id: str, report_id: str, new_name: str) -> None:
    """표준 PBI REST API로 보고서 이름을 변경한다. 409 시 ValueError('name_conflict:...') 발생."""
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/reports/{report_id}"
    await _pbi_patch_name(url, new_name)


async def pbi_rename_dataset(workspace_id: str, dataset_id: str, new_name: str) -> None:
    """표준 PBI REST API로 데이터셋 이름을 변경한다. 409 시 ValueError('name_conflict:...') 발생."""
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}"
    await _pbi_patch_name(url, new_name)


async def pbi_delete_report(workspace_id: str, report_id: str) -> None:
    """Power BI 워크스페이스에서 보고서를 삭제한다. 이미 없으면(404) 조용히 무시한다."""
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/reports/{report_id}",
            headers=headers,
        )
    if resp.status_code == 404:
        return
    resp.raise_for_status()
