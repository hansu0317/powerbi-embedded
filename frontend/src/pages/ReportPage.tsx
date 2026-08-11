import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pbi from "powerbi-client";
import {
  BarChart3,
  ChevronDown,
  Clock,
  Folder,
  Home as HomeIcon,
  Info,
  LayoutDashboard,
  LayoutList,
  LogOut,
  Maximize,
  Search,
  Settings,
  Star,
  TrendingUp,
  Upload,
  X,
} from "lucide-react";
import type { ReportData, ReportItem, SessionUser } from "../bootstrap";
import {
  fetchEmbed, fetchUploadStatus, logout, uploadPbix,
  fetchMyActivity, MyActivityRow, startReportUpdate,
} from "../api";
import { useFavorites } from "../useFavorites";
import { useRecents } from "../useRecents";
import { Pager, useFitRows } from "../Pager";
import { Rail, ContextBar } from "../AppShell";
import { categoryColor, withAlpha } from "../categoryColor";

// PowerBI 서비스 싱글턴 (탭 전체가 공유)
const powerbi = new pbi.service.Service(
  pbi.factories.hpmFactory,
  pbi.factories.wpmpFactory,
  pbi.factories.routerFactory,
);

// GET 필터 (PoC) — JS SDK의 공식 filters 설정으로 적용한다. URL 문자열에
// &filter=...를 붙이는 방식과 결과는 같지만, isLockedInViewMode로 필터 창의
// 제거(X) 버튼을 숨길 수 있다는 점이 다르다 — 그래도 진짜 RLS는 아니다
// (브라우저 devtools로 SDK를 직접 호출하면 여전히 우회 가능).
//
// 선택 UI(드롭다운) 없음, 한 사용자 한 값만 지원한다(users.filter_value 단일 컬럼).
function buildGetFilter(table: string, column: string, value: string): pbi.models.IBasicFilter {
  return {
    $schema: "http://powerbi.com/product/schema#basic",
    target: { table, column },
    operator: "In",
    values: [value],
    filterType: pbi.models.FilterType.Basic,
    // 필터 창에서 사용자가 제거(X)하지 못하게 잠근다 — 그래도 진짜 RLS는 아님(주석 위 참고).
    displaySettings: { isLockedInViewMode: true },
  };
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

  // 컨텍스트바 breadcrumb — 화면마다 다른 걸 예전엔 상단바 하나로 뭉뚱그렸다.
  const activeTabName = tabs.find((t) => t.id === active)?.name;
  const crumb =
    mode === "home" ? (
      "홈"
    ) : view === "my" ? (
      activeTabName ? (
        <>
          보고서 <span className="dim">›</span> {activeTabName}
        </>
      ) : (
        "보고서"
      )
    ) : view === "all" ? (
      <>
        보고서 <span className="dim">›</span> 전체 보고서
      </>
    ) : (
      <>
        보고서 <span className="dim">›</span> 보고서 등록
      </>
    );

  return (
    <div className="as-shell">
      <Rail
        logo={
          <span className="brand on-dark" style={{ fontSize: "0.68rem" }}>
            <span className="b-quali">q</span>
            <span className="b-soft">s</span>
          </span>
        }
        items={[
          {
            key: "home",
            icon: <HomeIcon size={19} />,
            label: "홈",
            active: mode === "home",
            onClick: () => goMode("home"),
          },
          {
            key: "reports",
            icon: <BarChart3 size={19} />,
            label: "보고서",
            active: mode === "reports",
            onClick: () => goMode("reports"),
          },
          ...(user.is_admin
            ? [{ key: "admin", icon: <Settings size={19} />, label: "관리자 포털", href: "/admin" }]
            : []),
        ]}
        footer={
          <button
            type="button"
            className="as-rail-item"
            title="로그아웃"
            onClick={async () => {
              sessionStorage.clear();
              await logout(csrf_token);
              window.location.href = "/login";
            }}
          >
            <LogOut size={18} />
          </button>
        }
      />
      <div className="as-content">
        <ContextBar
          crumb={crumb}
          right={
            <>
              {data.marketing_portal_url && (
                <a
                  href={data.marketing_portal_url}
                  target="_blank"
                  rel="noreferrer"
                  className="as-ctxbar-pill"
                >
                  ↗ 마케팅 포털
                </a>
              )}
              <button className="as-ctxbar-pill" onClick={() => setShowActivity(true)}>
                내 활동
              </button>
              <span className="as-ctxbar-user">{user.display_name}</span>
            </>
          }
        />
        {showActivity && <MyActivityModal onClose={() => setShowActivity(false)} />}

        {mode === "home" ? (
          <Home
            reports={reports}
            isAdmin={Boolean(user.is_admin)}
            viewerName={user.display_name}
            recentIds={recents}
            popular={data.popular || []}
            isFav={isFav}
            onOpen={openReport}
            onSearch={runSearch}
            onToggleFav={toggleFav}
            onGoAll={() => {
              setAllQuery("");
              setView("all");
              goMode("reports");
            }}
          />
        ) : (
          <div className="app-body rp-body-shell">
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
    </div>
  );
}

/* ── 홈 (메인 랜딩) — CyberClinic(헬스케어 CRM) + slothui(파일매니저) 참고 ──
   벤토 카드 대신: 큰 숫자 통계 → 색 채운 액션 타일 4개 → 최근 열람 아이콘 카드 →
   필터 가능한 표(파스텔 행) + 오른쪽 상세 패널. 표·타일 전부 실제 데이터 기준. */
type HomeFilter = "all" | "fav" | "managed" | "personal";

function Home({
  reports,
  isAdmin,
  viewerName,
  recentIds,
  popular,
  isFav,
  onOpen,
  onSearch,
  onGoAll,
  onToggleFav,
}: {
  reports: ReportItem[];
  isAdmin: boolean;
  viewerName: string;
  recentIds: number[];
  popular: { report_id: number; views: number }[];
  isFav: (id: number) => boolean;
  onOpen: (r: ReportItem) => void;
  onSearch: (q: string) => void;
  onGoAll: () => void;
  onToggleFav: (id: number) => void;
}) {
  const [q, setQ] = useState("");
  const [openSuggest, setOpenSuggest] = useState(false);
  const [filter, setFilter] = useState<HomeFilter>("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const byId = useMemo(() => new Map(reports.map((r) => [r.id, r])), [reports]);
  const favReports = useMemo(() => reports.filter((r) => isFav(r.id)), [reports, isFav]);
  const recentReports = useMemo(
    () =>
      recentIds
        .map((id) => byId.get(id))
        .filter((r): r is ReportItem => Boolean(r))
        .slice(0, 6),
    [recentIds, byId],
  );
  const topPopular = useMemo(() => {
    const r = popular[0] && byId.get(popular[0].report_id);
    return r || null;
  }, [popular, byId]);

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

  const filtered = useMemo(() => {
    switch (filter) {
      case "fav":
        return favReports;
      case "managed":
        return reports.filter((r) => r.report_type !== "personal");
      case "personal":
        return reports.filter((r) => r.report_type === "personal");
      default:
        return reports;
    }
  }, [filter, reports, favReports]);

  const detail = (selectedId && byId.get(selectedId)) || filtered[0] || null;

  // 고정 개수 대신 화면 높이에 맞춰 실제로 들어가는 행 수를 계산한다 — 스크롤이
  // 아예 안 생기는 걸 페이지네이션 하나로 보장하는 유일한 방법(고정 개수면 화면이
  // 작을 때 넘치고, 화면이 크면 남는 공간이 그냥 빈다).
  const [pageSize, tableRef] = useFitRows(40, 36, 5);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [filter, pageSize]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const curPage = Math.min(page, totalPages);
  const pageItems = filtered.slice((curPage - 1) * pageSize, curPage * pageSize);

  return (
    <main className="home home-fit">
      <div className="home-fixed-top">
      <div className="home-heading">
        <div>
          <span className="home-eyebrow">{isAdmin ? "ADMIN WORKSPACE" : "MY WORKSPACE"}</span>
          <h1>{isAdmin ? "운영 현황" : `${viewerName}님의 보고서`}</h1>
          <p>{isAdmin ? "보고서 사용 현황을 살펴보고 필요한 관리 화면으로 이동하세요." : "최근 사용한 보고서와 즐겨찾기를 한곳에서 확인하세요."}</p>
        </div>
        {isAdmin && <button className="btn btn-ghost" onClick={onGoAll}>전체 보고서 관리</button>}
      </div>
      <div className="home-toprow">
        <div className="home-stats">
          <div className="home-stat">
            <b>{reports.length}</b>전체 보고서
          </div>
          <div className="home-stat">
            <b>{favReports.length}</b>즐겨찾기
          </div>
          <div className="home-stat">
            <b>{recentReports.length}</b>최근 열람
          </div>
        </div>
        <form
          className="home-cmdbar"
          onSubmit={(e) => {
            e.preventDefault();
            onSearch(q.trim());
            setOpenSuggest(false);
          }}
        >
          <Search size={16} className="icn home-cmdbar-icon" />
          <input
            placeholder="보고서, 카테고리를 검색하세요"
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
                  {r.category && <span className="home-suggest-cat">{r.category}</span>}
                </li>
              ))}
            </ul>
          )}
        </form>
      </div>

      <div className="home-actions">
        <ActionTile
          Icon={Star}
          title="즐겨찾기"
          sub={favReports[0] ? favReports[0].name : "별표한 보고서가 없습니다"}
          onClick={() => setFilter("fav")}
        />
        <ActionTile
          Icon={Clock}
          title="최근 열람"
          sub={recentReports[0] ? recentReports[0].name : "아직 연 보고서가 없습니다"}
          onClick={() => setSelectedId(recentReports[0]?.id ?? null)}
        />
        <ActionTile
          Icon={TrendingUp}
          title="이번 주 인기"
          sub={topPopular ? topPopular.name : "집계된 데이터가 없습니다"}
          onClick={() => topPopular && setSelectedId(topPopular.id)}
        />
        {isAdmin ? (
          <ActionTile Icon={LayoutList} title="보고서 관리" sub={`${reports.length}건의 공개·권한 상태 확인`} onClick={onGoAll} />
        ) : (
          <ActionTile Icon={LayoutList} title="사용 가능한 보고서" sub={`${reports.length}건 보기`} onClick={() => setFilter("all")} />
        )}
      </div>

      {recentReports.length > 0 && (
        <>
          <div className="home-section-label">{isAdmin ? "최근 확인한 보고서" : "이어서 보기"}</div>
          <div className="home-recent-grid">
            {recentReports.map((r) => (
              <FileCard key={r.id} report={r} selected={detail?.id === r.id} onClick={() => setSelectedId(r.id)} onOpen={onOpen} />
            ))}
          </div>
        </>
      )}

      <div className="home-filterrow">
        <FilterChip active={filter === "all"} onClick={() => setFilter("all")}>전체</FilterChip>
        <FilterChip active={filter === "fav"} onClick={() => setFilter("fav")}>★ 즐겨찾기</FilterChip>
        <FilterChip active={filter === "managed"} onClick={() => setFilter("managed")}>공용</FilterChip>
        <FilterChip active={filter === "personal"} onClick={() => setFilter("personal")}>개인</FilterChip>
      </div>
      </div>

      <div className="home-main">
        <div className="home-tablewrap" ref={tableRef}>
          <table className="home-table">
            <thead>
              <tr>
                <th></th>
                <th>이름</th>
                <th>카테고리</th>
                <th>유형</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((r) => (
                <tr
                  key={r.id}
                  className={`home-table-row${detail?.id === r.id ? " sel" : ""}`}
                  onClick={() => setSelectedId(r.id)}
                >
                  <td>
                    <button
                      type="button"
                      className={`fav-star${isFav(r.id) ? " on" : ""}`}
                      title={isFav(r.id) ? "즐겨찾기 해제" : "즐겨찾기 추가"}
                      onClick={(e) => {
                        e.stopPropagation();
                        onToggleFav(r.id);
                      }}
                    >
                      <Star size={14} className="icn" fill={isFav(r.id) ? "currentColor" : "none"} />
                    </button>
                  </td>
                  <td>
                    <div className="home-table-name">
                      <span className="home-mini-ic" style={{ background: categoryColor(r.category) }}>
                        {r.report_type === "dashboard" ? (
                          <LayoutDashboard size={12} />
                        ) : (
                          <BarChart3 size={12} />
                        )}
                      </span>
                      {r.name}
                    </div>
                  </td>
                  <td>
                    <span
                      className="home-cat-pill"
                      style={{
                        background: withAlpha(categoryColor(r.category), "22"),
                        color: categoryColor(r.category),
                      }}
                    >
                      {r.category || "미분류"}
                    </span>
                  </td>
                  <td className="home-table-type">
                    {r.report_type === "personal" ? `개인 · ${r.owner_username || "-"}` : "공용"}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={4} className="home-table-empty">
                    표시할 보고서가 없습니다
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {detail && (
          <div className="home-detail">
            <span
              className="home-detail-icon"
              style={{ background: categoryColor(detail.category) }}
            >
              {detail.report_type === "dashboard" ? (
                <LayoutDashboard size={20} />
              ) : (
                <BarChart3 size={20} />
              )}
            </span>
            <div className="home-detail-name">{detail.name}</div>
            <div className="home-detail-cat">
              {detail.category || "미분류"} ·{" "}
              {detail.report_type === "personal" ? `개인 · ${detail.owner_username || "-"}` : "공용"}
            </div>
            <div className="home-detail-section-title">보고서 정보</div>
            <div className="home-detail-desc">{detail.description || "등록된 소개가 없습니다. 관리자는 보고서 설명을 추가해 사용자에게 변경 내용이나 활용 방법을 안내할 수 있습니다."}</div>
            <div className="home-detail-actions">
              <button className="btn btn-ghost" onClick={() => onToggleFav(detail.id)}>
                <Star size={14} className="icn" fill={isFav(detail.id) ? "currentColor" : "none"} />{" "}
                {isFav(detail.id) ? "즐겨찾기 해제" : "즐겨찾기"}
              </button>
              <button className="btn btn-primary" onClick={() => onOpen(detail)}>
                보고서 열기
              </button>
            </div>
          </div>
        )}
      </div>
      <Pager page={curPage} totalPages={totalPages} total={filtered.length} onPage={setPage} />
    </main>
  );
}

function ActionTile({
  Icon,
  title,
  sub,
  onClick,
}: {
  Icon: typeof Star;
  title: string;
  sub: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className="home-atile" onClick={onClick}>
      <span className="home-atile-badge">
        <Icon size={16} className="icn" />
      </span>
      <span className="home-atile-go">→</span>
      <div className="home-atile-title">{title}</div>
      <div className="home-atile-sub" title={sub}>
        {sub}
      </div>
    </button>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button type="button" className={`home-chip${active ? " on" : ""}`} onClick={onClick}>
      {children}
    </button>
  );
}

function FileCard({
  report,
  selected,
  onClick,
  onOpen,
}: {
  report: ReportItem;
  selected: boolean;
  onClick: () => void;
  onOpen: (r: ReportItem) => void;
}) {
  return (
    <div
      className={`home-filecard${selected ? " sel" : ""}`}
      onClick={onClick}
      onDoubleClick={() => onOpen(report)}
      title={`${report.name} — 더블클릭하면 바로 열립니다`}
    >
      <span className="home-filecard-icon" style={{ background: categoryColor(report.category) }}>
        {report.report_type === "dashboard" ? <LayoutDashboard size={18} /> : <BarChart3 size={18} />}
      </span>
      <div className="home-filecard-name">{report.name}</div>
      <div className="home-filecard-cat">{report.category || "미분류"}</div>
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
    <nav className="app-sidebar rp-sidepanel">
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
  canUpload: boolean;
  onToggleFav: (id: number) => void;
  onActivate: (id: number) => void;
  onClose: (id: number) => void;
  onGoUpload: () => void;
  csrf: string;
  user: SessionUser;
}) {
  // 탭별 임베드 인스턴스 참조 — 전체화면·보기모드 버튼이 활성 탭의 인스턴스를 직접 조작한다.
  // ref라 리렌더를 트리거하지 않고, 탭이 바뀌면 해당 탭의 것만 조회한다.
  const reportRefs = useRef<Record<number, { report: pbi.Report; isDashboard: boolean }>>({});
  const onReady = useCallback((rid: number, report: pbi.Report, isDashboard: boolean) => {
    reportRefs.current[rid] = { report, isDashboard };
  }, []);

  // React 훅은 조건부로 호출하면 안 된다 — tabs가 빈 상태(→ 아래 조기 return)에서
  // 첫 보고서를 열면(tabs.length 0→1) 이 컴포넌트가 이전 렌더보다 훅을 하나 더 호출하게 돼
  // "Rendered more hooks than during the previous render"로 화면 전체가 하얗게 죽는다.
  // 조기 return보다 반드시 앞에 선언해야 한다.
  const [showUpdate, setShowUpdate] = useState(false);
  type DisplayMode = "FitToPage" | "FitToWidth" | "ActualSize";
  const [displayModes, setDisplayModes] = useState<Record<number, DisplayMode>>({});

  if (tabs.length === 0) {
    return <ReportLanding reports={reports} canUpload={canUpload} onGoUpload={onGoUpload} />;
  }
  const activeTab = tabs.find((t) => t.id === active);
  const activeEntry = activeTab ? reportRefs.current[activeTab.id] : undefined;
  const activeReportItem = activeTab ? reports.find((r) => r.id === activeTab.id) : undefined;
  // 업데이트(콘텐츠 교체) 권한: 열람 권한(can_view)과는 완전히 별개 — 소유자 또는 admin만.
  const canEditActive =
    !!activeReportItem &&
    (user.is_admin || activeReportItem.owner_username === user.username) &&
    activeReportItem.report_type !== "dashboard";

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
          {!activeEntry?.isDashboard && canEditActive && (
            <button
              className="btn btn-ghost btn-sm rp-toolbar-update-btn"
              title="새 pbix로 콘텐츠만 교체 (데이터셋은 유지)"
              onClick={() => setShowUpdate(true)}
            >
              업데이트
            </button>
          )}
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
          <ReportPanel
            key={t.id}
            id={t.id}
            active={t.id === active}
            onReady={onReady}
          />
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

function ReportPanel({
  id,
  active,
  onReady,
}: {
  id: number;
  active: boolean;
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
        // GET 필터 (PoC) — 대시보드는 지원 안 함(필터 개념 자체가 없음).
        // 선택 UI 없이 배정된 values 전부를 IN 필터로 조용히 적용한다.
        // 서버 로그(GET_FILTER APPLY)는 "우리가 브라우저에 뭘 내려보냈다"까지만 알 수 있고,
        // 그 이후(브라우저가 실제로 적용했는지)는 서버가 볼 수 없다 — 그래서 여기 브라우저
        // 콘솔에도 남긴다. F12 → Console 탭에서 확인.
        const gf = !isDashboard && d.get_filter ? d.get_filter : null;
        if (gf) {
          console.info(`[get-filter] report ${id}: ${gf.key} ${gf.table}/${gf.column} eq`, gf.value);
        }
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
              filters: gf ? [buildGetFilter(gf.table, gf.column, gf.value)] : undefined,
              // 보고서별 차등 설정이 필요 없어 상수로 고정한다 (예전 DB 컬럼은 v12에서 제거).
              //  · 필터창은 끈다. 페이지 탭은 상단 툴바의 커스텀 드롭다운을 없앤 대신
              //    네이티브 하단 탭(pageNavigation)을 다시 켜서 페이지 이동을 지원한다.
              settings: {
                navContentPaneEnabled: false,
                filterPaneEnabled: false,
                layoutType: pbi.models.LayoutType.Custom,
                customLayout: {
                  displayOption: pbi.models.DisplayOption.FitToPage,
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
        onReady(id, report as pbi.Report, isDashboard);
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

  // 업데이트는 "같은 보고서의 수정본"만 받는다 — 엉뚱한 pbix를 잘못 고르면 관계없는
  // 페이지·시각화가 기존 데이터셋(RLS·관계·DAX) 위에 그대로 얹히므로, 파일명이 보고서
  // 이름과 일치할 때만 진행하게 막는다. 최종 확인은 서버가 한 번 더 한다(routes/report.py).
  const fileStem = file ? file.name.replace(/\.pbix$/i, "") : "";
  const nameMismatch = !!file && fileStem.toLowerCase() !== reportName.trim().toLowerCase();

  const submit = async () => {
    if (!file || nameMismatch) return;
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
          파일명이 <b>"{reportName}.pbix"</b>와 같아야 합니다 — 다른 보고서라면 "보고서 등록"을 이용하세요.
        </p>
        <div className="rp-filepick" style={{ marginBottom: 16 }}>
          <input
            type="file"
            accept=".pbix"
            disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </div>
        {nameMismatch && (
          <div className="rp-upload-feedback err">
            파일명이 "{reportName}.pbix"와 다릅니다 ("{file?.name}"). 같은 보고서의 수정본만 업데이트할 수 있어요.
          </div>
        )}
        {status.msg && <div className={`rp-upload-feedback ${status.tone}`}>{status.msg}</div>}
        <div className="rp-form-actions" style={{ marginTop: 16 }}>
          <button className="btn btn-primary" disabled={!file || nameMismatch || busy} onClick={submit}>
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

// 파일명에서 확장자만 뗀 값이 그대로 보고서 명이 된다(서버도 동일 로직 —
// routes/report.py의 _read_and_validate_pbix). 여기서는 미리보기 용도로만 쓴다.
function deriveReportName(fileName: string): string {
  return fileName.toLowerCase().endsWith(".pbix") ? fileName.slice(0, -5) : fileName;
}

function UploadView({ csrf }: { csrf: string }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
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
      const accepted = await uploadPbix(file, csrf, description.trim());
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
          {fileName && (
            <span className="rp-field-hint">
              보고서 명 : {deriveReportName(fileName)} (파일명 그대로 사용됩니다)
            </span>
          )}
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

        <div className="rp-upload-note">
          <Info size={15} className="icn" />
          <span>
            보고서 명은 파일명에서 자동으로 정해집니다
            (예: <code>test0101.pbix</code> → "test0101"). <b>이미 등록한 것과 같은
            파일명으로는 올릴 수 없습니다 — 기존 보고서 내용을 바꾸려면 그 보고서를
            열어서 '업데이트' 기능을 사용하세요.</b>
          </span>
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
