"""Power BI Embed Token 발급 + 표준 PBI REST API 헬퍼."""
import asyncio
import logging
import threading
import time
from datetime import datetime

import httpx

import config
from database import db_get_report, db_mark_report_deleted
from errors import AppError
from services.azure import get_access_token

logger = logging.getLogger("powerbi-gateway")

# ── Embed Token 인메모리 캐시 ─────────────────────────────────────────────────
# 보고서를 열 때마다 PBI API를 3번(GET report → GET dataset → POST GenerateToken)
# 호출하는 것을 줄이기 위한 캐시.
#
# 캐시 키: (report_id, pbi_username)
#   - report_id:   DB의 내부 ID. pbi_report_id와 1:1 대응.
#   - pbi_username: GenerateToken identity에 들어가는 값 — 사용자마다 다른 토큰 필요.
#   (RLS 역할은 조직 전체가 config.PBI_RLS_ROLE_NAME 하나만 공유하므로 캐시 키에 안 넣는다.)
#
# 캐시 값: embed_token, embed_url, expires_at(Unix timestamp)
#   - report_name과 tab_type은 관리자 작업으로 바뀔 수 있으므로 항상 DB에서 읽음.
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


def _get_cached_token(report_id: int, pbi_username: str) -> dict | None:
    with _embed_lock:
        entry = _embed_cache.get((report_id, pbi_username))
        if entry and time.time() < entry["expires_at"] - _EMBED_MARGIN_SEC:
            return entry
    return None


def _set_cached_token(
    report_id: int, pbi_username: str,
    embed_token: str, embed_url: str, expires_at: float,
):
    with _embed_lock:
        _embed_cache[(report_id, pbi_username)] = {
            "embed_token": embed_token,
            "embed_url":   embed_url,
            "expires_at":  expires_at,
        }


def invalidate_embed_cache(report_id: int) -> int:
    """보고서의 모든 사용자 embed 토큰 캐시를 즉시 퇴거한다. 삭제된 키 수 반환."""
    with _embed_lock:
        keys = [k for k in _embed_cache if k[0] == report_id]
        for k in keys:
            del _embed_cache[k]
    return len(keys)


def _build_embed_response(
    report_row: dict, pbi_report_id: str, embed_token: str, embed_url: str, expires_at: float,
) -> dict:
    return {
        "embed_token": embed_token,
        "embed_url":   embed_url,
        "expires_at":  expires_at,
        "report_id":   pbi_report_id,
        "report_name": report_row["name"],
        # 뷰어가 report/dashboard 임베드를 분기하는 데 필요한 값만 내려보낸다.
        "settings": {"tab_type": report_row["tab_type"]},
    }


def _parse_token_expiry(expiration_str: str) -> float:
    """PBI GenerateToken 응답의 expiration 문자열을 Unix timestamp로 변환."""
    try:
        dt = datetime.fromisoformat(expiration_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return time.time() + config.EMBED_TOKEN_LIFETIME * 60


async def get_embed_token(report_id: int, pbi_username: str) -> dict:
    """Power BI Embed Token 발급. RLS 역할은 config.PBI_RLS_ROLE_NAME 하나를 전 사용자 공용으로 쓴다."""
    report_row = await asyncio.to_thread(db_get_report, report_id)
    if not report_row or not report_row["pbi_report_id"]:
        raise AppError.REPORT_NOT_FOUND.http()

    # 1차 캐시 체크 (락 없이) — 대부분의 요청은 여기서 즉시 반환
    cached = _get_cached_token(report_id, pbi_username)
    if cached:
        return _build_embed_response(
            report_row, report_row["pbi_report_id"],
            cached["embed_token"], cached["embed_url"], cached["expires_at"],
        )

    # 2차: 키별 Lock 안에서 캐시 재확인 + PBI API 호출 (stampede 방지)
    # 동일 키 만료 시 첫 번째 요청만 PBI API를 호출하고, 대기하던 요청들은 락 해제 후 캐시를 재사용한다.
    key = (report_id, pbi_username)
    async with _get_fetch_lock(key):
        cached = _get_cached_token(report_id, pbi_username)
        if cached:
            return _build_embed_response(
                report_row, report_row["pbi_report_id"],
                cached["embed_token"], cached["embed_url"], cached["expires_at"],
            )

        if report_row["tab_type"] == "dashboard":
            return await _fetch_dashboard_token(report_row, pbi_username, report_id)

        pbi_report_id = report_row["pbi_report_id"]
        workspace_id  = config.resolve_workspace_id(report_row["pbi_workspace_id"])
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
            dataset_get_status = None
            if dataset_id:
                resp = await client.get(f"{report_api}/datasets/{dataset_id}", headers=headers)
                dataset_get_status = resp.status_code
                if resp.status_code == 200:
                    dataset_info = resp.json()

            body = {"accessLevel": "view"}
            # 데이터셋에 RLS 역할이 정의돼 있으면 Power BI가 identity를 강제한다
            # (없이 보내면 400). 역할 이름은 조직 전체가 공유하는 config.PBI_RLS_ROLE_NAME 고정값.
            if dataset_info is None or dataset_info.get("isEffectiveIdentityRequired"):
                identity = {"username": pbi_username, "datasets": [dataset_id]}
                if dataset_info is None or dataset_info.get("isEffectiveIdentityRolesRequired"):
                    identity["roles"] = [config.PBI_RLS_ROLE_NAME]
                body["identities"] = [identity]

            resp = await client.post(f"{report_api}/reports/{pbi_report_id}/GenerateToken", headers=headers, json=body)
            if resp.status_code != 200:
                logger.info(
                    "PBI RLS-DEBUG | user=%s | dataset GET status=%s isEffectiveIdentityRequired=%s | "
                    "GenerateToken REQ body=%s | GenerateToken RESP status=%s",
                    pbi_username, dataset_get_status,
                    dataset_info.get("isEffectiveIdentityRequired") if dataset_info else None,
                    body, resp.status_code,
                )
                raise AppError.EMBED_TOKEN_FAILED.http(detail=resp.text)
            token_data = resp.json()
            # GenerateToken 응답 본문(token 필드)은 실제 embed 인증정보라 원문으로 남기지 않는다 — expiration만 기록.
            logger.info(
                "PBI RLS-DEBUG | user=%s | dataset GET status=%s isEffectiveIdentityRequired=%s | "
                "GenerateToken REQ body=%s | GenerateToken RESP status=%s expiration=%s",
                pbi_username, dataset_get_status,
                dataset_info.get("isEffectiveIdentityRequired") if dataset_info else None,
                body, resp.status_code, token_data.get("expiration"),
            )

        expires_at = _parse_token_expiry(token_data.get("expiration", ""))
        _set_cached_token(report_id, pbi_username, token_data["token"], report_info["embedUrl"], expires_at)

        return _build_embed_response(
            report_row, pbi_report_id, token_data["token"], report_info["embedUrl"], expires_at,
        )


async def _fetch_dashboard_token(
    report_row: dict, pbi_username: str, report_id: int,
) -> dict:
    """대시보드(Dashboard) 임베드 토큰 발급.

    Report와 별개의 GenerateToken 엔드포인트를 쓰고, 페이지·필터창·데이터셋 개념이
    없다(report_settings의 해당 필드는 대시보드에 적용되지 않음 — 프론트는
    settings.tab_type === "dashboard"로 분기해 페이지/필터 UI를 안 그린다).

    한계: 대시보드는 여러 데이터셋의 타일을 모은 것이라 RLS에 필요한 정확한
    datasets 목록을 얻으려면 타일을 순회해야 한다 — 지금은 RLS 없이 열람만 지원.
    """
    dashboard_id = report_row["pbi_report_id"]
    workspace_id = config.resolve_workspace_id(report_row["pbi_workspace_id"])
    dash_api     = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"

    access_token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{dash_api}/dashboards/{dashboard_id}", headers=headers)
        if resp.status_code == 404:
            await asyncio.to_thread(db_mark_report_deleted, report_row["id"], dashboard_id, "embed 404 (dashboard)")
            raise AppError.REPORT_DELETED.http()
        if resp.status_code != 200:
            raise AppError.REPORT_FETCH_FAILED.http(detail=resp.text)
        dash_info = resp.json()

        body = {"accessLevel": "view"}

        resp = await client.post(f"{dash_api}/dashboards/{dashboard_id}/GenerateToken", headers=headers, json=body)
        if resp.status_code != 200:
            raise AppError.EMBED_TOKEN_FAILED.http(detail=resp.text)
        token_data = resp.json()

    expires_at = _parse_token_expiry(token_data.get("expiration", ""))
    _set_cached_token(report_id, pbi_username, token_data["token"], dash_info["embedUrl"], expires_at)
    return _build_embed_response(report_row, dashboard_id, token_data["token"], dash_info["embedUrl"], expires_at)


# ── 표준 PBI API — 이름 변경 / 삭제 ─────────────────────────────────────────

async def _pbi_request(method: str, url: str, json: dict | None = None) -> httpx.Response:
    """토큰 발급 + 표준 헤더로 PBI REST API를 호출하는 공통 헬퍼."""
    token = await asyncio.to_thread(get_access_token)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.request(method, url, headers=headers, json=json)


async def _pbi_patch_name(resource_url: str, new_name: str) -> None:
    """PBI REST API PATCH로 이름을 변경한다. 409 시 ValueError('name_conflict:...') 발생."""
    resp = await _pbi_request("PATCH", resource_url, json={"name": new_name})
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
    resp = await _pbi_request(
        "DELETE", f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/reports/{report_id}",
    )
    if resp.status_code == 404:
        return
    resp.raise_for_status()


async def pbi_delete_dataset(workspace_id: str, dataset_id: str) -> None:
    """Power BI 워크스페이스에서 데이터셋을 삭제한다(용량 누수 방지). 404는 무시."""
    resp = await _pbi_request(
        "DELETE", f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}",
    )
    if resp.status_code == 404:
        return
    resp.raise_for_status()


async def rename_with_retry(
    workspace_id: str, pbi_report_id: str, dataset_ids: list[str],
    display_name: str, fallback_name: str, username: str, initial_delay: float = 8,
) -> tuple[str, str | None]:
    """보고서·데이터셋 이름 변경. 최대 5회 시도, 최종 실패 시 (fallback_name, warning) 반환.

    정상 업로드 경로와 서버 재시작 후 복구 경로(recover_pending_imports) 모두에서 쓰인다.
    initial_delay: PBI가 import 직후 보고서를 활성화하는 데 걸리는 유예 시간(기본 8초).
    복구 경로는 이미 "Succeeded" 판정 이후 시간이 지났으므로 0으로 넘겨 서버 기동을
    (recover_pending_imports는 lifespan에서 await되어 기동을 막는다) 지연시키지 않는다.
    """
    if initial_delay:
        await asyncio.sleep(initial_delay)
    for attempt in range(5):
        if attempt > 0:
            await asyncio.sleep(5)
        try:
            await pbi_rename_report(workspace_id, pbi_report_id, display_name)
            for ds_id in dataset_ids:
                await pbi_rename_dataset(workspace_id, ds_id, display_name)
            logger.info("PBI RENAME OK | user=%-12s | display=%s (attempt=%s)", username, display_name, attempt + 1)
            return display_name, None
        except Exception as exc:
            if attempt == 4:
                logger.warning("PBI RENAME WARN | user=%s | display=%s | error=%s", username, display_name, exc)
                return fallback_name, f"rename failed after 5 attempts: {exc}"
