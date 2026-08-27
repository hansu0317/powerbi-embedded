"""보고서 관련 테이블 초기화.

users / department_report_access / app_config 와 로그인·활동 로그는 유지한다.
Power BI 워크스페이스의 실제 파일은 영향받지 않는다.

사용법:
    python3 scripts/reset_reports.py
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DB_CONFIG = {
    "host":            os.getenv("DB_HOST", "127.0.0.1"),
    "port":            int(os.getenv("DB_PORT", "5432")),
    "dbname":          os.getenv("DB_NAME", "powerbi_gateway"),
    "user":            os.getenv("DB_USER"),
    "password":        os.getenv("DB_PASSWORD"),
    "connect_timeout": 5,
}


def reset():
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # v10에서 report_meta·report_settings·report_rls는 reports 컬럼으로,
            # report_audit_log는 event_log(log_type='audit')로 통합됐다.
            cur.execute("DELETE FROM event_log WHERE log_type = 'audit'")
            cur.execute("TRUNCATE upload_jobs RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE user_report_marks RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE user_reports RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE reports RESTART IDENTITY CASCADE")
        conn.commit()
    print("초기화 완료 (users / department_report_access / app_config / 로그인·활동 로그 유지)")


if __name__ == "__main__":
    print("보고서 관련 데이터(reports, user_reports, upload_jobs, 즐겨찾기, 감사로그)를 전부 초기화합니다.")
    print("Power BI 워크스페이스의 파일은 삭제되지 않습니다.")
    confirm = input("계속하시겠습니까? (yes 입력): ").strip().lower()
    if confirm == "yes":
        reset()
    else:
        print("취소됨")
        sys.exit(0)
