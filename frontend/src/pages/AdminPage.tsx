// 관리자 포털 셸(레일+컨텍스트바+탭 전환) — 각 탭의 실제 내용은
// components/admin/*.tsx로 분리돼 있다(2026-08-27, 이 파일이 1682줄까지 커져서
// 나눴다 — 사용자/보고서/설정 탭 + 공용 조각(Field/Modal/AdminSyncBell)).
// 로그 탭은 학습용 코드 축소 과정에서 제거했다(git 이력의 v2 태그에 남아있음).
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  Home as HomeIcon,
  LayoutDashboard,
  Layers,
  LogOut,
  RefreshCw,
  Settings as SettingsIcon,
  Users as UsersIcon,
  X,
} from "lucide-react";
import type {
  AdminData,
  AdminReport,
  AdminUser,
} from "../lib/bootstrap";
import {
  SyncStatus,
  adminFetchReports,
  adminImportPbi,
  adminSyncStatus,
  adminToggleUser,
  adminToggleUpload,
  logout,
} from "../lib/api";
import { Rail, ContextBar } from "../components/AppShell";
import { AdminOverview } from "../components/admin/AdminOverview";
import { ReportFoldersSection } from "../components/admin/StructureSections";
import { AddUserModal, UsersSection } from "../components/admin/UsersSection";
import { AccessModal, ReportsSection } from "../components/admin/ReportsSection";
import { ConfigSection } from "../components/admin/ConfigSection";
import { AdminSyncBell, departmentOptions } from "../components/admin/shared";

// ReportPage.tsx가 관리자 로그인일 때 이 두 조각을 그대로 가져다 쓴다(같은 앱 안에서
// "관리자 포털로 가지 않고도" 동기화 알림/설정을 보여주기 위함) — export 유지.
export { ConfigSection, AdminSyncBell };

type SectionKey =
  | "overview" | "users" | "folders" | "reports";
type Toast = { msg: string; tone: "ok" | "err" | "" } | null;

const SECTIONS: {
  key: SectionKey;
  Icon: typeof LayoutDashboard;
  label: string;
}[] = [
  { key: "overview", Icon: LayoutDashboard, label: "현황" },
  { key: "users", Icon: UsersIcon, label: "사용자" },
  { key: "folders", Icon: Layers, label: "보고서 폴더" },
  { key: "reports", Icon: BarChart3, label: "보고서" },
];

export default function AdminPage({ data }: { data: AdminData }) {
  const { user, stats, csrf_token } = data;
  const [section, setSection] = useState<SectionKey>(() => {
    const requested = new URLSearchParams(window.location.search).get("section");
    return SECTIONS.some((item) => item.key === requested) ? requested as SectionKey : "overview";
  });
  const [users, setUsers] = useState<AdminUser[]>(data.users);
  const [reports, setReports] = useState<AdminReport[]>(data.reports);
  const [toast, setToast] = useState<Toast>(null);
  const [accessReport, setAccessReport] = useState<AdminReport | null>(null);
  const [showAddUser, setShowAddUser] = useState(false);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [syncDismissed, setSyncDismissed] = useState(false);
  const [importing, setImporting] = useState(false);
  const [adminMenuOpen, setAdminMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(
    () => new URLSearchParams(window.location.search).get("settings") === "1",
  );

  const showToast = useCallback((msg: string, tone: "ok" | "err" | "" = "") => {
    setToast({ msg, tone });
    setTimeout(() => setToast(null), 3000);
  }, []);

  // Power BI(폴더 이동·이름변경·신규·삭제)와 DB 불일치 자동 감지 (비차단)
  useEffect(() => {
    adminSyncStatus().then(setSync).catch(() => {});
  }, []);

  // 직원 업로드 등으로 페이지 로드 이후 생긴 보고서를 반영 (실패 시 기존 목록 유지)
  const refreshReports = useCallback(async () => {
    try {
      setReports(await adminFetchReports());
    } catch {
      /* 부트스트랩 목록 유지 */
    }
  }, []);

  useEffect(() => {
    if (section === "reports") refreshReports();
  }, [section, refreshReports]);

  const runImport = async () => {
    setImporting(true);
    try {
      const j = await adminImportPbi(csrf_token);
      showToast(
        `동기화 완료 — 신규 ${j.registered} · 갱신 ${j.skipped} · 삭제 ${j.deleted}`,
        "ok",
      );
      setTimeout(() => location.reload(), 1400);
    } catch (e) {
      showToast("동기화 실패: " + (e as Error).message, "err");
      setImporting(false);
    }
  };

  return (
    <div className="as-shell">
      <Rail
        items={[
          { key: "home", icon: <HomeIcon size={19} />, label: "홈", onClick: () => {
            sessionStorage.setItem("rp-mode", "home");
            window.location.href = "/";
          } },
          { key: "reports", icon: <BarChart3 size={19} />, label: "보고서", onClick: () => {
            sessionStorage.setItem("rp-mode", "reports");
            window.location.href = "/";
          } },
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
          crumb={
            <>
              관리자 포털 <span className="dim">›</span>{" "}
              {SECTIONS.find((s) => s.key === section)?.label}
            </>
          }
          right={<>
            <AdminSyncBell sync={sync} onGoReports={() => setSection("reports")} />
            <button type="button" className="ad-admin-trigger" onClick={() => setAdminMenuOpen(true)}>
              <Layers size={16} /> 관리
            </button>
            <button type="button" className="ad-admin-trigger" onClick={() => setSettingsOpen(true)}>
              <SettingsIcon size={16} /> 설정
            </button>
            <span className="as-ctxbar-user">{user.display_name}</span>
          </>}
        />

        <main className="app-main">
          {sync?.drift && !syncDismissed && (
            <div className="ad-sync-banner">
              <AlertTriangle size={18} className="icn ad-sync-icon" />
              <div className="ad-sync-text">
                <b>Power BI와 동기화가 필요합니다.</b>{" "}
                {(sync.new?.length ?? 0) > 0 && `신규 ${sync.new!.length}건`}
                {(sync.moved?.length ?? 0) > 0 &&
                  ` · 폴더 변경 ${sync.moved!.length}건`}
                {(sync.removed?.length ?? 0) > 0 &&
                  ` · 삭제 ${sync.removed!.length}건`}
                <span className="ad-sync-hint">
                  {" "}
                  — ‘가져오기’로 폴더·등록 상태를 맞춥니다.
                </span>
              </div>
              <button
                className="btn btn-primary btn-sm"
                disabled={importing}
                onClick={runImport}
              >
                <RefreshCw size={14} className="icn" />{" "}
                {importing ? "동기화 중..." : "지금 가져오기"}
              </button>
              <button
                className="ad-sync-close"
                title="닫기"
                onClick={() => setSyncDismissed(true)}
              >
                <X size={16} />
              </button>
            </div>
          )}
          <div className="ad-content">
            {section === "overview" && (
              <AdminOverview stats={stats} jobs={data.jobs} onGoSection={setSection} />
            )}
            {section === "users" && (
              <UsersSection
                users={users}
                csrf={csrf_token}
                showToast={showToast}
                onAdd={() => setShowAddUser(true)}
                onToggle={async (id) => {
                  try {
                    const { is_active } = await adminToggleUser(id, csrf_token);
                    setUsers((prev) =>
                      prev.map((u) => (u.id === id ? { ...u, is_active } : u)),
                    );
                    showToast(
                      is_active ? "계정이 활성화됐습니다." : "계정이 비활성화됐습니다.",
                      "ok",
                    );
                  } catch (e) {
                    showToast("오류: " + (e as Error).message, "err");
                  }
                }}
                onToggleUpload={async (id) => {
                  try {
                    const { can_upload } = await adminToggleUpload(id, csrf_token);
                    setUsers((prev) =>
                      prev.map((u) => (u.id === id ? { ...u, can_upload } : u)),
                    );
                    showToast(
                      can_upload ? "업로드가 허용됐습니다." : "업로드가 차단됐습니다.",
                      "ok",
                    );
                  } catch (e) {
                    showToast("오류: " + (e as Error).message, "err");
                  }
                }}
                onEdited={(updated) =>
                  setUsers((prev) =>
                    prev.map((u) => (u.id === updated.id ? updated : u)),
                  )
                }
              />
            )}
            {section === "reports" && (
              <ReportsSection
                reports={reports}
                csrf={csrf_token}
                showToast={showToast}
                onDeleted={(id) =>
                  setReports((prev) =>
                    prev.map((r) =>
                      r.id === id ? { ...r, status: "deleted" } : r,
                    ),
                  )
                }
                onManageAccess={setAccessReport}
              />
            )}
            {section === "folders" && <ReportFoldersSection csrf={csrf_token} showToast={showToast} />}
          </div>
        </main>
      </div>

      {adminMenuOpen && <div className="ad-settings-overlay" onClick={() => setAdminMenuOpen(false)}>
        <aside className="ad-settings-panel ad-admin-menu-panel" onClick={e => e.stopPropagation()}>
          <div className="ad-settings-head"><div><h2>관리자 포털</h2><p>관리할 항목을 선택하세요.</p></div><button onClick={() => setAdminMenuOpen(false)}><X size={18}/></button></div>
          <nav className="ad-admin-menu-list">
            {SECTIONS.map((s) => <button key={s.key} type="button" className={section === s.key ? "on" : ""} onClick={() => { setSection(s.key); setAdminMenuOpen(false); }}>
              <span><s.Icon size={17}/><b>{s.label}</b></span><span aria-hidden="true">›</span>
            </button>)}
          </nav>
        </aside>
      </div>}

      {settingsOpen && <div className="ad-settings-overlay" onClick={() => setSettingsOpen(false)}>
        <aside className="ad-settings-panel ad-config-panel" onClick={e => e.stopPropagation()}>
          <div className="ad-settings-head"><div><h2>설정</h2><p>포털 운영에 필요한 제한값을 변경합니다.</p></div><button onClick={() => setSettingsOpen(false)}><X size={18}/></button></div>
          <ConfigSection csrf={csrf_token} showToast={showToast}/>
        </aside>
      </div>}

      {showAddUser && (
        <AddUserModal
          csrf={csrf_token}
          departments={departmentOptions(users)}
          onClose={() => setShowAddUser(false)}
          onAdded={() => {
            showToast("사용자가 추가되었습니다. 목록을 새로고침합니다...", "ok");
            setTimeout(() => location.reload(), 1100);
          }}
          onError={(m) => showToast("오류: " + m, "err")}
        />
      )}

      {accessReport && (
        <AccessModal
          report={accessReport}
          csrf={csrf_token}
          departments={departmentOptions(users)}
          onClose={() => {
            setAccessReport(null);
            refreshReports(); // 방금 부여·해제한 결과를 '열람권한' 수에 반영
          }}
          showToast={showToast}
        />
      )}

      {toast && <div className={`ad-toast show ${toast.tone}`}>{toast.msg}</div>}
    </div>
  );
}
