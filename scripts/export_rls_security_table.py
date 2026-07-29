"""동적 RLS용 보안 테이블 DDL·데이터를 생성한다 (RLS 준비 2단계).

왜 필요한가
-----------
동적 RLS는 PBIX에 역할을 하나만 두고, DAX가 USERNAME()으로 현재 사용자를 알아내
'보안 테이블'에서 그 사람의 조회 범위를 찾는 방식이다. 그런데 보안 테이블은
PBIX가 읽는 데이터 원천(SQL Server / Databricks 등)에 있어야 하고, 게이트웨이 DB에
있는 것을 Power BI가 직접 읽을 수는 없다.

그래서 이 스크립트가 다리 역할을 한다 — 게이트웨이의 users를 읽어
데이터 원천에 그대로 넣을 수 있는 SQL(또는 CSV)을 만들어 준다.

    게이트웨이 users  ──(이 스크립트)──▶  데이터 원천 보안 테이블  ──▶  PBIX DAX

사용법
------
    python3 scripts/export_rls_security_table.py            # SQL 출력 (화면)
    python3 scripts/export_rls_security_table.py --csv      # CSV 출력
    python3 scripts/export_rls_security_table.py --ddl-only # 테이블 생성문만

    python3 scripts/export_rls_security_table.py > rls.sql  # 파일로 저장 후
                                                            # 데이터 원천에서 실행

주의
----
pbi_username 값이 보안 테이블의 키가 된다. RLS를 켜기 전에 이 값을 사번 등
실제 식별자로 맞춰 두어야 한다 (기본값인 이메일 그대로면 DAX가 매칭하지 못한다).
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
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

TABLE = "rls_user_scope"

DDL = f"""-- ═══════════════════════════════════════════════════════════════
-- 보안 테이블 — 데이터 원천(SQL Server / Databricks 등)에서 실행한다.
-- PBIX가 이 테이블을 모델에 포함하고, 역할 DAX가 USERNAME()으로 조회한다.
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE {TABLE} (
    user_key    VARCHAR(255) NOT NULL,   -- USERNAME()과 매칭될 값 (사번 등)
    department  VARCHAR(60),             -- 소속
    data_scope  VARCHAR(16)  NOT NULL,   -- self | department | all
    CONSTRAINT pk_{TABLE} PRIMARY KEY (user_key)
);
"""

DAX_GUIDE = f"""
-- ═══════════════════════════════════════════════════════════════
-- PBIX 역할 DAX — Power BI Desktop → 모델링 → 역할 관리
-- 역할 이름은 하나만 만든다 (예: 도메인). 사람·부서가 늘어도 수정하지 않는다.
-- ═══════════════════════════════════════════════════════════════

-- [1] 보안 테이블 자체에 거는 필터 (본인 행만 남긴다)
--     테이블: {TABLE}
[user_key] = USERNAME()

-- [2] 실제 데이터 테이블에 거는 필터 — 조회 범위에 따라 분기
--     테이블: (예) 매출
VAR scope = LOOKUPVALUE('{TABLE}'[data_scope], '{TABLE}'[user_key], USERNAME())
VAR dept  = LOOKUPVALUE('{TABLE}'[department], '{TABLE}'[user_key], USERNAME())
RETURN
    SWITCH(
        TRUE(),
        scope = "all",        TRUE(),                  -- 전사: 필터 없음
        scope = "department", [부서] = dept,            -- 소속 부서 전체
        scope = "self",       [담당자] = USERNAME(),    -- 본인 것만
        FALSE()                                        -- 매칭 실패 시 전부 차단
    )

-- 마지막 FALSE()가 중요하다. 매핑이 없는 사용자에게 데이터가 새어 나가는 것을
-- 막는다 (빈 화면이 보이면 매핑 누락을 의심할 것).
"""


def fetch_rows():
    with psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT pbi_username AS user_key, department, data_scope, username, display_name
                   FROM users
                   WHERE is_active = TRUE
                   ORDER BY username"""
            )
            return cur.fetchall()


def sql_literal(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def main():
    ap = argparse.ArgumentParser(description="동적 RLS 보안 테이블 생성")
    ap.add_argument("--csv", action="store_true", help="INSERT문 대신 CSV로 출력")
    ap.add_argument("--ddl-only", action="store_true", help="CREATE TABLE과 DAX 안내만 출력")
    args = ap.parse_args()

    if args.ddl_only:
        print(DDL)
        print(DAX_GUIDE)
        return

    rows = fetch_rows()
    if not rows:
        print("활성 사용자가 없습니다.", file=sys.stderr)
        return

    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["user_key", "department", "data_scope"])
        for r in rows:
            w.writerow([r["user_key"], r["department"] or "", r["data_scope"]])
        return

    # 기본: 데이터 원천에 그대로 붙여 넣을 수 있는 SQL
    print(DDL)
    print(f"-- 활성 사용자 {len(rows)}명 기준으로 생성됨")
    print(f"DELETE FROM {TABLE};")
    for r in rows:
        vals = ", ".join(sql_literal(r[k]) for k in ("user_key", "department", "data_scope"))
        print(f"INSERT INTO {TABLE} (user_key, department, data_scope) VALUES ({vals});"
              f"   -- {r['username']} ({r['display_name']})")
    print(DAX_GUIDE)

    # 점검 — 준비가 덜 된 항목을 미리 알려준다
    warn = [r for r in rows if "@" in str(r["user_key"])]
    if warn:
        print(f"\n-- ⚠ 주의: pbi_username이 이메일 형태인 사용자 {len(warn)}명 "
              f"({', '.join(r['username'] for r in warn[:5])}"
              f"{' 외' if len(warn) > 5 else ''}).", file=sys.stderr)
        print("--   보안 테이블 키(사번 등)로 바꾸지 않으면 DAX가 매칭하지 못한다.", file=sys.stderr)
    nodept = [r for r in rows if r["data_scope"] == "department" and not r["department"]]
    if nodept:
        print(f"-- ⚠ 주의: data_scope='department'인데 department가 비어 있는 사용자 "
              f"{len(nodept)}명. 아무 데이터도 보이지 않는다.", file=sys.stderr)


if __name__ == "__main__":
    main()
