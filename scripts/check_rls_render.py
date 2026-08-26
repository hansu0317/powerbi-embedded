"""RLS/GET 필터가 계정별로 실제 화면에 다르게 렌더링되는지 스크린샷으로 확인한다.

포털 로그인(비밀번호) 없이 확인 가능 — 서버가 실제로 쓰는 get_embed_token()을
그대로 호출해 계정별 embed token을 발급받고, powerbi-client SDK(프론트가 쓰는 것과
동일 패키지)를 로컬 Playwright 브라우저에 그대로 꽂아서 렌더링 → 스크린샷.

**토큰 발급 성공 ≠ 검증**이다(docs/01_RLS_적용가이드.md 참고) — 역할명·필터
컬럼명이 틀려도 토큰은 정상 발급되고 화면만 조용히 비거나 필터 없이 전체가
보인다. 이 스크립트가 그 마지막 한 걸음(실제 화면 비교)을 메운다.

사용법:
    python scripts/check_rls_render.py <report_id> <username1> [username2] [username3] ...

    예: python scripts/check_rls_render.py 4 user_amt user_eco

출력: scripts/.rls_render_out/<report_id>/shot_<username>.png (여러 장 나오면
      직접 눈으로 비교 — 값이 다른 계정끼리 화면이 실제로 달라야 정상).

전제: `pip install playwright && playwright install chromium` 및
      `frontend/node_modules/powerbi-client` 설치(`npm install` 실행됨) 완료.
읽기 전용, DB/PBI 어느 쪽도 바꾸지 않는다(embed token 발급은 매 조회마다 나는
정상 트래픽과 동일).
"""
import asyncio
import shutil
import sys
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PBI_CLIENT_BUNDLE = PROJECT_ROOT / "frontend" / "node_modules" / "powerbi-client" / "dist" / "powerbi.min.js"

HTML_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;height:100%;background:#fff}}
#report{{height:100vh;width:100vw}}
#status{{position:fixed;top:0;left:0;background:#000;color:#0f0;font:12px monospace;padding:4px 8px;z-index:9999}}</style>
<script src="powerbi.min.js"></script></head>
<body>
<div id="status">loading...</div>
<div id="report"></div>
<script>
  // UMD 번들이라 <script> 태그로 로드하면 전역 이름이 window.powerbi가 아니라
  // window["powerbi-client"]다 (frontend는 ESM import라 이 문제가 없음 — 순수
  // 검증용 스탠드얼론 하네스에서만 필요한 shim).
  var powerbi = window["powerbi-client"];
  window.onerror = function(msg) {{
    document.getElementById("status").textContent = "JS error: " + msg;
    window.__pbiError = String(msg);
  }};
  var getFilter = {get_filter_json};
  var config = {{
    type: "report",
    id: {report_id!r},
    embedUrl: {embed_url!r},
    accessToken: {embed_token!r},
    tokenType: powerbi.models.TokenType.Embed,
    // GET 필터(부서/관계사, PoC) — routes/report.py::_build_get_filter와 frontend
    // ReportPage.tsx::buildGetFilter가 실제로 만드는 것과 동일한 모양으로 재현한다.
    // 이 항목이 빠지면 GET 필터를 전혀 검증 못 하고 RLS identity만 확인하게 된다
    // (2026-08-24에 실제로 이 버그로 "필터 안 걸림"을 오판할 뻔했음).
    filters: getFilter ? [{{
      "$schema": "http://powerbi.com/product/schema#basic",
      target: {{ table: getFilter.table, column: getFilter.column }},
      operator: "In",
      values: [getFilter.value],
      filterType: powerbi.models.FilterType.Basic,
      displaySettings: {{ isLockedInViewMode: true }},
    }}] : undefined,
    settings: {{
      navContentPaneEnabled: false,
      filterPaneEnabled: false,
      layoutType: powerbi.models.LayoutType.Custom,
      customLayout: {{ displayOption: powerbi.models.DisplayOption.FitToPage }},
      panes: {{ pageNavigation: {{ visible: false }}, filters: {{ visible: false }} }}
    }}
  }};
  var service = new powerbi.service.Service(
    powerbi.factories.hpmFactory, powerbi.factories.wpmpFactory, powerbi.factories.routerFactory
  );
  var el = document.getElementById("report");
  var statusEl = document.getElementById("status");
  var report = service.embed(el, config);
  report.on("loaded", function() {{ statusEl.textContent = "loaded"; window.__pbiLoaded = true; }});
  report.on("rendered", function() {{ statusEl.textContent = "rendered"; window.__pbiRendered = true; }});
  report.on("error", function(e) {{
    statusEl.textContent = "error: " + JSON.stringify(e.detail);
    window.__pbiError = JSON.stringify(e.detail);
  }});
</script>
</body></html>
"""


async def gen_tokens(report_id: int, usernames: list[str], out_dir: Path) -> None:
    import json as _json
    from services.powerbi import get_embed_token
    from database.auth import db_get_user
    from routes.report import _build_get_filter

    for user in usernames:
        res = await get_embed_token(report_id, user)
        user_row = await asyncio.to_thread(db_get_user, user)
        get_filter = await _build_get_filter(user_row or {}, report_id) if user_row else None
        html = HTML_TEMPLATE.format(
            report_id=res["report_id"], embed_url=res["embed_url"], embed_token=res["embed_token"],
            get_filter_json=_json.dumps(get_filter, ensure_ascii=False),
        )
        (out_dir / f"rls_{user}.html").write_text(html, encoding="utf-8")
        print(f"  token 발급: {user} (len={len(res['embed_token'])}, get_filter={get_filter})")


def shoot(usernames: list[str], out_dir: Path, port: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for user in usernames:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/rls_{user}.html", wait_until="load")
            rendered, error = False, None
            for _ in range(40):  # 최대 ~20초
                rendered = page.evaluate("window.__pbiRendered === true")
                error = page.evaluate("window.__pbiError || null")
                if rendered or error:
                    break
                time.sleep(0.5)
            time.sleep(1.5)  # rendered 이후 실제 그리기 안정화 대기
            shot_path = out_dir / f"shot_{user}.png"
            page.screenshot(path=str(shot_path))
            print(f"  {user:<18} rendered={rendered} error={error} -> {shot_path}")
            page.close()
        browser.close()


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    report_id = int(sys.argv[1])
    usernames = sys.argv[2:]

    if not PBI_CLIENT_BUNDLE.exists():
        sys.exit(f"powerbi-client 번들이 없습니다: {PBI_CLIENT_BUNDLE}\n"
                  f"frontend 폴더에서 npm install을 먼저 실행하세요.")

    out_dir = PROJECT_ROOT / "scripts" / ".rls_render_out" / str(report_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(PBI_CLIENT_BUNDLE, out_dir / "powerbi.min.js")

    print(f"[1/3] report {report_id}에 대해 {len(usernames)}개 계정 embed token 발급 중...")
    asyncio.run(gen_tokens(report_id, usernames, out_dir))

    print("[2/3] 로컬 정적 서버 기동...")
    handler = lambda *a, **kw: SimpleHTTPRequestHandler(*a, directory=str(out_dir), **kw)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    Thread(target=httpd.serve_forever, daemon=True).start()

    print(f"[3/3] Playwright로 계정별 렌더링 스크린샷 촬영 중 (port={port})...")
    try:
        shoot(usernames, out_dir, port)
    finally:
        httpd.shutdown()

    print(f"\n완료 — {out_dir} 안의 shot_*.png를 서로 비교해서 계정별로 실제 화면이\n"
          f"다른지 눈으로 확인할 것 (같으면 필터/역할이 안 걸린 것).")


if __name__ == "__main__":
    main()
