"""현재 PostgreSQL public 스키마의 사용자 테이블을 읽기 전용으로 출력한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.pool import db_conn


def main() -> None:
    with db_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT table_name
                   FROM information_schema.tables
                   WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                   ORDER BY table_name"""
            )
            tables = [row["table_name"] for row in cursor.fetchall()]
    print(f"COUNT {len(tables)}")
    print("\n".join(tables))


if __name__ == "__main__":
    main()
