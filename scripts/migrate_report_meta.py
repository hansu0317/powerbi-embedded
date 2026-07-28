"""게이트웨이 DB 스키마 버전 마이그레이션.

서버 시작 전마다 실행된다 (server.sh, systemd ExecStartPre).

버전 규칙:
- v1 = 2026-07 기준선(baseline). 새 DB는 이것으로 전체 생성되고,
  그 이전부터 쓰던 DB도 전 구문이 멱등이라 안전하게 통과한다.
- v2부터는 MIGRATIONS 목록에 (버전, 함수)를 추가한다.
- **한 번 적용(기록)된 버전 함수는 수정 금지** — 고칠 게 있으면 다음 버전을 추가한다.
  (적용 이력과 코드가 어긋나면 DB마다 상태가 달라진다)
- 다운그레이드는 지원하지 않는다. 목표 버전 < 현재 버전이면 에러로 중단한다.

실행 방법:
  python scripts/migrate_report_meta.py              # 최신 버전까지 적용
  python scripts/migrate_report_meta.py --target 1   # v1까지만 적용
  bash scripts/server.sh start v1                    # 서버 기동 시 버전 지정
  DB_TARGET_VERSION=1 bash scripts/server.sh start   # 환경변수로도 가능

적용 이력은 schema_migrations 테이블에 기록된다 (버전당 1행, 트랜잭션 1개).
"""

import argparse
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


# ═══════════════════════════════════════════════════════════════════════════
# v1 — 기준선 (2026-07): 테이블 12개 + 제약·인덱스 + app_config 시드
# ═══════════════════════════════════════════════════════════════════════════

def _v1_baseline(cur):
    # ── 1. 테이블 생성 (새 서버 부트스트랩) ──────────────────────────
    cur.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            password VARCHAR(255) NOT NULL,          -- bcrypt 해시
            display_name VARCHAR(100) NOT NULL,
            pbi_username VARCHAR(255) NOT NULL,      -- GenerateToken identity
            roles TEXT[] NOT NULL DEFAULT ARRAY['도메인'],  -- RLS 역할 (report_rls.role_names와 동일 형식)
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
    # 사용자별 최근 본 보고서 (viewed_at 기준 최신순, 기록 시 최신 30건만 유지)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS user_recent_reports (
            user_id    INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
            report_id  INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            viewed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, report_id)
        )"""
    )

    # ── 2. 기존 서버 보강 (v1 이전 DB 호환 — 컬럼이 없으면 추가) ─────
    # 아래 백필이 category를 참조하므로, 컬럼을 먼저 보강한다(신규 DB에서 컬럼 부재 방지).
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS category VARCHAR(255)")
    # category는 Fabric 폴더 전체 경로("본부/팀")를 저장하므로 50자에서 확장한다.
    cur.execute("ALTER TABLE reports ALTER COLUMN category TYPE VARCHAR(255)")
    # 기존 개인 보고서 중 category가 NULL인 것에 소유자 username을 소급 적용한다.
    cur.execute(
        """UPDATE reports r
           SET category = u.username
           FROM users u
           WHERE r.owner_id = u.id
             AND r.report_type = 'personal'
             AND r.category IS NULL"""
    )
    # users.roles: 콤마 구분 TEXT → TEXT[] (report_rls.role_names와 형식 통일, 1NF)
    # 공백 정리 후 배열로 변환, 빈 요소 제거. 이미 배열이면 건너뜀(멱등).
    cur.execute(
        r"""DO $$
           BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name='users' AND column_name='roles'
                          AND data_type='text') THEN
               ALTER TABLE users ALTER COLUMN roles DROP DEFAULT;
               ALTER TABLE users ALTER COLUMN roles TYPE TEXT[]
                 USING array_remove(string_to_array(regexp_replace(roles, '\s*,\s*', ',', 'g'), ','), '');
               ALTER TABLE users ALTER COLUMN roles SET DEFAULT ARRAY['도메인'];
             END IF;
           END $$"""
    )
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("UPDATE users SET is_admin = TRUE WHERE username = 'admin'")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
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
    # report_type도 status처럼 CHECK로 오타·잘못된 값 유입을 막는다
    cur.execute("ALTER TABLE reports DROP CONSTRAINT IF EXISTS reports_type_check")
    cur.execute(
        """ALTER TABLE reports ADD CONSTRAINT reports_type_check
           CHECK (report_type IN ('managed', 'personal'))"""
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


# ═══════════════════════════════════════════════════════════════════════════
# v2 — 그룹 단위 열람 권한 (2026-07)
# ═══════════════════════════════════════════════════════════════════════════

def _v2_groups(cur):
    """팀/부서 단위 권한 부여: groups + user_groups(멤버) + group_reports(그룹×보고서).

    사용자의 열람 가능 = user_reports(직접 부여) OR 소속 그룹의 group_reports.
    entra_group_id는 추후 Entra(AD) 보안 그룹 동기화용 예약 컬럼."""
    cur.execute(
        """CREATE TABLE groups (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) NOT NULL UNIQUE,
            description TEXT,
            entra_group_id VARCHAR(36),
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""
    )
    cur.execute(
        """CREATE TABLE user_groups (
            user_id  INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, group_id)
        )"""
    )
    cur.execute(
        """CREATE TABLE group_reports (
            group_id  INTEGER NOT NULL REFERENCES groups(id)  ON DELETE CASCADE,
            report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            can_view BOOLEAN NOT NULL DEFAULT TRUE,
            granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (group_id, report_id)
        )"""
    )
    # 역방향 조회용: 그룹의 멤버 목록 / 보고서에 부여된 그룹 목록
    cur.execute("CREATE INDEX user_groups_group_idx ON user_groups (group_id)")
    cur.execute("CREATE INDEX group_reports_report_idx ON group_reports (report_id)")


# ═══════════════════════════════════════════════════════════════════════════
# v3 — 사용자 활동 로그 + 편의 기능 (2026-07)
# ═══════════════════════════════════════════════════════════════════════════

def _v3_activity_and_convenience(cur):
    """사용자 활동 로그(activity_log) + 기본 보고서 + 업로드 권한 + 보고서 설명.

    - activity_log: 사용자 행위 기록 (report_view 등). 인기 보고서 집계·관리자 로그
      화면의 데이터 원천. 관리자 '행위' 감사는 기존 report_audit_log가 계속 담당한다.
    - users.default_report_id: 뷰어 진입 시 자동으로 여는 보고서. 보고서가 삭제되면
      SET NULL로 조용히 해제된다 (열람 오류 방지).
    - users.can_upload: 업로드 권한 분리. 기존 사용자는 TRUE로 동작 유지.
    - reports.description: 보고서 설명 (검색 대상).
    - RLS 파이프라인(report_rls, users.pbi_username/roles)은 건드리지 않는다 —
      추후 동적 RLS(v4+)와 충돌 지점 없음.
    """
    cur.execute(
        """CREATE TABLE activity_log (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            username VARCHAR(50) NOT NULL,           -- 사용자 삭제 후에도 로그 판독 가능하도록 보존
            event VARCHAR(32) NOT NULL
                CHECK (event IN ('report_view', 'report_upload')),
            report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL,
            report_name VARCHAR(105),                -- 보고서 삭제 후에도 로그 판독 가능하도록 보존
            ip VARCHAR(45),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""
    )
    # 로그 화면 필터(기간·사용자·이벤트) + 인기 집계(report_id×기간)용 인덱스
    cur.execute("CREATE INDEX activity_log_created_idx ON activity_log (created_at DESC)")
    cur.execute("CREATE INDEX activity_log_user_idx ON activity_log (user_id, created_at DESC)")
    cur.execute("CREATE INDEX activity_log_report_idx ON activity_log (report_id, created_at DESC)")

    cur.execute(
        "ALTER TABLE users ADD COLUMN default_report_id INTEGER "
        "REFERENCES reports(id) ON DELETE SET NULL"
    )
    cur.execute("ALTER TABLE users ADD COLUMN can_upload BOOLEAN NOT NULL DEFAULT TRUE")
    cur.execute("ALTER TABLE reports ADD COLUMN description TEXT")

    # 로그 보존 기간 (일). 초과분은 일 1회 백그라운드에서 삭제된다.
    cur.execute(
        """INSERT INTO app_config (key, value, description)
           VALUES ('activity_log_retention_days', '90',
                   '사용자 활동 로그 보존 기간 (일). 초과분은 매일 자동 삭제.')
           ON CONFLICT (key) DO NOTHING"""
    )


# ═══════════════════════════════════════════════════════════════════════════
# 버전 레지스트리 — 새 스키마 변경은 여기에 (버전, 함수)로 추가한다
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# v4 — 데이터 신선도 관제 (2026-07)
# ═══════════════════════════════════════════════════════════════════════════

def _v4_dataset_freshness(cur):
    """데이터셋 새로고침(refresh) 상태 추적 테이블.

    PBI refresh는 실패해도 아무에게도 알리지 않는다 — 10분 동기화 루프가
    refresh 이력을 수집해 여기 저장하고, 뷰어는 '데이터 기준 시각' 배지를,
    관리자는 실패 현황을 본다. 실패 시 하루 한도 내에서 자동 재시도한다.
    (RLS 실전 적용은 스키마 변경 불필요 — v1의 report_rls·users.roles를 그대로 쓴다)
    """
    cur.execute(
        """CREATE TABLE dataset_refresh_status (
            pbi_dataset_id   VARCHAR(36) PRIMARY KEY,
            pbi_workspace_id VARCHAR(36),
            last_status      VARCHAR(24),        -- Completed|Failed|Unknown|Disabled|NotRefreshable
            last_success_at  TIMESTAMPTZ,        -- 뷰어 '데이터 기준' 배지의 원천
            last_attempt_at  TIMESTAMPTZ,
            failure_reason   TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            auto_retries_today   INTEGER NOT NULL DEFAULT 0,
            retry_date       DATE,               -- auto_retries_today의 기준 날짜
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""
    )
    cur.execute(
        """INSERT INTO app_config (key, value, description)
           VALUES ('refresh_auto_retry_max', '2',
                   'refresh 실패 시 데이터셋당 하루 자동 재시도 최대 횟수. 0=끔.')
           ON CONFLICT (key) DO NOTHING"""
    )


# ═══════════════════════════════════════════════════════════════════════════
# v5 — 서버 오류 추적 (2026-07)
# ═══════════════════════════════════════════════════════════════════════════

def _v5_error_log(cur):
    """5xx(서버·외부 서비스) 오류를 자동 기록하는 error_log.

    사용자 입력 실수(400/403/404/409 등)는 정상 흐름의 일부라 기록 대상이 아니다.
    Azure/PBI 장애·DB 장애·예상 밖 예외처럼 '관리자가 몰라서는 안 되는' 실패만
    main.py의 전역 예외 핸들러가 자동으로 여기 남긴다 (라우트별 코드 수정 불필요).
    error_code는 errors.py AppError.code와 매칭되거나, 처리 못한 예외는 'UNHANDLED'.
    """
    cur.execute(
        """CREATE TABLE error_log (
            id BIGSERIAL PRIMARY KEY,
            error_code VARCHAR(32) NOT NULL,
            http_status SMALLINT NOT NULL,
            message TEXT,
            username VARCHAR(50),           -- 세션에서 바로 읽음(추가 DB 조회 없음), 비로그인 요청은 NULL
            path VARCHAR(255),
            detail TEXT,                    -- 예외 상세(traceback 요약) — 관리자 진단용
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""
    )
    cur.execute("CREATE INDEX error_log_created_idx ON error_log (created_at DESC)")
    cur.execute("CREATE INDEX error_log_code_idx ON error_log (error_code, created_at DESC)")

    cur.execute(
        """INSERT INTO app_config (key, value, description)
           VALUES ('error_log_retention_days', '90',
                   '서버 오류 로그 보존 기간 (일). 초과분은 매일 자동 삭제.')
           ON CONFLICT (key) DO NOTHING"""
    )


# ═══════════════════════════════════════════════════════════════════════════
# v6 — Power BI 대시보드 임베딩 지원 (2026-07)
# ═══════════════════════════════════════════════════════════════════════════

def _v6_dashboard_support(cur):
    """Report 타입 외에 PBI Dashboard(여러 타일을 모은 것) 임베딩을 지원한다.

    새 테이블 없이 기존 reports/report_meta/report_settings/user_reports/
    group_reports를 그대로 재사용한다 — 대시보드도 'reports 행 하나'로 취급하고
    report_type='dashboard'로 구분한다(권한·그룹·즐겨찾기·활동로그가 전부 그대로 동작).
    report_settings.tab_type='dashboard'로 뷰어가 임베드 방식(type: dashboard)을 분기한다.
    대시보드는 페이지·필터창 개념이 없어 enable_filter/enable_page_nav/default_page는
    쓰지 않는다. pbi_dataset_id는 대시보드 자체엔 없어 NULL로 둔다.
    """
    cur.execute("ALTER TABLE reports DROP CONSTRAINT IF EXISTS reports_type_check")
    cur.execute(
        """ALTER TABLE reports ADD CONSTRAINT reports_type_check
           CHECK (report_type IN ('managed', 'personal', 'dashboard'))"""
    )


# ═══════════════════════════════════════════════════════════════════════════
# v7 — 보고서 콘텐츠 업데이트 (2026-07)
# ═══════════════════════════════════════════════════════════════════════════

def _v7_report_content_update(cur):
    """기존 보고서에 새 pbix를 올려 콘텐츠(페이지·시각화)만 교체하는 기능.

    Power BI의 UpdateReportContent API를 쓴다 — 대상 보고서의 페이지만 바뀌고
    데이터셋 바인딩(RLS·관계·DAX)은 그대로 유지된다. upload_jobs를 재사용하되
    job_type으로 신규 생성(create)과 업데이트(update)를 구분한다.
    권한은 보고서 소유자 또는 admin으로 한정 — can_view(열람 권한)와는 완전히
    별개 체크(routes/report.py에서 owner_id 비교로 처리, 이 마이그레이션은 스키마만).
    """
    cur.execute("ALTER TABLE upload_jobs ADD COLUMN job_type VARCHAR(16) NOT NULL DEFAULT 'create'")
    cur.execute(
        "ALTER TABLE upload_jobs ADD COLUMN target_report_id INTEGER "
        "REFERENCES reports(id) ON DELETE SET NULL"
    )
    cur.execute(
        """ALTER TABLE upload_jobs ADD CONSTRAINT upload_jobs_type_check
           CHECK (job_type IN ('create', 'update'))"""
    )


def _v8_config_desc_fix(cur):
    """embed_token_lifetime_min 설명 정정.

    실제 임베드 토큰 만료 시각은 항상 Power BI GenerateToken 응답의 expiration을
    그대로 쓴다(services/powerbi.py의 _parse_token_expiry) — 이 설정값은 그 응답
    파싱이 실패했을 때만 쓰이는 예비값이라, "Power BI 기본값 60분"이라는 기존
    설명이 마치 이 값을 바꾸면 실제 토큰 수명이 바뀌는 것처럼 오해하게 만든다.
    """
    cur.execute(
        """UPDATE app_config SET description = %s, updated_at = NOW()
           WHERE key = 'embed_token_lifetime_min'""",
        ("PBI 응답의 만료 시각 파싱에 실패했을 때만 쓰이는 예비값(분) — "
         "정상 동작 시 실제 토큰 수명은 항상 Power BI 응답값을 그대로 따른다.",),
    )


def _v9_drop_dead_permission_columns(cur):
    """user_reports.can_edit / can_manage 제거 — 죽은 컬럼.

    등록 시 TRUE로 채워지기만 하고(db_register_report) 코드 어디서도 읽지 않았다.
    실제 편집 권한(v7 콘텐츠 업데이트)은 이 컬럼과 무관하게 owner_id·is_admin을
    직접 비교해서 판정한다 — 사용자가 위임 가능한 편집 권한 UI를 만들지 않기로
    결정했을 때(권한 체크만 분리, can_edit 권한 부여 UI는 안 만들기로 함) 이미
    쓸모가 없어진 컬럼이었다.
    """
    cur.execute("ALTER TABLE user_reports DROP COLUMN IF EXISTS can_edit")
    cur.execute("ALTER TABLE user_reports DROP COLUMN IF EXISTS can_manage")


def _v10_consolidate_schema(cur):
    """19개 테이블 → 11개로 통합. 기능 변화 없음, 구조만 정리.

    A) report_meta·report_settings·report_rls·dataset_refresh_status를
       reports 컬럼으로 흡수. 넷 다 보고서 1개당 정확히 1행이라(dataset_refresh_status는
       report_meta.pbi_dataset_id 기준 1:1 확인됨 — db_get_freshness_targets가
       report_meta JOIN으로만 대상을 뽑았다) 굳이 별도 테이블일 이유가 없었다.
    B) activity_log·report_audit_log·error_log·login_attempts를 event_log로 통합.
       넷 다 "누가·언제·무슨 일" 모양이 같아 log_type 구분 컬럼 + 타입별 컬럼으로 흡수.
       보존기간이 다른 건(활동 90일 vs 오류 90일 vs 로그인 30일 vs 감사 영구) 기존
       app_config 키를 그대로 두고 WHERE log_type=... 조건으로 구현한다.
    C) user_favorites·user_recent_reports를 user_report_marks로 통합.
       둘 다 "이 사용자와 이 보고서의 관계" 같은 개념이라 is_favorite/viewed_at
       두 컬럼으로 한 행에 담긴다.
    """
    # ── A: report_meta / report_settings / report_rls / dataset_refresh_status → reports ──
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS pbi_report_id VARCHAR(50)")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS pbi_workspace_id VARCHAR(36)")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS pbi_dataset_id VARCHAR(36)")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS pbi_display_name VARCHAR(105)")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS folder_id VARCHAR(36)")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS default_page VARCHAR(255)")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS enable_filter BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS enable_page_nav BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS use_data_bot BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS preview_image_url TEXT")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS tab_type VARCHAR(32) NOT NULL DEFAULT 'report'")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS rls_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS rls_role_names TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_last_status VARCHAR(24)")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_last_success_at TIMESTAMPTZ")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_last_attempt_at TIMESTAMPTZ")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_failure_reason TEXT")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_consecutive_failures INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_auto_retries_today INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_retry_date DATE")

    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='report_meta') THEN
               UPDATE reports r SET
                   pbi_report_id = m.pbi_report_id, pbi_workspace_id = m.pbi_workspace_id,
                   pbi_dataset_id = m.pbi_dataset_id, pbi_display_name = m.pbi_display_name,
                   folder_id = m.folder_id
               FROM report_meta m WHERE m.report_id = r.id;
             END IF;
           END $$"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='report_settings') THEN
               UPDATE reports r SET
                   default_page = s.default_page, enable_filter = s.enable_filter,
                   enable_page_nav = s.enable_page_nav, use_data_bot = s.use_data_bot,
                   preview_image_url = s.preview_image_url, tab_type = s.tab_type
               FROM report_settings s WHERE s.report_id = r.id;
             END IF;
           END $$"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='report_rls') THEN
               UPDATE reports r SET rls_enabled = rr.enabled, rls_role_names = rr.role_names
               FROM report_rls rr WHERE rr.report_id = r.id;
             END IF;
           END $$"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='dataset_refresh_status') THEN
               UPDATE reports r SET
                   refresh_last_status = d.last_status, refresh_last_success_at = d.last_success_at,
                   refresh_last_attempt_at = d.last_attempt_at, refresh_failure_reason = d.failure_reason,
                   refresh_consecutive_failures = d.consecutive_failures,
                   refresh_auto_retries_today = d.auto_retries_today, refresh_retry_date = d.retry_date
               FROM dataset_refresh_status d WHERE d.pbi_dataset_id = r.pbi_dataset_id;
             END IF;
           END $$"""
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS reports_pbi_report_id_idx "
        "ON reports (pbi_report_id) WHERE pbi_report_id IS NOT NULL"
    )
    cur.execute("DROP TABLE IF EXISTS report_meta")
    cur.execute("DROP TABLE IF EXISTS report_settings")
    cur.execute("DROP TABLE IF EXISTS report_rls")
    cur.execute("DROP TABLE IF EXISTS dataset_refresh_status")

    # ── B: activity_log / report_audit_log / error_log / login_attempts → event_log ──
    cur.execute(
        """CREATE TABLE IF NOT EXISTS event_log (
            id BIGSERIAL PRIMARY KEY,
            log_type VARCHAR(16) NOT NULL CHECK (log_type IN ('activity', 'audit', 'error', 'login')),
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
        )"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='activity_log') THEN
               INSERT INTO event_log (log_type, user_id, username, report_id, report_name, event, ip, created_at)
               SELECT 'activity', user_id, username, report_id, report_name, event, ip, created_at FROM activity_log;
             END IF;
           END $$"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='report_audit_log') THEN
               INSERT INTO event_log (log_type, user_id, report_id, event, details, created_at)
               SELECT 'audit', actor_user_id, report_id, action, details, created_at FROM report_audit_log;
             END IF;
           END $$"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='error_log') THEN
               INSERT INTO event_log (log_type, event, http_status, message, username, path, detail, created_at)
               SELECT 'error', error_code, http_status, message, username, path, detail, created_at FROM error_log;
             END IF;
           END $$"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='login_attempts') THEN
               INSERT INTO event_log (log_type, username, ip, succeeded, created_at)
               SELECT 'login', username, ip_address, succeeded, attempted_at FROM login_attempts;
             END IF;
           END $$"""
    )
    cur.execute("DROP TABLE IF EXISTS activity_log")
    cur.execute("DROP TABLE IF EXISTS report_audit_log")
    cur.execute("DROP TABLE IF EXISTS error_log")
    cur.execute("DROP TABLE IF EXISTS login_attempts")

    cur.execute("CREATE INDEX IF NOT EXISTS event_log_login_lookup_idx ON event_log (username, ip, created_at) WHERE log_type='login'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_activity_created_idx ON event_log (created_at DESC) WHERE log_type='activity'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_activity_user_idx ON event_log (user_id, created_at DESC) WHERE log_type='activity'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_activity_report_idx ON event_log (report_id, created_at DESC) WHERE log_type='activity'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_audit_actor_idx ON event_log (user_id) WHERE log_type='audit'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_audit_created_idx ON event_log (created_at) WHERE log_type='audit'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_audit_details_gin ON event_log USING gin (details) WHERE log_type='audit'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_error_created_idx ON event_log (created_at DESC) WHERE log_type='error'")
    cur.execute("CREATE INDEX IF NOT EXISTS event_log_error_code_idx ON event_log (event, created_at DESC) WHERE log_type='error'")

    # ── C: user_favorites / user_recent_reports → user_report_marks ──
    cur.execute(
        """CREATE TABLE IF NOT EXISTS user_report_marks (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
            favorited_at TIMESTAMPTZ,
            viewed_at TIMESTAMPTZ,
            PRIMARY KEY (user_id, report_id)
        )"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='user_favorites') THEN
               INSERT INTO user_report_marks (user_id, report_id, is_favorite, favorited_at)
               SELECT user_id, report_id, TRUE, created_at FROM user_favorites
               ON CONFLICT (user_id, report_id) DO UPDATE
               SET is_favorite = TRUE, favorited_at = EXCLUDED.favorited_at;
             END IF;
           END $$"""
    )
    cur.execute(
        """DO $$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='user_recent_reports') THEN
               INSERT INTO user_report_marks (user_id, report_id, viewed_at)
               SELECT user_id, report_id, viewed_at FROM user_recent_reports
               ON CONFLICT (user_id, report_id) DO UPDATE SET viewed_at = EXCLUDED.viewed_at;
             END IF;
           END $$"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS user_report_marks_recent_idx "
        "ON user_report_marks (user_id, viewed_at DESC) WHERE viewed_at IS NOT NULL"
    )
    cur.execute("DROP TABLE IF EXISTS user_favorites")
    cur.execute("DROP TABLE IF EXISTS user_recent_reports")


def _v11_drop_removed_feature_columns(cur):
    """제거된 기능들의 컬럼·설정 정리.

    관리 화면에서 RLS 설정·신선도 관제·서버 오류 목록·기본 보고서 기능을 걷어내면서
    그 데이터를 담던 컬럼도 함께 정리한다. RLS 자체가 사라진 것은 아니다 —
    데이터셋에 역할이 정의돼 있으면 Power BI가 identity를 강제하므로(없으면 400),
    임베드 시 users.roles를 그대로 실어 보내는 최소 경로는 코드에 남아 있다.
    """
    # RLS 설정(보고서별 on/off·역할 지정) — users.roles만으로 충분해짐
    cur.execute("ALTER TABLE reports DROP COLUMN IF EXISTS rls_enabled")
    cur.execute("ALTER TABLE reports DROP COLUMN IF EXISTS rls_role_names")

    # 신선도 관제 — Fabric에서 직접 확인 가능해 중복이었다
    for col in ("refresh_last_status", "refresh_last_success_at", "refresh_last_attempt_at",
                "refresh_failure_reason", "refresh_consecutive_failures",
                "refresh_auto_retries_today", "refresh_retry_date"):
        cur.execute(f"ALTER TABLE reports DROP COLUMN IF EXISTS {col}")

    # 기본 보고서(접속 시 자동 열기)
    cur.execute("ALTER TABLE users DROP COLUMN IF EXISTS default_report_id")

    # 서버 오류 로그 — 파일 로그(logs/server.log)로 일원화
    cur.execute("DELETE FROM event_log WHERE log_type = 'error'")
    cur.execute("ALTER TABLE event_log DROP CONSTRAINT IF EXISTS event_log_log_type_check")
    cur.execute(
        """ALTER TABLE event_log ADD CONSTRAINT event_log_log_type_check
           CHECK (log_type IN ('activity', 'audit', 'login'))"""
    )
    cur.execute("DROP INDEX IF EXISTS event_log_error_created_idx")
    cur.execute("DROP INDEX IF EXISTS event_log_error_code_idx")

    # 쓰이지 않게 된 런타임 설정
    cur.execute(
        "DELETE FROM app_config WHERE key IN "
        "('refresh_auto_retry_max', 'error_log_retention_days', 'max_embed_rls_roles')"
    )


MIGRATIONS = [
    (1, _v1_baseline),
    (2, _v2_groups),
    (3, _v3_activity_and_convenience),
    (4, _v4_dataset_freshness),
    (5, _v5_error_log),
    (6, _v6_dashboard_support),
    (7, _v7_report_content_update),
    (8, _v8_config_desc_fix),
    (9, _v9_drop_dead_permission_columns),
    (10, _v10_consolidate_schema),
    (11, _v11_drop_removed_feature_columns),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def _get_current_version(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )"""
        )
        cur.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations")
        version = cur.fetchone()[0]
    conn.commit()
    return version


def migrate(target: int | None = None):
    target = LATEST_VERSION if target is None else target
    if target > LATEST_VERSION:
        raise SystemExit(f"목표 버전 v{target}이 없습니다. 최신은 v{LATEST_VERSION}입니다.")

    with psycopg2.connect(**DB_CONFIG) as conn:
        current = _get_current_version(conn)
        if target < current:
            raise SystemExit(
                f"다운그레이드는 지원하지 않습니다 (현재 v{current} → 목표 v{target}). "
                "백업 복원으로만 되돌릴 수 있습니다."
            )
        applied = []
        for version, fn in MIGRATIONS:
            if version <= current or version > target:
                continue
            # 버전당 트랜잭션 1개 — 중간 실패 시 그 버전 전체가 롤백된다
            with conn.cursor() as cur:
                fn(cur)
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
            applied.append(version)
            print(f"v{version} 적용 완료")

    if applied:
        print(f"database migration complete (v{current} → v{applied[-1]})")
    else:
        print(f"database migration complete (v{current}, 변경 없음)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="게이트웨이 DB 버전 마이그레이션")
    parser.add_argument(
        "--target", type=int, default=None,
        help=f"적용할 목표 버전 (기본: 최신 v{LATEST_VERSION}). 환경변수 DB_TARGET_VERSION으로도 지정 가능",
    )
    args = parser.parse_args()
    env_target = os.getenv("DB_TARGET_VERSION")
    target = args.target if args.target is not None else (int(env_target) if env_target else None)
    migrate(target)
