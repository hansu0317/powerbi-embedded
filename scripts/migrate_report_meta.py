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
                    report_id        INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
                    pbi_report_id    VARCHAR(50)  NOT NULL,   -- Power BI 보고서 ID
                    pbi_workspace_id VARCHAR(36),             -- Power BI 워크스페이스 ID
                    pbi_dataset_id   VARCHAR(36),             -- Power BI 데이터셋 ID
                    pbi_display_name VARCHAR(105),            -- 워크스페이스 표시 이름 (username__name)
                    folder_id        VARCHAR(36),             -- [Fabric only] 사용자 폴더 ID, Pro 모드는 NULL
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
            # 런타임 설정 — 코드 재배포 없이 관리자가 관리자 포털 또는 SQL로 변경 가능
            cur.execute(
                """CREATE TABLE IF NOT EXISTS app_config (
                    key         VARCHAR(64) PRIMARY KEY,
                    value       TEXT        NOT NULL,
                    description TEXT,
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            # 사용자별 즐겨찾기 (기기 간 유지를 위해 DB에 저장)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS user_favorites (
                    user_id    INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
                    report_id  INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, report_id)
                )"""
            )
            # 사용자별 최근 본 보고서 (viewed_at 기준 최신순, 조회 시 LIMIT)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS user_recent_reports (
                    user_id    INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
                    report_id  INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                    viewed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, report_id)
                )"""
            )

            # ── 2. 기존 서버 보강 (컬럼이 없으면 추가) ───────────────────────
            # 기존 개인 보고서 중 category가 NULL인 것에 소유자 username을 소급 적용한다.
            # UI가 category 기준 폴더 트리로 변경되면서 개인 보고서도 Fabric 폴더명(username)이
            # category가 되어야 사이드바에 올바른 폴더로 표시된다.
            cur.execute(
                """UPDATE reports r
                   SET category = u.username
                   FROM users u
                   WHERE r.owner_id = u.id
                     AND r.report_type = 'personal'
                     AND r.category IS NULL"""
            )
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("UPDATE users SET is_admin = TRUE WHERE username = 'admin'")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS category VARCHAR(50)")
            cur.execute("ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS pbi_workspace_id VARCHAR(36)")
            cur.execute("ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL")

            # report_meta: 컬럼 이름 정규화 (Fabric 전용 명칭 → 공통 명칭, 멱등)
            cur.execute(
                """DO $$
                   BEGIN
                     IF EXISTS (SELECT 1 FROM information_schema.columns
                                WHERE table_name='report_meta' AND column_name='fabric_folder_id') THEN
                       ALTER TABLE report_meta RENAME COLUMN fabric_folder_id TO folder_id;
                     END IF;
                     IF EXISTS (SELECT 1 FROM information_schema.columns
                                WHERE table_name='report_meta' AND column_name='fabric_display_name') THEN
                       ALTER TABLE report_meta RENAME COLUMN fabric_display_name TO pbi_display_name;
                     END IF;
                   END $$"""
            )
            cur.execute("ALTER TABLE report_meta ADD COLUMN IF NOT EXISTS pbi_display_name VARCHAR(105)")
            cur.execute("ALTER TABLE report_meta ADD COLUMN IF NOT EXISTS folder_id VARCHAR(36)")
            # 불필요 컬럼 제거 (로컬 디스크 저장 방식 폐기)
            cur.execute("ALTER TABLE report_meta DROP COLUMN IF EXISTS local_file_path")

            # app_config 키 이름 정규화 (fabric_ → pbi_, 기존 값 이관 후 구키 삭제)
            cur.execute(
                """INSERT INTO app_config (key, value, description)
                   SELECT 'pbi_sync_interval', value, description
                   FROM app_config WHERE key = 'fabric_sync_interval'
                   ON CONFLICT (key) DO NOTHING"""
            )
            cur.execute("DELETE FROM app_config WHERE key = 'fabric_sync_interval'")

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
            # 열람 통계 기능 제거 — report_views 테이블 폐기 (조회수가 새로고침/관리자 자체조회로 부풀려져 신뢰도 낮음)
            cur.execute("DROP TABLE IF EXISTS report_views")

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
            # upload_jobs: fabric_succeeded → pbi_succeeded 상태 이관
            cur.execute("UPDATE upload_jobs SET status='pbi_succeeded' WHERE status='fabric_succeeded'")

            cur.execute("DROP INDEX IF EXISTS upload_jobs_active_name_uidx")
            cur.execute("DROP INDEX IF EXISTS upload_jobs_inflight_name_uidx")
            cur.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS upload_jobs_inflight_name_uidx
                   ON upload_jobs (user_id, LOWER(report_name))
                   WHERE status IN ('publishing', 'accepted', 'unknown', 'pbi_succeeded', 'db_failed')"""
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
            # 보고서 목록 ORDER BY category NULLS LAST, name 쿼리 대응 복합 인덱스.
            # category가 NULL인 보고서(카테고리 미지정)는 항상 끝에 오도록 NULLS LAST 포함.
            # WHERE status='active' 부분 인덱스로 deleted·disabled 행을 스캔에서 제외한다.
            cur.execute(
                "CREATE INDEX IF NOT EXISTS reports_category_name_idx "
                "ON reports (category NULLS LAST, name) WHERE status = 'active'"
            )
            # 서버 시작 복구: status로 진행 중 작업을 찾는 쿼리
            cur.execute("CREATE INDEX IF NOT EXISTS upload_jobs_status_idx ON upload_jobs (status)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS user_recent_viewed_idx "
                "ON user_recent_reports (user_id, viewed_at DESC)"
            )
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
            # audit_log.details JSONB 검색용 GIN 인덱스.
            # details @> '{"category":"제조"}' 같은 쿼리가 풀스캔 없이 동작하도록.
            cur.execute(
                "CREATE INDEX IF NOT EXISTS report_audit_log_details_gin "
                "ON report_audit_log USING gin (details)"
            )
            # ── 5. app_config 기본값 씨드 (없는 키만 INSERT) ─────────────────
            # use_fabric 키 제거 (Pro 전용으로 고정)
            cur.execute("DELETE FROM app_config WHERE key = 'use_fabric'")

            defaults = [
                ("max_pbix_size_mb",         "1024", "업로드 허용 최대 파일 크기 (MB). Power BI Import API 한도 1 GB."),
                ("max_uploads_per_day",       "10",  "사용자당 하루 업로드 최대 횟수."),
                ("max_personal_reports",      "20",  "사용자당 개인 보고서 최대 등록 수."),
                ("report_name_max_len",       "50",  "보고서 이름 최대 글자 수."),
                ("password_min_len",           "8",  "사용자 비밀번호 최소 글자 수."),
                ("pbi_sync_interval",        "600",  "PBI 삭제 동기화 주기 (초). 0 이면 자동 동기화 비활성."),
                ("login_block_max_fail",       "5",  "로그인 실패 N회 초과 시 IP+계정 차단."),
                ("login_block_minutes",       "15",  "로그인 차단 유지 시간 (분)."),
                ("import_poll_max",          "100",  "게시 완료 대기 최대 횟수 (import_poll_interval_sec 간격)."),
                ("import_poll_interval_sec",   "3",  "게시 상태 조회 간격 (초). 기본 3초 × 100회 = 최대 5분 대기."),
                ("embed_token_lifetime_min",  "60",  "임베드 토큰 유효 시간 (분). Power BI 기본값 60분."),
                ("pbi_token_cache_margin_sec","300", "Azure AD 토큰 만료 N초 전에 갱신. 기본 5분."),
                ("max_embed_rls_roles",       "10",  "GenerateToken 시 identity에 담을 RLS 역할 최대 개수."),
            ]
            for key, value, desc in defaults:
                cur.execute(
                    """INSERT INTO app_config (key, value, description)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (key) DO NOTHING""",
                    (key, value, desc),
                )

            # ── 6. 시퀀스 복구 (초기 데이터를 명시적 ID로 넣은 DB 대비) ──────
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
