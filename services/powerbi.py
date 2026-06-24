"""Power BI Embed Token 발급 + 표준 PBI REST API 헬퍼."""
import asyncio
import threading
import time
from datetime import datetime

import httpx

from config import WORKSPACE_ID, EMBED_TOKEN_LIFETIME
from database import db_get_report, db_mark_report_deleted
from errors import AppError
from services.azure import get_access_token

# ── Embed Token 인메모리 캐시 ─────────────────────────────────────────────────
# 보고서를 열 때마다 PBI API를 3번(GET report → GET dataset → POST GenerateToken)
# 호출하는 것을 줄이기 위한 캐시.
#
# 캐시 키: (report_id, pbi_username, roles)
#   - report_id:   DB의 내부 ID. pbi_report_id와 1:1 대응.
#   - pbi_username: GenerateToken identity에 들어가는 값 — 사용자마다 다른 토큰 필요.
#   - roles:       RLS 역할 문자열 — 역할이 다르면 다른 토큰 필요.
#
# 캐시 값: embed_token, embed_url, expires_at(Unix timestamp)
#   - report_name, settings(enable_filter 등)는 관리자가 바꿀 수 있으므로 항상 DB에서 읽음.
#   - embed_token·embed_url만 캐시 대상. 이 두 값은 PBI 측에서만 변경됨.
#
# 만료 처리: PBI API 응답의 expiration 필드를 파싱해 캐시 만료 시각으로 사용.
#   만료 5분 전에 캐시 미스 처리하여 토큰 만료 직전 요청이 실패하는 것을 방지.
_embed_cache: dict[tuple, dict] = {}
_embed_lock  = threading.Lock()
_EMBED_MARGIN_SEC = 300  # 만료 N초 전에 캐시 무효화

# Stampede 방지: 동일 키의 캐시 만료 시 여러 요청이 동시에 PBI API를 호출하지 않도록
# 키별 asyncio.Lock을 유지한다. 첫 번째 요청만 PBI API를 호출하고 나머지는 대기 후 캐시를 재사용.
_fetch_locks: dict[tuple, asyncio.Lock] = {}
_fetch_locks_mutex = threading.Lock()


def _get_fetch_lock(key: tuple) -> asyncio.Lock:
    with _fetch_locks_mutex:
        if key not in _fetch_locks:
            _fetch_locks[key] = asyncio.Lock()
        return _fetch_locks[key]


def _get_cached_token(report_id: int, pbi_username: str, roles: str) -> dict | None:
    with _embed_lock:
        entry = _embed_cache.get((report_id, pbi_username, roles))
        if entry and time.time() < entry["expires_at"] - _EMBED_MARGIN_SEC:
            return entry
    return None


def _set_cached_token(
    report_id: int, pbi_username: str, roles: str,
    embed_token: str, embed_url: str, expires_at: float,
):
    with _embed_lock:
        _embed_cache[(report_id, pbi_username, roles)] = {
            "embed_token": embed_token,
            "embed_url":   embed_url,
            "expires_at":  expires_at,
        }


def _parse_token_expiry(expiration_str: str) -> float:
    """PBI GenerateToken 응답의 expiration 문자열을 Unix timestamp로 변환."""
    try:
        dt = datetime.fromisoformat(expiration_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return time.time() + EMBED_TOKEN_LIFETIME * 60


async def get_embed_token(report_id: int, pbi_username: str, roles: str) -> dict:
    """Power BI Embed Token 발급. roles는 DB에 저장된 콤마 구분 문자열."""
    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["pbi_report_id"]:
        raise AppError.REPORT_NOT_FOUND.http()

    # 1차 캐시 체크 (락 없이) — 대부분의 요청은 여기서 즉시 반환
    cached = _get_cached_token(report_id, pbi_username, roles)
    if cached:
        return {
            "embed_token": cached["embed_token"],
            "embed_url":   cached["embed_url"],
            "report_id":   report_row["pbi_report_id"],
            "report_name": report_row["name"],
            "settings": {
                "default_page":    report_row["default_page"],
                "enable_filter":   report_row["enable_filter"],
                "enable_page_nav": report_row["enable_page_nav"],
                "use_data_bot":    report_row["use_data_bot"],
                "tab_type":        report_row["tab_type"],
            },
        }

    # 2차: 키별 Lock 안에서 캐시 재확인 + PBI API 호출 (stampede 방지)
    # 동일 키 만료 시 첫 번째 요청만 PBI API를 호출하고, 대기하던 요청들은 락 해제 후 캐시를 재사용한다.
    key = (report_id, pbi_username, roles)
    async with _get_fetch_lock(key):
        cached = _get_cached_token(report_id, pbi_username, roles)
        if cached:
            return {
                "embed_token": cached["embed_token"],
                "embed_url":   cached["embed_url"],
                "report_id":   report_row["pbi_report_id"],
                "report_name": report_row["name"],
                "settings": {
                    "default_page":    report_row["default_page"],
                    "enable_filter":   report_row["enable_filter"],
                    "enable_page_nav": report_row["enable_page_nav"],
                    "use_data_bot":    report_row["use_data_bot"],
                    "tab_type":        report_row["tab_type"],
                },
            }

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

        expires_at = _parse_token_expiry(token_data.get("expiration", ""))
        _set_cached_token(report_id, pbi_username, roles, token_data["token"], report_info["embedUrl"], expires_at)

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
