// 일반 사용자 포털 셸(레일+컨텍스트바+탭 상태 관리) — 각 화면의 실제 내용은
// components/report/*.tsx로 분리돼 있다(2026-08-27, 이 파일이 1600줄까지 커져서
// 나눴다 — 홈/내 보고서·전체 보고서(+임베드)/모달 3종/업로드 화면).
import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  History,
  Home as HomeIcon,
  LayoutDashboard,
  LogOut,
  Users,
  X,
} from "lucide-react";
import type { ReportData, ReportItem } from "../lib/bootstrap";
import { ReportFolder, adminSyncStatus, fetchReportFolders, logout, SyncStatus } from "../lib/api";
import { useFavorites } from "../hooks/useFavorites";
import { useRecents } from "../hooks/useRecents";
import { Rail, ContextBar } from "../components/AppShell";
import { ReportSidebar, type BrowserMode, type ReportView } from "../components/ReportSidebar";
import { ConfigSection, AdminSyncBell } from "./AdminPage";
import { Home } from "../components/report/Home";
import { AllReportsView, MyReportsView } from "../components/report/ReportViews";
import { UploadView } from "../components/report/UploadView";
import { MyActivityModal } from "../components/report/modals";
import { ACTIVE_KEY, OpenTab, TABS_KEY, loadTabs } from "../components/report/tabState";

type View = ReportView;

type Mode = "home" | "reports";
const MODE_KEY = "rp-mode";

export default function ReportPage({ data }: { data: ReportData }) {
  const { user, reports, csrf_token } = data;
  const canUpload = user.can_upload !== false;

  const { isFav, toggle: toggleFav } = useFavorites(data.favorites, csrf_token);
  const { recents, push: pushRecent } = useRecents(data.recents, csrf_token, data.recents_limit);

  // 사이드바 트리가 report_folders의 실제 parent_id 계층을 그대로 쓰도록 여기서 한 번만
  // 불러와 내려준다 — 예전엔 report.category 문자열을 "/"로 쪼개 계층을 흉내냈는데,
  // 그 방식은 폴더 이름 자체에 구분자를 넣어야 해서 관리자 화면(진짜 parent_id 트리)과
  // 서로 다른 걸 보여주는 문제가 있었다(2026-08-12).
  const [folders, setFolders] = useState<ReportFolder[]>([]);
  useEffect(() => { fetchReportFolders().then(setFolders).catch(() => {}); }, []);

  // 열람 보고서 = 열람 가능한 보고서 전체(폴더 트리).
  //  - 관리자: 모든 보고서(권한과 무관하게 다 봄)
  //  - 일반 사용자: 관리자 포털에서 열람권한을 부여받은 보고서 + 본인 업로드
  // 메인 영역은 카드로 쏟지 않고 '선택하세요' 안내만 (트리에서 고름).
  const myReports = reports;
  const [mode, setMode] = useState<Mode>(
    () => (sessionStorage.getItem(MODE_KEY) as Mode) || "home",
  );
  const [view, setView] = useState<View>("my");
  const [browserMode, setBrowserMode] = useState<BrowserMode>(
    () => (sessionStorage.getItem("report-browser-mode") as BrowserMode) || "tree",
  );
  const [showActivity, setShowActivity] = useState(false);
  const [adminMenuOpen, setAdminMenuOpen] = useState(false);
  // 관리자에게만 필요한 데이터라 is_admin일 때만 부른다 — 일반 사용자는 /api/admin/sync-status가
  // 403이라 어차피 못 쓰는 값을 매번 조회할 이유가 없다(2026-08-12, 알림 벨을 홈/보고서
  // 화면에도 노출하면서 추가 — AdminSyncBell 참고).
  const [sync, setSync] = useState<SyncStatus | null>(null);
  useEffect(() => {
    if (user.is_admin) adminSyncStatus().then(setSync).catch(() => {});
  }, [user.is_admin]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsToast, setSettingsToast] = useState<{ msg: string; tone: "ok" | "err" | "" } | null>(null);
  const [allQuery, setAllQuery] = useState("");
  const [tabs, setTabs] = useState<OpenTab[]>(() => loadTabs());
  const [active, setActive] = useState<number | null>(
    () => Number(sessionStorage.getItem(ACTIVE_KEY)) || null,
  );

  useEffect(() => {
    sessionStorage.setItem(TABS_KEY, JSON.stringify(tabs));
    sessionStorage.setItem(ACTIVE_KEY, String(active ?? ""));
  }, [tabs, active]);

  // 저장된 활성 탭이 없거나 더 이상 열람 가능한 보고서가 아니면 첫 유효 탭을 선택한다.
  // 이 보정이 없으면 탭 데이터는 남아 있는데 active가 null이라 보고서 영역이 비어 보인다.
  useEffect(() => {
    const validIds = new Set(myReports.map((report) => report.id));
    const validTabs = tabs.filter((tab) => validIds.has(tab.id));
    if (validTabs.length !== tabs.length) setTabs(validTabs);
    if (validTabs.length && !validTabs.some((tab) => tab.id === active)) {
      setActive(validTabs[0].id);
    } else if (!validTabs.length && active !== null) {
      setActive(null);
    }
  }, [myReports, tabs, active]);

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

  // 보고서를 여러 개 열면 탭이 줄줄이 쌓여 가로 스크롤 없이는 찾기 힘들어진다 —
  // 한 번에 정리할 수 있는 탈출구.
  const closeAllTabs = useCallback(() => {
    setTabs([]);
    setActive(null);
  }, []);

  const changeBrowserMode = (next: BrowserMode) => {
    setBrowserMode(next);
    sessionStorage.setItem("report-browser-mode", next);
    setTabs([]);
    setActive(null);
  };
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
              {user.is_admin && <>
                <AdminSyncBell sync={sync} onGoReports={() => { window.location.href = "/admin?section=reports"; }} />
                <button className="as-ctxbar-pill" onClick={() => setAdminMenuOpen(true)}>관리</button>
                <button className="as-ctxbar-pill" onClick={() => setSettingsOpen(true)}>설정</button>
              </>}
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
        {adminMenuOpen && <div className="ad-settings-overlay" onClick={() => setAdminMenuOpen(false)}>
          <aside className="ad-settings-panel ad-admin-menu-panel" onClick={(event) => event.stopPropagation()}>
            <div className="ad-settings-head"><div><h2>관리자 포털</h2><p>확인할 관리 화면을 선택하세요.</p></div><button onClick={() => setAdminMenuOpen(false)}><X size={18}/></button></div>
            <nav className="ad-admin-menu-list">
              <AdminMenuLink section="overview" label="현황" icon={<LayoutDashboard size={17}/>}/>
              <AdminMenuLink section="users" label="사용자" icon={<Users size={17}/>}/>
              <AdminMenuLink section="reports" label="보고서" icon={<BarChart3 size={17}/>}/>
              <AdminMenuLink section="logs" label="로그" icon={<History size={17}/>}/>
            </nav>
          </aside>
        </div>}
        {settingsOpen && <div className="ad-settings-overlay" onClick={() => setSettingsOpen(false)}>
          <aside className="ad-settings-panel ad-config-panel" onClick={(event) => event.stopPropagation()}>
            <div className="ad-settings-head"><div><h2>설정</h2><p>포털 운영에 필요한 제한값을 변경합니다.</p></div><button onClick={() => setSettingsOpen(false)}><X size={18}/></button></div>
            <ConfigSection csrf={csrf_token} showToast={(msg, tone = "") => {
              setSettingsToast({ msg, tone });
              window.setTimeout(() => setSettingsToast(null), 3000);
            }}/>
          </aside>
        </div>}
        {settingsToast && <div className={`ad-toast show ${settingsToast.tone}`}>{settingsToast.msg}</div>}

        {/* mode/view는 그냥 화면을 숨기고 보여주는 용도일 뿐 — 예전엔 {cond ? A : B}로 안 쓰는
            쪽을 언마운트했는데, 그러면 MyReportsView 안의 ReportPanel(Power BI iframe)도 같이
            사라졌다가 새로 만들어져서 사용자가 클릭해둔 슬라이서·크로스필터 선택이 홈 갔다
            오면 다 풀렸다(2026-08-27 리포트: "시화공장 눌러서 그래프 봤는데 홈 갔다 오면
            사라짐"). 그래서 둘 다 항상 마운트해두고 display만 토글한다 — display:contents는
            래퍼 자체가 레이아웃에 안 끼어서(자식이 곧바로 부모의 flex 아이템이 됨) 기존
            .home/.app-body flex 레이아웃을 그대로 유지한다. */}
        <div style={{ display: mode === "home" ? "contents" : "none" }}>
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
        </div>
        <div style={{ display: mode === "reports" ? "contents" : "none" }}>
          <div className="app-body rp-body-shell">
          <ReportSidebar
              reports={reports}
              myReports={myReports}
              folders={folders}
              view={view}
              activeId={active}
              isAdmin={Boolean(user.is_admin)}
              canUpload={canUpload}
              browserMode={browserMode}
              isFav={isFav}
              onSelectView={setView}
              onOpen={openReport}
            />
            <main className="app-main">
              {/* view 전환도 같은 이유로 언마운트 대신 display 토글 — "전체 보고서"나
                  "보고서 등록"을 갔다 와도 열려있던 보고서 탭의 선택 상태가 유지된다. */}
              <div style={{ display: view === "my" ? "contents" : "none" }}>
                <MyReportsView
                  reports={myReports}
                  tabs={tabs}
                  active={active}
                  isFav={isFav}
                  canUpload={canUpload}
                  onToggleFav={toggleFav}
                  onActivate={setActive}
                  onOpen={openReport}
                  onClose={closeTab}
                  onCloseAll={closeAllTabs}
                  onGoUpload={() => setView("upload")}
                  csrf={csrf_token}
                  user={user}
                  browserMode={browserMode}
                  onBrowserMode={changeBrowserMode}
                />
              </div>
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
              {view === "upload" && canUpload && <UploadView csrf={csrf_token} isAdmin={Boolean(user.is_admin)} />}
            </main>
          </div>
        </div>
      </div>
    </div>
  );
}

function AdminMenuLink({ section, label, icon }: { section: string; label: string; icon: React.ReactNode }) {
  return <a href={`/admin?section=${section}`}><span>{icon}<b>{label}</b></span><span aria-hidden="true">›</span></a>;
}
