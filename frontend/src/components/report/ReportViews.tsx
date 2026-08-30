// "내 보고서"(탭+임베드) / "전체 보고서"(검색+표) 화면 — pages/ReportPage.tsx에서
// 2026-08-27에 분리했다(분리 배경은 components/report/tabState.ts 상단 주석 참고).
// Power BI JS SDK 서비스 싱글턴(탭 전체가 공유)도 여기서 만든다 — 실제로 .embed()를
// 호출하는 ReportPanel이 이 파일에 있기 때문.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pbi from "powerbi-client";
import { BarChart3, LayoutDashboard, Maximize, Search, Star, Upload, X } from "lucide-react";

import type { ReportItem } from "../../lib/bootstrap";
import { fetchEmbed } from "../../lib/api";
import { Pager, useFitRows } from "../Pager";
import type { BrowserMode } from "../ReportSidebar";
import type { OpenTab } from "./tabState";
import { ReportInfoModal } from "./modals";

const powerbi = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
);

// GET 필터(부서, 2026-08-24) — JS SDK의 공식 filters 설정으로 적용한다. URL 문자열에
// &filter=...를 붙이는 방식과 결과는 같지만, isLockedInViewMode로 필터 창의
// 제거(X) 버튼을 숨길 수 있다는 점이 다르다 — 그래도 진짜 RLS는 아니다
// (브라우저 devtools로 SDK를 직접 호출하면 여전히 우회 가능 — 보안 경계로 쓰지 말 것).
function buildGetFilter(table: string, column: string, value: string): pbi.models.IBasicFilter {
  return {
    $schema: "http://powerbi.com/product/schema#basic",
    target: { table, column },
    operator: "In",
    values: [value],
    filterType: pbi.models.FilterType.Basic,
    displaySettings: { isLockedInViewMode: true },
  };
}

// 페이지 맞춤 설정(맞춤/폭맞춤/실제크기) — "설정"/"관리"는 <a href> 전체 페이지 이동이라
// 갔다 오면 이 SPA 전체가 새로 마운트된다. tabs/active와 마찬가지로 sessionStorage에
// 남겨두지 않으면 리포트별로 골라둔 크기가 매번 기본값(FitToPage)으로 되돌아간다
// (2026-08-20 리포트).
type DisplayMode = "FitToPage" | "FitToWidth" | "ActualSize";
const DISPLAY_MODES_KEY = "rp-display-modes";

function loadDisplayModes(): Record<number, DisplayMode> {
  try {
    return JSON.parse(sessionStorage.getItem(DISPLAY_MODES_KEY) || "{}");
  } catch {
    return {};
  }
}

/* ── 내 보고서 (랜딩 / 탭 + 임베드, 탭은 하단) ─────────── */
export function MyReportsView({
  reports,
  tabs,
  active,
  isFav,
  canUpload,
  onToggleFav,
  onActivate,
  onOpen,
  onClose,
  onCloseAll,
  onGoUpload,
  browserMode,
  onBrowserMode,
}: {
  reports: ReportItem[];
  tabs: OpenTab[];
  active: number | null;
  isFav: (id: number) => boolean;
  canUpload: boolean;
  onToggleFav: (id: number) => void;
  onActivate: (id: number) => void;
  onOpen: (report: ReportItem) => void;
  onClose: (id: number) => void;
  onCloseAll: () => void;
  onGoUpload: () => void;
  browserMode: BrowserMode;
  onBrowserMode: (mode: BrowserMode) => void;
}) {
  // 탭별 임베드 인스턴스 참조 — 전체화면·보기모드 버튼이 활성 탭의 인스턴스를 직접 조작한다.
  // ref라 리렌더를 트리거하지 않고, 탭이 바뀌면 해당 탭의 것만 조회한다.
  const reportRefs = useRef<Record<number, { report: pbi.Report; isDashboard: boolean; container: HTMLDivElement }>>({});
  const [, setReadyVersion] = useState(0);
  const onReady = useCallback((rid: number, report: pbi.Report, isDashboard: boolean, container: HTMLDivElement) => {
    reportRefs.current[rid] = { report, isDashboard, container };
    setReadyVersion((version) => version + 1);
  }, []);

  // React 훅은 조건부로 호출하면 안 된다 — tabs가 빈 상태(→ 아래 조기 return)에서
  // 첫 보고서를 열면(tabs.length 0→1) 이 컴포넌트가 이전 렌더보다 훅을 하나 더 호출하게 돼
  // "Rendered more hooks than during the previous render"로 화면 전체가 하얗게 죽는다.
  // 조기 return보다 반드시 앞에 선언해야 한다.
  const [displayModes, setDisplayModes] = useState<Record<number, DisplayMode>>(() => loadDisplayModes());
  useEffect(() => {
    sessionStorage.setItem(DISPLAY_MODES_KEY, JSON.stringify(displayModes));
  }, [displayModes]);

  const activeTab = tabs.find((t) => t.id === active);
  const activeEntry = activeTab ? reportRefs.current[activeTab.id] : undefined;

  const activeDisplay: DisplayMode = activeTab ? (displayModes[activeTab.id] ?? "FitToPage") : "FitToPage";
  const setDisplay = async (opt: DisplayMode) => {
    if (!activeTab || !activeEntry) return;
    try {
      await activeEntry.report.updateSettings({
      layoutType: pbi.models.LayoutType.Custom,
      customLayout: { displayOption: pbi.models.DisplayOption[opt] },
      });
      setDisplayModes((prev) => ({ ...prev, [activeTab.id]: opt }));
    } catch {
      /* SDK가 설정 변경을 거부하면 선택 표시도 바꾸지 않는다. */
    }
  };
  const goFullscreen = async () => {
    if (!activeEntry) return;
    try {
      // SDK report.fullscreen()은 iframe 내부 캔버스가 기존 크기를 유지하는 경우가 있다.
      // 실제 embed 컨테이너를 전체화면으로 만들고 크기 변경 후 맞춤을 다시 적용한다.
      await activeEntry.container.requestFullscreen();
      if (!activeEntry.isDashboard) {
        window.setTimeout(() => activeEntry.report.updateSettings({
          layoutType: pbi.models.LayoutType.Custom,
          customLayout: { displayOption: pbi.models.DisplayOption.FitToPage },
        }), 150);
      }
    } catch {
      // 구형 브라우저에서는 SDK 전체화면을 최후 수단으로 사용한다.
      activeEntry.report.fullscreen();
    }
  };

  useEffect(() => {
    const restoreDisplay = () => {
      if (document.fullscreenElement || !activeTab || !activeEntry || activeEntry.isDashboard) return;
      const selected = displayModes[activeTab.id] ?? "FitToPage";
      window.setTimeout(() => activeEntry.report.updateSettings({
        layoutType: pbi.models.LayoutType.Custom,
        customLayout: { displayOption: pbi.models.DisplayOption[selected] },
      }), 100);
    };
    document.addEventListener("fullscreenchange", restoreDisplay);
    return () => document.removeEventListener("fullscreenchange", restoreDisplay);
  }, [activeTab, activeEntry, displayModes]);

  // 탭이 많이 열리면 가로 스크롤 없이는 활성 탭이 화면 밖으로 밀려날 수 있다 —
  // 탭을 열거나 전환할 때마다 그 탭이 항상 보이는 위치로 자동 스크롤한다.
  const tabsBarRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!active) return;
    tabsBarRef.current
      ?.querySelector<HTMLElement>(`[data-tab-id="${active}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [active]);

  if (tabs.length === 0) {
    return <ReportLanding reports={reports} canUpload={canUpload} onGoUpload={onGoUpload} onOpen={onOpen} browserMode={browserMode} onBrowserMode={onBrowserMode} />;
  }
  return (
    <div className="rp-workarea">
      {/* 탭 바 — 상단, 파일탐색기 탭처럼 열린 보고서를 한눈에 전환. 세로 휠 스크롤을
          가로로 돌려준다 — 트랙패드 없이 마우스 휠만 있는 환경에서도 탭이 많을 때
          스크롤할 수 있게(발견하기 어려운 Shift+휠 대신). */}
      <div
        className="rp-tabsbar"
        ref={tabsBarRef}
        onWheel={(e) => {
          if (e.deltaY === 0 || e.deltaX !== 0) return;
          e.currentTarget.scrollLeft += e.deltaY;
          e.preventDefault();
        }}
      >
        {tabs.map((t) => (
          <div
            key={t.id}
            data-tab-id={t.id}
            className={`rp-tab${t.id === active ? " active" : ""}`}
            title={t.name}
            onClick={() => onActivate(t.id)}
          >
            <span className="rp-tab-label">{t.name}</span>
            <button
              className="rp-tab-close"
              title="닫기"
              onClick={(e) => {
                e.stopPropagation();
                onClose(t.id);
              }}
            >
              <X size={14} />
            </button>
          </div>
        ))}
        {tabs.length > 1 && (
          <button className="rp-tabsbar-closeall" title="열린 탭 모두 닫기" onClick={onCloseAll}>
            전체 닫기
          </button>
        )}
      </div>
      {activeTab && (
        <div className="rp-report-toolbar">
          <span className="rp-report-toolbar-name">{activeTab.name}</span>
          <button
            className={`rp-report-toolbar-fav${isFav(activeTab.id) ? " on" : ""}`}
            title={isFav(activeTab.id) ? "즐겨찾기 해제" : "즐겨찾기 추가"}
            onClick={() => onToggleFav(activeTab.id)}
          >
            <Star
              size={16}
              className="icn"
              fill={isFav(activeTab.id) ? "currentColor" : "none"}
            />
            <span className="rp-toolbar-label">즐겨찾기</span>
          </button>
          {!activeEntry?.isDashboard && (
            <div className="rp-toolbar-fitgroup">
              <button className={`rp-toolbar-fitbtn${activeDisplay === "FitToPage" ? " active" : ""}`} aria-pressed={activeDisplay === "FitToPage"} title="페이지에 맞춤" onClick={() => setDisplay("FitToPage")}>
                {activeDisplay === "FitToPage" && <span aria-hidden="true">✓ </span>}맞춤
              </button>
              <button className={`rp-toolbar-fitbtn${activeDisplay === "FitToWidth" ? " active" : ""}`} aria-pressed={activeDisplay === "FitToWidth"} title="폭에 맞춤" onClick={() => setDisplay("FitToWidth")}>
                {activeDisplay === "FitToWidth" && <span aria-hidden="true">✓ </span>}폭맞춤
              </button>
              <button className={`rp-toolbar-fitbtn${activeDisplay === "ActualSize" ? " active" : ""}`} aria-pressed={activeDisplay === "ActualSize"} title="실제 크기" onClick={() => setDisplay("ActualSize")}>
                {activeDisplay === "ActualSize" && <span aria-hidden="true">✓ </span>}실제크기
              </button>
            </div>
          )}
          <button className="rp-report-toolbar-fav" title="전체화면" onClick={goFullscreen}>
            <Maximize size={16} className="icn" />
            <span className="rp-toolbar-label">전체화면</span>
          </button>
        </div>
      )}
      <div className="rp-panels">
        {tabs.map((t) => (
          <ReportPanel
            key={t.id}
            id={t.id}
            active={t.id === active}
            onReady={onReady}
          />
        ))}
      </div>
    </div>
  );
}

function ReportLanding({
  reports: _reports,
  canUpload,
  onGoUpload,
  onOpen: _onOpen,
  browserMode: _browserMode,
  onBrowserMode: _onBrowserMode,
}: {
  reports: ReportItem[];
  canUpload: boolean;
  onGoUpload: () => void;
  onOpen: (report: ReportItem) => void;
  browserMode: BrowserMode;
  onBrowserMode: (mode: BrowserMode) => void;
}) {
  return (
    <div className="rp-landing">
      <div className="rp-landing-head">
        <div>
          <h1 className="rp-landing-hi">열람 보고서</h1>
          <p className="rp-landing-sub">권한이 있거나 내가 올린, 열람 가능한 보고서입니다</p>
        </div>
        {canUpload && (
          <button className="btn btn-ghost" onClick={onGoUpload}>
            <Upload size={16} className="icn" /> 새 보고서 등록
          </button>
        )}
      </div>
    </div>
  );
}

function ReportPanel({
  id,
  active,
  onReady,
}: {
  id: number;
  active: boolean;
  onReady: (rid: number, report: pbi.Report, isDashboard: boolean, container: HTMLDivElement) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let renewTimer: number | undefined;
    const el = ref.current;

    // 임베드 토큰(1시간) 만료 5분 전에 재발급해 iframe이 죽지 않게 한다.
    // 실패하면 1분 뒤 재시도 — 만료 전까지 여러 번 기회가 있다.
    const scheduleRenew = (report: pbi.Embed, expiresAt: number) => {
      const delay = Math.max(expiresAt * 1000 - Date.now() - 5 * 60 * 1000, 30_000);
      renewTimer = window.setTimeout(async () => {
        if (cancelled) return;
        try {
          const d = await fetchEmbed(id);
          if (cancelled) return;
          await report.setAccessToken(d.embed_token);
          scheduleRenew(report, d.expires_at);
        } catch {
          if (!cancelled) scheduleRenew(report, Date.now() / 1000 + 6 * 60);
        }
      }, delay);
    };

    (async () => {
      try {
        const d = await fetchEmbed(id);
        if (cancelled || !el) return;
        const s = d.settings || {};
        const isDashboard = s.tab_type === "dashboard";
        // GET 필터(부서) — 대시보드는 대상 아님, report_id 바뀔 때마다 새로 계산.
        const gf = !isDashboard && d.get_filter ? d.get_filter : null;
        // 대시보드는 페이지·필터창 개념이 없어 report 전용 설정을 넣으면 SDK가
        // 무시하거나 오류를 낼 수 있다 — 타입별로 별도 config를 만든다 (v6).
        const config: pbi.IEmbedConfiguration = isDashboard
          ? {
              type: "dashboard",
              id: d.report_id,
              embedUrl: d.embed_url,
              accessToken: d.embed_token,
              tokenType: pbi.models.TokenType.Embed,
            }
          : {
              type: "report",
              id: d.report_id,
              embedUrl: d.embed_url,
              accessToken: d.embed_token,
              tokenType: pbi.models.TokenType.Embed,
              // GET 필터(부서, PoC — 진짜 RLS 아님) 적용 대상 보고서만 값이 들어간다.
              filters: gf ? [buildGetFilter(gf.table, gf.column, gf.value)] : undefined,
              // 보고서별 차등 설정이 필요 없어 상수로 고정한다 (예전 DB 컬럼은 v12에서 제거).
              //  · 필터창은 끈다. 페이지 탭은 상단 툴바의 커스텀 드롭다운을 없앤 대신
              //    네이티브 하단 탭(pageNavigation)을 다시 켜서 페이지 이동을 지원한다.
              settings: {
                navContentPaneEnabled: false,
                filterPaneEnabled: false,
                layoutType: pbi.models.LayoutType.Custom,
                customLayout: {
                  // 이 보고서에 대해 전에 골라둔 맞춤 모드가 있으면(설정/관리를 갔다 온
                  // 뒤 재마운트되는 경우 포함) 그대로 이어서 연다 — 없으면 기본값.
                  displayOption: pbi.models.DisplayOption[loadDisplayModes()[id] ?? "FitToPage"],
                },
                panes: {
                  // PBIX 내부의 '메인' 같은 페이지 탭은 숨기고 포털 탭만 노출한다.
                  pageNavigation: { visible: false },
                  filters: { visible: false },
                },
                // 시각화 우측 상단에 뜨는 드릴 업/다운 아이콘 제거
                commands: [
                  { drill: { displayOption: pbi.models.CommandDisplayOption.Hidden } },
                ],
              },
            };
        const report = powerbi.embed(el, config);
        scheduleRenew(report, d.expires_at);
        onReady(id, report as pbi.Report, isDashboard, el);
        report.on("loaded", () => {
          if (cancelled) return;
          setLoading(false);
        });
        report.on("error", (ev: any) => {
          if (cancelled) return;
          setLoading(false);
          // Power BI SDK가 주는 ev.detail은 개발자용 원시 객체라 그대로 보여주면
          // 사용자가 못 알아본다 — 콘솔에는 남기고 화면엔 사람이 읽을 문장만 노출.
          console.error("Power BI embed error", ev.detail);
          setError("보고서를 불러오는 중 문제가 발생했습니다. 새로고침해도 안 되면 관리자에게 문의하세요.");
        });
      } catch (e) {
        if (!cancelled) {
          setLoading(false);
          setError(`보고서 로드 실패: ${(e as Error).message}`);
        }
      }
    })();
    return () => {
      cancelled = true;
      if (renewTimer) window.clearTimeout(renewTimer);
      if (el) powerbi.reset(el);
    };
  }, [id, onReady]);

  return (
    <div className={`rp-panel${active ? " active" : ""}`}>
      {loading && !error && (
        <div className="rp-panel-loading">보고서 불러오는 중...</div>
      )}
      {error && <div className="rp-panel-error">{error}</div>}
      <div className="rp-embed" ref={ref} />
    </div>
  );
}

/* ── 전체 보고서 (검색 + 표) ───────────────────────────── */
export function AllReportsView({
  reports,
  query,
  onQuery,
  isFav,
  canUpload,
  onToggleFav,
  onOpen,
  onGoUpload,
}: {
  reports: ReportItem[];
  query: string;
  onQuery: (q: string) => void;
  isFav: (id: number) => boolean;
  canUpload: boolean;
  onToggleFav: (id: number) => void;
  onOpen: (r: ReportItem) => void;
  onGoUpload: () => void;
}) {
  const filtered = useMemo(() => {
    const k = query.trim().toLowerCase();
    if (!k) return reports;
    return reports.filter(
      (r) =>
        r.name.toLowerCase().includes(k) ||
        (r.category || "").toLowerCase().includes(k) ||
        (r.description || "").toLowerCase().includes(k),
    );
  }, [query, reports]);

  const [preview, setPreview] = useState<ReportItem | null>(null);

  const [pageSize, tableRef] = useFitRows(42, 44); // 화면 높이에 맞춰 행 수 자동 — 스크롤 없이
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  useEffect(() => setPage(1), [query, pageSize]);
  const cur = Math.min(page, totalPages);
  const pageItems = filtered.slice((cur - 1) * pageSize, cur * pageSize);

  return (
    <div className="rp-page rp-page-fit">
      <div className="rp-page-head">
        <h1 className="rp-page-title">전체 보고서</h1>
        {canUpload && (
          <button className="btn btn-primary" onClick={onGoUpload}>
            <Upload size={15} className="icn" /> 보고서 등록
          </button>
        )}
      </div>
      <div className="rp-search">
        <Search size={17} className="icn rp-search-icon" />
        <input
          placeholder="보고서 이름·카테고리·설명으로 검색"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />
      </div>

      <div className="card-table rp-all-table rp-fit-table" ref={tableRef}>
        <table>
          {/* 유형·소유자 컬럼은 뺐다 — 공용 보고서가 대부분이라 정보량이 없고,
              개인/소유자 구분이 필요하면 행 클릭 → 미리보기 모달에서 보인다 */}
          <colgroup>
            <col style={{ width: "62%" }} />
            <col style={{ width: "38%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>보고서 명</th>
              <th>카테고리</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((r) => (
              <tr
                key={r.id}
                className="rp-all-row"
                onClick={() => setPreview(r)}
                title="클릭하여 미리보기"
              >
                <td className="rp-all-name">
                  {r.report_type === "dashboard"
                    ? <LayoutDashboard size={15} className="icn" />
                    : <BarChart3 size={15} className="icn" />} {r.name}
                  {r.description && (
                    <span className="rp-all-desc">{r.description}</span>
                  )}
                </td>
                <td>{r.category || "-"}</td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={2} className="rp-all-empty">
                  표시할 보고서가 없습니다
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Pager
        page={cur}
        totalPages={totalPages}
        total={filtered.length}
        onPage={setPage}
      />

      {preview && (
        <ReportInfoModal
          report={preview}
          fav={isFav(preview.id)}
          onToggleFav={() => onToggleFav(preview.id)}
          onOpen={() => {
            onOpen(preview);
            setPreview(null);
          }}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}
