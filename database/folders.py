"""포털 보고서 폴더 — 소유권과 화면 위치를 분리한다."""
from database.pool import db_conn

# 폴더를 "볼 수 있는가"와 "그 안에 보고서를 넣을 수 있는가"에 같은 규칙을 쓴다:
# 관리자이거나 / 본인 소유이거나 / shared이거나 / 같은 그룹(owner 기준)이면 된다.
# f는 대상 report_folders 행의 별칭 — 이 상수를 쓰는 쿼리는 항상 FROM report_folders f를 가져야 한다.
# 자리표시자 순서: is_admin, user_id(owner_id 비교), user_id(그룹 비교) — 총 3개, 호출부마다 이 순서로 넘긴다.
_CAN_WRITE_FOLDER_SQL = """(
    %s OR f.owner_id=%s OR f.visibility='shared'
    OR (f.visibility='group' AND EXISTS (
        SELECT 1 FROM user_groups me JOIN user_groups owner_group USING(group_id)
        WHERE me.user_id=%s AND owner_group.user_id=f.owner_id))
)"""


def db_get_report_folders(user_id: int, is_admin: bool) -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT f.id,f.name,f.parent_id,f.owner_id,f.visibility,f.fabric_folder_id,
                                  u.username AS owner_username,
                                  (SELECT COUNT(*) FROM reports r WHERE r.portal_folder_id=f.id AND r.status='active') AS report_count
                           FROM report_folders f LEFT JOIN users u ON u.id=f.owner_id
                           WHERE {_CAN_WRITE_FOLDER_SQL}
                           ORDER BY f.parent_id NULLS FIRST,f.name""", (is_admin,user_id,user_id))
            return cur.fetchall()


def db_can_write_folder(folder_id: int, user_id: int, is_admin: bool) -> bool:
    """이 폴더에 새 보고서를 등록/이동해도 되는지 — 업로드(routes/report.py)에서 쓴다."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM report_folders f WHERE f.id=%s AND {_CAN_WRITE_FOLDER_SQL}",
                        (folder_id, is_admin, user_id, user_id))
            return cur.fetchone() is not None

def db_get_folder(folder_id: int) -> dict | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,parent_id,owner_id,visibility,fabric_folder_id FROM report_folders WHERE id=%s",(folder_id,))
            return cur.fetchone()

def db_move_report_to_folder(report_id: int, folder_id: int, actor_id: int, is_admin: bool) -> dict | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id,name,visibility FROM report_folders f WHERE id=%s AND {_CAN_WRITE_FOLDER_SQL}",
                        (folder_id,is_admin,actor_id,actor_id))
            folder=cur.fetchone()
            if not folder: return None
            cur.execute("""UPDATE reports SET portal_folder_id=%s,category=%s,visibility=%s,updated_by=%s,updated_at=NOW()
                           WHERE id=%s AND (%s OR owner_id=%s) RETURNING id,pbi_report_id,pbi_workspace_id""",
                        (folder_id,folder['name'],folder['visibility'],actor_id,report_id,is_admin,actor_id))
            row=cur.fetchone()
        conn.commit()
    return row
