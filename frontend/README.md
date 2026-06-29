# qualisoft BI 포털 — 프론트엔드 (Vite + React + TypeScript)

기존 Jinja/HTML 화면을 React + TypeScript 로 재구현한 프론트엔드.
**백엔드(FastAPI 라우트·DB)는 변경하지 않는다.** 빌드 산출물을 FastAPI 가 그대로 서빙한다.

## 동작 방식

- 빌드 결과물은 `../static/dist/app.js` · `app.css` 로 출력된다 (파일명 고정).
- `templates/login.html · report.html · admin.html` 은 얇은 셸로,
  서버 컨텍스트(user/reports/stats/...)를 `window.__BOOTSTRAP__` 로 주입하고 위 번들을 로드한다.
- React 는 `__BOOTSTRAP__.page` 값(`login`/`report`/`admin`)으로 렌더할 화면을 결정한다.
- API 는 기존 엔드포인트(`/api/embed`, `/api/upload`, `/api/admin/*`)를 그대로 호출한다.

## 명령

```bash
npm install        # 최초 1회
npm run build      # → ../static/dist/app.{js,css} 생성 (커밋 대상)
npm run dev        # 로컬 HMR 개발 서버 (선택, 백엔드와 별도)
```

운영 서버는 Node 없이 동작한다 — 커밋된 `static/dist/` 를 FastAPI 가 서빙하므로,
화면을 수정하면 `npm run build` 후 산출물을 함께 커밋한다.

## 디자인 토큰

소프트 세이지 팔레트는 `src/theme.css` 의 CSS 변수에 정의돼 있다
(`--sage #9CCC65`, `--sage-light #C5E1A5`, `--sage-bg #F4F8EE`, `--sage-hover #7CB342`).
