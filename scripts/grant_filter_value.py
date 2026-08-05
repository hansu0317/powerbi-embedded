"""사용자에게 GET 필터 값을 배정/회수한다 (set_report_filter.py의 짝).

한 사용자가 같은 filter_key에 여러 값을 가질 수 있다(예: 서진오토모티브=AMT,
에코플라스틱=ECO 둘 다 소속) — 그런 사용자는 배정된 값 전부가 IN 필터로 한꺼번에
적용된다(선택 UI 없음, routes/report.py의 _build_get_filter 참고).

사용법:
    python scripts/grant_filter_value.py dev1 company_code AMT
    python scripts/grant_filter_value.py dev1 company_code AMT --revoke
    python scripts/grant_filter_value.py dev1 --list                    # 이 사용자의 배정 전체 조회
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
    ap = argparse.ArgumentParser(description="사용자에게 GET 필터 값 배정/회수")
    ap.add_argument("username", help="users.username")
    ap.add_argument("filter_key", nargs="?", help="필터 종류 (예: company_code)")
    ap.add_argument("value", nargs="?", help="배정할 값 (예: AMT)")
    ap.add_argument("--revoke", action="store_true", help="배정 회수")
    ap.add_argument("--list", action="store_true", help="이 사용자의 배정 전체 조회")
    args = ap.parse_args()

    if not args.list and (not args.filter_key or not args.value):
        ap.error("filter_key/value를 지정하거나 --list를 쓰세요")

    with psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (args.username,))
            row = cur.fetchone()
            if not row:
                print(f"'{args.username}' 사용자를 찾을 수 없습니다.")
                return
            user_id = row["id"]

            if args.list:
                cur.execute(
                    "SELECT filter_key, value FROM user_filter_values WHERE user_id = %s ORDER BY filter_key, value",
                    (user_id,),
                )
                rows = cur.fetchall()
                if not rows:
                    print(f"'{args.username}' 배정된 필터 값 없음.")
                for r in rows:
                    print(f"  {r['filter_key']} = {r['value']}")
                return

            if args.revoke:
                cur.execute(
                    "DELETE FROM user_filter_values WHERE user_id = %s AND filter_key = %s AND value = %s",
                    (user_id, args.filter_key, args.value),
                )
                conn.commit()
                print(f"'{args.username}': {args.filter_key}={args.value} 회수 "
                      f"({'완료' if cur.rowcount else '원래 없었음'}).")
            else:
                cur.execute(
                    """INSERT INTO user_filter_values (user_id, filter_key, value)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    (user_id, args.filter_key, args.value),
                )
                conn.commit()
                print(f"'{args.username}': {args.filter_key}={args.value} 배정 "
                      f"({'완료' if cur.rowcount else '이미 있었음'}).")


if __name__ == "__main__":
    main()
