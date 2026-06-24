# /* fabric */
"""Fabric 워크스페이스 폴더 관리 (Preview API).

Power BI REST API가 아닌 Fabric REST API를 사용한다.
두 API 모두 같은 서비스 주체 토큰으로 접근 가능.
"""
import asyncio
import logging

import httpx

from services.azure import get_fabric_token

FABRIC_BASE = "https://api.fabric.microsoft.com/v1"
logger = logging.getLogger("powerbi-gateway")


async def get_or_create_folder(workspace_id: str, folder_name: str) -> str | None:
    """폴더 ID 반환. 없으면 생성. 실패 시 None 반환 (비치명적)."""
    token = await asyncio.to_thread(get_fabric_token)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{FABRIC_BASE}/workspaces/{workspace_id}/folders"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                for folder in resp.json().get("value", []):
                    if folder.get("displayName") == folder_name:
                        logger.debug("FOLDER EXISTS | name=%s | id=%s", folder_name, folder["id"])
                        return folder["id"]

            resp = await client.post(url, headers=headers, json={"displayName": folder_name})
            if resp.status_code in (200, 201):
                folder_id = resp.json()["id"]
                logger.info("FOLDER CREATED | name=%s | id=%s", folder_name, folder_id)
                return folder_id

            logger.warning("FOLDER CREATE FAIL | name=%s | status=%s | body=%.200s",
                           folder_name, resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("FOLDER ERROR | name=%s | error=%s", folder_name, exc)

    return None


async def move_item_to_folder(workspace_id: str, item_id: str, folder_id: str) -> bool:
    """Fabric 아이템을 폴더로 이동. 성공 여부 반환."""
    token = await asyncio.to_thread(get_fabric_token)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{FABRIC_BASE}/workspaces/{workspace_id}/items/{item_id}/move"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, headers=headers, json={"targetFolderId": folder_id})
            if resp.status_code in (200, 204):
                return True
            logger.warning("MOVE ITEM FAIL | item=%s | status=%s | body=%.200s",
                           item_id, resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("MOVE ITEM ERROR | item=%s | error=%s", item_id, exc)

    return False
