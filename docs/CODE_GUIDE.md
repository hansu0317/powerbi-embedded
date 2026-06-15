# 백엔드 코드 가이드

## 전체 구조

```
직원 브라우저
   │ ① 로그인 / 보고서 열람 / .pbix 업로드
   ▼
게이트웨이 서버 (main.py  포트 8082)
   │                            │
   │ ② 계정·권한 조회            │ ③ 서비스 주체 명의로 API 호출
   ▼                            ▼
PostgreSQL                  Azure AD + Fabric REST API
                                 │
                                 ▼
                         Microsoft Fabric 워크스페이스
```

**핵심**: 서비스 주체(Azure 앱 계정) 1개가 모든 Fabric 작업을 대신한다.  
보고서 **데이터**는 서버를 안 거치고 Fabric ↔ 브라우저가 직접 통신한다.  
서버는 "토큰을 줄지 말지"만 결정한다.

---

## 용어

| 용어 | 뜻 |
|---|---|
| 서비스 주체 | 사람이 아닌 앱용 Azure 계정 (`.env`의 CLIENT_ID/SECRET) |
| Embed Token | "이 보고서를 이 신원으로 볼 수 있다"는 1회용 열쇠. GenerateToken API가 발급 |
| Import | .pbix 파일을 워크스페이스에 올려 보고서 + 의미모델로 변환하는 Power BI API |
| 의미 모델 | 보고서가 읽는 데이터 모델. .pbix 하나가 보고서 + 의미모델 한 쌍 |
| RLS | 같은 보고서라도 사람마다 보이는 데이터 행이 다른 Fabric 기능 |
| 멱등 | 몇 번 실행해도 결과가 같음 (마이그레이션 스크립트의 성질) |

---

## main.py 주요 기능

### 설정 (파일 상단)
`.env`를 읽어 Azure 인증정보, DB 접속정보, 운영 한도를 상수로 만든다.  
`SECRET_KEY`가 32자 미만이면 서버가 아예 시작하지 않는다.

### DB 커넥션 풀 (`db_pool`, `db_conn`)
프로세스당 커넥션 풀(기본 2~20개)로 빌려 쓰고 반납한다.  
모든 **쓰기** 함수는 `conn.commit()`을 명시적으로 호출한다.

### DB 헬퍼 (`db_*`)

| 함수 | 하는 일 |
|---|---|
| `db_authenticate` | 아이디 조회 → bcrypt 비밀번호 검증 → `is_active` 확인 |
| `db_login_allowed` / `db_record_login` | 15분 내 5회 실패 시 차단 |
| `db_get_reports` | 이 사용자가 볼 수 있는 `active` 보고서만 (사이드바 목록) |
| `db_get_report` | 보고서 1건의 Fabric ID + 설정 + RLS 정책 조회 |
| `db_reserve_upload` | 업로드 예약 — 한도 검사 + 중복 차단 + advisory lock |
| `db_register_report` | 완료된 보고서를 reports/meta/권한/감사로그에 한 트랜잭션으로 등록 |
| `db_mark_report_deleted` / `db_restore_report` | Fabric 삭제 동기화용 상태 전환 |
| `db_record_view` | 열람 이력을 `report_views`에 기록 |

### Azure 토큰 (`get_access_token`, `get_fabric_token`)
Power BI API용과 Fabric API용 scope가 달라 2개다.  
받은 토큰은 메모리에 캐시하고 만료 5분 전 자동 갱신한다.

### 임베드 토큰 발급 (`get_embed_token`)
1. DB에서 `pbi_report_id` 조회
2. Fabric에서 보고서 정보 조회 (404면 DB도 즉시 `deleted`)
3. RLS가 필요하면 `pbi_username` + 역할을 identity로 실어 GenerateToken 호출
4. 토큰 + embedUrl + 화면 설정 반환 → SDK가 렌더링

### 세션과 CSRF
- 세션: 서명된 쿠키 (8시간). 서버에 저장소 없음 → 프로세스를 늘려도 동작
- CSRF: 로그인 폼과 업로드 API는 세션 토큰과 대조

### 업로드 파이프라인 (`process_upload`)

```
검증 (.pbix, ZIP 서명, ≤1GB, 이름 1~50자)
  ↓ db_reserve_upload()          상태: publishing
  ↓ fabric_get_or_create_folder() 사용자 폴더 확보
  ↓ POST /imports (내부이름: 사용자__이름__uuid)
  ↓                              상태: accepted
  ↓ 3초 간격 폴링 (최대 5분)
  ↓ fabric_rename_new_items()    내부이름 → 원래 이름
    └ 409 이름 충돌 시 → fabric_delete_items() 롤백 → 사용자에게 에러
  ↓                              상태: fabric_succeeded
  ↓ db_register_report()         본인 + 관리자 권한 자동 부여
  ↓                              상태: completed
```

### Fabric 삭제 동기화 (`sync_fabric_reports`)
서버 시작 시 1회 + 10분 주기로 워크스페이스 목록과 DB를 대조한다.  
Fabric에 없는 `active` 보고서 → `deleted`. 다시 나타나면 → `active`.  
Azure 장애 시 목록 조회가 실패하면 그 회차는 건너뛴다 (전체 삭제 사고 방지).

### 라우트 요약

| 라우트 | 인증 | 하는 일 |
|---|---|---|
| `GET/POST /login` | - | 로그인 (실패 횟수 차단 + CSRF) |
| `GET /` | 세션 | 보고서 뷰어 SPA |
| `GET /api/embed/{id}` | 세션 + 권한 | 임베드 토큰 발급 |
| `POST /api/upload` | 세션 + CSRF | .pbix 게시 파이프라인 |
| `POST /api/admin/sync-fabric` | 관리자 + CSRF | 수동 삭제 동기화 |
| `GET /health` | - | DB 연결 확인 |

### async 패턴
psycopg2·msal은 동기 라이브러리이므로 DB/토큰 호출은  
`await asyncio.to_thread(...)` 로 감싸 별도 스레드에서 실행한다.  
HTTP는 처음부터 비동기인 `httpx`를 쓴다.

---

## 파일 구성

```
main.py                          서버 전체
scripts/migrate_report_meta.py   DB 스키마 (테이블 변경은 여기만)
scripts/publish_pbix.py          관리자용 CLI 게시 (공용 보고서)
scripts/import_fabric_reports.py 기존 Fabric 보고서 일괄 DB 등록
scripts/server.sh                시작/종료/재시작/상태
templates/login.html             로그인 화면
templates/report.html            보고서 뷰어 SPA (탭, 사이드바, 업로드)
templates/docs.html              관리자용 내부 문서
docs/                            설계·운영 문서
deploy/powerbi-gateway.service   systemd 유닛
```

---

## 메모리와 캐시

| 항목 | 위치 | 수명 |
|---|---|---|
| Azure 토큰 2종 | 서버 메모리 | ~1시간, 만료 5분 전 갱신 |
| DB 연결 | 커넥션 풀 (2~20) | 프로세스 수명 |
| 세션 | 브라우저 쿠키 (서버 키로 서명) | 8시간 — 재시작해도 로그인 유지 |
| 임베드 토큰 | 캐시 안 함 | 매 요청마다 발급 (권한 회수 즉시 반영 위해 의도적) |
| 보고서 데이터 | 서버 미경유 | Fabric ↔ 브라우저 직접 통신 |

---

## 흔한 작업 빠른 참조

| 상황 | 방법 |
|---|---|
| 사용자 추가 / 권한 부여 | `OPERATIONS.md`의 SQL |
| 업로드가 멈췄을 때 | 서버 재시작 (자동 복구) |
| Fabric 삭제가 반영 안 될 때 | 10분 대기 또는 `POST /api/admin/sync-fabric` |
| 공용 보고서 게시 | `python3 scripts/publish_pbix.py 파일.pbix 이름` |
