"""그룹(팀/부서 단위 권한) — 그룹 CRUD, 멤버 관리, 그룹 단위 보고서 접근."""
import psycopg2.errors

from database.pool import db_conn
from errors import AppError

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
