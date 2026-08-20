"""환경변수 로드 + app_config DB 테이블에서 런타임 설정 읽기."""
import logging
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("powerbi-gateway")

# ── Azure AD / Power BI ───────────────────────────────────────────────────────
TENANT_ID     = os.getenv("TENANT_ID")
CLIENT_ID     = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
WORKSPACE_ID  = os.getenv("WORKSPACE_ID")
SECRET_KEY    = os.getenv("SECRET_KEY")

# 동적 RLS 역할 이름 — 조직 전체가 이 역할 하나만 공유한다(docs/01_RLS_적용가이드.md).
# Power BI Desktop에서 만드는 보안 역할 이름과 반드시 일치해야 한다. 사람마다 다르게
# 줄 필요가 없어서(실제로 전원 동일값이었음, 2026-08 확인) 사용자별 컬럼 대신 값 하나로 둔다.
# WORKSPACE_ID와 같은 이유로 .env에 둔다 — 바뀔 수 있는 외부(Power BI) 값이라, 바뀔 때
# 코드 수정·재배포 없이 .env만 고치고 재시작하면 되게. 단, 실제로 바꾸려면 이 값과 일치하게
# 관련 PBIX 전부의 "Manage roles" 이름도 같이 바꿔야 한다 — .env만 바꾸면 반대로 다 깨진다.
PBI_RLS_ROLE_NAME = os.getenv("PBI_RLS_ROLE_NAME", "도메인")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# 네비게이션 허브 — 설정 시에만 상단바에 "마케팅 포털" 링크 노출(선택, 없으면 링크 없음)
MARKETING_PORTAL_URL = os.getenv("MARKETING_PORTAL_URL")

# MS 계정 로그인(SSO) — Azure AD 앱 등록(위 CLIENT_ID/TENANT_ID와 동일 앱 재사용)에
# "웹" 플랫폼 리디렉션 URI를 등록해야 동작한다(2026-08-20 도입). 값이 없으면 로그인
# 화면에서 "Microsoft 계정으로 로그인" 버튼 자체를 숨긴다 — 이 저장소를 다른 환경으로
# 포팅했을 때 Azure Portal 설정 전에는 자동으로 기존 비밀번호 로그인만 보이게 하기 위함.
SSO_REDIRECT_URI = os.getenv("SSO_REDIRECT_URI")

if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY는 32자 이상의 랜덤 문자열로 설정해야 합니다.")

# ── DB ────────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":            os.getenv("DB_HOST", "127.0.0.1"),
    "port":            int(os.getenv("DB_PORT", "5432")),
    "dbname":          os.getenv("DB_NAME", "powerbi_gateway"),
    "user":            os.getenv("DB_USER"),
    "password":        os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
}

# ── Power BI REST API 엔드포인트 ─────────────────────────────────────────────
PBI_API    = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"
PBI_GROUPS = "https://api.powerbi.com/v1.0/myorg/groups"


def resolve_workspace_id(workspace_id: str | None) -> str:
    """보고서별 워크스페이스(pbi_workspace_id)가 없으면 기본 워크스페이스를 쓴다."""
    return workspace_id or WORKSPACE_ID


# ── app_config 로더 ───────────────────────────────────────────────────────────
def _load_app_config() -> dict:
    """DB app_config 테이블에서 런타임 설정을 읽는다."""
    try:
        with psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM app_config")
                return {row["key"]: row["value"] for row in cur.fetchall()}
    except Exception as exc:
        logger.warning("app_config 로드 실패, 기본값 사용: %s", exc)
        return {}


def _int(cfg: dict, key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (ValueError, TypeError):
        return default


def reload_app_config():
    """app_config를 다시 읽어 아래 런타임 상수를 갱신한다.

    서버 기동 시 1회 + 관리자 포털에서 설정을 저장할 때마다 호출된다.
    소비 측은 반드시 `config.MAX_PBIX_SIZE`처럼 속성으로 읽어야 갱신이 반영된다
    (`from config import MAX_PBIX_SIZE`는 import 시점 값으로 고정되므로 금지)."""
    cfg = _load_app_config()
    g = globals()
    g["MAX_PBIX_SIZE"]        = _int(cfg, "max_pbix_size_mb",          1024) * 1024 * 1024
    g["MAX_UPLOADS_PER_DAY"]  = _int(cfg, "max_uploads_per_day",         int(os.getenv("MAX_UPLOADS_PER_DAY",  "10")))
    g["MAX_PERSONAL_REPORTS"] = _int(cfg, "max_personal_reports",        int(os.getenv("MAX_PERSONAL_REPORTS", "20")))
    g["REPORT_NAME_MAX_LEN"]  = _int(cfg, "report_name_max_len",         50)
    g["PASSWORD_MIN_LEN"]     = _int(cfg, "password_min_len",             8)
    g["PBI_SYNC_INTERVAL"]    = _int(cfg, "pbi_sync_interval",           int(os.getenv("PBI_SYNC_INTERVAL", "600")))
    g["LOGIN_BLOCK_MAX_FAIL"] = _int(cfg, "login_block_max_fail",         5)
    g["LOGIN_BLOCK_MINUTES"]  = _int(cfg, "login_block_minutes",         15)
    g["IMPORT_POLL_MAX"]      = _int(cfg, "import_poll_max",            100)
    g["IMPORT_POLL_INTERVAL"] = _int(cfg, "import_poll_interval_sec",     3)
    g["EMBED_TOKEN_LIFETIME"] = _int(cfg, "embed_token_lifetime_min",    60)
    g["PBI_TOKEN_CACHE_MARGIN_SEC"] = _int(cfg, "pbi_token_cache_margin_sec", 300)
    g["ACTIVITY_LOG_RETENTION_DAYS"] = _int(cfg, "activity_log_retention_days", 90)
    g["REFRESH_AUTO_RETRY_MAX"] = _int(cfg, "refresh_auto_retry_max", 2)
    g["ERROR_LOG_RETENTION_DAYS"] = _int(cfg, "error_log_retention_days", 90)
    g["RECENTS_LIMIT"]        = _int(cfg, "recents_limit",              10)
    g["ACTIVITY_LOG_MAX_ROWS"] = _int(cfg, "activity_log_max_rows",   1000)
    g["ADMIN_UPLOAD_JOBS_LIMIT"] = _int(cfg, "admin_upload_jobs_limit", 30)


reload_app_config()
