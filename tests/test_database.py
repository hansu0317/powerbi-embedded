"""Run real SQL in connection-local PostgreSQL temporary tables; never commit."""
from contextlib import contextmanager
import unittest
from unittest.mock import patch

import psycopg2
from psycopg2.extras import RealDictCursor
import config
from database import admin, reports
from scripts import init_schema


class PermissionSQLTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(**config.DB_CONFIG, cursor_factory=RealDictCursor)
        cur = self.conn.cursor()
        # Only temp tables are visible. No query can resolve to production tables.
        cur.execute("SET LOCAL search_path TO pg_temp")
        for stmt in init_schema.TABLES:
            cur.execute(stmt.replace("CREATE TABLE IF NOT EXISTS", "CREATE TEMP TABLE IF NOT EXISTS"))
        for stmt in init_schema.MIGRATIONS + init_schema.INDEXES:
            cur.execute(stmt)
        cur.execute("""INSERT INTO users(id,username,password,display_name,pbi_username,department,is_admin,is_active)
          VALUES (1,'viewer','unused','Viewer','viewer','Sales',false,true),
                 (2,'admin','unused','Admin','admin',null,true,true),
                 (3,'disabled','unused','Disabled','disabled','Sales',false,false)""")
        cur.execute("""INSERT INTO reports(id,name,report_type,visibility,owner_id,status) VALUES
          (1,'No grant','managed','personal',null,'active'),
          (2,'Direct','managed','personal',null,'active'),
          (3,'Department','managed','personal',null,'active'),
          (4,'Shared','managed','shared',null,'active'),
          (5,'Owner','personal','personal',1,'active'),
          (6,'Denied department','managed','personal',null,'active'),
          (7,'Denied shared','managed','shared',null,'active'),
          (8,'Deleted','managed','shared',null,'deleted')""")
        cur.execute("INSERT INTO user_reports(user_id,report_id,can_view) VALUES(1,2,true),(1,6,false),(1,7,false)")
        cur.execute("INSERT INTO department_report_access(department,report_id) VALUES('Sales',3),('Sales',6)")

        @contextmanager
        def connection():
            yield self.conn
        self.patches = [patch.object(m, "db_conn", connection) for m in (admin, reports)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.conn.rollback()
        self.conn.close()

    def test_user_list_single_check_and_admin_popup_agree(self):
        expected = {2, 3, 4, 5}
        self.assertEqual({r["id"] for r in reports.db_get_reports("viewer")}, expected)
        self.assertEqual({i for i in range(1, 9) if reports.db_can_view_report("viewer", i)}, expected)
        self.assertEqual({r["id"] for r in admin.db_get_user_report_list(1)}, expected)
        for i in range(1, 9):
            with self.subTest(report=i):
                access = {r["id"]: r for r in admin.db_get_report_access(i)}
                self.assertEqual(access[1]["can_view"], i in expected)
        users = {u["id"]: u for u in admin.db_admin_get_users()}
        self.assertEqual(users[1]["report_count"], 4)
        self.assertEqual(users[2]["report_count"], 7)
        self.assertEqual(users[3]["report_count"], 0)

    def test_admin_and_disabled_accounts(self):
        self.assertEqual(len(admin.db_get_user_report_list(2)), 7)
        self.assertEqual(admin.db_get_user_report_list(3), [])
        self.assertEqual(reports.db_get_reports("disabled"), [])
        self.assertFalse(reports.db_can_view_report("disabled", 4))

    def test_schema_rerun_preserves_shared_and_legacy_objects(self):
        cur = self.conn.cursor()
        cur.execute("CREATE TEMP TABLE groups(id int)")
        cur.execute("ALTER TABLE users ADD COLUMN company_code text")
        cur.execute("INSERT INTO groups VALUES(1)")
        for stmt in init_schema.MIGRATIONS + init_schema.INDEXES:
            cur.execute(stmt)
        cur.execute("SELECT visibility FROM reports WHERE id=4")
        self.assertEqual(cur.fetchone()["visibility"], "shared")
        cur.execute("SELECT COUNT(*) AS n FROM groups")
        self.assertEqual(cur.fetchone()["n"], 1)
        cur.execute("SELECT email, company_code FROM users LIMIT 1")
        self.assertIn("email", cur.fetchone())


if __name__ == "__main__":
    unittest.main()
