"""게이트웨이 DB 스키마 부트스트랩 + 마이그레이션.

서버 시작 전마다 실행된다 (server.sh, systemd ExecStartPre).
- 새 서버: 모든 테이블을 처음부터 생성한다 (사용자 계정만 수동 등록하면 됨).
- 기존 서버: 빠진 컬럼/인덱스를 추가하고 더 이상 쓰지 않는 컬럼을 제거한다.
모든 구문은 멱등(idempotent)이라 몇 번을 실행해도 안전하다.
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


def migrate():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # ── 1. 테이블 생성 (새 서버 부트스트랩) ──────────────────────────
            cur.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) NOT NULL UNIQUE,
                    password VARCHAR(255) NOT NULL,          -- bcrypt 해시
                    display_name VARCHAR(100) NOT NULL,
                    pbi_username VARCHAR(255) NOT NULL,      -- GenerateToken identity
                    roles TEXT NOT NULL DEFAULT '도메인',    -- RLS 역할 (콤마 구분)
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50) NOT NULL,
                    report_type VARCHAR(16) NOT NULL,        -- managed | personal
                    owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    deleted_at TIMESTAMPTZ,                  -- Fabric 삭제 감지 시각
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS report_meta (
                    report_id INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
                    pbi_report_id VARCHAR(50) NOT NULL,
                    pbi_workspace_id VARCHAR(36),
                    pbi_dataset_id VARCHAR(36),
                    fabric_folder_id VARCHAR(36),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS report_settings (
                    report_id INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
                    default_page VARCHAR(255),
                    enable_filter BOOLEAN NOT NULL DEFAULT FALSE,
                    enable_page_nav BOOLEAN NOT NULL DEFAULT FALSE,
                    use_data_bot BOOLEAN NOT NULL DEFAULT FALSE,
                    preview_image_url TEXT,
                    tab_type VARCHAR(32) NOT NULL DEFAULT 'report',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS report_rls (
                    report_id INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    role_names TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS user_reports (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    can_view BOOLEAN NOT NULL DEFAULT TRUE,
                    can_edit BOOLEAN NOT NULL DEFAULT FALSE,
                    can_manage BOOLEAN NOT NULL DEFAULT FALSE,
                    granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, report_id)
                )"""
            )
            cur.execute(
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
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS report_audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL,
                    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    action VARCHAR(64) NOT NULL,
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS login_attempts (
                    id BIGSERIAL PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    ip_address VARCHAR(64) NOT NULL,
                    succeeded BOOLEAN NOT NULL,
                    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            # 보고서 열람 이력 — 인기 보고서 파악, 사용자 활동 추적, 감사에 활용
            cur.execute(
                """CREATE TABLE IF NOT EXISTS report_views (
                    id BIGSERIAL PRIMARY KEY,
                    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    user_id   INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
                    viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )

            # ── 2. 기존 서버 보강 (컬럼이 없으면 추가) ───────────────────────
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("UPDATE users SET is_admin = TRUE WHERE username = 'admin'")
            # 계정 활성화 여부 — FALSE 시 로그인 차단 (계정 삭제 없이 비활성화)
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
            # 계정 생성·수정 시각
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
            # 마지막 로그인 시각 — 휴면 계정 탐지, 보안 감사용
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS pbi_workspace_id VARCHAR(36)")
            # 어떤 upload_job이 어떤 report를 만들었는지 추적
            cur.execute("ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL")

            # ── 3. 레거시 정리 (Sys_PbiReport 이관 잔재 — 사용 코드 없음) ────
            cur.execute("DROP INDEX IF EXISTS reports_source_key_uidx")
            for column in ("env_key", "origin_name", "owner_identity", "source_system",
                           "source_key", "is_global", "created_by_identity", "updated_by_identity"):
                cur.execute(f"ALTER TABLE reports DROP COLUMN IF EXISTS {column}")
            for column in ("owner_id", "pbi_app_id", "config", "custom_code"):
                cur.execute(f"ALTER TABLE report_meta DROP COLUMN IF EXISTS {column}")
            cur.execute("ALTER TABLE report_settings DROP COLUMN IF EXISTS settings")
            cur.execute("ALTER TABLE report_rls DROP COLUMN IF EXISTS identity_source")
            cur.execute("ALTER TABLE report_rls DROP COLUMN IF EXISTS config")
            cur.execute("ALTER TABLE report_audit_log DROP COLUMN IF EXISTS actor_identity")
            cur.execute("ALTER TABLE users DROP COLUMN IF EXISTS fabric_workspace_id")

            # ── 4. 제약과 인덱스 ─────────────────────────────────────────────
            cur.execute("ALTER TABLE reports DROP CONSTRAINT IF EXISTS reports_name_key")
            cur.execute("ALTER TABLE reports DROP CONSTRAINT IF EXISTS reports_status_check")
            cur.execute(
                """ALTER TABLE reports ADD CONSTRAINT reports_status_check
                   CHECK (status IN ('active', 'disabled', 'archived', 'deleted'))"""
            )
            # 관리 보고서는 이름이 전체에서 유일, 개인 보고서는 소유자 안에서 유일
            cur.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS reports_managed_name_uidx
                   ON reports (LOWER(name)) WHERE owner_id IS NULL"""
            )
            cur.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS reports_personal_owner_name_uidx
                   ON reports (owner_id, LOWER(name)) WHERE owner_id IS NOT NULL"""
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS report_meta_pbi_report_id_idx "
                "ON report_meta (pbi_report_id)"
            )
            # 진행 중인 같은 이름의 업로드는 사용자당 1건만 허용.
            # 'completed'는 제외 — 완료 후 Fabric에서 삭제했다가 같은 이름으로
            # 다시 올리는 흐름을 막지 않는다 (살아있는 중복은 reports 인덱스가 막음).
            cur.execute("DROP INDEX IF EXISTS upload_jobs_active_name_uidx")
            cur.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS upload_jobs_inflight_name_uidx
                   ON upload_jobs (user_id, LOWER(report_name))
                   WHERE status IN ('publishing', 'accepted', 'unknown', 'fabric_succeeded', 'db_failed')"""
            )
            cur.execute("CREATE INDEX IF NOT EXISTS user_reports_report_idx ON user_reports (report_id)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS reports_owner_idx ON reports (owner_id) WHERE owner_id IS NOT NULL"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS upload_jobs_user_day_idx ON upload_jobs (user_id, created_at)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS login_attempts_lookup_idx "
                "ON login_attempts (username, ip_address, attempted_at)"
            )
            # 홈 화면 보고서 목록 — status='active' 필터가 가장 빈번한 쿼리
            cur.execute("CREATE INDEX IF NOT EXISTS reports_status_idx ON reports (status)")
            # 서버 시작 복구: status로 진행 중 작업을 찾는 쿼리
            cur.execute("CREATE INDEX IF NOT EXISTS upload_jobs_status_idx ON upload_jobs (status)")
            # upload_job → report 역방향 조회 (어떤 작업이 이 보고서를 만들었나)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS upload_jobs_report_idx "
                "ON upload_jobs (report_id) WHERE report_id IS NOT NULL"
            )
            # 관리자 "누가 어떤 보고서 열었나" 감사 쿼리
            cur.execute(
                "CREATE INDEX IF NOT EXISTS report_audit_log_actor_idx ON report_audit_log (actor_user_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS report_audit_log_created_idx ON report_audit_log (created_at)"
            )
            # 인기 보고서 / 사용자 열람 이력 조회
            cur.execute(
                "CREATE INDEX IF NOT EXISTS report_views_report_idx ON report_views (report_id, viewed_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS report_views_user_idx ON report_views (user_id, viewed_at)"
            )

            # ── 5. 시퀀스 복구 (초기 데이터를 명시적 ID로 넣은 DB 대비) ──────
            cur.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('reports', 'id'),
                    GREATEST((SELECT COALESCE(MAX(id), 0) FROM reports), 1),
                    (SELECT COUNT(*) > 0 FROM reports)
                )
                """
            )
    print("database migration complete")


if __name__ == "__main__":
    migrate()
