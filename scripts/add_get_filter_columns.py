"""GET 필터(PoC) 컬럼 추가 — users.filter_key/filter_value, reports.filter_table/
filter_column/filter_key.

배경: 이 5개 컬럼은 routes/report.py::_build_get_filter와 database/auth.py::db_get_user가
이미 SQL에서 직접 참조하고 있어 앱 동작에 필수인데(로그인 자체가 이 컬럼들을 SELECT함),
scripts/init_schema.py에는 처음부터 빠져 있었다 — 과거 어느 시점에 psql로 직접
ALTER TABLE만 실행하고 스크립트로 남기지 않은 것으로 보인다(2026-08-12 발견).

증상: 기존 DB에서는 컬럼이 이미 있어 안 드러나지만, init_schema.py만으로 새로 만든
DB(예: 새 Windows PC로 포팅)에서는 로그인 시도 즉시
`column "filter_key" does not exist`로 서버가 죽는다.

SECO 자체는 이제 이 GET 필터를 쓰지 않는다(department/data_scope 동적 RLS로 대체,
docs/08_GET_필터_방식_참고.md 참고) — 그래도 컬럼·코드는 로그인 정상 동작을 위해
필요하고, 로그인 없는 완전 공개 임베드가 필요한 다른 고객사가 생기면 그대로 재사용한다.

실행 전 백업 권장:
    pg_dump -h 127.0.0.1 -U <계정> -d powerbi_gateway -F c -f backups/before_get_filter_columns.dump

실행 방법 (재실행해도 안전 — 전부 IF NOT EXISTS):
    python scripts/add_get_filter_columns.py
"""
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "powerbi_gateway"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
}


def run():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_key VARCHAR(30)"
            )
            cur.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_value VARCHAR(60)"
            )
            cur.execute(
                "ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_table VARCHAR(120)"
            )
            cur.execute(
                "ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_column VARCHAR(120)"
            )
            cur.execute(
                "ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_key VARCHAR(30)"
            )
        conn.commit()
    # 한글은 일부러 안 쓴다 — 이 줄은 server.ps1/server.sh가 리다이렉트 없이 콘솔에
    # 그대로 찍는 유일한 지점이라, 한글(2칸 폭 문자)을 쓰면 구형 콘솔 창(conhost)의
    # 폭 재계산 버그로 "필필터터"처럼 글자가 겹쳐 찍힌다(2026-08-20, chcp 65001로도
    # 못 고침 — 코드페이지가 아니라 conhost 렌더링 자체의 버그라서). 상세 로그가
    # 필요하면 logs/server.log를 본다(이 스크립트 결과는 거기 안 남지만, 앱 자체
    # 로그는 전부 파일로 감).
    print("GET filter (PoC) columns: OK (5 columns added, left empty, SECO unused).")


if __name__ == "__main__":
    run()
