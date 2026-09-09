# Power BI 사내 포털

FastAPI + PostgreSQL + React로 만든 App-Owns-Data 보고서 포털입니다. 직원은 포털에 로그인하고, 서버가 허용한 보고서의 보기 전용 임베드 토큰을 받아 열람합니다.

**2026-09-09 점검: 소규모 사용에 맞는 기본 구조는 있으나, 현 환경을 그대로 운영 가능하다고 판정할 수는 없습니다.** 실제 용량은 PPU 예약 풀 `PP3`로 확인됐습니다. Microsoft는 PPU 단독을 App-Owns-Data 운영 용량으로 인정하지 않습니다. 회사가 지원 유료 용량을 준비해야 포털 열람 직원의 개인 Power BI 라이선스가 필요 없는 구성이 됩니다. [Microsoft 운영 라이선스 조건](https://learn.microsoft.com/en-us/power-bi/guidance/powerbi-implementation-planning-usage-scenario-embed-for-your-customers#licensing)

## 사용자와 관리자 안내

- 일반 사용자: [보고서 열람 가이드](docs/07_일반사용자_보고서열람가이드.md), 실행 중인 포털의 **/help**.
- 운영자: [운영 점검 결과](docs/08_프로젝트_점검결과.md), [운영 가이드](docs/04_서버_운영_가이드.md).
- 보고서 제작자: [RLS와 표시 필터](docs/01_RLS_적용가이드.md), [용량·라이선스](docs/05_라이선스_용량_비용가이드.md).
- 개발자: [학습 로드맵](docs/00_학습_로드맵.md), [DB·API 흐름](docs/02_백엔드_DB_API_흐름.md).

![일반 사용자 보고서 열람 흐름](static/guide/viewer-flow.svg)

## 실행 준비

점검 환경은 Python 3.12, PostgreSQL 16, Node.js 24입니다. 다른 버전 조합은 별도 검증이 필요합니다. 프론트엔드 의존성은 lockfile로 설치합니다.

1. Entra ID 앱 등록과 서비스 주체 자격 증명을 준비합니다.
2. Fabric/Power BI 관리자가 해당 서비스 주체의 API·임베딩 사용을 허용합니다.
3. 서비스 주체를 보고서 및 의미 모델의 워크스페이스 Member 또는 Admin으로 등록합니다.
4. 운영용 지원 유료 용량을 워크스페이스에 배정하고, 보고서를 게시합니다.
5. PostgreSQL DB와 앱 DB 계정을 준비하고 `.env.example`을 `.env`로 복사하여 값을 채웁니다.
6. 사내 HTTPS 주소와 Microsoft 서비스로 나가는 네트워크를 준비합니다.

서비스 주체 설정과 직원 계정 등록은 별개입니다. SSO를 쓰는 경우 `SSO_REDIRECT_URI`와 Entra 웹 리디렉션 URI가 일치해야 하며, 관리자가 포털 계정의 이메일을 Microsoft 계정과 연결해야 합니다. [서비스 주체 설정](https://learn.microsoft.com/en-us/power-bi/developer/embedded/embed-service-principal)

### Windows

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # 최초 1회만 실행한 후 값을 편집
cd frontend
npm ci
npm run build
cd ..
python scripts/init_schema.py
python scripts/create_admin.py  # 신규 DB의 최초 관리자만 생성
.\scripts\server.ps1 start
```

### Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 최초 1회만 실행한 후 값을 편집
cd frontend
npm ci
npm run build
cd ..
python scripts/init_schema.py
python scripts/create_admin.py
bash scripts/server.sh start
```

서버 스크립트는 기동 전에 스키마를 확인합니다. `PORT` 기본값은 8249이며 서버는 백그라운드로 실행됩니다. `start|stop|restart|status`로 제어합니다. `/health`는 앱·DB 응답을 확인하며 Power BI 렌더링 성공을 의미하지 않습니다.

## 실제 권한 규칙

활성 일반 사용자는 활성 보고서에 대해 다음 조건으로 열람합니다.

```text
개별 명시 차단 없음 AND
(본인 소유 OR 포털 공용(shared) OR 개인 허용 OR 부서 허용)
```

- 관리자는 보고서 열람 권한 검사를 건너뛰며 활성 보고서를 봅니다.
- 부서 이름은 `users.department`와 정확히 일치해야 합니다.
- `personal`은 기본 비공개이며 개인·부서 허용으로 공유할 수 있습니다.
- `can_upload`는 업로드 권한입니다. 열람 권한과 독립입니다.
- 신규 업로드는 항상 비공개로 시작합니다.
- 포털에서 권한을 회수하면 다음 요청부터 차단됩니다. 이미 발급된 Power BI 토큰은 만료까지 유효할 수 있습니다.

**부서 표시 필터는 RLS가 아닙니다.** 다른 부서의 데이터를 숨겨야 하면 Power BI 모델 RLS와 EffectiveIdentity를 적용하고 실제 일반 사용자 토큰으로 검증해야 합니다. 현재 코드에는 RLS 토큰 전달 경로가 있지만 모델의 보안 규칙을 자동으로 만들지는 않습니다.

## 코드 구조

```text
main.py            앱 조립, 세션, 보안/캐시 헤더, 백그라운드 작업
config.py          .env와 코드 상수 (변경 후 재시작)
deps.py            사용자 인증, CSRF, 관리자 의존성
database/          SQL과 DB 커넥션 풀
routes/            로그인, 보고서, 업로드, 관리자 API
services/          Entra, Power BI/Fabric 연동, 동기화, 백업
frontend/src/      React + TypeScript
static/dist/       커밋되는 빌드 결과
static/guide/      재사용 가능한 일반 사용자 흐름도
templates/         HTML 셸, /help, 관리자 /docs
scripts/           초기화, 기동, 용량 점검, 운영 보조
tests/             인증·권한·토큰·백업 회귀 검증
```

SQL은 `database/`에서 관리합니다. 임베드 토큰 발급은 `services/powerbi.py`를 거칩니다. 업로드의 Power BI Import 오케스트레이션은 현재 `routes/report_upload.py`에 남아 있습니다.

`app_config` 테이블은 과거 호환을 위해 남아 있으나 실행 코드가 읽지 않습니다. 관리자 설정 핫 리로드, 콘텐츠 업데이트, 내보내기 API는 현재 제공하지 않습니다. 보고서 삭제는 Power BI 보고서에도 반영되며 의미 모델은 자동 삭제하지 않습니다.

## 검증

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe scripts/check_capacity.py
```

테스트는 설정된 PostgreSQL 연결이 필요합니다. DB 회귀 검증은 연결 전용 임시 테이블에서 수행하고 롤백하며, 실제 Power BI 요청은 모의 응답으로 대체합니다. 실제 용량 조회는 별도 스크립트입니다. 종료 코드 0은 지원 SKU, 2는 비지원/용량 없음, 3은 확인 불가를 뜻합니다.

## 운영 시 필수 확인

- 현재 PPU/PP3에서 지원 유료 용량으로 전환하고 실제 보고서 열람을 재검증합니다.
- HTTPS를 적용한 뒤 `COOKIE_SECURE=true`로 설정합니다.
- 유효한 DB 백업, 별도 보관, 격리 DB 복원을 확인합니다.
- 민감 보고서의 행 격리는 모델 RLS로 검증합니다.
- 동시 열람과 업로드 부하는 실제 데이터·용량으로 측정합니다. 현재 1GB PBIX 전체 메모리 적재와 프로세스 내 작업이 있어 인원수만으로 처리량을 보장할 수 없습니다.
- Uvicorn은 단일 worker를 기준으로 운영합니다. worker를 늘리면 백그라운드 동기화·백업이 중복 실행될 수 있습니다.

검토하여 정리한 공통 문서만 Git 추적 대상으로 허용했습니다. `.env`, 백업·로그, PPT, 외부 참고 이미지는 계속 제외합니다. 저장소의 [독점 라이선스](LICENSE)는 외부 고객사 사용권과 별개 계약 확인이 필요하며 Microsoft 서비스 이용권을 부여하지 않습니다.
