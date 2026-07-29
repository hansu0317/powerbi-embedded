# PowerBI Gateway

흩어진 Power BI 보고서를 한 곳에서 열람하고, 직원이 직접 `.pbix`를 올리는 **사내 통합 BI 포털**.
보고서 권한을 사내 DB로 통제하고 직원을 `app.powerbi.com`에 노출하지 않는다 (App-Owns-Data).

**Power BI 계정은 관리자용 1개(Pro)만 필요** — 열람 직원은 PBI 계정이 아예 없다.
Azure 앱 등록(서비스 주체) 1개가 모든 직원을 대신해 임베드 토큰을 발급받는다.

```
직원 브라우저  →  이 게이트웨이(FastAPI)  →  Power BI REST API
                        ↓
                  PostgreSQL — 누가 무엇을 볼 수 있는지
```

보고서 데이터 자체는 브라우저가 Microsoft 서버에서 직접 받아 그린다. 게이트웨이는 **권한 판정과 토큰 발급만** 한다.

---

## 요구 사항

| 구분 | 버전 | 비고 |
|---|---|---|
| Python | **3.11** | 3.12+ 미검증 |
| PostgreSQL | **15** 권장 (13 이상) | 마이그레이션은 15에서 검증 |
| Node.js | **20** | 프론트엔드 빌드에만 필요 |
| 포트 | **8247** | 인바운드 허용 필요 |

**네트워크 (아웃바운드 HTTPS 443)** — `login.microsoftonline.com`, `api.powerbi.com`, `api.fabric.microsoft.com`

**사전 준비 (없으면 동작하지 않음)**
1. Azure Entra ID에 앱 등록 → 테넌트/클라이언트 ID + 시크릿 발급
2. Power BI 관리 포털에서 **서비스 주체의 API 사용 허용** ON (테넌트 관리자 권한 필요)
3. 그 앱을 대상 워크스페이스에 **멤버 이상**으로 추가

> 2번이 꺼져 있으면 토큰 발급이 401, 3번이 빠지면 보고서 조회가 404로 실패한다.
> 애플리케이션 코드로는 우회할 수 없다.

---

## 설치 (Linux)

```bash
git clone <repo-url> && cd powerbi-embedded

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # 값 채우기 (아래 '환경 설정' 참고)

createdb powerbi_gateway  # PostgreSQL DB 생성

cd frontend && npm install && npm run build && cd ..

bash scripts/server.sh start
curl http://127.0.0.1:8247/health     # {"status":"ok", ...}
```

스키마 마이그레이션은 `server.sh`가 **기동 전 자동 실행**한다. 수동 실행은 `python3 scripts/migrate_report_meta.py`.

### 서버 제어

```bash
bash scripts/server.sh start|stop|restart|status
bash scripts/server.sh start v10      # DB를 특정 버전까지만 적용하고 기동
tail -f logs/server.log
```

---

## 설치 (Windows — 클라우드 PC)

Microsoft 파트너 프로그램으로 받는 Windows 개발 PC 기준. **WSL2와 네이티브 중 먼저 선택한다.**

| | WSL2 (Ubuntu) — 권장 | 네이티브 Windows |
|---|---|---|
| `scripts/server.sh` | 그대로 동작 | 못 씀 → `scripts/server.ps1` 사용 |
| 운영 서버(리눅스)와 동일성 | 거의 같음 | 차이 있음 |
| PostgreSQL | WSL 안에 설치 | Windows 설치본 |

> 운영 서버가 리눅스이므로 **WSL2가 "개발 PC에선 되는데 서버에선 안 되는" 문제를 크게 줄여준다.**
> 네이티브를 택하면 `server.ps1`로 기동한다 (포그라운드 실행, Ctrl+C 종료).

### 공통 설치 목록

```
Python 3.11          (pyenv-win 사용 시 프로젝트별 버전 관리 가능)
PostgreSQL 15
Node.js 20           (nvm-windows 권장)
Git for Windows      (SSH 키 포함)
VS Code              (WSL 사용 시 Remote - WSL 확장)
```

### Windows에서 반드시 먼저 잡을 것

**① 한글 인코딩** — Windows 기본은 `cp949`라 UTF-8 한글이 깨진다.
시스템 환경 변수에 아래를 넣어두면 대부분 해결된다.

```
PYTHONUTF8=1
```

**② 줄바꿈(CRLF)** — git이 `.sh`를 CRLF로 바꾸면 WSL/리눅스에서 실행되지 않는다.
레포의 `.gitattributes`가 이를 고정하므로 **클론 전에 파일이 있는지 확인**한다.

**③ 방화벽** — 8247 인바운드 허용. 다른 PC에서 접속하려면 필수.

**④ `.env` 전달** — 레포에 포함되지 않으므로 PC를 새로 받을 때마다 별도로 넣어야 한다.
Azure Key Vault 또는 사내 비밀번호 관리자에 보관하고, 평문 공유(메신저·메일)는 하지 않는다.

### Windows 기동

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd frontend; npm install; npm run build; cd ..

.\scripts\server.ps1          # 마이그레이션 후 기동 (Ctrl+C 종료)
.\scripts\server.ps1 v10      # 특정 스키마 버전까지만 적용
```

---

## 환경 설정

`.env` — **불변 설정. 변경 시 서버 재시작 필요.** git에 절대 커밋하지 않는다.

```ini
# Azure / Power BI
TENANT_ID=<디렉터리(테넌트) ID>
CLIENT_ID=<응용 프로그램(클라이언트) ID>
CLIENT_SECRET=<클라이언트 비밀>
WORKSPACE_ID=<워크스페이스 GUID>

# 보안
SECRET_KEY=<32자 이상 랜덤 문자열>   # 미만이면 기동 즉시 중단된다
COOKIE_SECURE=false                  # HTTPS 적용 시 true

# PostgreSQL
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=powerbi_gateway
DB_USER=<계정>
DB_PASSWORD=<비밀번호>
```

**런타임 설정**(업로드 한도, 로그인 차단 기준, 동기화 주기 등)은 `app_config` 테이블에 있고
**관리자 포털 → 설정**에서 바꾸면 재시작 없이 즉시 반영된다.

---

## 구조

```
main.py            앱 조립 — 미들웨어·라우터·lifespan(시작 복구/백그라운드 루프)
config.py          .env + app_config(DB) 로더, 핫 리로드
errors.py          AppError — 모든 HTTP 에러의 중앙 레지스트리
database.py        커넥션 풀 + 모든 SQL (db_* 함수)
deps.py            세션 사용자·CSRF·관리자 Depends

routes/auth.py     /login /logout
routes/report.py   / /api/embed /api/upload /api/favorites ...
routes/admin.py    /admin /api/admin/*

services/azure.py    서비스 주체 토큰 (msal, 캐시)
services/powerbi.py  임베드 토큰 발급·캐시, PBI REST 헬퍼
services/fabric.py   PBI↔DB 동기화, 시작 시 복구

frontend/          React + TypeScript 소스 (사람이 편집)
static/dist/       npm run build 산출물 (브라우저가 받는 것)
templates/         Jinja HTML 셸 — 서버가 __BOOTSTRAP__ 주입
scripts/           마이그레이션·서버 제어·보조 스크립트
```

**규칙** — `routes/`는 SQL을 직접 쓰지 않고 `database.py`의 `db_*`만 호출한다.
Power BI REST 호출도 `routes/`가 직접 하지 않고 `services/`를 거친다.

---

## 데이터베이스

테이블 11개. 스키마 버전은 `schema_migrations`에 기록된다 (현재 **v12**).

| 테이블 | 내용 |
|---|---|
| `users` | 계정 (bcrypt 해시, 역할, 관리자·업로드 권한, RLS 매핑) |
| `reports` | 보고서 본체 + PBI 연결 정보 (18컬럼) |
| `user_reports` | 개인 열람 권한 |
| `groups` / `user_groups` / `group_reports` | 그룹 단위 권한 |
| `upload_jobs` | 업로드 상태 머신 (재시작 복구용) |
| `user_report_marks` | 즐겨찾기 · 최근 본 |
| `event_log` | 활동 · 관리 감사 · 로그인 시도 (`log_type`으로 구분) |
| `app_config` | 런타임 설정 |
| `schema_migrations` | 적용된 스키마 버전 |

**열람 가능 판정** = 직접 부여(`user_reports`) **OR** 소속 그룹 부여(`group_reports`).
관리자는 권한 확인을 건너뛴다. 이 판정 SQL은 `database.py`의 `_CAN_VIEW_REPORT_SQL` **한 곳**에서만 관리한다.

### 스키마 변경 규칙 (중요)

`scripts/migrate_report_meta.py`의 `MIGRATIONS`에 `(버전, 함수)`로만 추가한다.

- **적용된 버전 함수는 절대 수정하지 않는다.** 고칠 것이 있으면 다음 버전을 추가한다.
- 다운그레이드는 지원하지 않는다 — 되돌리려면 백업 복원뿐이다.
- **스키마 변경 전에는 반드시 백업**한다.

```bash
pg_dump -h 127.0.0.1 -U <계정> -d powerbi_gateway -F c -f backup_$(date +%Y%m%d).dump
```

> `pg_dump` 버전이 서버보다 낮으면 실패한다. 서버와 같은 메이저 버전을 사용할 것.

---

## 운영 시 알아둘 것

**프로덕션 전환에는 전용 용량이 필요하다.**
Pro 공유 용량에서의 App-Owns-Data 임베딩은 Microsoft가 **개발·테스트 전용**으로 규정한다.
직원 대상 상시 운영은 Azure Power BI Embedded(A SKU) 또는 Fabric F64+ 배정이 필요하다.
용량을 배정해도 **코드·DB는 수정하지 않는다** — 워크스페이스에 용량만 붙이면 된다.

**기존 Power BI 항목은 수정·이동·삭제하지 않는다.** 업로드는 신규 생성만 한다.
업로드된 보고서는 `계정__보고서명` 형식으로 게시되며, 이 접두사가 소유자 추적의 근거다.

**퇴사자는 삭제가 아니라 비활성화로 처리한다.** 화면에 계정 삭제 기능을 두지 않은 것도 같은 이유다.

---

## RLS (행 수준 보안) — 준비 상태와 적용 절차

**두 계층을 구분해야 한다.**

| 계층 | 통제 대상 | 담당 | 상태 |
|---|---|---|---|
| 1층 | 어떤 **보고서**가 보이는가 | 게이트웨이 DB | **동작 중** |
| 2층 | 보고서 안에서 어떤 **행**이 보이는가 | Power BI RLS | 준비 완료, 미적용 |

1층은 개인 부여(`user_reports`)와 그룹 부여(`group_reports`)의 OR 판정으로 이미 동작한다.
2층(RLS)은 **보고서마다 선택**이다 — PBIX에 역할이 정의된 데이터셋에만 적용되고,
없으면 Power BI가 identity를 요구하지 않아 그냥 열린다.

### 채택 방식 — 동적 RLS

역할을 조직 수만큼 만드는 대신 **역할 하나**만 두고, DAX가 `USERNAME()`으로 사용자를
알아내 보안 테이블에서 조회 범위를 찾는다. 사람이나 부서가 늘어도 **PBIX를 다시 게시하지 않는다.**

정적 RLS(역할=부서)로는 "개인별 데이터 권한"을 표현할 수 없다 — 사람 수만큼 역할을
만들어야 하기 때문이다. 그래서 동적 방식을 택했다.

### 데이터 흐름

```
게이트웨이 users          →  보안 테이블(데이터 원천)  →  PBIX 역할 DAX
 pbi_username(식별자)         user_key                    USERNAME()으로 조회
 department(소속)             department
 data_scope(범위)             data_scope
```

`users.data_scope` 값은 세 가지다.

| 값 | 의미 |
|---|---|
| `self` | 본인 행만 (기본값) |
| `department` | 소속 부서 전체 |
| `all` | 전사 — 관리자는 마이그레이션에서 이 값으로 초기화된다 |

### 적용 절차

**1. 식별자 맞추기** — `users.pbi_username`이 보안 테이블의 키가 된다.

   **이 프로젝트는 로그인 아이디(`users.username`)를 그대로 키로 쓴다.** 이미 유일하고
   변하지 않으며, 이메일과 달리 계정 정책이나 도메인 변경에 영향받지 않는다.
   `pbi_username`은 Microsoft 로그인 계정이 아니라 **"이 사람이 누구인지 Power BI에
   알려주는 문자열"**일 뿐이므로 실재하는 이메일일 필요가 없다.

   단, PBIX의 DAX가 비교하는 값(사번·이메일 등)이 따로 있다면 **그쪽에 맞춰야 한다.**
   3단계에서 DAX를 확인한 뒤 필요하면 이 값을 바꾼다.

**2. 소속·범위 입력** — `users.department`, `users.data_scope`를 채운다.

**3. 보안 테이블 생성** — 아래 스크립트가 DDL과 INSERT문을 만들어 준다.
   출력물을 **데이터 원천(SQL Server·Databricks 등)에서** 실행한다.

```bash
python3 scripts/export_rls_security_table.py            # SQL 출력
python3 scripts/export_rls_security_table.py --csv      # CSV 출력
python3 scripts/export_rls_security_table.py --ddl-only # DDL·DAX 안내만
```

식별자가 이메일이거나 `department`가 비어 있으면 **경고를 함께 출력**한다.

**4. PBIX 작업** — Power BI Desktop에서 보안 테이블을 모델에 추가하고,
   역할 하나를 만들어 `--ddl-only` 출력의 DAX를 붙인다. 재게시.

**5. 검증** — 보고서 하나로 시범 적용하고 **브라우저에서 실제 렌더링까지** 확인한다.
   토큰 발급 성공은 검증이 아니다.

> **주의** — 역할명·식별자가 어긋나면 토큰은 정상 발급되고 **렌더링만 실패**한다
> (`Failed to open the MSOLAP connection`). 매핑이 없으면 빈 화면이 나온다.
> 시범 적용 때 일부러 틀린 값도 한 번 넣어 보면 증상을 미리 알 수 있다.

### 되돌리기

RLS 적용 직전 상태에 `rls-before` 태그가 있다.

```bash
git show rls-before      # 그 시점 상태 확인
```

---

## 문서

| 문서 | 내용 |
|---|---|
| `docs/PROJECT.md` | 구조·흐름·DB·API 전체 — 프로젝트 파악은 이것부터 |
| `docs/OPERATIONS.md` | 서버 운영, 배포, 업로드 복구, 트러블슈팅 |
| `docs/LEARNING.md` | Python·TypeScript 문법 체계 (코드베이스 예제) |
| `pdfpptx/PowerBI_Gateway_운영·사용_매뉴얼.pptx` | 설치·사용자/관리자 매뉴얼·운영·API·확장 시나리오 |

API 전체 스펙은 서버의 **`/openapi.json`** 에서 항상 최신으로 확인할 수 있다
(Postman·Insomnia에 그대로 import 가능). 대화형 문서(`/docs`·`/redoc`)는 보안상 비활성화돼 있다.
