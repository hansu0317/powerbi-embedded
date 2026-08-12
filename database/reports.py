"""보고서 열람 목록 · 즐겨찾기 · 최근 본 보고서.

_CAN_VIEW_REPORT_SQL — 열람 가능 판정 SQL의 유일한 소스. database/admin.py의
db_admin_get_users도 이걸 그대로 가져다 쓴다(중복 정의 금지 — 판정 규칙은 여기 한 곳에서만)."""
import config
from database.pool import db_conn

# ── 보고서 ────────────────────────────────────────────────────────────────────

# 열람 가능 판정: (직접 부여 OR 그룹 부여) AND NOT 개별 명시 차단.
# user_reports 행의 의미: 행 없음=개별 설정 없음(그룹 결과를 그대로 따름), TRUE=직접 허용,
# FALSE=명시적 차단 — 그룹으로 부여됐어도 이 차단이 최우선으로 이긴다(그룹 멤버 중 특정
# 1명만 제외하고 싶을 때 이 행 하나만 FALSE로 넣으면 됨. db_set_report_access 참고).
# 아래 3곳(db_get_reports, db_can_view_report, db_admin_get_users)에서 동일하게 쓰이며,
# 모두 사용자 별칭 u, 보고서 별칭 r을 전제로 한다.
_CAN_VIEW_REPORT_SQL = """(
                    NOT EXISTS (SELECT 1 FROM user_reports udeny
                                WHERE udeny.user_id = u.id AND udeny.report_id = r.id AND NOT udeny.can_view)
                AND (
                       r.owner_id = u.id
                    OR r.visibility = 'shared'
                    OR (r.visibility = 'group' AND EXISTS (
                         SELECT 1 FROM user_groups me
                         JOIN user_groups owner_group ON owner_group.group_id = me.group_id
                         WHERE me.user_id = u.id AND owner_group.user_id = r.owner_id))
                    OR
                       EXISTS (SELECT 1 FROM user_reports ur
                               WHERE ur.user_id = u.id AND ur.report_id = r.id AND ur.can_view)
                    OR EXISTS (SELECT 1 FROM user_groups ug
                               JOIN group_reports gr ON gr.group_id = ug.group_id
                               WHERE ug.user_id = u.id AND gr.report_id = r.id AND gr.can_view)
                    ))"""


def db_get_reports(username: str) -> list:
    """사용자가 열람 가능한 보고서 목록 — 직접 부여 + 그룹 부여 합집합."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT r.id, r.name, r.report_type, r.owner_id, r.category, r.description,
                          owner.username AS owner_username, r.tab_type, r.portal_folder_id, r.visibility,
                          EXISTS (SELECT 1 FROM group_reports gr
                                  WHERE gr.report_id=r.id AND gr.can_view) AS has_group_access
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
                          owner.username AS owner_username, r.tab_type, r.portal_folder_id, r.visibility,
                          EXISTS (SELECT 1 FROM group_reports gr
                                  WHERE gr.report_id=r.id AND gr.can_view) AS has_group_access
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


def db_get_user_recents(user_id: int, limit: int | None = None) -> list:
    """사용자의 최근 본 보고서 ID 목록 (active 보고서만, 최신순).

    limit 기본값은 config.RECENTS_LIMIT(app_config 'recents_limit', 관리자 설정에서
    재시작 없이 변경 가능) — 함수 기본 인자로 `limit: int = config.RECENTS_LIMIT`처럼
    쓰면 import 시점 값에 고정돼 설정 변경이 반영 안 되므로(config.py의
    reload_app_config 주석 참고) 반드시 호출 시점에 읽는다."""
    if limit is None:
        limit = config.RECENTS_LIMIT
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
                          r.tab_type, r.filter_table, r.filter_column, r.filter_key
                   FROM reports r
                   WHERE r.id = %s""",
                (report_id,),
            )
            return cur.fetchone()


def db_find_report(owner_id: int, name: str):
    """같은 이름의 '살아있는' 보고서 조회. deleted 상태는 재사용 가능.

    filter_table도 같이 반환한다 — GET 필터(routes/report.py의 _build_get_filter)가
    걸린 보고서는 일반 재업로드(전체 덮어쓰기)로 데이터셋이 통째로 바뀌면
    filter_table/column이 더 이상 안 맞을 수 있어서, 호출부가 이 값으로 막는다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, owner_id, pbi_report_id, filter_table
                   FROM reports
                   WHERE owner_id = %s AND LOWER(name) = LOWER(%s) AND status <> 'deleted'""",
                (owner_id, name),
            )
            return cur.fetchone()
