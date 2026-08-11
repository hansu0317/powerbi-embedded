"""포털 보고서 폴더 — 소유권과 화면 위치를 분리한다."""
from database.pool import db_conn

def db_get_report_folders(user_id: int, is_admin: bool) -> list:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT f.id,f.name,f.parent_id,f.owner_id,f.visibility,f.fabric_folder_id,
                                  u.username AS owner_username,
                                  (SELECT COUNT(*) FROM reports r WHERE r.portal_folder_id=f.id AND r.status='active') AS report_count
                           FROM report_folders f LEFT JOIN users u ON u.id=f.owner_id
                           WHERE %s OR f.visibility='shared' OR f.owner_id=%s
                              OR (f.visibility='group' AND EXISTS (
                                  SELECT 1 FROM user_groups me JOIN user_groups owner_group USING(group_id)
                                  WHERE me.user_id=%s AND owner_group.user_id=f.owner_id))
                           ORDER BY f.parent_id NULLS FIRST,f.name""", (is_admin,user_id,user_id))
            return cur.fetchall()

def db_create_report_folder(name: str, parent_id: int | None, owner_id: int | None,
                            visibility: str, actor_id: int) -> dict:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO report_folders(name,parent_id,owner_id,visibility,created_by)
                           VALUES(%s,%s,%s,%s,%s) RETURNING id,name,parent_id,owner_id,visibility""",
                        (name,parent_id,owner_id,visibility,actor_id))
            row=cur.fetchone()
        conn.commit()
    return row

def db_update_report_folder(folder_id: int, name: str, parent_id: int | None,
                            visibility: str, actor_id: int, is_admin: bool) -> dict | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE report_folders SET name=%s,parent_id=%s,visibility=%s,updated_at=NOW()
                           WHERE id=%s AND (%s OR owner_id=%s)
                             AND (%s IS NULL OR %s<>id)
                           RETURNING id,name,parent_id,owner_id,visibility""",
                        (name,parent_id,visibility,folder_id,is_admin,actor_id,parent_id,parent_id))
            row=cur.fetchone()
            if row:
                cur.execute("UPDATE reports SET category=%s,visibility=%s,updated_at=NOW() WHERE portal_folder_id=%s",
                            (name,visibility,folder_id))
        conn.commit()
    return row

def db_delete_report_folder(folder_id: int, actor_id: int, is_admin: bool) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""DELETE FROM report_folders f WHERE f.id=%s AND (%s OR f.owner_id=%s)
                           AND NOT EXISTS(SELECT 1 FROM reports r WHERE r.portal_folder_id=f.id AND r.status='active')
                           AND NOT EXISTS(SELECT 1 FROM report_folders c WHERE c.parent_id=f.id)
                           RETURNING id""", (folder_id,is_admin,actor_id))
            ok=cur.fetchone() is not None
        conn.commit()
    return ok

def db_get_folder(folder_id: int) -> dict | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,parent_id,owner_id,visibility,fabric_folder_id FROM report_folders WHERE id=%s",(folder_id,))
            return cur.fetchone()

def db_move_report_to_folder(report_id: int, folder_id: int, actor_id: int, is_admin: bool) -> dict | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,visibility FROM report_folders WHERE id=%s AND (%s OR owner_id=%s OR visibility='shared')",
                        (folder_id,is_admin,actor_id))
            folder=cur.fetchone()
            if not folder: return None
            cur.execute("""UPDATE reports SET portal_folder_id=%s,category=%s,visibility=%s,updated_by=%s,updated_at=NOW()
                           WHERE id=%s AND (%s OR owner_id=%s) RETURNING id,pbi_report_id,pbi_workspace_id""",
                        (folder_id,folder['name'],folder['visibility'],actor_id,report_id,is_admin,actor_id))
            row=cur.fetchone()
        conn.commit()
    return row
