# 데이터베이스 설계

## 설계 원칙

- 스키마는 `scripts/migrate_report_meta.py` 한 곳에서 관리 (멱등, 서버 시작 전 자동 실행)
- 물리 삭제 없음 — 보고서는 `status='deleted'`로 소프트 삭제, 이력 영구 보존
- 보고서 ID는 전부 PostgreSQL에서 관리 (`.env`에 두지 않음)

---

## 테이블 10개

### 핵심 (보고서 정보)

| 테이블 | 역할 |
|---|---|
| `reports` | 보고서 신분·유형(managed/personal)·상태·소유권 |
| `report_meta` | Fabric ID 연결 (pbi_report_id, workspace_id, dataset_id, folder_id) |
| `report_settings` | 화면 옵션 (기본 페이지, 필터 창, 페이지 탐색, 탭 유형 등) |
| `report_rls` | RLS 정책 (enabled, role_names 배열) |

### 권한

| 테이블 | 역할 |
|---|---|
| `users` | 계정 (bcrypt 해시, is_active, last_login_at, is_admin) |
| `user_reports` | 사용자 ↔ 보고서 다대다 권한 (can_view, can_edit, can_manage) |

### 이력·운영

| 테이블 | 역할 |
|---|---|
| `upload_jobs` | 개인 업로드 상태 머신 (publishing → accepted → fabric_succeeded → completed) |
| `report_views` | 열람 이력 (인기 보고서 분석용) |
| `report_audit_log` | 보고서 등록·삭제·복구 감사 이력 |
| `login_attempts` | 로그인 시도 기록 (15분 5회 실패 차단) |

---

## 주요 설계 결정

**reports 이름 유일성**
- managed: `LOWER(name)` 전체 유일
- personal: `(owner_id, LOWER(name))` 유일 → user01/test01과 user02/test01 동시 가능

**Fabric 이름 충돌 vs DB 이름**
Fabric은 워크스페이스 전체에서 표시 이름이 유일해야 한다.
게이트웨이 화면에는 항상 DB의 `reports.name`이 표시되므로 사용자 경험은 동일하다.
업로드 시 내부 이름(`사용자__이름__uuid`)으로 올린 뒤 이름을 변경한다.

**소프트 삭제 이유**
물리 삭제 시 `user_reports`, `report_audit_log`까지 CASCADE로 사라진다.
`status='deleted'`로 두면 권한·감사 이력 보존 + 재업로드 시 `active`로 복구 가능.

---

## ERD

```mermaid
erDiagram
    users {
        int     id              PK
        varchar username        UK
        varchar password
        varchar display_name
        varchar pbi_username
        text    roles
        boolean is_admin
        boolean is_active
        timestamptz created_at
        timestamptz updated_at
        timestamptz last_login_at
    }

    reports {
        int     id              PK
        varchar name
        varchar report_type
        int     owner_id        FK
        varchar status
        timestamptz deleted_at
        int     created_by      FK
        int     updated_by      FK
        timestamptz created_at
        timestamptz updated_at
    }

    report_meta {
        int     report_id       PK  FK
        varchar pbi_report_id   UK
        varchar pbi_workspace_id
        varchar pbi_dataset_id
        varchar fabric_folder_id
        timestamptz created_at
        timestamptz updated_at
    }

    report_settings {
        int     report_id       PK  FK
        varchar default_page
        boolean enable_filter
        boolean enable_page_nav
        boolean use_data_bot
        text    preview_image_url
        varchar tab_type
        timestamptz updated_at
    }

    report_rls {
        int     report_id       PK  FK
        boolean enabled
        text[]  role_names
        timestamptz updated_at
    }

    user_reports {
        int     user_id         PK  FK
        int     report_id       PK  FK
        boolean can_view
        boolean can_edit
        boolean can_manage
        int     granted_by      FK
        timestamptz granted_at
    }

    upload_jobs {
        bigint  id              PK
        int     user_id         FK
        int     report_id       FK
        varchar report_name
        varchar status
        varchar import_id
        varchar pbi_report_id
        text    error_message
        timestamptz created_at
        timestamptz updated_at
    }

    report_views {
        bigint  id              PK
        int     report_id       FK
        int     user_id         FK
        timestamptz viewed_at
    }

    report_audit_log {
        bigint  id              PK
        int     report_id       FK
        int     actor_user_id   FK
        varchar action
        jsonb   details
        timestamptz created_at
    }

    login_attempts {
        bigint  id              PK
        varchar username
        varchar ip_address
        boolean succeeded
        timestamptz attempted_at
    }

    users        ||--o{ reports          : "owns"
    reports      ||--|| report_meta      : "has"
    reports      ||--|| report_settings  : "has"
    reports      ||--|| report_rls       : "has"
    users        ||--o{ user_reports     : "granted to"
    reports      ||--o{ user_reports     : "accessed by"
    users        ||--o{ upload_jobs      : "uploads"
    reports      ||--o{ upload_jobs      : "created by"
    reports      ||--o{ report_views     : "viewed in"
    users        ||--o{ report_views     : "views"
    reports      ||--o{ report_audit_log : "logged"
    users        ||--o{ report_audit_log : "acted by"
```

---

## 핵심 관계 요약

| 관계 | 설명 |
|---|---|
| `users → reports` (owner_id) | 개인 보고서 소유권. managed는 NULL |
| `reports → report_meta` | 1:1, Fabric ID 연결. CASCADE 삭제 |
| `reports → report_settings/rls` | 1:1, 화면 옵션과 RLS 정책 |
| `users ↔ reports` (user_reports) | 다:다, 열람 권한 매핑 |
| `upload_jobs → reports` | 완료 후 어떤 작업이 어느 보고서를 만들었는지 |
| `report_views` | 열람 이력 — 누가 언제 무엇을 봤는지 |
| `report_audit_log` | 변경 이력 — 등록·삭제·복구 |
