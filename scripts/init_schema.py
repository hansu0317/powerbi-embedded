"""게이트웨이 DB 스키마 생성 (버전 이력 없는 단일 스크립트).

기존 migrate_report_meta.py의 v1~v14 마이그레이션 이력을 하나로 합친 것 —
운영 DB는 이 버전 하나만 새로 만들고 시작하므로, 과거 테이블을 만들었다 지우는
전환 과정을 거칠 필요가 없다. 실제 운영 중인 DB(v14 적용 완료 상태)를
`pg_dump --schema-only`로 그대로 추출해서 만들었다 (기억으로 재구성하지 않음).

모든 문장이 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS라서
서버 시작마다 다시 실행해도 안전하다 — v1~v14처럼 순서가 있는 "버전"이 아니라
그냥 "이 스키마가 없으면 만든다"이므로 schema_migrations 같은 버전 추적이
필요 없다.

**주의 — 이후 스키마를 바꿀 때**: 이 파일을 계속 고쳐 쓰지 말 것.
새 변경사항은 여기에 ALTER 문을 추가하는 대신, 배포 시 관리자가 직접 실행하는
별도 SQL로 처리하거나, 팀 필요에 따라 버전 있는 마이그레이션 체계로 다시
전환할 것 — 지금은 "운영 DB가 1개뿐이고 변경이 드물다"는 전제로 단순화한 것.

실행 방법:
    python scripts/init_schema.py
"""
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "powerbi_gateway"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
}

TABLES = [
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) NOT NULL UNIQUE,
        password VARCHAR(255) NOT NULL,
        display_name VARCHAR(100) NOT NULL,
        pbi_username VARCHAR(255) NOT NULL,
        is_admin BOOLEAN NOT NULL DEFAULT FALSE,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_login_at TIMESTAMPTZ,
        can_upload BOOLEAN NOT NULL DEFAULT TRUE,
        department VARCHAR(60),
        data_scope VARCHAR(16) NOT NULL DEFAULT 'self'
            CHECK (data_scope IN ('self', 'department', 'all'))
    )""",
    """CREATE TABLE IF NOT EXISTS groups (
        id SERIAL PRIMARY KEY,
        name VARCHAR(50) NOT NULL UNIQUE,
        description TEXT,
        entra_group_id VARCHAR(36),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS report_folders (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        parent_id INTEGER REFERENCES report_folders(id) ON DELETE CASCADE,
        owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        visibility VARCHAR(16) NOT NULL DEFAULT 'personal'
            CHECK (visibility IN ('personal', 'group', 'shared')),
        fabric_folder_id VARCHAR(36),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id SERIAL PRIMARY KEY,
        name VARCHAR(50) NOT NULL,
        report_type VARCHAR(16) NOT NULL
            CHECK (report_type IN ('managed', 'personal', 'dashboard')),
        owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'disabled', 'archived', 'deleted')),
        deleted_at TIMESTAMPTZ,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        category VARCHAR(255),
        description TEXT,
        pbi_report_id VARCHAR(50),
        pbi_workspace_id VARCHAR(36),
        pbi_dataset_id VARCHAR(36),
        pbi_display_name VARCHAR(105),
        folder_id VARCHAR(36),
        tab_type VARCHAR(32) NOT NULL DEFAULT 'report'
    )""",
    """CREATE TABLE IF NOT EXISTS user_groups (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, group_id)
    )""",
    """CREATE TABLE IF NOT EXISTS group_reports (
        group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
        can_view BOOLEAN NOT NULL DEFAULT TRUE,
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (group_id, report_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_reports (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
        can_view BOOLEAN NOT NULL DEFAULT TRUE,
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, report_id)
    )""",
    """CREATE TABLE IF NOT EXISTS upload_jobs (
        id BIGSERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        report_name VARCHAR(50) NOT NULL,
        status VARCHAR(32) NOT NULL,
        import_id VARCHAR(36),
        pbi_report_id VARCHAR(36),
        pbi_workspace_id VARCHAR(36),
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL,
        job_type VARCHAR(16) NOT NULL DEFAULT 'create'
            CHECK (job_type IN ('create', 'update')),
        target_report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_report_marks (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
        is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
        favorited_at TIMESTAMPTZ,
        viewed_at TIMESTAMPTZ,
        PRIMARY KEY (user_id, report_id)
    )""",
    """CREATE TABLE IF NOT EXISTS event_log (
        id BIGSERIAL PRIMARY KEY,
        log_type VARCHAR(16) NOT NULL CHECK (log_type IN ('activity', 'audit', 'login')),
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        username VARCHAR(100),
        report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL,
        report_name VARCHAR(105),
        event VARCHAR(64),
        ip VARCHAR(64),
        succeeded BOOLEAN,
        http_status SMALLINT,
        message TEXT,
        path VARCHAR(255),
        detail TEXT,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS app_config (
        key VARCHAR(64) PRIMARY KEY,
        value TEXT NOT NULL,
        description TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
]

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS report_folders_scope_name_uidx ON report_folders (COALESCE(parent_id, 0), COALESCE(owner_id, 0), LOWER(name))",
    "CREATE INDEX IF NOT EXISTS report_folders_parent_idx ON report_folders(parent_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS reports_managed_name_uidx ON reports (LOWER(name)) WHERE owner_id IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS reports_personal_owner_name_uidx ON reports (owner_id, LOWER(name)) WHERE owner_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS reports_pbi_report_id_idx ON reports (pbi_report_id) WHERE pbi_report_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS reports_owner_idx ON reports (owner_id) WHERE owner_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS reports_status_idx ON reports (status)",
    "CREATE INDEX IF NOT EXISTS reports_category_name_idx ON reports (category NULLS LAST, name) WHERE status = 'active'",
    "CREATE INDEX IF NOT EXISTS user_groups_group_idx ON user_groups (group_id)",
    "CREATE INDEX IF NOT EXISTS group_reports_report_idx ON group_reports (report_id)",
    "CREATE INDEX IF NOT EXISTS user_reports_report_idx ON user_reports (report_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS upload_jobs_inflight_name_uidx ON upload_jobs (user_id, LOWER(report_name)) "
        "WHERE status IN ('publishing', 'accepted', 'unknown', 'pbi_succeeded', 'db_failed')",
    "CREATE INDEX IF NOT EXISTS upload_jobs_user_day_idx ON upload_jobs (user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS upload_jobs_status_idx ON upload_jobs (status)",
    "CREATE INDEX IF NOT EXISTS upload_jobs_report_idx ON upload_jobs (report_id) WHERE report_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS user_report_marks_recent_idx ON user_report_marks (user_id, viewed_at DESC) WHERE viewed_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS event_log_login_lookup_idx ON event_log (username, ip, created_at) WHERE log_type = 'login'",
    "CREATE INDEX IF NOT EXISTS event_log_activity_created_idx ON event_log (created_at DESC) WHERE log_type = 'activity'",
    "CREATE INDEX IF NOT EXISTS event_log_activity_user_idx ON event_log (user_id, created_at DESC) WHERE log_type = 'activity'",
    "CREATE INDEX IF NOT EXISTS event_log_activity_report_idx ON event_log (report_id, created_at DESC) WHERE log_type = 'activity'",
    "CREATE INDEX IF NOT EXISTS event_log_audit_actor_idx ON event_log (user_id) WHERE log_type = 'audit'",
    "CREATE INDEX IF NOT EXISTS event_log_audit_created_idx ON event_log (created_at) WHERE log_type = 'audit'",
    "CREATE INDEX IF NOT EXISTS event_log_audit_details_gin ON event_log USING gin (details) WHERE log_type = 'audit'",
]

MIGRATIONS = [
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS portal_folder_id INTEGER REFERENCES report_folders(id) ON DELETE SET NULL",
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS visibility VARCHAR(16) NOT NULL DEFAULT 'personal' CHECK (visibility IN ('personal','group','shared'))",
    "CREATE INDEX IF NOT EXISTS reports_portal_folder_idx ON reports(portal_folder_id) WHERE status = 'active'",
    """INSERT INTO report_folders(name, owner_id, visibility, created_by)
       SELECT DISTINCT COALESCE(NULLIF(r.category, ''), '미분류'), r.owner_id,
              CASE WHEN r.owner_id IS NULL THEN 'shared' ELSE 'personal' END, r.owner_id
       FROM reports r
       WHERE NOT EXISTS (
         SELECT 1 FROM report_folders f
         WHERE f.name = COALESCE(NULLIF(r.category, ''), '미분류')
           AND f.owner_id IS NOT DISTINCT FROM r.owner_id)""",
    """UPDATE reports r SET portal_folder_id = f.id,
              visibility = 'personal'
       FROM report_folders f
       WHERE r.portal_folder_id IS NULL
         AND f.name = COALESCE(NULLIF(r.category, ''), '미분류')
         AND f.owner_id IS NOT DISTINCT FROM r.owner_id""",
    # 기존 owner 없는 관리 보고서는 종전의 직접/그룹 권한을 유지한다. 자동 전체공개 금지.
    "UPDATE reports SET visibility='personal' WHERE owner_id IS NULL AND visibility='shared'",
]

VIEWS = [
    # company_code/company_scope/company_parent는 scripts/add_company_hierarchy.py(2026-08)에서
    # 추가된 회사 계층 RLS용 컬럼 — department/data_scope와 같은 패턴. CREATE OR REPLACE VIEW는
    # 기존 컬럼을 빼거나 순서를 바꿀 수 없으므로(PostgreSQL 제약), 여기 정의는 항상 실제 뷰의
    # 최신 형태와 일치시켜야 한다 — 안 맞으면 서버 시작 시 init_schema()가 에러로 죽는다.
    """CREATE OR REPLACE VIEW v_rls_user_scope AS
       SELECT u.pbi_username AS user_key, u.department, u.data_scope,
              u.company_code, u.company_scope, cc.parent_code AS company_parent
       FROM users u
       LEFT JOIN company_codes cc ON cc.code = u.company_code
       WHERE u.is_active = TRUE""",
]

# app_config 기본값 — 없는 키만 채운다 (이미 있으면 관리자가 바꾼 값을 보존)
APP_CONFIG_DEFAULTS = [
    ("max_pbix_size_mb",          "1024", "업로드 허용 최대 파일 크기 (MB). Power BI Import API 한도 1 GB."),
    ("max_uploads_per_day",        "10",  "사용자당 하루 업로드 최대 횟수."),
    ("max_personal_reports",       "20",  "사용자당 개인 보고서 최대 등록 수."),
    ("report_name_max_len",        "50",  "보고서 이름 최대 글자 수."),
    ("password_min_len",            "8",  "사용자 비밀번호 최소 글자 수."),
    ("pbi_sync_interval",         "600",  "PBI 삭제 동기화 주기 (초). 0 이면 자동 동기화 비활성."),
    ("login_block_max_fail",        "5",  "로그인 실패 N회 초과 시 IP+계정 차단."),
    ("login_block_minutes",        "15",  "로그인 차단 유지 시간 (분)."),
    ("import_poll_max",           "100",  "게시 완료 대기 최대 횟수 (import_poll_interval_sec 간격)."),
    ("import_poll_interval_sec",    "3",  "게시 상태 조회 간격 (초)."),
    ("embed_token_lifetime_min",   "60",  "PBI 응답의 만료 시각 파싱에 실패했을 때만 쓰이는 예비값(분)."),
    ("pbi_token_cache_margin_sec","300",  "Azure AD 토큰 만료 N초 전에 갱신. 기본 5분."),
    ("activity_log_retention_days","90",  "사용자 활동 로그 보존 기간 (일). 초과분은 매일 자동 삭제."),
]


def init_schema():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            for stmt in TABLES:
                cur.execute(stmt)
            for stmt in MIGRATIONS:
                cur.execute(stmt)
            for stmt in INDEXES:
                cur.execute(stmt)
            for stmt in VIEWS:
                cur.execute(stmt)
            for key, value, desc in APP_CONFIG_DEFAULTS:
                cur.execute(
                    "INSERT INTO app_config (key, value, description) VALUES (%s, %s, %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (key, value, desc),
                )
            # 초기 데이터를 명시적 ID로 넣은 DB 대비 시퀀스 보정
            cur.execute(
                """SELECT setval(
                       pg_get_serial_sequence('reports', 'id'),
                       GREATEST((SELECT COALESCE(MAX(id), 0) FROM reports), 1),
                       (SELECT COUNT(*) > 0 FROM reports)
                   )"""
            )
        conn.commit()
    print("schema init complete")


if __name__ == "__main__":
    init_schema()
