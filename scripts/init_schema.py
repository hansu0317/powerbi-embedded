"""신규·기존 DB에 누락된 테이블·컬럼·인덱스만 추가한다.

기동 시 권한·기존 데이터·기존 모델 참조를 변경하지 않는다.
삭제나 의미 변경이 필요한 마이그레이션은 백업 후 별도로 실행한다.
실행: python scripts/init_schema.py
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
        department VARCHAR(60)
    )""",
    """CREATE TABLE IF NOT EXISTS report_folders (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        parent_id INTEGER REFERENCES report_folders(id) ON DELETE CASCADE,
        owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        visibility VARCHAR(16) NOT NULL DEFAULT 'personal'
            CHECK (visibility IN ('personal', 'shared')),
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
    """CREATE TABLE IF NOT EXISTS user_reports (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
        can_view BOOLEAN NOT NULL DEFAULT TRUE,
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, report_id)
    )""",
    # 부서 단위 열람권한(1층) — 개인별 예외는 위 user_reports가 최우선으로 이긴다
    # (database/reports.py::_CAN_VIEW_REPORT_SQL 참고). department 값은 users.department와
    # 리터럴 일치로 비교하며(대소문자·별칭 정규화 없음, GET 필터와 동일 원칙), 그룹처럼
    # 별도로 "부서를 만들고 소속을 관리"하지 않는다 — users.department 자체가 이미
    # 단일 진실 소스라 이중 관리를 만들지 않는다(2026-08-26, 옛 groups/user_groups/
    # group_reports를 대체).
    """CREATE TABLE IF NOT EXISTS department_report_access (
        department VARCHAR(60) NOT NULL,
        report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
        can_view BOOLEAN NOT NULL DEFAULT TRUE,
        granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (department, report_id)
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
    "CREATE INDEX IF NOT EXISTS user_reports_report_idx ON user_reports (report_id)",
    "CREATE INDEX IF NOT EXISTS department_report_access_report_idx ON department_report_access (report_id)",
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
    "CREATE INDEX IF NOT EXISTS event_log_popular_report_idx ON event_log (created_at DESC, report_id) "
        "WHERE log_type = 'activity' AND event = 'report_view' AND report_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS event_log_audit_actor_idx ON event_log (user_id) WHERE log_type = 'audit'",
    "CREATE INDEX IF NOT EXISTS event_log_audit_created_idx ON event_log (created_at) WHERE log_type = 'audit'",
    "CREATE INDEX IF NOT EXISTS event_log_audit_details_gin ON event_log USING gin (details) WHERE log_type = 'audit'",
]

# 여러 번 실행해도 사용자 권한과 데이터를 보존하는 추가 작업만 허용한다.
MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
    "CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique_idx ON users (LOWER(email)) WHERE email IS NOT NULL",
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS portal_folder_id INTEGER REFERENCES report_folders(id) ON DELETE SET NULL",
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS visibility VARCHAR(16) NOT NULL DEFAULT 'personal' CHECK (visibility IN ('personal','shared'))",
    "CREATE INDEX IF NOT EXISTS reports_portal_folder_idx ON reports(portal_folder_id) WHERE status = 'active'",
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_table VARCHAR(128)",
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_column VARCHAR(128)",
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
        conn.commit()
    print("schema init complete")


if __name__ == "__main__":
    init_schema()
