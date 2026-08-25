# PowerBI Gateway

흩어진 Power BI 보고서를 한 곳에서 열람하고, 직원이 직접 `.pbix`를 올리는 **사내 통합 BI 포털**.
보고서 권한을 사내 DB로 통제하고 직원을 `app.powerbi.com`에 노출하지 않는다 (App-Owns-Data).

**Power BI 계정은 운영용 1개(PPU)만 필요** — 열람 직원은 PBI 계정이 아예 없다.
Azure 앱 등록(서비스 주체) 1개가 모든 직원을 대신해 임베드 토큰을 발급받는다.
(Pro는 용량이 자동으로 딸려오지 않아 서비스 주체 임베딩 자체가 안 될 수 있어 PPU를 쓴다 —
근거는 `docs/05_라이선스_용량_비용가이드.md`.)

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
| 포트 | **8249** | 인바운드 허용 필요 |

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
curl http://127.0.0.1:8249/health     # {"status":"ok", ...}
```

DB 스키마 확인/생성은 `server.sh`가 **기동 전 자동 실행**한다. 수동 실행은 `python3 scripts/init_schema.py`.

### 서버 제어

```bash
bash scripts/server.sh start|stop|restart|status
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

**③ 방화벽** — 8249 인바운드 허용. 다른 PC에서 접속하려면 필수.

**④ `.env` 전달** — 레포에 포함되지 않으므로 PC를 새로 받을 때마다 별도로 넣어야 한다.
Azure Key Vault 또는 사내 비밀번호 관리자에 보관하고, 평문 공유(메신저·메일)는 하지 않는다.

### Windows 기동

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd frontend; npm install; npm run build; cd ..

.\scripts\server.ps1          # 스키마 확인 후 기동 (Ctrl+C 종료)
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

# 서버 포트 (선택, 없으면 8249) — scripts/server.sh·server.ps1이 읽는다.
# 이 저장소를 쓰는 PC마다 로컬 포트 점유 상황이 다를 수 있어(예: VS Code가 8247을
# 점유) 스크립트에 고정하지 않고 .env로 뺐다 — 상시 배포 서버는 앞단(nginx 등)이
# 이미 특정 포트로 프록시하고 있을 테니 그 값을 여기에 맞춰 넣으면 된다.
# PORT=8249

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
deps.py            세션 사용자·CSRF·관리자 Depends

database/          커넥션 풀(pool.py) + 도메인별 SQL (db_* 함수)
  pool.py            커넥션 풀, db_conn() 컨텍스트 매니저
  auth.py            로그인·사용자 조회
  reports.py         보고서 조회·권한·즐겨찾기
  uploads.py         업로드 작업, 보고서 등록
  folders.py         포털 보고서 폴더
  groups.py          그룹, 회사 계층(RLS)
  activity.py        활동·감사 로그
  admin.py           관리자 통계·설정·사용자 관리

routes/auth.py     /login /logout
routes/report.py   / /api/embed /api/upload /api/favorites ...
routes/folders.py  /api/report-folders, 보고서 폴더 이동
routes/admin.py    /admin /api/admin/*

services/azure.py          서비스 주체 토큰 (msal, 캐시)
services/powerbi.py        임베드 토큰 발급·캐시, PBI REST 헬퍼
services/fabric.py         PBI↔DB 동기화, 시작 시 복구
services/fabric_folders.py Fabric 폴더/Item 이동
services/backup.py         평일 정기 DB 백업

frontend/          React + TypeScript 소스 (사람이 편집)
static/dist/       npm run build 산출물 (브라우저가 받는 것)
templates/         Jinja HTML 셸 — 서버가 __BOOTSTRAP__ 주입
scripts/           스키마 초기화·서버 제어·보조 스크립트
```

**규칙** — `routes/`는 SQL을 직접 쓰지 않고 `database/`의 `db_*`만 호출한다.
Power BI REST 호출도 `routes/`가 직접 하지 않고 `services/`를 거친다.

세부 파일 지도와 읽는 순서는 `docs/00_학습_로드맵.md`를 참고한다.

---

## 데이터베이스

테이블 12개 + 뷰 1개. `scripts/init_schema.py` 하나가 전체 스키마를 정의한다 (버전 이력 없음 — 자세한 배경은 아래 "스키마 변경 규칙" 참고). 전체 목록·비고는 `docs/02_백엔드_DB_API_흐름.md` 참고.

| 테이블/뷰 | 내용 |
|---|---|
| `users` | 계정 (bcrypt 해시, 관리자·업로드 권한, `department` GET 필터 값) |
| `reports` | 보고서 본체 + PBI 연결 정보, `owner_id`/`visibility`(personal/group/shared) |
| `report_folders` | 계층형 포털 폴더 |
| `user_reports` | 개인 열람 권한 (`can_view`: NULL=설정없음/TRUE=허용/FALSE=명시적 차단) |
| `groups` / `user_groups` / `group_reports` | 그룹 단위 권한 |
| `upload_jobs` | 업로드 상태 머신 (재시작 복구용) |
| `user_report_marks` | 즐겨찾기 · 최근 본 |
| `event_log` | 활동 · 관리 감사 · 로그인 시도 (`log_type`으로 구분) |
| `app_config` | 런타임 설정 |

**열람 가능 판정** = (직접 부여(`user_reports`) **OR** 소속 그룹 부여(`group_reports`)) **AND NOT** 개별 명시 차단.
관리자는 권한 확인을 건너뛴다. 이 판정 SQL은 `database/reports.py`의 `_CAN_VIEW_REPORT_SQL` **한 곳**에서만 관리한다.

### 스키마 변경 규칙 (중요)

`scripts/init_schema.py`가 전체 스키마를 `CREATE TABLE IF NOT EXISTS`로 선언한다 — 서버 시작마다 실행돼도 안전하다 (2026-08~, 그 이전엔 `schema_migrations`로 버전을 추적하는 `scripts/migrate_report_meta.py`를 썼으나, 운영 DB가 1개뿐이고 변경이 드물다는 전제로 단순화했다. 과거 이력이 궁금하면 그 파일을 참고).

- **이 파일을 계속 고쳐 쓰지 않는다.** 새 변경사항은 배포 시 관리자가 직접 실행하는 별도 SQL로 처리한다.
- 다운그레이드는 지원하지 않는다 — 되돌리려면 백업 복원뿐이다.
- **스키마 변경 전에는 반드시 백업**한다.

```bash
pg_dump -h 127.0.0.1 -U <계정> -d powerbi_gateway -F c -f backup_$(date +%Y%m%d).dump
```

> `pg_dump` 버전이 서버보다 낮으면 실패한다. 서버와 같은 메이저 버전을 사용할 것.

---

## 운영 시 알아둘 것

**"캐패시티 없는 Pro 공유"가 아니라 PPU 예약 용량(PP3)이 워크스페이스에 붙어 있다 —
2026-08-14에 `scripts/check_capacity.py`로 라이브 재확인함(`isOnDedicatedCapacity: true`,
capacityId가 PP3 capacity와 일치, `docs/05_라이선스_용량_비용가이드.md` 참고).** 예전
버전의 이 문단은 "지금 Pro 공유 용량 위에서 운영 중이라 체험판 배너가 뜬다"고 적혀
있었는데, 그건 MS 공식 문서([Capacity and SKUs in Power BI embedded
analytics](https://learn.microsoft.com/en-us/power-bi/developer/embedded/embedded-capacity))의
일반론만 보고 쓴 추정이었지 이 워크스페이스를 직접 조회한 결과가 아니었다 — 실제로
조회해보니 틀린 서술이었다.

MS 문서는 이렇게 명시한다:

> Free trial tokens are limited to development testing only. Once going to production,
> a capacity must be purchased. **Until a capacity is purchased, the Free trial version
> banner will continue to appear at the top of the embedded report.**

이 워크스페이스는 캐패시티(PP3)가 이미 붙어 있으므로 위 "캐패시티 미구매" 케이스에
해당하지 않는다 — 체험판 배너가 뜰 이유가 없다. 다만 한 가지 남는 불확실성이 있다:
MS가 "최종 사용자 라이선스 불필요"를 공식 보장하는 목록은 F/A/EM/P SKU고 **PP(Premium
Per User)는 이 목록에 없다** — 실제로 되는 건 확인된 사실이지 공식 보장 문서는 아니다.
그래서 배너가 뜨는지 자체는 실제로 로그인해서 보고서를 열어 눈으로 30초만 확인하면
끝나는 일이고, 아직 그 확인은 안 했다. `scripts/check_capacity.py`를 재실행하면
캐패시티 배정이 그 사이 바뀌었는지도 같이 확인된다.

**기존 Power BI 항목은 수정·이동·삭제하지 않는다.** 업로드는 신규 생성만 한다.
업로드된 보고서는 `계정__보고서명` 형식으로 게시되며, 이 접두사가 소유자 추적의 근거다.

**퇴사자는 삭제가 아니라 비활성화로 처리한다.** 화면에 계정 삭제 기능을 두지 않은 것도 같은 이유다.

---

## RLS (행 수준 보안) — 두 계층

> **전제조건: 보고서가 Power BI 워크스페이스에 게시(Publish)돼 있어야 한다.**
> "웹에 게시(Publish to Web)"로 만든 공개 링크(`app.powerbi.com/view?r=...`)는 이 전제를
> 만족하지 않는다 — 완전 익명 공개라 "지금 보는 사람이 누구인지" 자체가 없고, RLS도
> URL 쿼리스트링 필터도 전혀 지원하지 않는다(MS 공식 문서: "Query string filtering
> doesn't work with Publish to web"). 아래 두 계층은 전부 워크스페이스에 정식
> 게시된 보고서에 이 앱(Service Principal)이 Embed Token으로 접근하는 것을 전제로 한다.

| 계층 | 통제 대상 | 담당 | 상태 |
|---|---|---|---|
| 1층 | 어떤 **보고서**가 보이는가 | 게이트웨이 DB | **동작 중** |
| 2층 | 보고서 안에서 어떤 **행**이 보이는가 | GET 필터 | 보고서별 수동 적용 |

1층은 개인 부여(`user_reports`)와 그룹 부여(`group_reports`)의 OR 판정으로 이미 동작한다.

**2층은 GET 필터다(2026-08-24 결정)** — Power BI의 RLS 역할·DAX는 안 쓴다. 서버가
embed 시점에 사용자의 `users.department` 값을 그 보고서의 `reports.filter_table`/
`filter_column`에 맞춰 화면 필터로 걸어준다(`routes/report.py::_build_get_filter`).
PBIX에 역할을 아예 안 만들어도 되고, DAX·`USERNAME()`·보안 테이블 Import가 전혀
필요 없다 — 다만 **브라우저 devtools로 우회 가능한 표시용 필터**라 진짜 보안 경계는
아니다. 전체 절차·주의사항은 **`docs/01_RLS_적용가이드.md`** 에 있다.

---

## 문서

`docs/`는 내부 참고용이라 **git에서 제외된다**(`.gitignore`) — 이 PC를 벗어나 인수인계할
때는 저장소 클론만으로는 따라오지 않으므로 폴더 자체를 별도로 전달해야 한다. 클라이언트
ID/테넌트 ID 등 사내 값이 평문으로 들어 있어(비밀번호·시크릿은 없음) 공개 저장소에는
올리지 않는 편이 안전하다.

번호(00~11)는 만들어진 순서일 뿐 읽는 순서가 아니다 — "지금 뭐가 궁금한가"로 4갈래로
묶었다. 어디에 뭐가 있는지 헷갈리면 이 표에서 카테고리부터 찾을 것.

### 인프라 구축 — 지금 뭐가 어떻게 붙어있나

| 문서 | 내용 |
|---|---|
| `docs/06_인프라_현황.md` | Entra 앱 등록·워크스페이스·용량 현재 값, DB 직접 접속법, 관리 화면 4곳 정리, 전체 아키텍처 그림 |
| `docs/04_서버_운영_가이드.md` | `server.ps1` 기동·중지, 자동 백업 스케줄, PID/포트 트러블슈팅, 검증 스크립트 실행 순서 |

### 제한사항·전제조건 — 이건 왜 안 되고, 이건 뭘 전제로 하나

| 문서 | 내용 |
|---|---|
| `docs/05_라이선스_용량_비용가이드.md` | 왜 PPU만으로 되는지, 왜 F/A SKU를 안 사는지, 감수 중인 리스크(PPU는 공식 라이선스-면제 SKU 목록 밖) |

### 개발 — 코드를 고치거나 새 기능을 붙일 때

| 문서 | 내용 |
|---|---|
| `docs/00_학습_로드맵.md` | 처음 이 프로젝트를 볼 때 시작점 — 요청 흐름/DB 풀/인증/CORS/캐시/설정/파일 지도를 코드로 따라가며 학습 |
| `docs/02_백엔드_DB_API_흐름.md` | DB 스키마, 열람 권한 판정 SQL, 임베드 토큰·업로드 흐름 |
| `docs/01_RLS_적용가이드.md` | RLS(2층) 적용 절차 — PBIX 역할·DAX 작성법, 고객사별 확장 계약, 트러블슈팅 |

> `docs/09_PowerBI_Fabric_API_개발가이드.md`(Power BI REST vs Fabric REST 구분, 새 API 체크리스트)는
> 지금 신규 API 개발 계획이 없어 삭제했다 — 실제로 필요해지는 시점에 다시 쓰는 게 정확하다.

### 검증 — 배포 전에 뭘 확인해야 하나

| 문서 | 내용 |
|---|---|
| `docs/11_QA_전수테스트_시나리오.md` | 배포 전 회귀 체크리스트 — 인증·관리자 CRUD·폴더·RLS·업로드 시나리오 |

API 전체 스펙은 서버의 **`/openapi.json`** 에서 항상 최신으로 확인할 수 있다
(Postman·Insomnia에 그대로 import 가능). 대화형 문서(`/docs`·`/redoc`)는 보안상 비활성화돼 있다.
