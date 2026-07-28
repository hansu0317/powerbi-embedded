"""PostgreSQL 커넥션 풀 + 모든 DB 쿼리 함수."""
import logging
import os
from contextlib import contextmanager

import bcrypt
import psycopg2
import psycopg2.errors
import psycopg2.extras
import psycopg2.pool

import config
from config import DB_CONFIG, WORKSPACE_ID
from errors import AppError

logger = logging.getLogger("powerbi-gateway")

# ── 커넥션 풀 ─────────────────────────────────────────────────────────────────
# 요청마다 연결을 새로 맺지 않도록 프로세스당 풀을 사용한다.
# 모든 쓰기 함수는 명시적으로 commit → 반환 전 rollback으로 트랜잭션 잔재만 정리한다.
db_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=int(os.getenv("DB_POOL_MIN", "2")),
    maxconn=int(os.getenv("DB_POOL_MAX", "20")),
    cursor_factory=psycopg2.extras.RealDictCursor,
    **DB_CONFIG,
)


@contextmanager
def db_conn():
    conn = db_pool.getconn()
    broken = False
    try:
        yield conn
        conn.rollback()
    except psycopg2.Error:
        broken = True
        raise
    except Exception:
        try:
            conn.rollback()
        except psycopg2.Error:
            broken = True
        raise
    finally:
        db_pool.putconn(conn, close=broken)


# ── 헬스 ─────────────────────────────────────────────────────────────────────

def db_health_check():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")


# ── 인증 ─────────────────────────────────────────────────────────────────────

def db_check_and_get_user(username: str, ip: str):
    """로그인 차단 확인 + 사용자 행 조회를 하나의 DB 커넥션에서 처리.

    반환: ("blocked", None) | ("ok", row | None)

    기존에는 db_login_allowed → db_authenticate 로 두 번 커넥션을 열었다.
    차단 체크와 사용자 SELECT를 같은 커넥션 안에서 순서대로 실행해 1회로 줄인다.
    bcrypt 비교는 CPU 집약적이므로 DB 커넥션을 닫은 뒤 호출자가 수행한다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS count FROM event_log "
                "WHERE log_type = 'login' AND username = %s AND ip = %s AND succeeded = FALSE "
                "AND created_at >= NOW() - %s * INTERVAL '1 minute'",
                (username, ip, config.LOGIN_BLOCK_MINUTES),
            )
            if cur.fetchone()["count"] >= config.LOGIN_BLOCK_MAX_FAIL:
                return "blocked", None
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, password, is_admin, is_active "
                "FROM users WHERE username = %s",
                (username,),
            )
            return "ok", cur.fetchone()


def db_verify_password(row, password: str):
    """bcrypt 비교 후 사용자 정보 반환. DB 접근 없는 순수 CPU 연산.

    반환: user dict(성공) / "inactive"(비활성 계정) / None(아이디·비밀번호 불일치)
    """
    if not row:
        return None
    if not bcrypt.checkpw(password.encode(), row["password"].encode()):
        return None
    if not row["is_active"]:
        return "inactive"
    return row


def db_record_login(username: str, ip: str, succeeded: bool):
    """로그인 시도를 기록한다. 성공 시 실패 이력 초기화 + last_login_at 갱신."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if succeeded:
                cur.execute(
                    "DELETE FROM event_log WHERE log_type = 'login' AND username = %s AND ip = %s",
                    (username, ip),
                )
                cur.execute("UPDATE users SET last_login_at = NOW() WHERE username = %s", (username,))
            cur.execute(
                "INSERT INTO event_log (log_type, username, ip, succeeded) VALUES ('login', %s, %s, %s)",
                (username, ip, succeeded),
            )
        conn.commit()


def db_cleanup_login_attempts():
    """30일 초과 로그인 시도 기록을 삭제한다.

    기존에는 db_record_login() 안에서 매 로그인마다 DELETE를 실행했다.
    로그인 응답 경로에서 제거하고 서버 시작 시 + 일 1회 백그라운드에서 실행한다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM event_log WHERE log_type = 'login' AND created_at < NOW() - INTERVAL '30 days'"
            )
        conn.commit()


# ── 사용자 ────────────────────────────────────────────────────────────────────

def db_get_user(username: str):
    """세션 사용자 조회. 비활성 계정은 None — 로그인 이후 비활성화돼도 다음 요청부터 즉시 차단된다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, pbi_username, roles, is_admin, can_upload "
                "FROM users WHERE username = %s AND is_active = TRUE",
                (username,),
            )
            return cur.fetchone()


# ── 보고서 ────────────────────────────────────────────────────────────────────

# 열람 가능 판정: 직접 부여(user_reports) OR 소속 그룹에 부여(group_reports).
# 아래 3곳(db_get_reports, db_can_view_report, db_admin_get_users)에서 동일하게 쓰이며,
# 모두 사용자 별칭 u, 보고서 별칭 r을 전제로 한다.
_CAN_VIEW_REPORT_SQL = """(
                       EXISTS (SELECT 1 FROM user_reports ur
                               WHERE ur.user_id = u.id AND ur.report_id = r.id AND ur.can_view)
                    OR EXISTS (SELECT 1 FROM user_groups ug
                               JOIN group_reports gr ON gr.group_id = ug.group_id
                               WHERE ug.user_id = u.id AND gr.report_id = r.id AND gr.can_view))"""


def db_get_reports(username: str) -> list:
    """사용자가 열람 가능한 보고서 목록 — 직접 부여 + 그룹 부여 합집합."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT r.id, r.name, r.report_type, r.owner_id, r.category, r.description,
                          owner.username AS owner_username,
                          r.preview_image_url, r.tab_type
                   FROM reports r
                   LEFT JOIN users owner ON owner.id = r.owner_id
                   JOIN users u ON u.username = %s
                   WHERE r.status = 'active' AND {_CAN_VIEW_REPORT_SQL}
                   ORDER BY r.category NULLS LAST, r.name""",
                (username,),
            )
            return cur.fetchall()


def db_get_all_active_reports() -> list:
    """관리자용: 권한 무관하게 active 보고서 전체 반환."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.owner_id, r.category, r.description,
                          owner.username AS owner_username,
                          r.preview_image_url, r.tab_type
                   FROM reports r
                   LEFT JOIN users owner ON owner.id = r.owner_id
                   WHERE r.status = 'active'
                   ORDER BY r.category NULLS LAST, r.name"""
            )
            return cur.fetchall()


def db_get_user_favorites(user_id: int) -> list:
    """사용자의 즐겨찾기 보고서 ID 목록 (active 보고서만, 최신 등록순)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT f.report_id
                   FROM user_report_marks f
                   JOIN reports r ON r.id = f.report_id
                   WHERE f.user_id = %s AND f.is_favorite AND r.status = 'active'
                   ORDER BY f.favorited_at DESC""",
                (user_id,),
            )
            return [row["report_id"] for row in cur.fetchall()]


def db_set_favorite(user_id: int, report_id: int, on: bool) -> None:
    """즐겨찾기 추가/해제 (멱등). 최근 본 기록이 없던 행이 즐겨찾기 해제로 완전히
    비면(둘 다 NULL/FALSE) user_report_marks 행 자체를 정리한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if on:
                cur.execute(
                    """INSERT INTO user_report_marks (user_id, report_id, is_favorite, favorited_at)
                       VALUES (%s, %s, TRUE, NOW())
                       ON CONFLICT (user_id, report_id) DO UPDATE SET is_favorite = TRUE, favorited_at = NOW()""",
                    (user_id, report_id),
                )
            else:
                cur.execute(
                    "UPDATE user_report_marks SET is_favorite = FALSE, favorited_at = NULL "
                    "WHERE user_id = %s AND report_id = %s",
                    (user_id, report_id),
                )
                cur.execute(
                    "DELETE FROM user_report_marks WHERE user_id = %s AND report_id = %s "
                    "AND NOT is_favorite AND viewed_at IS NULL",
                    (user_id, report_id),
                )
        conn.commit()


def db_get_user_recents(user_id: int, limit: int = 8) -> list:
    """사용자의 최근 본 보고서 ID 목록 (active 보고서만, 최신순)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.report_id
                   FROM user_report_marks t
                   JOIN reports r ON r.id = t.report_id
                   WHERE t.user_id = %s AND t.viewed_at IS NOT NULL AND r.status = 'active'
                   ORDER BY t.viewed_at DESC LIMIT %s""",
                (user_id, limit),
            )
            return [row["report_id"] for row in cur.fetchall()]


def db_add_recent(user_id: int, report_id: int, keep: int = 30) -> None:
    """최근 본 보고서 기록 (이미 있으면 viewed_at 갱신). 최신 keep건만 남기고 정리.

    즐겨찾기 행과 같은 테이블을 쓰므로, 순위 밖으로 밀린 행은 즐겨찾기가 아닐 때만
    완전히 삭제한다 — 즐겨찾기는 최근 본 목록에서만 빠지고 유지된다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_report_marks (user_id, report_id, viewed_at) VALUES (%s, %s, NOW()) "
                "ON CONFLICT (user_id, report_id) DO UPDATE SET viewed_at = NOW()",
                (user_id, report_id),
            )
            cur.execute(
                """UPDATE user_report_marks SET viewed_at = NULL
                   WHERE user_id = %s AND viewed_at IS NOT NULL AND report_id NOT IN (
                       SELECT report_id FROM user_report_marks
                       WHERE user_id = %s AND viewed_at IS NOT NULL
                       ORDER BY viewed_at DESC LIMIT %s)""",
                (user_id, user_id, keep),
            )
            cur.execute(
                "DELETE FROM user_report_marks WHERE user_id = %s AND viewed_at IS NULL AND NOT is_favorite",
                (user_id,),
            )
        conn.commit()


def db_get_pbi_report_map() -> dict:
    """활성 보고서의 pbi_report_id → {category, name} 매핑 (Fabric 드리프트 비교용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT pbi_report_id, category, name
                   FROM reports
                   WHERE status = 'active' AND pbi_report_id IS NOT NULL"""
            )
            return {
                row["pbi_report_id"]: {"category": row["category"], "name": row["name"]}
                for row in cur.fetchall()
            }


def db_hard_delete_report(report_id: int) -> bool:
    """보고서를 DB에서 완전히 삭제한다 (CASCADE로 하위 테이블 자동 정리)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reports WHERE id = %s", (report_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def db_can_view_report(username: str, report_id: int) -> bool:
    """사용자가 해당 보고서를 열람할 수 있는지 단건 조회.

    열람 가능 = 직접 부여(user_reports) OR 소속 그룹에 부여(group_reports)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT 1 FROM users u
                   JOIN reports r ON r.id = %s AND r.status = 'active'
                   WHERE u.username = %s AND {_CAN_VIEW_REPORT_SQL}""",
                (report_id, username),
            )
            return cur.fetchone() is not None


def db_get_report(report_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.owner_id,
                          r.pbi_report_id, r.pbi_dataset_id, r.pbi_workspace_id,
                          r.default_page, r.enable_filter, r.enable_page_nav,
                          r.use_data_bot, r.tab_type
                   FROM reports r
                   WHERE r.id = %s""",
                (report_id,),
            )
            return cur.fetchone()


def db_find_report(owner_id: int, name: str):
    """같은 이름의 '살아있는' 보고서 조회. deleted 상태는 재사용 가능."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, owner_id, pbi_report_id
                   FROM reports
                   WHERE owner_id = %s AND LOWER(name) = LOWER(%s) AND status <> 'deleted'""",
                (owner_id, name),
            )
            return cur.fetchone()


# ── 업로드 잡 ─────────────────────────────────────────────────────────────────

def db_reserve_update(actor_id: int, target_report_id: int, report_name: str) -> int:
    """보고서 콘텐츠 업데이트 예약 (v7). 새 보고서를 만들지 않으므로 개인 보고서
    개수 한도는 검사하지 않는다 — 일일 업로드 한도와 동시 실행 잠금만 재사용한다.
    권한(소유자·admin) 검증은 호출자(routes/report.py)에서 이미 끝난 상태로 들어온다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (actor_id,))
            cur.execute(
                "SELECT COUNT(*) AS count FROM upload_jobs WHERE user_id = %s AND created_at >= CURRENT_DATE",
                (actor_id,),
            )
            if cur.fetchone()["count"] >= config.MAX_UPLOADS_PER_DAY:
                raise AppError.RATE_UPLOAD_DAILY.http(max=config.MAX_UPLOADS_PER_DAY)
            try:
                cur.execute(
                    "INSERT INTO upload_jobs (user_id, report_name, status, job_type, target_report_id) "
                    "VALUES (%s, %s, 'publishing', 'update', %s) RETURNING id",
                    (actor_id, report_name, target_report_id),
                )
                row = cur.fetchone()
            except psycopg2.errors.UniqueViolation as exc:
                conn.rollback()
                raise AppError.UPLOAD_IN_PROGRESS.http(name=report_name) from exc
        conn.commit()
    return row["id"]


def db_reserve_upload(user_id: int, report_name: str, is_update: bool = False) -> int:
    """업로드 예약. DB 제약으로 다중 프로세스 경합을 막는다.

    is_update=True(같은 이름의 내 보고서를 다시 올리는 경우)면 개인 보고서 개수
    한도는 검사하지 않는다 — 새 보고서가 생기는 게 아니라 기존 것을 갱신하기 때문이다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (user_id,))
            if not is_update:
                cur.execute(
                    "SELECT COUNT(*) AS count FROM reports "
                    "WHERE owner_id = %s AND report_type = 'personal' AND status <> 'deleted'",
                    (user_id,),
                )
                if cur.fetchone()["count"] >= config.MAX_PERSONAL_REPORTS:
                    raise AppError.RATE_PERSONAL_MAX.http(max=config.MAX_PERSONAL_REPORTS)
            cur.execute(
                "SELECT COUNT(*) AS count FROM upload_jobs WHERE user_id = %s AND created_at >= CURRENT_DATE",
                (user_id,),
            )
            if cur.fetchone()["count"] >= config.MAX_UPLOADS_PER_DAY:
                raise AppError.RATE_UPLOAD_DAILY.http(max=config.MAX_UPLOADS_PER_DAY)
            try:
                cur.execute(
                    "INSERT INTO upload_jobs (user_id, report_name, status) VALUES (%s, %s, 'publishing') RETURNING id",
                    (user_id, report_name),
                )
                row = cur.fetchone()
            except psycopg2.errors.UniqueViolation as exc:
                conn.rollback()
                raise AppError.UPLOAD_IN_PROGRESS.http(name=report_name) from exc
        conn.commit()
    return row["id"]


def db_count_other_reports_using_dataset(pbi_dataset_id: str, exclude_report_id: int) -> int:
    """같은 PBI 데이터셋을 쓰는 다른 활성 보고서 수.

    관리자 삭제 시 데이터셋까지 지워도 되는지 판단용 — 0이면 안전하게 삭제 가능."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS count
                   FROM reports
                   WHERE pbi_dataset_id = %s AND id <> %s AND status <> 'deleted'""",
                (pbi_dataset_id, exclude_report_id),
            )
            return cur.fetchone()["count"]


def db_fail_stuck_upload_job(job_id: int) -> bool:
    """예상 밖 예외로 중단된 업로드 잡을 실패 처리한다 (업로드 태스크의 최후 방어선).

    publishing·accepted만 대상 — 그 외 상태(failed/conflict/unknown/pbi_succeeded/
    db_failed)는 각 실패 지점에서 이미 기록됐거나 재시작 복구 대상이므로 덮지 않는다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE upload_jobs
                   SET status = 'failed', updated_at = NOW(),
                       error_message = '예상치 못한 오류로 업로드가 중단되었습니다. 다시 시도해 주세요.'
                   WHERE id = %s AND status IN ('publishing', 'accepted')""",
                (job_id,),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def db_fail_stale_publishing_jobs() -> int:
    """서버 시작 시 고아가 된 'publishing' 잡을 실패 처리한다.

    publishing(파일 수신~Import 접수 전)은 메모리의 업로드 태스크만 진행시킬 수
    있으므로, 재시작 직후 남아 있으면 전부 복구 불가다. 방치하면 부분 UNIQUE
    인덱스 때문에 같은 이름 재업로드가 계속 409로 막힌다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE upload_jobs
                   SET status = 'failed', updated_at = NOW(),
                       error_message = '서버 재시작으로 업로드가 중단되었습니다. 다시 업로드해 주세요.'
                   WHERE status = 'publishing'"""
            )
            count = cur.rowcount
        conn.commit()
    return count


def db_get_upload_job(job_id: int, user_id: int) -> dict | None:
    """업로드 잡 단건 조회. user_id로 소유자 검증."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, report_name, report_id, error_message "
                "FROM upload_jobs WHERE id = %s AND user_id = %s",
                (job_id, user_id),
            )
            return cur.fetchone()


def db_update_upload_job(job_id: int, status: str, **values):
    allowed = {"import_id", "pbi_report_id", "error_message", "pbi_workspace_id", "report_id"}
    updates = {k: v for k, v in values.items() if k in allowed}
    assignments = ["status = %s", "updated_at = NOW()"]
    params = [status]
    for k, v in updates.items():
        assignments.append(f"{k} = %s")
        params.append(v)
    params.append(job_id)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE upload_jobs SET {', '.join(assignments)} WHERE id = %s", params)
        conn.commit()


def db_register_report(
    name: str,
    pbi_report_id: str,
    owner_id: int,
    pbi_dataset_id: str | None = None,
    pbi_workspace_id: str | None = None,
    pbi_display_name: str | None = None,
    category: str | None = None,
    description: str | None = None,
):
    """업로드된 보고서를 등록하고 소유자에게 열람 권한을 부여한다.

    관리자는 권한 확인을 우회(is_admin)하므로 user_reports 행을 만들지 않는다 —
    만들면 '열람권한' 수만 부풀린다.
    category는 Fabric 폴더 경로(개인 보고서는 업로더 username)로 전달하면 사이드바 폴더 트리에 반영된다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("LOCK TABLE reports IN SHARE ROW EXCLUSIVE MODE")
            cur.execute(
                """SELECT setval(
                       pg_get_serial_sequence('reports', 'id'),
                       GREATEST((SELECT COALESCE(MAX(id), 0) FROM reports), 1),
                       (SELECT COUNT(*) > 0 FROM reports)
                   )"""
            )
            cur.execute(
                "SELECT id FROM reports WHERE owner_id = %s AND LOWER(name) = LOWER(%s) FOR UPDATE",
                (owner_id, name),
            )
            existing = cur.fetchone()
            if existing:
                report_id = existing["id"]
                cur.execute(
                    "UPDATE reports SET status = 'active', deleted_at = NULL, updated_at = NOW(), "
                    "updated_by = %s, category = COALESCE(category, %s), "
                    "description = COALESCE(%s, description), "
                    "pbi_report_id = %s, pbi_workspace_id = %s, pbi_dataset_id = %s, pbi_display_name = %s "
                    "WHERE id = %s",
                    (owner_id, category, description, pbi_report_id,
                     config.resolve_workspace_id(pbi_workspace_id), pbi_dataset_id, pbi_display_name, report_id),
                )
            else:
                cur.execute(
                    """INSERT INTO reports (
                           name, report_type, owner_id, status, category, description, created_by, updated_by,
                           pbi_report_id, pbi_workspace_id, pbi_dataset_id, pbi_display_name
                       ) VALUES (%s, 'personal', %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (name, owner_id, category, description, owner_id, owner_id,
                     pbi_report_id, config.resolve_workspace_id(pbi_workspace_id), pbi_dataset_id, pbi_display_name),
                )
                report_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
                   VALUES (%s, %s, TRUE, %s)
                   ON CONFLICT (user_id, report_id) DO UPDATE SET can_view=TRUE""",
                (owner_id, report_id, owner_id),
            )
            cur.execute(
                """INSERT INTO event_log (log_type, report_id, user_id, event, details)
                   VALUES ('audit', %s, %s, 'personal_report_registered',
                           jsonb_build_object('pbi_report_id', %s, 'name', %s))""",
                (report_id, owner_id, pbi_report_id, name),
            )
        conn.commit()
    return report_id


# ── Fabric 동기화 보조 ────────────────────────────────────────────────────────

def db_get_synced_reports():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, status, pbi_report_id,
                          COALESCE(pbi_workspace_id, %s) AS pbi_workspace_id
                   FROM reports
                   WHERE status IN ('active', 'deleted')""",
                (WORKSPACE_ID,),
            )
            return cur.fetchall()


def db_mark_report_deleted(report_id: int, pbi_report_id: str, reason: str) -> bool:
    """PBI 워크스페이스에서 보고서가 사라진 것을 DB에 반영한다 (소프트 삭제)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET status='deleted', deleted_at=NOW(), updated_at=NOW() WHERE id=%s AND status<>'deleted'",
                (report_id,),
            )
            if cur.rowcount:
                cur.execute(
                    """INSERT INTO event_log (log_type, report_id, event, details)
                       VALUES ('audit', %s, 'pbi_deleted', jsonb_build_object('pbi_report_id', %s, 'reason', %s))""",
                    (report_id, pbi_report_id, reason),
                )
        conn.commit()
        return bool(cur.rowcount)


def db_restore_report(report_id: int, pbi_report_id: str) -> bool:
    """삭제 처리됐던 보고서가 PBI 워크스페이스에 다시 나타나면 active로 되돌린다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET status='active', deleted_at=NULL, updated_at=NOW() WHERE id=%s AND status='deleted'",
                (report_id,),
            )
            if cur.rowcount:
                cur.execute(
                    "INSERT INTO event_log (log_type, report_id, event, details) VALUES ('audit', %s, 'pbi_restored', jsonb_build_object('pbi_report_id', %s))",
                    (report_id, pbi_report_id),
                )
        conn.commit()
        return bool(cur.rowcount)


def db_get_pending_imports():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.user_id, j.report_name, j.import_id, u.username
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   WHERE j.status IN ('accepted', 'unknown') AND j.import_id IS NOT NULL
                   ORDER BY j.id"""
            )
            return cur.fetchall()


def db_get_recoverable_jobs():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.user_id, j.report_name, j.pbi_report_id, j.pbi_workspace_id,
                          u.username
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   WHERE j.status IN ('pbi_succeeded', 'db_failed') AND j.pbi_report_id IS NOT NULL
                   ORDER BY j.id"""
            )
            return cur.fetchall()


# ── 관리자 ────────────────────────────────────────────────────────────────────

def db_admin_get_stats() -> dict:
    """관리자 현황 화면이 쓰는 집계 전부. 스칼라 서브쿼리로 묶어 DB 왕복 1회로 처리한다.

    부트스트랩(stats)과 자가진단(/api/admin/system-status)이 같은 화면을 채우므로
    한 함수로 합쳤다 — 예전엔 두 함수가 active_reports를 각자 세고 있었다.
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                    (SELECT COUNT(*) FROM users       WHERE is_active = TRUE)           AS active_users,
                    (SELECT COUNT(*) FROM reports     WHERE status    = 'active')        AS active_reports,
                    (SELECT COUNT(*) FROM upload_jobs WHERE created_at >= CURRENT_DATE)  AS today_uploads,
                    (SELECT COUNT(*) FROM upload_jobs WHERE status = 'completed'
                                                       AND created_at >= CURRENT_DATE)  AS today_success,
                    (SELECT COUNT(*) FROM upload_jobs
                     WHERE status IN ('failed','unknown','db_failed')
                       AND created_at >= NOW() - INTERVAL '7 days')                      AS failed_jobs_7d,
                    (SELECT COUNT(*) FROM event_log WHERE log_type = 'activity')         AS activity_rows"""
            )
            row = cur.fetchone()
    return dict(row)


def db_admin_get_users() -> list:
    """사용자 목록 + 열람 가능 보고서 수 (직접 부여 + 그룹 경유, active만)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT u.id, u.username, u.display_name, u.pbi_username, u.roles,
                          u.is_admin, u.is_active, u.can_upload, u.last_login_at, u.created_at,
                          (SELECT COUNT(*) FROM reports r
                           WHERE r.status = 'active' AND {_CAN_VIEW_REPORT_SQL}
                          ) AS report_count
                   FROM users u ORDER BY u.id"""
            )
            return cur.fetchall()


def db_get_user_report_list(user_id: int) -> list:
    """사용자가 열람 가능한 보고서 목록 + 경로(직접 부여 여부, 경유 그룹명들).

    관리자 포털 사용자 화면의 '보고서 N' 클릭 팝업용."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.category,
                          (ur.user_id IS NOT NULL) AS direct,
                          COALESCE(ARRAY_AGG(DISTINCT g.name)
                                   FILTER (WHERE g.name IS NOT NULL), '{}') AS via_groups
                   FROM reports r
                   LEFT JOIN user_reports ur
                          ON ur.report_id = r.id AND ur.user_id = %s AND ur.can_view
                   LEFT JOIN user_groups ug ON ug.user_id = %s
                   LEFT JOIN group_reports gr
                          ON gr.group_id = ug.group_id AND gr.report_id = r.id AND gr.can_view
                   LEFT JOIN groups g ON g.id = gr.group_id
                   WHERE r.status = 'active'
                     AND (ur.user_id IS NOT NULL OR gr.group_id IS NOT NULL)
                   GROUP BY r.id, r.name, r.category, ur.user_id
                   ORDER BY r.category NULLS LAST, r.name""",
                (user_id, user_id),
            )
            return cur.fetchall()


def db_admin_add_user(username: str, pw_hash: str, display_name: str,
                      pbi_username: str, roles: list[str], is_admin: bool,
                      can_upload: bool = True, group_ids: list[int] | None = None) -> int:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password, display_name, pbi_username, roles, is_admin, can_upload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (username, pw_hash, display_name, pbi_username, roles, is_admin, can_upload),
            )
            row = cur.fetchone()
            user_id = row["id"]
            if group_ids:
                cur.executemany(
                    "INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s)",
                    [(user_id, gid) for gid in group_ids],
                )
        conn.commit()
    return user_id


def db_admin_toggle_user_active(user_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = NOT is_active, updated_at = NOW() "
                "WHERE id = %s AND username != 'admin' RETURNING is_active",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["is_active"] if row else None


def db_admin_get_reports() -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.name, r.report_type, r.status, r.created_at, r.category, r.description,
                          u.username AS owner_username,
                          r.pbi_report_id, r.pbi_display_name, r.pbi_dataset_id,
                          COALESCE(r.pbi_workspace_id, %s) AS pbi_workspace_id,
                          COUNT(ur.user_id) FILTER (WHERE NOT vu.is_admin) AS viewer_count,
                          (SELECT COUNT(*) FROM group_reports gr
                           WHERE gr.report_id = r.id AND gr.can_view) AS group_count
                   FROM reports r
                   LEFT JOIN users u ON u.id = r.owner_id
                   LEFT JOIN user_reports ur ON ur.report_id = r.id AND ur.can_view = TRUE
                   LEFT JOIN users vu ON vu.id = ur.user_id
                   WHERE r.status <> 'deleted'
                   GROUP BY r.id, u.username
                   ORDER BY r.id""",
                (WORKSPACE_ID,)
            )
            return cur.fetchall()


def db_import_pbi_item(
    pbi_item_id: str,
    name: str,
    pbi_workspace_id: str,
    folder_id: str | None,
    category: str | None,
    actor_id: int,
    pbi_dataset_id: str | None = None,
    is_dashboard: bool = False,
) -> bool:
    """PBI에서 가져온 공용 항목(보고서 또는 대시보드)을 DB에 등록한다.

    이미 등록된 항목(pbi_report_id 기준)은 category만 갱신하고 False를 반환한다.
    신규 등록 성공 시 True. 권한은 부여하지 않는다 — 관리자가 별도로 설정한다.

    보고서와 대시보드는 등록 절차가 같고 세 가지만 다르다:
      report_type, tab_type(뷰어 임베드 분기 신호), 그리고 대시보드는
      pbi_dataset_id가 없다(여러 데이터셋의 타일 모음이라 단일 ID가 없음).

    이름이 '계정__보고서명' 형식이고 그 계정이 존재하면 개인 보고서로 복원한다
    (DB 재구축 후 가져오기에서 개인 보고서가 전부 공용이 되는 것을 막는다).
    """
    tab_type    = "dashboard" if is_dashboard else "report"
    dataset_id  = None if is_dashboard else pbi_dataset_id
    audit_event = "dashboard_imported" if is_dashboard else "managed_report_imported"

    # PBI 표시 이름이 '계정__보고서명' 규칙이면 원래 개인 보고서였다는 뜻이다.
    # DB를 새로 구축하고 가져오기를 하면 이 정보가 없어 전부 공용이 돼버리므로,
    # 접두사의 계정이 실제로 존재할 때만 개인 보고서로 되돌린다.
    owner_id, display_name = None, name
    if not is_dashboard and "__" in name:
        prefix, _, rest = name.partition("__")
        if prefix and rest:
            with db_conn() as probe:
                with probe.cursor() as pcur:
                    pcur.execute("SELECT id FROM users WHERE username = %s", (prefix,))
                    row = pcur.fetchone()
            if row:
                owner_id, display_name = row["id"], rest

    report_type = "dashboard" if is_dashboard else ("personal" if owner_id else "managed")
    if owner_id:
        audit_event = "personal_report_restored"

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM reports WHERE pbi_report_id = %s", (pbi_item_id,))
            existing = cur.fetchone()
            if existing:
                if category:
                    cur.execute(
                        "UPDATE reports SET category = %s, updated_at = NOW() "
                        "WHERE id = %s AND category IS DISTINCT FROM %s",
                        (category, existing["id"], category),
                    )
                    conn.commit()
                return False
            cur.execute(
                """INSERT INTO reports (
                       name, report_type, owner_id, status, category, created_by, updated_by,
                       pbi_report_id, pbi_workspace_id, pbi_dataset_id, folder_id, tab_type
                   ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (display_name, report_type, owner_id, category, actor_id, actor_id,
                 pbi_item_id, pbi_workspace_id, dataset_id, folder_id, tab_type),
            )
            report_id = cur.fetchone()["id"]
            if owner_id:
                # 소유자에게 열람 권한을 돌려준다 (업로드 시 부여했던 것과 동일)
                cur.execute(
                    """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (user_id, report_id) DO UPDATE SET can_view = TRUE""",
                    (owner_id, report_id, actor_id),
                )
            cur.execute(
                """INSERT INTO event_log (log_type, report_id, user_id, event, details)
                   VALUES ('audit', %s, %s, %s,
                           jsonb_build_object('pbi_item_id', %s, 'name', %s, 'category', %s))""",
                (report_id, actor_id, audit_event, pbi_item_id, display_name, category),
            )
        conn.commit()
    return True


def db_admin_set_category(report_id: int, category: str | None, admin_user_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET category=%s, updated_at=NOW(), updated_by=%s WHERE id=%s",
                (category or None, admin_user_id, report_id),
            )
            cur.execute(
                "INSERT INTO event_log (log_type, report_id, user_id, event, details) "
                "VALUES ('audit', %s, %s, 'admin_set_category', jsonb_build_object('category', %s))",
                (report_id, admin_user_id, category),
            )
        conn.commit()


def db_admin_soft_delete_report(report_id: int, admin_user_id: int) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reports SET status='deleted', deleted_at=NOW(), updated_at=NOW(), updated_by=%s "
                "WHERE id=%s AND status!='deleted' RETURNING id",
                (admin_user_id, report_id),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO event_log (log_type, report_id, user_id, event, details) "
                    "VALUES ('audit', %s, %s, 'admin_deleted', '{}'::jsonb)",
                    (report_id, admin_user_id),
                )
        conn.commit()
    return bool(row)


def db_admin_get_upload_jobs(limit: int = 30) -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT j.id, j.report_name, j.status, j.error_message,
                          j.created_at, j.updated_at, u.username,
                          r.category
                   FROM upload_jobs j
                   JOIN users u ON u.id = j.user_id
                   LEFT JOIN reports r ON r.id = j.report_id
                   ORDER BY j.id DESC LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()


def db_get_report_access(report_id: int) -> list:
    """보고서에 대한 모든 활성 사용자의 열람 권한 현황을 반환한다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.username, u.display_name, u.is_admin,
                          COALESCE(ur.can_view, FALSE) AS can_view
                   FROM users u
                   LEFT JOIN user_reports ur ON ur.user_id = u.id AND ur.report_id = %s
                   WHERE u.is_active = TRUE
                   ORDER BY u.is_admin DESC, u.username""",
                (report_id,),
            )
            return cur.fetchall()


def db_set_report_access(report_id: int, user_id: int, can_view: bool, granted_by: int) -> None:
    """보고서에 대한 특정 사용자의 열람 권한을 설정한다.

    없는 보고서·사용자 ID면 FK 위반이 나는데, 이는 서버 장애가 아니라 잘못된 요청이므로
    404로 변환한다 (그대로 두면 500).
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
          try:
            if can_view:
                cur.execute(
                    """INSERT INTO user_reports (user_id, report_id, can_view, granted_by)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (user_id, report_id) DO UPDATE SET can_view = TRUE, granted_by = EXCLUDED.granted_by""",
                    (user_id, report_id, granted_by),
                )
            else:
                cur.execute(
                    "UPDATE user_reports SET can_view = FALSE WHERE user_id = %s AND report_id = %s",
                    (user_id, report_id),
                )
          except psycopg2.errors.ForeignKeyViolation as exc:
            conn.rollback()
            raise AppError.REPORT_NOT_FOUND.http() from exc
        conn.commit()


def db_get_app_config() -> list:
    """app_config 전체 행 (관리자 포털 설정 화면용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key, value, description, updated_at FROM app_config ORDER BY key")
            return cur.fetchall()


# ── 그룹 (팀/부서 단위 권한) ─────────────────────────────────────────────────

def db_admin_get_groups() -> list:
    """그룹 목록 + 멤버 수 + 부여된 보고서 수."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT g.id, g.name, g.description, g.created_at,
                          (SELECT COUNT(*) FROM user_groups ug WHERE ug.group_id = g.id) AS member_count,
                          (SELECT COUNT(*) FROM group_reports gr
                           WHERE gr.group_id = g.id AND gr.can_view) AS report_count
                   FROM groups g ORDER BY g.name"""
            )
            return cur.fetchall()


def db_admin_create_group(name: str, description: str, actor_id: int) -> int:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO groups (name, description, created_by) VALUES (%s, %s, %s) RETURNING id",
                (name, description or None, actor_id),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def db_admin_delete_group(group_id: int) -> bool:
    """그룹 삭제 — 멤버·보고서 부여는 CASCADE로 함께 제거된다 (개별 부여는 무관)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM groups WHERE id = %s", (group_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def db_get_group_members(group_id: int) -> list:
    """활성 사용자 전체 + 이 그룹 소속 여부 (그룹 멤버 편집 모달용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.username, u.display_name, u.is_admin,
                          (ug.user_id IS NOT NULL) AS is_member
                   FROM users u
                   LEFT JOIN user_groups ug ON ug.user_id = u.id AND ug.group_id = %s
                   WHERE u.is_active = TRUE
                   ORDER BY u.is_admin DESC, u.username""",
                (group_id,),
            )
            return cur.fetchall()


def db_set_group_member(group_id: int, user_id: int, member: bool, added_by: int) -> None:
    """그룹 멤버 추가/제거. 없는 그룹·사용자면 404 (FK 위반 → 500 방지)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
          try:
            if member:
                cur.execute(
                    """INSERT INTO user_groups (user_id, group_id, added_by) VALUES (%s, %s, %s)
                       ON CONFLICT (user_id, group_id) DO NOTHING""",
                    (user_id, group_id, added_by),
                )
            else:
                cur.execute(
                    "DELETE FROM user_groups WHERE user_id = %s AND group_id = %s",
                    (user_id, group_id),
                )
          except psycopg2.errors.ForeignKeyViolation as exc:
            conn.rollback()
            raise AppError.USER_NOT_FOUND.http() from exc
        conn.commit()


def db_get_report_group_access(report_id: int) -> list:
    """그룹 전체 + 이 보고서 부여 여부 (권한 모달 '그룹' 탭용)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT g.id, g.name,
                          (SELECT COUNT(*) FROM user_groups ug WHERE ug.group_id = g.id) AS member_count,
                          COALESCE(gr.can_view, FALSE) AS can_view
                   FROM groups g
                   LEFT JOIN group_reports gr ON gr.group_id = g.id AND gr.report_id = %s
                   ORDER BY g.name""",
                (report_id,),
            )
            return cur.fetchall()


def db_set_report_group_access(report_id: int, group_id: int, can_view: bool, granted_by: int) -> None:
    """그룹 단위 열람 권한 설정. 없는 보고서·그룹이면 404 (FK 위반 → 500 방지)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
          try:
            if can_view:
                cur.execute(
                    """INSERT INTO group_reports (group_id, report_id, can_view, granted_by)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (group_id, report_id) DO UPDATE
                       SET can_view = TRUE, granted_by = EXCLUDED.granted_by""",
                    (group_id, report_id, granted_by),
                )
            else:
                cur.execute(
                    "DELETE FROM group_reports WHERE group_id = %s AND report_id = %s",
                    (group_id, report_id),
                )
          except psycopg2.errors.ForeignKeyViolation as exc:
            conn.rollback()
            raise AppError.REPORT_NOT_FOUND.http() from exc
        conn.commit()


def db_update_app_config(key: str, value: str) -> bool:
    """존재하는 app_config 키의 값을 갱신한다. 없는 키는 거부(False).

    키 생성은 마이그레이션 시드에서만 한다 — 오타 키가 조용히 쌓이는 것을 방지."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_config SET value = %s, updated_at = NOW() WHERE key = %s",
                (value, key),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


# ── 활동 로그 (v3) ────────────────────────────────────────────────────────────

def db_log_activity(user_id: int, username: str, event: str,
                    report_id: int | None = None, report_name: str | None = None,
                    ip: str | None = None, dedupe_minutes: int = 0) -> None:
    """사용자 활동 1건 기록. username·report_name은 원본 삭제 후에도 판독 가능하도록 함께 보존.

    dedupe_minutes > 0이면 같은 사용자×보고서×이벤트가 그 시간 안에 이미 있으면 기록하지
    않는다 — 토큰 자동 재발급(1시간마다)·새로고침 탭 복원이 조회수를 부풀리는 것을 막는다
    (과거 report_views 테이블을 폐기했던 바로 그 문제의 재발 방지)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if dedupe_minutes > 0:
                cur.execute(
                    """INSERT INTO event_log (log_type, user_id, username, event, report_id, report_name, ip)
                       SELECT 'activity', %s, %s, %s, %s, %s, %s
                       WHERE NOT EXISTS (
                           SELECT 1 FROM event_log
                           WHERE log_type = 'activity' AND user_id = %s AND report_id = %s AND event = %s
                             AND created_at > NOW() - %s * INTERVAL '1 minute')""",
                    (user_id, username, event, report_id, report_name, ip,
                     user_id, report_id, event, dedupe_minutes),
                )
            else:
                cur.execute(
                    "INSERT INTO event_log (log_type, user_id, username, event, report_id, report_name, ip) "
                    "VALUES ('activity', %s, %s, %s, %s, %s, %s)",
                    (user_id, username, event, report_id, report_name, ip),
                )
        conn.commit()


def db_get_activity_log(username: str | None = None, event: str | None = None,
                        date_from: str | None = None, date_to: str | None = None,
                        limit: int = 1000) -> list:
    """활동 로그 조회 (관리자 로그 화면·CSV 내보내기용). 필터는 전부 선택."""
    conds, params = ["log_type = 'activity'"], []
    if username:
        conds.append("username ILIKE %s")
        params.append(f"%{username}%")
    if event:
        conds.append("event = %s")
        params.append(event)
    if date_from:
        conds.append("created_at >= %s::date")
        params.append(date_from)
    if date_to:
        conds.append("created_at < %s::date + INTERVAL '1 day'")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    params.append(limit)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, username, event, report_name, ip, created_at
                   FROM event_log {where}
                   ORDER BY created_at DESC LIMIT %s""",
                params,
            )
            return cur.fetchall()


def db_get_user_activity_log(user_id: int, limit: int = 200) -> list:
    """본인 활동 로그 (v6) — 일반 사용자용. 관리자 로그 화면(db_get_activity_log)과
    달리 user_id로 강제 고정해 다른 사람 기록을 절대 못 본다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, event, report_name, created_at
                   FROM event_log WHERE log_type = 'activity' AND user_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, limit),
            )
            return cur.fetchall()


def db_get_audit_log(date_from: str | None = None, date_to: str | None = None,
                     limit: int = 1000) -> list:
    """관리 행위 감사 로그 조회 (event_log의 log_type='audit' — 등록/삭제/권한변경)."""
    conds, params = ["a.log_type = 'audit'"], []
    if date_from:
        conds.append("a.created_at >= %s::date")
        params.append(date_from)
    if date_to:
        conds.append("a.created_at < %s::date + INTERVAL '1 day'")
        params.append(date_to)
    where = "WHERE " + " AND ".join(conds)
    params.append(limit)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT a.id, a.event AS action, a.details, a.created_at,
                          u.username AS actor, r.name AS report_name
                   FROM event_log a
                   LEFT JOIN users u ON u.id = a.user_id
                   LEFT JOIN reports r ON r.id = a.report_id
                   {where}
                   ORDER BY a.created_at DESC LIMIT %s""",
                params,
            )
            return cur.fetchall()


def db_cleanup_activity_log() -> int:
    """보존 기간(app_config: activity_log_retention_days) 초과분 삭제. 일 1회 백그라운드 실행."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM event_log WHERE log_type = 'activity' AND created_at < NOW() - %s * INTERVAL '1 day'",
                (config.ACTIVITY_LOG_RETENTION_DAYS,),
            )
            deleted = cur.rowcount
        conn.commit()
    return deleted


def db_get_popular_report_ids(days: int = 30, limit: int = 20) -> list:
    """최근 N일 조회수 상위 보고서 [(report_id, views)]. active 보고서만.

    호출자(홈 부트스트랩)가 사용자의 열람 가능 목록과 교집합을 내서 노출한다 —
    권한 없는 보고서의 존재가 인기 목록으로 새는 것을 방지."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.report_id, COUNT(*) AS views
                   FROM event_log a
                   JOIN reports r ON r.id = a.report_id AND r.status = 'active'
                   WHERE a.log_type = 'activity' AND a.event = 'report_view'
                     AND a.created_at >= NOW() - %s * INTERVAL '1 day'
                   GROUP BY a.report_id
                   ORDER BY views DESC, a.report_id
                   LIMIT %s""",
                (days, limit),
            )
            return cur.fetchall()


def db_admin_toggle_user_upload(user_id: int):
    """업로드 권한 토글. 반환: 변경 후 can_upload (없는 사용자·관리자는 None).

    관리자 계정은 차단 대상에서 제외한다 — toggle_active의 admin 보호와 같은 원칙."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET can_upload = NOT can_upload, updated_at = NOW() "
                "WHERE id = %s AND NOT is_admin RETURNING can_upload",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["can_upload"] if row else None


# ── v4: 시스템 자가진단 ──────────────────────────────────────────────────────



