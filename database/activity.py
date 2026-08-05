"""활동 로그 · 감사 로그 · 인기 보고서 집계."""
import config
from database.pool import db_conn

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
