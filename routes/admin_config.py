"""관리자 포털 라우트: 런타임 설정(app_config) 조회/변경.

routes/admin.py에서 2026-08-27에 분리했다(분리 배경은 admin_users.py 상단 주석 참고)."""
import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.requests import Request

import config
from database import db_get_app_config, db_update_app_config
from deps import require_admin_user, require_admin_csrf, json_body
from errors import AppError

router = APIRouter()
logger = logging.getLogger("powerbi-gateway")

# 설정 키별 허용 범위 — 관리자 실수로 서비스를 마비시키는 값(0 한도, 폴링 폭주 등)을 차단한다.
# 키 추가 시 여기와 scripts/init_schema.py의 APP_CONFIG_DEFAULTS에 함께 등록할 것.
CONFIG_LIMITS = {
    "max_pbix_size_mb":           (1, 1024),   # PBI Import API 자체 한도 1GB
    "max_uploads_per_day":        (1, 100),
    "max_personal_reports":       (1, 200),
    "report_name_max_len":        (10, 100),
    "password_min_len":           (4, 64),
    "pbi_sync_interval":          (0, 86400),  # 0 = 자동 동기화 끔
    "login_block_max_fail":       (1, 100),
    "login_block_minutes":        (1, 1440),
    "import_poll_max":            (10, 1000),
    "import_poll_interval_sec":   (1, 60),
    "embed_token_lifetime_min":   (5, 60),
    "pbi_token_cache_margin_sec": (0, 3600),
    "activity_log_retention_days": (7, 3650),
    "error_log_retention_days":  (7, 3650),
    "recents_limit":              (1, 50),
    "activity_log_max_rows":      (100, 10000),
    "admin_upload_jobs_limit":    (5, 500),
}


@router.get("/api/admin/config")
async def api_admin_get_config(user: dict = Depends(require_admin_user)):
    """런타임 설정(app_config) 목록."""
    rows = await asyncio.to_thread(db_get_app_config)
    return {"config": rows}


@router.post("/api/admin/config")
async def api_admin_set_config(request: Request, user: dict = Depends(require_admin_csrf)):
    """런타임 설정 변경 — 저장 즉시 재시작 없이 반영된다.

    키는 init_schema.py가 시드한 것만 허용하고, 값은 정수만 받는다(현재 키 전부 정수)."""
    body = await json_body(request)
    key, value = str(body.get("key", "")), str(body.get("value", "")).strip()
    if key not in CONFIG_LIMITS:
        raise AppError.CONFIG_KEY_UNKNOWN.http(key=key)
    try:
        num = int(value)
    except ValueError:
        raise AppError.CONFIG_VALUE_INVALID.http(value=value)
    lo, hi = CONFIG_LIMITS[key]
    if not (lo <= num <= hi):
        raise AppError.CONFIG_VALUE_OUT_OF_RANGE.http(key=key, min=lo, max=hi)
    updated = await asyncio.to_thread(db_update_app_config, key, value)
    if not updated:
        raise AppError.CONFIG_KEY_UNKNOWN.http(key=key)
    config.reload_app_config()
    logger.info("ADMIN CONFIG | admin=%s | %s=%s", user["username"], key, value)
    return {"key": key, "value": value}
