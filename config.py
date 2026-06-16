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
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

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


# ── app_config 로더 ───────────────────────────────────────────────────────────
def _load_app_config() -> dict:
    """DB app_config 테이블에서 런타임 설정을 읽는다. 서버 기동 시 1회 로드."""
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


_cfg = _load_app_config()

MAX_PBIX_SIZE        = _int(_cfg, "max_pbix_size_mb",          1024) * 1024 * 1024
MAX_UPLOADS_PER_DAY  = _int(_cfg, "max_uploads_per_day",         int(os.getenv("MAX_UPLOADS_PER_DAY",  "10")))
MAX_PERSONAL_REPORTS = _int(_cfg, "max_personal_reports",        int(os.getenv("MAX_PERSONAL_REPORTS", "20")))
REPORT_NAME_MAX_LEN  = _int(_cfg, "report_name_max_len",         50)
PASSWORD_MIN_LEN     = _int(_cfg, "password_min_len",             8)
PBI_SYNC_INTERVAL    = _int(_cfg, "pbi_sync_interval",           int(os.getenv("PBI_SYNC_INTERVAL", "600")))
LOGIN_BLOCK_MAX_FAIL = _int(_cfg, "login_block_max_fail",         5)
LOGIN_BLOCK_MINUTES  = _int(_cfg, "login_block_minutes",         15)
IMPORT_POLL_MAX      = _int(_cfg, "import_poll_max",            100)
IMPORT_POLL_INTERVAL = _int(_cfg, "import_poll_interval_sec",     3)
EMBED_TOKEN_LIFETIME = _int(_cfg, "embed_token_lifetime_min",    60)
MAX_EMBED_RLS_ROLES  = _int(_cfg, "max_embed_rls_roles",         10)
