# 운영 가이드

## 보고서 유형

| 유형 | report_type | 소유 | 권한 부여 방식 |
|---|---|---|---|
| 공용 보고서 | `managed` | NULL | 관리자가 SQL로 지정 |
| 개인 보고서 | `personal` | 업로드한 사용자 | 소유자 + `is_admin=true` 관리자 자동 |

---

## 서버 운영

```bash
# 개발/테스트
bash scripts/server.sh start
bash scripts/server.sh status
bash scripts/server.sh restart
bash scripts/server.sh stop

# 상태 확인
curl http://127.0.0.1:8082/health
tail -f logs/server.log
```

서버 시작 시 스키마 마이그레이션이 자동 실행된다. 실패하면 서버가 시작되지 않는다.

---

## 최초 설치 (새 서버)

```bash
sudo systemctl start postgresql
python3 scripts/migrate_report_meta.py

# 관리자 비밀번호 해시 생성
python3 -c "import bcrypt; print(bcrypt.hashpw(b'비밀번호', bcrypt.gensalt()).decode())"
```

```sql
-- 관리자 계정 등록
INSERT INTO users (username, password, display_name, pbi_username, roles, is_admin)
VALUES ('admin', '$2b$12$...', '관리자', 'admin@회사도메인', '도메인', TRUE);
```

---

## 사용자·권한 관리

```sql
-- 사용자 추가
INSERT INTO users (username, password, display_name, pbi_username, roles, is_admin)
VALUES ('user01', '$2b$12$...', '김생산', 'user01@회사도메인', '도메인', FALSE);

-- 보고서 열람 권한 부여
INSERT INTO user_reports (user_id, report_id, can_view, granted_by, granted_at)
SELECT u.id, r.id, TRUE, 1, NOW()
FROM users u, reports r
WHERE u.username = 'user01' AND r.name = '생산현황';

-- 권한 회수
DELETE FROM user_reports
WHERE user_id = (SELECT id FROM users WHERE username='user01')
  AND report_id = (SELECT id FROM reports WHERE name='생산현황');

-- 계정 비활성화 (삭제 없이)
UPDATE users SET is_active = FALSE WHERE username = 'user01';
```

---

## 현황 조회 SQL

```sql
-- 보고서 목록 + 권한 현황
SELECT r.id, r.name, r.report_type, r.status,
       owner.username AS owner, viewer.username AS viewer
FROM reports r
LEFT JOIN users owner  ON owner.id = r.owner_id
LEFT JOIN user_reports ur ON ur.report_id = r.id
LEFT JOIN users viewer ON viewer.id = ur.user_id
ORDER BY r.id, viewer.username;

-- 업로드 이력
SELECT j.id, u.username, j.report_name, j.status, j.error_message, j.created_at
FROM upload_jobs j
JOIN users u ON u.id = j.user_id
ORDER BY j.id DESC;

-- 열람 이력 (인기 보고서)
SELECT r.name, COUNT(*) AS views
FROM report_views rv
JOIN reports r ON r.id = rv.report_id
GROUP BY r.name ORDER BY views DESC;
```

---

## 공용 보고서 게시 (관리자 CLI)

```bash
python3 scripts/publish_pbix.py scripts/pbxifile/생산현황.pbix
```

완료 후 출력되는 SQL로 사용자 권한을 부여한다.

---

## 기존 Fabric 보고서 일괄 등록 (`import_fabric_reports.py`)

서버를 처음 구축할 때 이미 Fabric에 있는 보고서를 DB에 등록한다.

```bash
# 미리보기 (DB 변경 없음)
python3 scripts/import_fabric_reports.py --dry-run

# SECO 폴더 보고서만 가져오기
python3 scripts/import_fabric_reports.py --folder SECO

# 가져오면서 user01에게 권한 부여
python3 scripts/import_fabric_reports.py --folder SECO --grant user01
```

---

## Fabric 삭제 동기화

Fabric에서 보고서를 삭제하면 자동으로 DB에 반영된다.

- 서버 시작 시 1회 + 10분 주기로 자동 실행
- 열람 시 Fabric 404 응답 → 즉시 처리
- 수동 즉시 실행: `POST /api/admin/sync-fabric`

---

## 업로드 복구

업로드 도중 서버가 재시작되면 자동 복구된다.

```sql
-- 현재 업로드 상태 확인
SELECT id, report_name, status, error_message, updated_at
FROM upload_jobs WHERE status NOT IN ('completed', 'failed', 'conflict')
ORDER BY id DESC;
```

- `accepted` 상태로 멈춘 것: 재시작 시 Fabric Import 상태 재조회 후 이어서 진행
- `db_failed` 상태: 재시작 시 DB 등록 자동 재시도
- `unknown` 상태: Fabric을 직접 확인 후 관리자가 정리 (`conflict`로 수동 업데이트)

---

## 로그와 백업

```bash
# 실시간 로그
tail -f logs/server.log

# 재시작 시 이전 로그 위치: logs/YYYYMMDD/server-HHMMSS.log (30일 보관)

# DB 백업
pg_dump -Fc powerbi_gateway > powerbi_gateway_$(date +%Y%m%d).dump
```

---

## 운영 설정 (`.env`)

```ini
SECRET_KEY=32자-이상-랜덤-문자열      # 필수, 미달 시 서버 시작 안 됨
COOKIE_SECURE=false                   # HTTPS 적용 후 true로 변경
MAX_UPLOADS_PER_DAY=10               # 사용자당 일 업로드 횟수 제한
MAX_PERSONAL_REPORTS=20              # 사용자당 개인 보고서 총 개수 제한
FABRIC_SYNC_INTERVAL=600             # Fabric 동기화 주기 (초, 0=끔)
```

외부 공개 시 nginx 리버스 프록시 설정:
```nginx
client_max_body_size 1025m;
proxy_read_timeout 360s;
```

---

## 제한사항 및 주의사항

### Fabric 이름 충돌
Fabric은 워크스페이스 전체에서 보고서 이름이 유일해야 한다 (폴더와 무관).

| 상황 | 결과 |
|---|---|
| user01이 `생산일보.pbix` 업로드 | ✅ 성공 |
| user02가 `생산일보.pbix` 업로드 | ❌ 충돌 — 다른 이름으로 재시도 |
| user01이 삭제 후 user02가 업로드 | ✅ 성공 |

충돌 시 방금 올린 Fabric 항목은 자동 롤백(삭제)된다. 기존 항목은 건드리지 않는다.

### 기존 Fabric 항목 불변 원칙
게이트웨이는 기존 Fabric 보고서를 수정·이동·삭제하지 않는다.  
삭제는 업로드 충돌 롤백(방금 올린 항목만)과 Fabric에서 직접 삭제하는 경우만 해당한다.

### 업로드 없이 Fabric 직접 게시한 경우
Fabric 콘솔에서 직접 올린 보고서는 `import_fabric_reports.py`로 DB에 등록해야 한다.  
등록하지 않으면 게이트웨이 화면에 표시되지 않는다.
