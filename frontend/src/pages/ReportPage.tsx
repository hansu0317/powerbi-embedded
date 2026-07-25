import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pbi from "powerbi-client";
import {
  BarChart3,
  ChevronDown,
  Clock,
  Download,
  FileArchive,
  Folder,
  Home as HomeIcon,
  Info,
  LayoutDashboard,
  LayoutList,
  Maximize,
  Pin,
  Search,
  Star,
  TrendingUp,
  Upload,
  X,
} from "lucide-react";
import type { ReportData, ReportItem, SessionUser } from "../bootstrap";
import {
  fetchEmbed, fetchUploadStatus, logout, setDefaultReport, uploadPbix,
  downloadReportPbix, startPptxExport, pollPptxExport, downloadPptxExport,
  fetchMyActivity, MyActivityRow, startReportUpdate,
} from "../api";
import { useFavorites } from "../useFavorites";
import { useRecents } from "../useRecents";
import { Pager, useFitRows } from "../Pager";

// PowerBI 서비스 싱글턴 (탭 전체가 공유)
const powerbi = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
);

/** Blob을 파일로 내려받게 한다 (PBIX/PPTX 다운로드 공통). */
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

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
  const canUpload = user.can_upload !== false;

  const { isFav, toggle: toggleFav } = useFavorites(data.favorites, csrf_token);
  const { recents, push: pushRecent } = useRecents(data.recents, csrf_token);

  // 기본 보고서: 서버 저장값을 초기값으로, 핀 토글 시 즉시 갱신 (낙관적 UI)
  const [defaultId, setDefaultId] = useState<number | null>(
    user.default_report_id ?? null,
  );
  const toggleDefault = useCallback(
    (id: number) => {
      const next = defaultId === id ? null : id;
      setDefaultId(next);
      setDefaultReport(next, csrf_token).catch(() => setDefaultId(defaultId));
    },
    [defaultId, csrf_token],
  );

  // 열람 보고서 = 열람 가능한 보고서 전체(폴더 트리).
  //  - 관리자: 모든 보고서(권한과 무관하게 다 봄)
  //  - 일반 사용자: 관리자 포털에서 열람권한을 부여받은 보고서 + 본인 업로드
  // 메인 영역은 카드로 쏟지 않고 '선택하세요' 안내만 (트리에서 고름).
  const myReports = reports;
  const [mode, setMode] = useState<Mode>(
    () => (sessionStorage.getItem(MODE_KEY) as Mode) || "home",
  );
  const [view, setView] = useState<View>("my");
  const [showActivity, setShowActivity] = useState(false);
  const [allQuery, setAllQuery] = useState("");
  const [tabs, setTabs] = useState<OpenTab[]>(() => loadTabs());
  const [active, setActive] = useState<number | null>(
    () => Number(sessionStorage.getItem(ACTIVE_KEY)) || null,
  );

  useEffect(() => {
    sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs));
    sessionStorage.setItem(ACTIVE_KEY, String(active ?? ""));
  }, [tabs, active]);

  // 기본 보고서 자동 열기 — 새 세션(복원할 탭 없음)에서만. 기존 작업 흐름은 방해하지 않는다.
  const autoOpened = useRef(false);
  useEffect(() => {
    if (autoOpened.current || tabs.length > 0 || !defaultId) return;
    const target = reports.find((r) => r.id === defaultId);
    if (target) {
      autoOpened.current = true;
      openReport(target);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          <button className="topbar-btn" onClick={() => setShowActivity(true)}>
            내 활동
          </button>
          {data.marketing_portal_url && (
            <a
              href={data.marketing_portal_url}
              target="_blank"
              rel="noreferrer"
              className="topbar-btn"
            >
              ↗ 마케팅 포털
            </a>
          )}
          {user.is_admin && (
            <a href="/admin" className="topbar-btn">
              관리자 포털
            </a>
          )}
          <button
            className="topbar-btn primary"
            onClick={async () => {
              sessionStorage.clear();
              await logout(csrf_token);
              window.location.href = "/login";
            }}
          >
            로그아웃
          </button>
        </div>
      </header>
      {showActivity && <MyActivityModal onClose={() => setShowActivity(false)} />}

      {mode === "home" ? (
        <Home
          reports={reports}
          displayName={user.display_name}
          isAdmin={Boolean(user.is_admin)}
          recentIds={recents}
          popular={data.popular || []}
          isFav={isFav}
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
            myReports={myReports}
            view={view}
            activeId={active}
            isAdmin={Boolean(user.is_admin)}
            canUpload={canUpload}
            isFav={isFav}
            onSelectView={setView}
            onOpen={openReport}
          />
          <main className="app-main">
            {view === "my" && (
              <MyReportsView
                reports={myReports}
                tabs={tabs}
                active={active}
                isFav={isFav}
                defaultId={defaultId}
                onToggleDefault={toggleDefault}
                canUpload={canUpload}
                onToggleFav={toggleFav}
                onActivate={setActive}
                onClose={closeTab}
                onGoUpload={() => setView("upload")}
                csrf={csrf_token}
                user={user}
              />
            )}
            {view === "all" && (
              <AllReportsView
                reports={reports}
                query={allQuery}
                onQuery={setAllQuery}
                isFav={isFav}
                canUpload={canUpload}
                onToggleFav={toggleFav}
                onOpen={openReport}
                onGoUpload={() => setView("upload")}
              />
            )}
            {view === "upload" && canUpload && <UploadView csrf={csrf_token} />}
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
  isAdmin,
  recentIds,
  popular,
  isFav,
  onOpen,
  onSearch,
  onGoAll,
}: {
  reports: ReportItem[];
  displayName: string;
  isAdmin: boolean;
  recentIds: number[];
  popular: { report_id: number; views: number }[];
  isFav: (id: number) => boolean;
  onOpen: (r: ReportItem) => void;
  onSearch: (q: string) => void;
  onGoAll: () => void;
}) {
  const [q, setQ] = useState("");
  const [openSuggest, setOpenSuggest] = useState(false);
  const byId = useMemo(() => new Map(reports.map((r) => [r.id, r])), [reports]);
  const favReports = reports.filter((r) => isFav(r.id)).slice(0, 4);
  const recentReports = recentIds
    .map((id) => byId.get(id))
    .filter((r): r is ReportItem => Boolean(r))
    .slice(0, 4);
  const popularReports = popular
    .map((p) => byId.get(p.report_id))
    .filter((r): r is ReportItem => Boolean(r))
    .slice(0, 4);

  const suggestions = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return [];
    return reports
      .filter(
        (r) =>
          r.name.toLowerCase().includes(k) ||
          (r.category || "").toLowerCase().includes(k) ||
          (r.description || "").toLowerCase().includes(k),
      )
      .slice(0, 8);
  }, [q, reports]);

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
            setOpenSuggest(false);
          }}
        >
          <Search size={19} className="icn home-search-icon" />
          <input
            placeholder="보고서를 검색하세요"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOpenSuggest(true);
            }}
            onFocus={() => setOpenSuggest(true)}
            onBlur={() => setTimeout(() => setOpenSuggest(false), 120)}
          />
          <button type="submit" className="btn btn-primary">
            검색
          </button>
          {openSuggest && suggestions.length > 0 && (
            <ul className="home-suggest">
              {suggestions.map((r) => (
                <li
                  key={r.id}
                  className="home-suggest-item"
                  onMouseDown={() => {
                    onOpen(r);
                    setOpenSuggest(false);
                  }}
                >
                  <BarChart3 size={15} className="icn" />
                  <span className="home-suggest-name">{r.name}</span>
                  {r.category && (
                    <span className="home-suggest-cat">{r.category}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
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
        />
        <HomeCard
          title="최근 본 보고서"
          Icon={Clock}
          empty="최근 연 보고서가 없습니다"
          items={recentReports}
          onOpen={onOpen}
        />
        <HomeCard
          title="인기 보고서"
          Icon={TrendingUp}
          accent="#e0763c"
          empty="최근 30일 조회 데이터가 쌓이면 표시됩니다"
          items={popularReports}
          onOpen={onOpen}
        />
        {isAdmin && (
          <HomeCard
            title="전체 보고서"
            Icon={LayoutList}
            empty="열람 가능한 보고서가 없습니다"
            items={reports.slice(0, 4)}
            onOpen={onOpen}
            footer={
              <button className="home-card-more" onClick={onGoAll}>
                전체 보기 ({reports.length}) →
              </button>
            }
          />
        )}
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
  onOpen,
  footer,
}: {
  title: string;
  Icon: typeof Star;
  accent?: string;
  empty: string;
  items: ReportItem[];
  onOpen: (r: ReportItem) => void;
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
          items.map((r) => (
            <div key={r.id} className="home-row" onClick={() => onOpen(r)} title={r.name}>
              <BarChart3 size={16} className="icn home-row-icon" />
              <span className="home-row-name">{r.name}</span>
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
  myReports,
  view,
  activeId,
  isAdmin,
  canUpload,
  isFav,
  onSelectView,
  onOpen,
}: {
  reports: ReportItem[];
  myReports: ReportItem[];
  view: View;
  activeId: number | null;
  isAdmin: boolean;
  canUpload: boolean;
  isFav: (id: number) => boolean;
  onSelectView: (v: View) => void;
  onOpen: (r: ReportItem) => void;
}) {
  const favReports = reports.filter((r) => isFav(r.id));
  const { folderTree, uncategorized } = useMemo(() => {
    // category("본부/팀" 경로 문자열)를 "/"로 쪼개 N단계 폴더 트리를 만든다
    const root = new Map<string, FolderNode>();
    const u: ReportItem[] = [];
    for (const r of myReports) {
      const parts = (r.category || "").split("/").filter(Boolean);
      if (parts.length === 0) {
        u.push(r);
        continue;
      }
      let level = root;
      let node: FolderNode | null = null;
      let path = "";
      for (const name of parts) {
        path = path ? `${path}/${name}` : name;
        if (!level.has(name)) level.set(name, { name, path, children: new Map(), reports: [] });
        node = level.get(name)!;
        level = node.children;
      }
      node!.reports.push(r);
    }
    return { folderTree: root, uncategorized: u };
  }, [myReports]);

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
          <Folder size={17} className="icn" /> 열람 보고서
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
            {[...folderTree.values()].map((node) => (
              <TreeGroup
                key={node.path}
                node={node}
                depth={0}
                collapsed={collapsed}
                onToggle={toggle}
                activeId={activeId}
                onOpen={onOpen}
              />
            ))}
            {uncategorized.map((r) => (
              <TreeItem
                key={r.id}
                report={r}
                active={r.id === activeId}
                onOpen={onOpen}
              />
            ))}
            {myReports.length === 0 && (
              <div className="rp-tree-empty">열람 가능한 보고서가 없습니다</div>
            )}
          </div>
        )}

        {isAdmin && (
          <div
            className={`app-nav-item${view === "all" ? " active" : ""}`}
            onClick={() => onSelectView("all")}
          >
            <LayoutList size={17} className="icn" /> 전체 보고서
          </div>
        )}
        {canUpload && (
          <div
            className={`app-nav-item${view === "upload" ? " active" : ""}`}
            onClick={() => onSelectView("upload")}
          >
            <Upload size={17} className="icn" /> 보고서 등록
          </div>
        )}
      </div>
    </nav>
  );
}

type FolderNode = {
  name: string;
  path: string; // "본부/팀" — 접기 상태 키
  children: Map<string, FolderNode>;
  reports: ReportItem[];
};

function TreeGroup({
  node,
  depth,
  collapsed,
  onToggle,
  activeId,
  onOpen,
}: {
  node: FolderNode;
  depth: number;
  collapsed: Record<string, boolean>;
  onToggle: (path: string) => void;
  activeId: number | null;
  onOpen: (r: ReportItem) => void;
}) {
  return (
    <div className={`rp-group${collapsed[node.path] ? " collapsed" : ""}`}>
      <div
        className="rp-group-header"
        style={{ paddingLeft: 30 + depth * 12 }}
        onClick={() => onToggle(node.path)}
      >
        <ChevronDown size={13} className="icn rp-group-arrow" /> {node.name}
      </div>
      <div className="rp-group-body">
        {[...node.children.values()].map((child) => (
          <TreeGroup
            key={child.path}
            node={child}
            depth={depth + 1}
            collapsed={collapsed}
            onToggle={onToggle}
            activeId={activeId}
            onOpen={onOpen}
          />
        ))}
        {node.reports.map((r) => (
          <TreeItem
            key={r.id}
            report={r}
            active={r.id === activeId}
            onOpen={onOpen}
            indent
            depth={depth}
          />
        ))}
      </div>
    </div>
  );
}

function TreeItem({
  report,
  active,
  onOpen,
  indent,
  depth,
}: {
  report: ReportItem;
  active: boolean;
  onOpen: (r: ReportItem) => void;
  indent?: boolean;
  depth?: number;
}) {
  return (
    <div
      className={`rp-tree-item${active ? " active" : ""}${indent ? " indent" : ""}`}
      style={depth ? { paddingLeft: 46 + depth * 12 } : undefined}
      onClick={() => onOpen(report)}
      title={report.name}
    >
      {report.report_type === "dashboard"
        ? <LayoutDashboard size={15} className="icn" />
        : <BarChart3 size={15} className="icn" />}
      <span className="rp-tree-label">{report.name}</span>
    </div>
  );
}

/* ── 내 보고서 (랜딩 / 탭 + 임베드, 탭은 하단) ─────────── */
function MyReportsView({
  reports,
  tabs,
  active,
  isFav,
  defaultId,
  onToggleDefault,
  canUpload,
  onToggleFav,
  onActivate,
  onClose,
  onGoUpload,
  csrf,
  user,
}: {
  reports: ReportItem[];
  tabs: OpenTab[];
  active: number | null;
  isFav: (id: number) => boolean;
  defaultId: number | null;
  onToggleDefault: (id: number) => void;
  canUpload: boolean;
  onToggleFav: (id: number) => void;
  onActivate: (id: number) => void;
  onClose: (id: number) => void;
  onGoUpload: () => void;
  csrf: string;
  user: SessionUser;
}) {
  // 탭별 데이터 신선도(마지막 refresh 성공 시각) + RLS 적용 여부 — ReportPanel이 임베드 응답에서 올려준다
  const [freshMap, setFreshMap] = useState<
    Record<number, { asOf: string | null; status: string | null; rlsEnabled: boolean }>
  >({});
  const onFreshness = useCallback(
    (rid: number, asOf: string | null, status: string | null, rlsEnabled: boolean) =>
      setFreshMap((prev) => ({ ...prev, [rid]: { asOf, status, rlsEnabled } })),
    [],
  );

  // 다운로드·내보내기 실패 등 짧게 보여주고 사라지는 툴바 알림 — alert() 대신 사용
  const [toolbarMsg, setToolbarMsg] = useState<{ text: string; tone: "ok" | "err" } | null>(null);
  const notify = useCallback((text: string, tone: "ok" | "err") => {
    setToolbarMsg({ text, tone });
    window.setTimeout(() => setToolbarMsg(null), 4000);
  }, []);

  // 탭별 임베드 인스턴스 참조 — 전체화면·보기모드 버튼이 활성 탭의 인스턴스를 직접 조작한다.
  // ref라 리렌더를 트리거하지 않고, 탭이 바뀌면 해당 탭의 것만 조회한다.
  const reportRefs = useRef<Record<number, { report: pbi.Report; isDashboard: boolean }>>({});
  const onReady = useCallback((rid: number, report: pbi.Report, isDashboard: boolean) => {
    reportRefs.current[rid] = { report, isDashboard };
  }, []);

  // PPTX는 PBI가 백그라운드에서 변환하는 비동기 작업이라 exporting으로 진행 중 상태를 표시한다.
  const [exporting, setExporting] = useState<number | null>(null);

  const downloadPbix = async (reportId: number, name: string) => {
    try {
      const blob = await downloadReportPbix(reportId);
      triggerDownload(blob, `${name}.pbix`);
      notify(`'${name}.pbix' 다운로드를 시작했습니다.`, "ok");
    } catch (e) {
      notify("PBIX 다운로드 실패: " + (e as Error).message, "err");
    }
  };

  const exportPptx = async (reportId: number, name: string) => {
    setExporting(reportId);
    try {
      const { export_id } = await startPptxExport(reportId, csrf);
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await pollPptxExport(reportId, export_id);
        if (s.status === "Succeeded") {
          const blob = await downloadPptxExport(reportId, export_id);
          triggerDownload(blob, `${name}.pptx`);
          notify(`'${name}.pptx' 다운로드를 시작했습니다.`, "ok");
          setExporting(null);
          return;
        }
        if (s.status === "Failed") {
          notify("PPTX 내보내기 실패 — 전용 용량이 필요한 기능일 수 있습니다.", "err");
          setExporting(null);
          return;
        }
      }
      notify("PPTX 내보내기 시간 초과 — 잠시 후 다시 시도해 주세요.", "err");
    } catch (e) {
      notify("PPTX 내보내기 실패: " + (e as Error).message, "err");
    } finally {
      setExporting(null);
    }
  };

  // React 훅은 조건부로 호출하면 안 된다 — tabs가 빈 상태(→ 아래 조기 return)에서
  // 첫 보고서를 열면(tabs.length 0→1) 이 컴포넌트가 이전 렌더보다 훅을 하나 더 호출하게 돼
  // "Rendered more hooks than during the previous render"로 화면 전체가 하얗게 죽는다.
  // 조기 return보다 반드시 앞에 선언해야 한다.
  const [showUpdate, setShowUpdate] = useState(false);

  if (tabs.length === 0) {
    return <ReportLanding reports={reports} canUpload={canUpload} onGoUpload={onGoUpload} />;
  }
  const activeTab = tabs.find((t) => t.id === active);
  const fresh = activeTab ? freshMap[activeTab.id] : undefined;
  const activeEntry = activeTab ? reportRefs.current[activeTab.id] : undefined;
  const activeReportItem = activeTab ? reports.find((r) => r.id === activeTab.id) : undefined;
  // 업데이트(콘텐츠 교체) 권한: 열람 권한(can_view)과는 완전히 별개 — 소유자 또는 admin만.
  const canEditActive =
    !!activeReportItem &&
    (user.is_admin || activeReportItem.owner_username === user.username) &&
    activeReportItem.report_type !== "dashboard";

  const setDisplay = (opt: keyof typeof pbi.models.DisplayOption) => {
    activeEntry?.report.updateSettings({
      layoutType: pbi.models.LayoutType.Custom,
      customLayout: { displayOption: pbi.models.DisplayOption[opt] },
    }).catch(() => {});
  };
  const goFullscreen = () => {
    try {
      activeEntry?.report.fullscreen();
    } catch {
      /* 대시보드 등 일부 타입은 fullscreen 미지원일 수 있음 — 무시 */
    }
  };
  return (
    <div className="rp-workarea">
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
          <button
            className={`rp-report-toolbar-fav rp-toolbar-pin${defaultId === activeTab.id ? " on" : ""}`}
            title={
              defaultId === activeTab.id
                ? "기본 보고서 해제"
                : "기본 보고서로 설정 (접속 시 자동으로 열림)"
            }
            onClick={() => onToggleDefault(activeTab.id)}
          >
            <Pin
              size={16}
              className="icn"
              fill={defaultId === activeTab.id ? "currentColor" : "none"}
            />
            <span className="rp-toolbar-label">기본</span>
          </button>
          {!activeEntry?.isDashboard && (
            <div className="rp-toolbar-fitgroup">
              <button className="rp-toolbar-fitbtn" title="페이지에 맞춤" onClick={() => setDisplay("FitToPage")}>
                맞춤
              </button>
              <button className="rp-toolbar-fitbtn" title="폭에 맞춤" onClick={() => setDisplay("FitToWidth")}>
                폭맞춤
              </button>
              <button className="rp-toolbar-fitbtn" title="실제 크기" onClick={() => setDisplay("ActualSize")}>
                실제크기
              </button>
            </div>
          )}
          <button className="rp-report-toolbar-fav" title="전체화면" onClick={goFullscreen}>
            <Maximize size={16} className="icn" />
            <span className="rp-toolbar-label">전체화면</span>
          </button>
          {!activeEntry?.isDashboard && (
            <>
              <button
                className="rp-report-toolbar-fav"
                title="PBIX로 다운로드"
                onClick={() => downloadPbix(activeTab.id, activeTab.name)}
              >
                <Download size={16} className="icn" />
                <span className="rp-toolbar-label">다운로드</span>
              </button>
              <button
                className="rp-report-toolbar-fav"
                title="PPTX로 내보내기 (전용 용량 필요)"
                onClick={() => exportPptx(activeTab.id, activeTab.name)}
                disabled={exporting === activeTab.id}
              >
                <FileArchive size={16} className="icn" />
                <span className="rp-toolbar-label">{exporting === activeTab.id ? "내보내는 중..." : "PPTX"}</span>
              </button>
              {canEditActive && (
                <button
                  className="btn btn-ghost btn-sm rp-toolbar-update-btn"
                  title="새 pbix로 콘텐츠만 교체 (데이터셋·RLS는 유지)"
                  onClick={() => setShowUpdate(true)}
                >
                  업데이트
                </button>
              )}
            </>
          )}
          {fresh?.rlsEnabled && (
            <span
              className="rp-rls-badge"
              title="이 보고서는 사용자 역할에 따라 보이는 데이터(행)가 다를 수 있습니다"
            >
              개인화 데이터
            </span>
          )}
          {fresh && <FreshnessBadge asOf={fresh.asOf} status={fresh.status} />}
        </div>
      )}
      {toolbarMsg && (
        <div className={`rp-upload-feedback rp-toolbar-toast ${toolbarMsg.tone}`}>
          {toolbarMsg.text}
        </div>
      )}
      {showUpdate && activeTab && (
        <UpdateReportModal
          reportId={activeTab.id}
          reportName={activeTab.name}
          csrf={csrf}
          onClose={() => setShowUpdate(false)}
        />
      )}
      <div className="rp-panels">
        {tabs.map((t) => (
          <ReportPanel key={t.id} id={t.id} active={t.id === active} onFreshness={onFreshness} onReady={onReady} />
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
  canUpload,
  onGoUpload,
}: {
  reports: ReportItem[];
  canUpload: boolean;
  onGoUpload: () => void;
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

      <div className="rp-landing-empty">
        <BarChart3 size={52} className="icn" />
        {reports.length === 0 ? (
          <p>아직 열람 가능한 보고서가 없습니다</p>
        ) : (
          <p>왼쪽 ‘열람 보고서’ 목록에서 보고서를 선택하세요</p>
        )}
      </div>
    </div>
  );
}

/** 데이터 기준(마지막 refresh 성공) 배지 — 26시간 넘으면 경고, refresh 실패면 위험. */
function FreshnessBadge({ asOf, status }: { asOf: string | null; status: string | null }) {
  if (status === "Failed") {
    return (
      <span className="rp-fresh danger" title="데이터셋 새로고침이 실패했습니다. 관리자에게 문의하세요.">
        데이터 갱신 실패
      </span>
    );
  }
  if (!asOf) {
    // NotRefreshable(DirectQuery 등) 또는 아직 수집 전 — 예전엔 아무것도 안 보여줘서
    // "고장인지 정상인지" 구분이 안 됐다. 중립 배지로 상태를 명시한다.
    return (
      <span
        className="rp-fresh muted"
        title="이 보고서는 자동 새로고침 이력이 없거나 아직 수집되지 않았습니다"
      >
        데이터 갱신 정보 없음
      </span>
    );
  }
  const d = new Date(asOf);
  const ageHours = (Date.now() - d.getTime()) / 3_600_000;
  const label = `${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return (
    <span
      className={`rp-fresh${ageHours > 26 ? " warn" : ""}`}
      title={ageHours > 26 ? "데이터가 하루 이상 갱신되지 않았습니다" : "마지막 데이터 새로고침 성공 시각"}
    >
      데이터 기준 {label}
    </span>
  );
}

function ReportPanel({
  id,
  active,
  onFreshness,
  onReady,
}: {
  id: number;
  active: boolean;
  onFreshness: (rid: number, asOf: string | null, status: string | null, rlsEnabled: boolean) => void;
  onReady: (rid: number, report: pbi.Report, isDashboard: boolean) => void;
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
          onFreshness(id, d.data_as_of ?? null, d.refresh_status ?? null, Boolean(d.rls_enabled));
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
        onFreshness(id, d.data_as_of ?? null, d.refresh_status ?? null, Boolean(d.rls_enabled));
        const s = d.settings || {};
        const isDashboard = s.tab_type === "dashboard";
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
              settings: {
                navContentPaneEnabled: Boolean(s.enable_page_nav),
                filterPaneEnabled: Boolean(s.enable_filter),
                layoutType: pbi.models.LayoutType.Custom,
                customLayout: {
                  displayOption: pbi.models.DisplayOption.FitToPage,
                },
                panes: {
                  pageNavigation: { visible: Boolean(s.enable_page_nav) },
                  filters: { visible: Boolean(s.enable_filter), expanded: false },
                },
              },
            };
        if (!isDashboard && s.default_page) config.pageName = s.default_page;
        const report = powerbi.embed(el, config);
        scheduleRenew(report, d.expires_at);
        onReady(id, report as pbi.Report, isDashboard);
        report.on("loaded", () => !cancelled && setLoading(false));
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
  }, [id, onFreshness, onReady]);

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

  const tableRef = useRef<HTMLDivElement>(null);
  const pageSize = useFitRows(tableRef, 42, 44); // 화면 높이에 맞춰 행 수 자동
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
                  {r.report_type === "personal" && (
                    <span className="pill pending" style={{ marginLeft: 6 }}>
                      개인
                    </span>
                  )}
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

/** 보고서 콘텐츠 업데이트 모달 (v7) — 새 pbix로 페이지·시각화만 교체, 데이터셋은 유지.
 * 소유자·admin만 열 수 있다(toolbar에서 canEditActive로 이미 걸러짐). */
function UpdateReportModal({
  reportId,
  reportName,
  csrf,
  onClose,
}: {
  reportId: number;
  reportName: string;
  csrf: string;
  onClose: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ msg: string; tone: "" | "ok" | "err" }>({ msg: "", tone: "" });

  const submit = async () => {
    if (!file) return;
    if (
      !confirm(
        `"${reportName}"의 페이지·시각화를 "${file.name}" 내용으로 완전히 교체합니다.\n` +
          "되돌릴 수 없습니다 (데이터셋·RLS 설정은 유지됩니다). 계속할까요?",
      )
    )
      return;
    setBusy(true);
    setStatus({ msg: "업로드 중...", tone: "" });
    try {
      const accepted = await startReportUpdate(reportId, file, csrf);
      const jobId = accepted.job_id;
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const s = await fetchUploadStatus(jobId, csrf);
        if (s.status === "completed") {
          setStatus({ msg: "업데이트 완료! 잠시 후 새로고침됩니다.", tone: "ok" });
          setTimeout(() => location.reload(), 1200);
          return;
        }
        if (["failed", "unknown", "conflict", "db_failed"].includes(s.status)) {
          setStatus({ msg: s.error || "업데이트 실패", tone: "err" });
          setBusy(false);
          return;
        }
        setStatus({ msg: `처리 중 (${s.status})...`, tone: "" });
      }
      setStatus({ msg: "시간 초과 — 관리자에게 문의하세요.", tone: "err" });
      setBusy(false);
    } catch (e) {
      setStatus({ msg: (e as Error).message, tone: "err" });
      setBusy(false);
    }
  };

  return (
    <div className="rp-modal-overlay" onClick={busy ? undefined : onClose}>
      <div className="rp-modal" onClick={(e) => e.stopPropagation()}>
        {!busy && (
          <button className="rp-modal-x" onClick={onClose}>
            <X size={18} />
          </button>
        )}
        <div className="rp-modal-name">보고서 업데이트 — {reportName}</div>
        <p className="rp-landing-sub" style={{ marginBottom: 16 }}>
          새 pbix로 페이지·시각화만 교체합니다. 데이터셋(RLS·관계·DAX)은 그대로 유지됩니다.
        </p>
        <div className="rp-filepick" style={{ marginBottom: 16 }}>
          <input
            type="file"
            accept=".pbix"
            disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </div>
        {status.msg && <div className={`rp-upload-feedback ${status.tone}`}>{status.msg}</div>}
        <div className="rp-form-actions" style={{ marginTop: 16 }}>
          <button className="btn btn-primary" disabled={!file || busy} onClick={submit}>
            {busy ? "처리 중..." : "업데이트 시작"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 내 활동 로그 모달 (v6) — 관리자 로그 화면과 별개로, 일반 사용자가 자기 이력만 본다. */
function MyActivityModal({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<MyActivityRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchMyActivity()
      .then(setRows)
      .catch(() => setError("활동 로그 조회 실패"));
  }, []);

  return (
    <div className="rp-modal-overlay" onClick={onClose}>
      <div className="rp-modal rp-activity-modal" onClick={(e) => e.stopPropagation()}>
        <button className="rp-modal-x" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="rp-modal-name">내 활동</div>
        <div className="rp-activity-list">
          {error && <div className="rp-panel-error">{error}</div>}
          {!error && !rows && <div className="rp-landing-sub">불러오는 중...</div>}
          {rows && rows.length === 0 && <div className="rp-landing-sub">활동 기록이 없습니다.</div>}
          {rows?.map((r) => (
            <div key={r.id} className="rp-activity-row">
              <span className={`pill ${r.event === "report_upload" ? "pending" : "active"}`}>
                {r.event === "report_upload" ? "업로드" : "열람"}
              </span>
              <span className="rp-activity-name">{r.report_name || "-"}</span>
              <span className="rp-activity-time">{String(r.created_at).replace("T", " ").slice(0, 16)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ReportInfoModal({
  report,
  fav,
  onToggleFav,
  onOpen,
  onClose,
}: {
  report: ReportItem;
  fav: boolean;
  onToggleFav: () => void;
  onOpen: () => void;
  onClose: () => void;
}) {
  return (
    <div className="rp-modal-overlay" onClick={onClose}>
      <div className="rp-modal" onClick={(e) => e.stopPropagation()}>
        <button className="rp-modal-x" title="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="rp-modal-hero">
          <BarChart3 size={30} className="icn" />
        </div>
        <h2 className="rp-modal-name">{report.name}</h2>
        <dl className="rp-modal-info">
          <div>
            <dt>폴더</dt>
            <dd>{report.category || "미분류"}</dd>
          </div>
          <div>
            <dt>구분</dt>
            <dd>
              {report.report_type === "managed"
                ? "공용 보고서"
                : `개인 보고서 · ${report.owner_username || "-"}`}
            </dd>
          </div>
          {report.description && (
            <div>
              <dt>설명</dt>
              <dd>{report.description}</dd>
            </div>
          )}
        </dl>
        <div className="rp-modal-actions">
          <button
            className={`btn btn-ghost rp-modal-fav${fav ? " on" : ""}`}
            onClick={onToggleFav}
          >
            <Star size={15} className="icn" fill={fav ? "currentColor" : "none"} />{" "}
            {fav ? "즐겨찾기 해제" : "즐겨찾기"}
          </button>
          <button className="btn btn-primary" onClick={onOpen}>
            보고서 열기
          </button>
        </div>
      </div>
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
  const [description, setDescription] = useState("");
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
      const accepted = await uploadPbix(file, csrf, reportName.trim(), description.trim());
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
          <label>보고서 설명 (선택)</label>
          <input
            placeholder="어떤 데이터를 보여주는 보고서인지 적어두면 검색·발견이 쉬워집니다"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={500}
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
