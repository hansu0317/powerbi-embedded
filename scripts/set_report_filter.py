"""보고서 하나에 GET 필터(테이블/컬럼)를 지정한다.

reports.filter_table/filter_column은 PBIX마다 다를 수 있어(관계사 코드,
공장 코드 등) 자동으로 알아낼 방법이 없다 — Power BI Desktop에서 그 PBIX를
직접 열어 확인한 값을 여기 넣어준다.

사용법:
    python scripts/set_report_filter.py "회계원가" DimPartner CompanyCode
    python scripts/set_report_filter.py "회계원가" --clear   # 필터 해제
"""
import argparse
import os
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


def main():
    ap = argparse.ArgumentParser(description="보고서 GET 필터(테이블/컬럼) 지정")
    ap.add_argument("report_name", help="reports.name (관리자 포털에 보이는 이름)")
    ap.add_argument("table", nargs="?", help="필터 대상 테이블명 (예: DimPartner)")
    ap.add_argument("column", nargs="?", help="필터 대상 컬럼명 (예: CompanyCode)")
    ap.add_argument("--clear", action="store_true", help="필터 해제 (NULL로 되돌림)")
    args = ap.parse_args()

    if not args.clear and (not args.table or not args.column):
        ap.error("table/column을 지정하거나 --clear를 쓰세요")

    with psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, category FROM reports WHERE name = %s", (args.report_name,))
            rows = cur.fetchall()
            if not rows:
                print(f"'{args.report_name}' 이름의 보고서를 찾을 수 없습니다.")
                return
            if len(rows) > 1:
                print(f"이름이 같은 보고서가 {len(rows)}개입니다 — id로 직접 확인하세요:")
                for r in rows:
                    print(f"  id={r['id']} category={r['category']}")
                return

            report_id = rows[0]["id"]
            table_val  = None if args.clear else args.table
            column_val = None if args.clear else args.column
            cur.execute(
                "UPDATE reports SET filter_table = %s, filter_column = %s WHERE id = %s",
                (table_val, column_val, report_id),
            )
        conn.commit()

    if args.clear:
        print(f"'{args.report_name}' 필터 해제 완료.")
    else:
        print(f"'{args.report_name}' → {args.table}/{args.column} 필터 지정 완료.")


if __name__ == "__main__":
    main()
