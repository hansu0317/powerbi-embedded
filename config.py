"""환경변수 로드 + 런타임 제한값 상수."""
import os

from dotenv import load_dotenv

load_dotenv()

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


# ── 런타임 제한값 ─────────────────────────────────────────────────────────────
# 예전엔 app_config DB 테이블에서 읽어와 관리자 포털 "설정" 화면에서 재시작 없이
# 바꿀 수 있었다(2026-08-27, 학습용 코드 축소 과정에서 그 UI·API를 제거하며 여기
# 고정값으로 되돌렸다 — git 이력의 v2 태그에 DB 기반 버전이 남아있어 필요하면
# 되살릴 수 있다). 값을 바꾸려면 이 파일을 고치고 재배포해야 한다. 일부(*_PER_DAY,
# *_REPORTS, SYNC_INTERVAL)는 기존처럼 .env로도 덮어쓸 수 있게 남겨뒀다.
MAX_PBIX_SIZE        = 1024 * 1024 * 1024  # PBI Import API 자체 한도 1GB
MAX_UPLOADS_PER_DAY  = int(os.getenv("MAX_UPLOADS_PER_DAY", "10"))
MAX_PERSONAL_REPORTS = int(os.getenv("MAX_PERSONAL_REPORTS", "20"))
REPORT_NAME_MAX_LEN  = 50
PASSWORD_MIN_LEN     = 8
PBI_SYNC_INTERVAL    = int(os.getenv("PBI_SYNC_INTERVAL", "600"))
LOGIN_BLOCK_MAX_FAIL = 5
LOGIN_BLOCK_MINUTES  = 15
IMPORT_POLL_MAX      = 100
IMPORT_POLL_INTERVAL = 3
EMBED_TOKEN_LIFETIME = 60   # 분
PBI_TOKEN_CACHE_MARGIN_SEC = 300
RECENTS_LIMIT        = 10
ADMIN_UPLOAD_JOBS_LIMIT = 30
