"""GET 필터를 "관계사 코드" 전용에서 범용으로 일반화 (add_company_code_filter.py 후속).

문제: user_company_codes/company_code라는 이름 자체가 이 고객사(관계사 구분) 전용이라,
다른 고객사가 공장코드·지역코드 등 다른 기준으로 필터링하고 싶으면 테이블·코드를
새로 짜야 했다. filter_key를 데이터로 다루면(문자열 하나), 같은 코드로 어떤
고객사든 대응 가능하다.

바꾸는 것
---------
1. reports.filter_key 추가 — 이 보고서가 어떤 필터 종류(예: 'company_code',
   'factory_code')를 쓰는지. filter_table/column과 세트로 다닌다.
2. user_company_codes(user_id, company_code, is_default) → user_filter_values
   (user_id, filter_key, value) — 다대다는 그대로, "무슨 코드"인지만 데이터로 뺐다.
   is_default는 실제로 쓰인 적이 없어(선택 UI 자체가 없음, 배정된 값 전부를 IN
   필터로 씀) 뺐다.
3. 기존 데이터(관계사 코드)는 filter_key='company_code'로 그대로 이관.

실행:
    python scripts/generalize_get_filter.py
"""
import os
from pathlib import Path

import psycopg2
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

DEFAULT_FILTER_KEY = "company_code"  # 기존 데이터(관계사 코드)가 이관될 filter_key


def main():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS filter_key VARCHAR(30)")
            cur.execute(
                "UPDATE reports SET filter_key = %s "
                "WHERE filter_table IS NOT NULL AND filter_key IS NULL",
                (DEFAULT_FILTER_KEY,),
            )
            print(f"reports.filter_key 채움: {cur.rowcount}건")

            cur.execute("""CREATE TABLE IF NOT EXISTS user_filter_values (
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                filter_key  VARCHAR(30) NOT NULL,
                value       VARCHAR(60) NOT NULL,
                added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, filter_key, value)
            )""")

            # user_company_codes가 있으면(이전 PoC 스크립트가 만든 것) 그대로 이관 후 정리
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'user_company_codes')"
            )
            if cur.fetchone()[0]:
                cur.execute(
                    """INSERT INTO user_filter_values (user_id, filter_key, value)
                       SELECT user_id, %s, company_code FROM user_company_codes
                       ON CONFLICT (user_id, filter_key, value) DO NOTHING""",
                    (DEFAULT_FILTER_KEY,),
                )
                print(f"user_company_codes → user_filter_values 이관: {cur.rowcount}건")
                cur.execute("DROP TABLE user_company_codes")
                print("user_company_codes 테이블 제거 (user_filter_values로 대체됨)")

        conn.commit()
    print("done")


if __name__ == "__main__":
    main()
