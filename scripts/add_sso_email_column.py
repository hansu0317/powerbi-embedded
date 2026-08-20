"""MS 계정 로그인(SSO) 컬럼 추가 — users.email.

배경: SSO 로그인은 Microsoft가 돌려준 이메일로 users 테이블에서 계정을 찾는다.
기존 pbi_username은 RLS 보안 테이블 키(사번 등일 수 있음, scripts/export_rls_security_table.py
참고)라 이메일이라는 보장이 없어 그대로 쓸 수 없다 — 그래서 전용 컬럼을 하나 둔다.

값이 비어 있는(NULL) 계정은 그냥 SSO 로그인 대상이 아닐 뿐, 비밀번호 로그인은 지금처럼
그대로 동작한다 — 계정별로 관리자가 채워주는 opt-in 방식(2026-08-20).

실행 방법 (재실행해도 안전 — 전부 IF NOT EXISTS):
    python scripts/add_sso_email_column.py
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
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)"
            )
            # 이메일이 채워진 계정끼리는 중복을 막는다(같은 MS 계정이 두 DB 계정에
            # 매칭되면 어느 쪽으로 로그인될지 모호해짐). NULL은 여러 개 허용해야
            # 해서(대부분 계정이 당분간 NULL) 일반 UNIQUE 대신 부분 인덱스를 쓴다.
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique_idx "
                "ON users (LOWER(email)) WHERE email IS NOT NULL"
            )
        conn.commit()
    # 한글 안 쓰는 이유는 scripts/add_get_filter_columns.py의 같은 print() 옆 주석 참고
    # (conhost 폭 재계산 버그, chcp로도 못 고침, 2026-08-20).
    print("SSO login column: OK (users.email added, left empty, admin fills in per user).")


if __name__ == "__main__":
    run()
