import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pbi from "powerbi-client";
import {
  BarChart3,
  ChevronDown,
  Clock,
  Folder,
  Home as HomeIcon,
  Info,
  LayoutList,
  Search,
  Star,
  Upload,
  X,
} from "lucide-react";
import type { ReportData, ReportItem } from "../bootstrap";
import { fetchEmbed, fetchUploadStatus } from "../api";
import { useFavorites } from "../useFavorites";
import { useRecents } from "../useRecents";

/* 즐겨찾기 별 토글 버튼 */
function FavStar({
  on,
  onToggle,
  size = 17,
}: {
  on: boolean;
  onToggle: () => void;
  size?: number;
}) {
  return (
    <button
      type="button"
      className={`fav-star${on ? " on" : ""}`}
      title={on ? "즐겨찾기 해제" : "즐겨찾기 추가"}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
    >
      <Star size={size} className="icn" fill={on ? "currentColor" : "none"} />
    </button>
  );
}

// PowerBI 서비스 싱글턴 (탭 전체가 공유)
const powerbi = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
);

type View = "my" | "all" | "upload";
interface OpenTab {
  id: number;
  name: string;
}

const TABS_KEY = "open-tabs";
const ACTIVE_KEY = "active-tab";
const GROUPS_KEY = "sb-groups";

function loadTabs(): OpenTab[] {
  try {
    return JSON.parse(sessionStorage.getItem(TABS_KEY) || "[]");
  } catch {
    return [];
  }
}

type Mode = "home" | "reports";
const MODE_KEY = "rp-mode";

export default function ReportPage({ data }: { data: ReportData }) {
  const { user, reports, csrf_token } = data;

  const { isFav, toggle: toggleFav } = useFavorites(data.favorites, csrf_token);
  const { recents, push: pushRecent } = useRecents(data.recents, csrf_token);
  const [mode, setMode] = useState<Mode>(
    () => (sessionStorage.getItem(MODE_KEY) as Mode) || "home",
  );
  const [view, setView] = useState<View>("my");
  const [allQuery, setAllQuery] = useState("");
  const [tabs, setTabs] = useState<OpenTab[]>(() => loadTabs());
  const [active, setActive] = useState<number | null>(
    () => Number(sessionStorage.getItem(ACTIVE_KEY)) || null,
  );

  useEffect(() => {
    sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs));
    sessionStorage.setItem(ACTIVE_KEY, String(active ?? ""));
  }, [tabs, active]);

  const goMode = useCallback((m: Mode) => {
    setMode(m);
    sessionStorage.setItem(MODE_KEY, m);
  }, []);

  const openReport = useCallback(
    (report: ReportItem) => {
      setTabs((prev) =>
        prev.some((t) => t.id === report.id)
          ? prev
          : [...prev, { id: report.id, name: report.name }],
      );
      setActive(report.id);
      setView("my");
      pushRecent(report.id);
      goMode("reports");
    },
    [pushRecent, goMode],
  );

  const closeTab = useCallback((id: number) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      setActive((cur) =>
        cur !== id ? cur : next.length ? next[next.length - 1].id : null,
      );
      return next;
    });
  }, []);

  const runSearch = useCallback(
    (q: string) => {
      setAllQuery(q);
      setView("all");
      goMode("reports");
    },
    [goMode],
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <button
          className="topbar-brand"
          title="홈으로"
          onClick={() => goMode("home")}
        >
          <span className="brand">
            <span className="b-quali">quali</span>
            <span className="b-soft">soft</span>
          </span>
        </button>
        <nav className="topbar-nav">
          <button
            className={`topbar-link${mode === "home" ? " active" : ""}`}
            onClick={() => goMode("home")}
          >
            <HomeIcon size={16} className="icn" /> 홈
          </button>
          <button
            className={`topbar-link${mode === "reports" ? " active" : ""}`}
            onClick={() => goMode("reports")}
          >
            <BarChart3 size={16} className="icn" /> 리포트
          </button>
        </nav>
        <div className="topbar-spacer" />
        <div className="topbar-right">
          <span className="topbar-user">{user.display_name}</span>
          {user.is_admin && (
            <a
              href="/admin"
              className="topbar-btn"
              target="qualisoft-admin"
              rel="noopener"
              title="새 탭에서 열림 — 보고서 뷰어는 그대로 유지됩니다"
            >
              관리자 포털 ↗
            </a>
          )}
          <a
            href="/logout"
            className="topbar-btn primary"
            onClick={() => sessionStorage.clear()}
          >
            로그아웃
          </a>
        </div>
      </header>

      {mode === "home" ? (
        <Home
          reports={reports}
          displayName={user.display_name}
          recentIds={recents}
          isFav={isFav}
          onToggleFav={toggleFav}
          onOpen={openReport}
          onSearch={runSearch}
          onGoAll={() => {
            setAllQuery("");
            setView("all");
            goMode("reports");
          }}
        />
      ) : (
        <div className="app-body">
          <Sidebar
            reports={reports}
            view={view}
            activeId={active}
            isFav={isFav}
            onSelectView={setView}
            onOpen={openReport}
          />
          <main className="app-main">
            {view === "my" && (
              <MyReportsView
                reports={reports}
                displayName={user.display_name}
                tabs={tabs}
                active={active}
                isFav={isFav}
                onToggleFav={toggleFav}
                onActivate={setActive}
                onClose={closeTab}
                onOpen={openReport}
                onGoUpload={() => setView("upload")}
              />
            )}
            {view === "all" && (
              <AllReportsView
                reports={reports}
                query={allQuery}
                onQuery={setAllQuery}
                isFav={isFav}
                onToggleFav={toggleFav}
                onOpen={openReport}
              />
            )}
            {view === "upload" && <UploadView csrf={csrf_token} />}
          </main>
        </div>
      )}
    </div>
  );
}

/* ── 홈 (메인 랜딩 — 05_main 스타일) ───────────────────── */
function Home({
  reports,
  displayName,
  recentIds,
  isFav,
  onToggleFav,
  onOpen,
  onSearch,
  onGoAll,
}: {
  reports: ReportItem[];
  displayName: string;
  recentIds: number[];
  isFav: (id: number) => boolean;
  onToggleFav: (id: number) => void;
  onOpen: (r: ReportItem) => void;
  onSearch: (q: string) => void;
  onGoAll: () => void;
}) {
  const [q, setQ] = useState("");
  const byId = useMemo(() => new Map(reports.map((r) => [r.id, r])), [reports]);
  const favReports = reports.filter((r) => isFav(r.id)).slice(0, 5);
  const recentReports = recentIds
    .map((id) => byId.get(id))
    .filter((r): r is ReportItem => Boolean(r))
    .slice(0, 5);

  return (
    <main className="home">
      <section className="home-hero">
        <div className="home-hero-deco" aria-hidden />
        <h1 className="home-headline">
          Business Innovation <span className="thin">by</span>
          <br />
          Data Driven <span className="accent">Analytics</span>
        </h1>
        <p className="home-greet">
          {displayName}님, qualisoft BI 포털에 오신 것을 환영합니다
        </p>
        <form
          className="home-search"
          onSubmit={(e) => {
            e.preventDefault();
            onSearch(q.trim());
          }}
        >
          <Search size={19} className="icn home-search-icon" />
          <input
            placeholder="보고서를 검색하세요"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button type="submit" className="btn btn-primary">
            검색
          </button>
        </form>
      </section>

      <section className="home-cards">
        <HomeCard
          title="즐겨찾기"
          Icon={Star}
          accent="#f5b301"
          empty="별표한 보고서가 여기 모입니다"
          items={favReports}
          onOpen={onOpen}
          onToggleFav={onToggleFav}
          isFav={isFav}
        />
        <HomeCard
          title="최근 본 보고서"
          Icon={Clock}
          empty="최근 연 보고서가 없습니다"
          items={recentReports}
          onOpen={onOpen}
          onToggleFav={onToggleFav}
          isFav={isFav}
        />
        <HomeCard
          title="전체 보고서"
          Icon={LayoutList}
          empty="열람 가능한 보고서가 없습니다"
          items={reports.slice(0, 5)}
          onOpen={onOpen}
          onToggleFav={onToggleFav}
          isFav={isFav}
          footer={
            <button className="home-card-more" onClick={onGoAll}>
              전체 보기 ({reports.length}) →
            </button>
          }
        />
      </section>
    </main>
  );
}

function HomeCard({
  title,
  Icon,
  accent,
  empty,
  items,
  isFav,
  onOpen,
  onToggleFav,
  footer,
}: {
  title: string;
  Icon: typeof Star;
  accent?: string;
  empty: string;
  items: ReportItem[];
  isFav: (id: number) => boolean;
  onOpen: (r: ReportItem) => void;
  onToggleFav: (id: number) => void;
  footer?: React.ReactNode;
}) {
  return (
    <div className="home-card">
      <div className="home-card-head">
        <span className="home-card-title">{title}</span>
        <span className="home-card-badge" style={accent ? { color: accent } : undefined}>
          <Icon size={18} className="icn" />
        </span>
      </div>
      <div className="home-card-list">
        {items.length === 0 ? (
          <div className="home-card-empty">{empty}</div>
        ) : (
          items.map((r, i) => (
            <div key={r.id} className="home-row" onClick={() => onOpen(r)}>
              <span className="home-row-no">{i + 1}</span>
              <BarChart3 size={15} className="icn home-row-icon" />
              <span className="home-row-name" title={r.name}>
                {r.name}
              </span>
              <FavStar
                size={15}
                on={isFav(r.id)}
                onToggle={() => onToggleFav(r.id)}
              />
            </div>
          ))
        )}
      </div>
      {footer && <div className="home-card-foot">{footer}</div>}
    </div>
  );
}

/* ── 사이드바 ─────────────────────────────────────────── */
function Sidebar({
  reports,
  view,
  activeId,
  isFav,
  onSelectView,
  onOpen,
}: {
  reports: ReportItem[];
  view: View;
  activeId: number | null;
  isFav: (id: number) => boolean;
  onSelectView: (v: View) => void;
  onOpen: (r: ReportItem) => void;
}) {
  const favReports = reports.filter((r) => isFav(r.id));
  const { grouped, uncategorized } = useMemo(() => {
    const g = new Map<string, ReportItem[]>();
    const u: ReportItem[] = [];
    for (const r of reports) {
      if (r.category) {
        if (!g.has(r.category)) g.set(r.category, []);
        g.get(r.category)!.push(r);
      } else u.push(r);
    }
    return { grouped: g, uncategorized: u };
  }, [reports]);

  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(GROUPS_KEY) || "{}");
    } catch {
      return {};
    }
  });
  const toggle = (cat: string) =>
    setCollapsed((prev) => {
      const next = { ...prev, [cat]: !prev[cat] };
      sessionStorage.setItem(GROUPS_KEY, JSON.stringify(next));
      return next;
    });

  return (
    <nav className="app-sidebar">
      <div className="app-sidebar-title">보고서</div>
      <div className="app-sidebar-scroll">
        <div
          className={`app-nav-item${view === "my" ? " active" : ""}`}
          onClick={() => onSelectView("my")}
        >
          <Folder size={17} className="icn" /> 내 보고서
        </div>

        {view === "my" && (
          <div className="rp-tree">
            {favReports.length > 0 && (
              <div className="rp-group">
                <div className="rp-group-header rp-group-fav">
                  <Star size={12} className="icn" fill="currentColor" /> 즐겨찾기
                </div>
                <div className="rp-group-body">
                  {favReports.map((r) => (
                    <TreeItem
                      key={"fav-" + r.id}
                      report={r}
                      active={r.id === activeId}
                      onOpen={onOpen}
                      indent
                    />
                  ))}
                </div>
              </div>
            )}
            {[...grouped.entries()].map(([cat, items]) => (
              <div
                key={cat}
                className={`rp-group${collapsed[cat] ? " collapsed" : ""}`}
              >
                <div className="rp-group-header" onClick={() => toggle(cat)}>
                  <ChevronDown size={13} className="icn rp-group-arrow" /> {cat}
                </div>
                <div className="rp-group-body">
                  {items.map((r) => (
                    <TreeItem
                      key={r.id}
                      report={r}
                      active={r.id === activeId}
                      onOpen={onOpen}
                      indent
                    />
                  ))}
                </div>
              </div>
            ))}
            {uncategorized.map((r) => (
              <TreeItem
                key={r.id}
                report={r}
                active={r.id === activeId}
                onOpen={onOpen}
              />
            ))}
            {reports.length === 0 && (
              <div className="rp-tree-empty">열람 가능한 보고서가 없습니다</div>
            )}
          </div>
        )}

        <div
          className={`app-nav-item${view === "all" ? " active" : ""}`}
          onClick={() => onSelectView("all")}
        >
          <LayoutList size={17} className="icn" /> 전체 보고서
        </div>
        <div
          className={`app-nav-item${view === "upload" ? " active" : ""}`}
          onClick={() => onSelectView("upload")}
        >
          <Upload size={17} className="icn" /> 보고서 등록
        </div>
      </div>
    </nav>
  );
}

function TreeItem({
  report,
  active,
  onOpen,
  indent,
}: {
  report: ReportItem;
  active: boolean;
  onOpen: (r: ReportItem) => void;
  indent?: boolean;
}) {
  return (
    <div
      className={`rp-tree-item${active ? " active" : ""}${indent ? " indent" : ""}`}
      onClick={() => onOpen(report)}
      title={report.name}
    >
      <BarChart3 size={15} className="icn" />
      <span className="rp-tree-label">{report.name}</span>
    </div>
  );
}

/* ── 내 보고서 (랜딩 / 탭 + 임베드, 탭은 하단) ─────────── */
function MyReportsView({
  reports,
  displayName,
  tabs,
  active,
  isFav,
  onToggleFav,
  onActivate,
  onClose,
  onOpen,
  onGoUpload,
}: {
  reports: ReportItem[];
  displayName: string;
  tabs: OpenTab[];
  active: number | null;
  isFav: (id: number) => boolean;
  onToggleFav: (id: number) => void;
  onActivate: (id: number) => void;
  onClose: (id: number) => void;
  onOpen: (r: ReportItem) => void;
  onGoUpload: () => void;
}) {
  if (tabs.length === 0) {
    return (
      <ReportLanding
        reports={reports}
        displayName={displayName}
        isFav={isFav}
        onToggleFav={onToggleFav}
        onOpen={onOpen}
        onGoUpload={onGoUpload}
      />
    );
  }
  return (
    <div className="rp-workarea">
      <div className="rp-panels">
        {tabs.map((t) => (
          <ReportPanel key={t.id} id={t.id} active={t.id === active} />
        ))}
      </div>
      {/* 탭 바 — 하단 */}
      <div className="rp-tabsbar">
        {tabs.map((t) => (
          <div
            key={t.id}
            className={`rp-tab${t.id === active ? " active" : ""}`}
            onClick={() => onActivate(t.id)}
          >
            <BarChart3 size={14} className="icn" />
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
      </div>
    </div>
  );
}

function ReportLanding({
  reports,
  displayName,
  isFav,
  onToggleFav,
  onOpen,
  onGoUpload,
}: {
  reports: ReportItem[];
  displayName: string;
  isFav: (id: number) => boolean;
  onToggleFav: (id: number) => void;
  onOpen: (r: ReportItem) => void;
  onGoUpload: () => void;
}) {
  const favReports = reports.filter((r) => isFav(r.id));

  const grid = (items: ReportItem[]) => (
    <div className="rp-card-grid">
      {items.map((r) => (
        <div
          key={r.id}
          className="rp-card"
          role="button"
          tabIndex={0}
          onClick={() => onOpen(r)}
          onKeyDown={(e) => e.key === "Enter" && onOpen(r)}
        >
          <div className="rp-card-icon">
            <BarChart3 size={22} className="icn" />
          </div>
          <div className="rp-card-body">
            <div className="rp-card-name" title={r.name}>
              {r.name}
            </div>
            <div className="rp-card-meta">
              {r.category ? (
                <span className="rp-card-tag">{r.category}</span>
              ) : (
                <span className="rp-card-tag muted">미분류</span>
              )}
            </div>
          </div>
          <FavStar on={isFav(r.id)} onToggle={() => onToggleFav(r.id)} />
        </div>
      ))}
    </div>
  );

  return (
    <div className="rp-landing">
      <div className="rp-landing-head">
        <div>
          <h1 className="rp-landing-hi">{displayName}님, 안녕하세요 👋</h1>
          <p className="rp-landing-sub">열람할 보고서를 선택해 시작하세요</p>
        </div>
        <button className="btn btn-ghost" onClick={onGoUpload}>
          <Upload size={16} className="icn" /> 새 보고서 등록
        </button>
      </div>

      {reports.length === 0 ? (
        <div className="rp-landing-empty">
          <BarChart3 size={52} className="icn" />
          <p>아직 열람 가능한 보고서가 없습니다</p>
        </div>
      ) : (
        <>
          {favReports.length > 0 && (
            <>
              <h2 className="rp-landing-section">
                <Star size={16} className="icn" fill="currentColor" /> 즐겨찾기
              </h2>
              {grid(favReports)}
            </>
          )}
          <h2 className="rp-landing-section">전체 보고서</h2>
          {grid(reports)}
        </>
      )}
    </div>
  );
}

function ReportPanel({ id, active }: { id: number; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const el = ref.current;
    (async () => {
      try {
        const d = await fetchEmbed(id);
        if (cancelled || !el) return;
        const s = d.settings || {};
        const config: pbi.IEmbedConfiguration = {
          type: "report",
          id: d.report_id,
          embedUrl: d.embed_url,
          accessToken: d.embed_token,
          tokenType: pbi.models.TokenType.Embed,
          settings: {
            navContentPaneEnabled: Boolean(s.enable_page_nav),
            filterPaneEnabled: Boolean(s.enable_filter),
            layoutType: pbi.models.LayoutType.Custom,
            customLayout: {
              displayOption: pbi.models.DisplayOption.FitToWidth,
            },
            panes: {
              pageNavigation: { visible: Boolean(s.enable_page_nav) },
              filters: { visible: Boolean(s.enable_filter), expanded: false },
            },
          },
        };
        if (s.default_page) config.pageName = s.default_page;
        const report = powerbi.embed(el, config);
        report.on("loaded", () => !cancelled && setLoading(false));
        report.on("error", (ev: any) => {
          if (cancelled) return;
          setLoading(false);
          setError(`오류: ${JSON.stringify(ev.detail)}`);
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
      if (el) powerbi.reset(el);
    };
  }, [id]);

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
function AllReportsView({
  reports,
  query,
  onQuery,
  isFav,
  onToggleFav,
  onOpen,
}: {
  reports: ReportItem[];
  query: string;
  onQuery: (q: string) => void;
  isFav: (id: number) => boolean;
  onToggleFav: (id: number) => void;
  onOpen: (r: ReportItem) => void;
}) {
  const filtered = useMemo(() => {
    const k = query.trim().toLowerCase();
    if (!k) return reports;
    return reports.filter(
      (r) =>
        r.name.toLowerCase().includes(k) ||
        (r.category || "").toLowerCase().includes(k),
    );
  }, [query, reports]);

  return (
    <div className="rp-page">
      <h1 className="rp-page-title">전체 보고서</h1>
      <div className="rp-search">
        <Search size={17} className="icn rp-search-icon" />
        <input
          placeholder="검색어를 입력하세요"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />
      </div>

      <div className="card-table rp-all-table">
        <table>
          <colgroup>
            <col style={{ width: "44%" }} />
            <col style={{ width: "22%" }} />
            <col style={{ width: "22%" }} />
            <col style={{ width: "12%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>보고서 명</th>
              <th>카테고리</th>
              <th>소유자명</th>
              <th style={{ textAlign: "center" }}>즐겨찾기</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr
                key={r.id}
                className="rp-all-row"
                onClick={() => onOpen(r)}
                title="클릭하여 열기"
              >
                <td className="rp-all-name">
                  <BarChart3 size={15} className="icn" /> {r.name}
                </td>
                <td>{r.category || "-"}</td>
                <td>
                  {r.owner_username ||
                    (r.report_type === "managed" ? "공용" : "-")}
                </td>
                <td style={{ textAlign: "center" }}>
                  <FavStar
                    on={isFav(r.id)}
                    onToggle={() => onToggleFav(r.id)}
                  />
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} className="rp-all-empty">
                  표시할 보고서가 없습니다
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="rp-total">Total {filtered.length} records</div>
    </div>
  );
}

/* ── 보고서 등록 (업로드 페이지) ───────────────────────── */
const DONE_STATES = new Set(["completed", "failed", "conflict", "unknown"]);
const STATUS_LABELS: Record<string, string> = {
  accepted: "PBI 접수됨",
  publishing: "PBI 변환 중",
  pbi_succeeded: "DB 등록 중",
};

function UploadView({ csrf }: { csrf: string }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
  const [reportName, setReportName] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ msg: string; tone: "" | "ok" | "err" }>(
    { msg: "", tone: "" },
  );

  const submit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setStatus({ msg: ".pbix 파일을 선택해 주세요.", tone: "err" });
      return;
    }
    setBusy(true);
    setStatus({ msg: `'${file.name}' 전송 중...`, tone: "" });
    try {
      const accepted = await uploadWithName(file, reportName.trim(), csrf);
      const jobId = accepted.job_id;
      const name = accepted.report_name;
      setStatus({ msg: `'${name}' PBI 게시 중... (보통 30초~2분)`, tone: "" });

      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await fetchUploadStatus(jobId, csrf);
        if (s.status === "completed") {
          setStatus({
            msg: `'${name}' 게시 완료! 목록을 새로고침합니다...`,
            tone: "ok",
          });
          sessionStorage.removeItem(TABS_KEY);
          sessionStorage.removeItem(ACTIVE_KEY);
          setTimeout(() => location.reload(), 1500);
          return;
        }
        if (DONE_STATES.has(s.status))
          throw new Error(s.error || `게시 실패 (${s.status})`);
        setStatus({
          msg: `'${name}' ${STATUS_LABELS[s.status] || s.status}... (보통 30초~2분)`,
          tone: "",
        });
      }
    } catch (e) {
      setStatus({ msg: `업로드 실패: ${(e as Error).message}`, tone: "err" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rp-page">
      <h1 className="rp-page-title">보고서 등록</h1>
      <div className="rp-form-card">
        <div className="rp-field">
          <label>보고서 명</label>
          <input
            placeholder="비워 두면 파일명이 보고서 이름이 됩니다"
            value={reportName}
            onChange={(e) => setReportName(e.target.value)}
            disabled={busy}
          />
        </div>

        <div className="rp-field">
          <label>
            Report 파일 선택 <span className="rp-req">(.pbix) *</span>
          </label>
          <div className="rp-filepick">
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              파일 선택
            </button>
            <span className="rp-filename">{fileName || "선택된 파일 없음"}</span>
            <input
              ref={fileRef}
              type="file"
              accept=".pbix"
              hidden
              onChange={(e) => setFileName(e.target.files?.[0]?.name || "")}
            />
          </div>
        </div>

        <div className="rp-upload-note">
          <Info size={15} className="icn" /> 업로드한 보고서는 본인 폴더로 자동
          분류됩니다. RLS가 필요하면 관리자에게 요청하세요.
        </div>

        {status.msg && (
          <div className={`rp-upload-feedback ${status.tone}`}>{status.msg}</div>
        )}

        <div className="rp-form-actions">
          <button className="btn btn-primary" disabled={busy} onClick={submit}>
            {busy ? "처리 중..." : "파일 업로드"}
          </button>
        </div>
      </div>
    </div>
  );
}

// report_name 을 함께 전송하는 업로드 (api.uploadPbix 는 file 만 전송)
async function uploadWithName(file: File, reportName: string, csrf: string) {
  const fd = new FormData();
  fd.append("file", file);
  if (reportName) fd.append("report_name", reportName);
  const res = await fetch("/api/upload", {
    method: "POST",
    body: fd,
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as any).detail;
    const msg =
      (typeof detail === "object" ? detail?.message : detail) || res.statusText;
    throw new Error(msg);
  }
  return data as { job_id: number; report_name: string; status: string };
}
