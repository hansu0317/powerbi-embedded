"""PostgreSQL 접근 계층 — 도메인별 서브모듈을 하나의 database 패키지로 묶는다.

바깥에서는 예전처럼 `from database import db_get_user` 식으로 쓰면 되고, 실제 구현은
아래 서브모듈에 나뉘어 있다 (파일당 ~600줄을 넘기지 않도록 도메인 단위로 분리했다).
새 함수를 추가할 땐 알맞은 서브모듈에 넣고 여기 재노출만 추가하면 된다.

    pool.py      커넥션 풀 + db_conn 컨텍스트 매니저 (다른 모든 모듈이 이걸 쓴다)
    auth.py      로그인 인증 + 세션 사용자 조회
    reports.py   보고서 열람 목록·즐겨찾기·최근본 (+ _CAN_VIEW_REPORT_SQL 단일 소스)
    uploads.py   업로드 잡 상태 머신 + Fabric 동기화 보조
    admin.py     관리자 포털 전용 (사용자·보고서 관리, PBI 가져오기, 런타임 설정)
    groups.py    그룹(팀/부서 단위 권한)
    activity.py  활동 로그 · 감사 로그 · 인기 보고서 집계
"""
from database.pool import db_conn, db_health_check
from database.auth import (
    db_check_and_get_user, db_verify_password, db_record_login,
    db_cleanup_login_attempts, db_get_user,
    db_get_user_by_email, db_sso_record_login,
)
from database.reports import (
    db_get_reports, db_get_all_active_reports, db_get_user_favorites, db_set_favorite,
    db_get_user_recents, db_add_recent, db_get_pbi_report_map, db_hard_delete_report,
    db_can_view_report, db_get_report, db_find_report,
)
from database.uploads import (
    db_reserve_update, db_reserve_upload, db_count_other_reports_using_dataset,
    db_fail_stuck_upload_job, db_fail_stale_publishing_jobs, db_get_upload_job,
    db_update_upload_job, db_register_report, db_get_synced_reports,
    db_mark_report_deleted, db_restore_report, db_get_pending_imports,
    db_get_recoverable_jobs,
)
from database.admin import (
    db_admin_get_stats, db_admin_get_users, db_get_user_report_list, db_admin_add_user,
    db_admin_update_user, db_admin_toggle_user_active, db_admin_toggle_user_upload,
    db_admin_get_reports, db_import_pbi_item, db_admin_soft_delete_report,
    db_admin_set_report_visibility,
    db_admin_get_upload_jobs, db_get_report_access, db_set_report_access,
    db_get_app_config, db_update_app_config,
)
from database.groups import (
    db_admin_get_groups, db_admin_create_group, db_admin_delete_group,
    db_get_group_members, db_set_group_member, db_get_report_group_access,
    db_set_report_group_access,
)
from database.activity import (
    db_log_activity, db_get_activity_log, db_get_user_activity_log, db_get_audit_log,
    db_cleanup_activity_log, db_get_popular_report_ids,
)
from database.folders import (
    db_get_report_folders, db_get_writable_folders, db_get_folder, db_move_report_to_folder,
    db_can_write_folder, db_ensure_folder_path,
)

__all__ = [
    "db_conn", "db_health_check",
    "db_check_and_get_user", "db_verify_password", "db_record_login",
    "db_cleanup_login_attempts", "db_get_user",
    "db_get_user_by_email", "db_sso_record_login",
    "db_get_reports", "db_get_all_active_reports", "db_get_user_favorites", "db_set_favorite",
    "db_get_user_recents", "db_add_recent", "db_get_pbi_report_map", "db_hard_delete_report",
    "db_can_view_report", "db_get_report", "db_find_report",
    "db_reserve_update", "db_reserve_upload", "db_count_other_reports_using_dataset",
    "db_fail_stuck_upload_job", "db_fail_stale_publishing_jobs", "db_get_upload_job",
    "db_update_upload_job", "db_register_report", "db_get_synced_reports",
    "db_mark_report_deleted", "db_restore_report", "db_get_pending_imports",
    "db_get_recoverable_jobs",
    "db_admin_get_stats", "db_admin_get_users", "db_get_user_report_list", "db_admin_add_user",
    "db_admin_update_user", "db_admin_toggle_user_active", "db_admin_toggle_user_upload",
    "db_admin_get_reports", "db_import_pbi_item", "db_admin_soft_delete_report",
    "db_admin_set_report_visibility",
    "db_admin_get_upload_jobs", "db_get_report_access", "db_set_report_access",
    "db_get_app_config", "db_update_app_config",
    "db_admin_get_groups", "db_admin_create_group", "db_admin_delete_group",
    "db_get_group_members", "db_set_group_member", "db_get_report_group_access",
    "db_set_report_group_access",
    "db_log_activity", "db_get_activity_log", "db_get_user_activity_log", "db_get_audit_log",
    "db_cleanup_activity_log", "db_get_popular_report_ids",
    "db_get_report_folders", "db_get_writable_folders", "db_get_folder", "db_move_report_to_folder",
    "db_can_write_folder", "db_ensure_folder_path",
]
