import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Bell,
  BarChart3,
  Download,
  History,
  Home as HomeIcon,
  LayoutDashboard,
  Layers,
  LogOut,
  Plus,
  RefreshCw,
  Settings as SettingsIcon,
  Upload,
  Users as UsersIcon,
  X,
} from "lucide-react";
import type {
  AdminData,
  AdminReport,
  AdminUser,
} from "../lib/bootstrap";
import {
  AccessUser,
  AdminGroup,
  AppConfigRow,
  BulkAddResult,
  GroupAccess,
  GroupMember,
  SyncStatus,
  UserReportRow,
  adminGetUserReports,
  adminAddUser,
  adminEditUser,
  adminBulkAddUsers,
  adminCreateGroup,
  adminDeleteGroup,
  adminDeleteReport,
  adminFetchReports,
  adminGetAccess,
  adminGetConfig,
  adminGetGroupAccess,
  adminGetGroupMembers,
  adminGetGroups,
  adminImportPbi,
  adminSetAccess,
  adminSetConfig,
  adminSetGroupAccess,
  adminSetReportVisibility,
  adminSetGroupMember,
  adminSyncStatus,
  adminToggleUser,
  adminToggleUpload,
  adminGetLogs,
  logQueryString,
  LogRow,
  logout,
} from "../lib/api";
import { Pager, usePaged, useFitRows } from "../components/Pager";
import { Rail, ContextBar } from "../components/AppShell";
import { categoryColor, withAlpha } from "../utils/categoryColor";
import { AdminOverview } from "../components/admin/AdminOverview";
import { ReportFoldersSection } from "../components/admin/StructureSections";

type SectionKey =
  | "overview" | "users" | "groups" | "folders" | "reports" | "logs";
type Toast = { msg: string; tone: "ok" | "err" | "" } | null;

const SECTIONS: {
  key: SectionKey;
  Icon: typeof LayoutDashboard;
  label: string;
}[] = [
  { key: "overview", Icon: LayoutDashboard, label: "현황" },
  { key: "users", Icon: UsersIcon, label: "사용자" },
  { key: "groups", Icon: Layers, label: "그룹" },
  { key: "folders", Icon: Layers, label: "보고서 폴더" },
  { key: "reports", Icon: BarChart3, label: "보고서" },
  { key: "logs", Icon: History, label: "로그" },
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
            {section === "logs" && <LogsSection />}
            {section === "groups" && (
              <GroupsSection csrf={csrf_token} showToast={showToast} />
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

/** Power BI 동기화 필요 여부 알림 벨 — 관리자 포털과 홈/보고서 화면(ReportPage.tsx, admin
 * 로그인일 때만) 양쪽에서 쓴다(2026-08-12). sync는 호출부가 (이미 갖고 있거나 직접 조회한)
 * adminSyncStatus() 결과를 그대로 넘긴다 — 여기서 다시 fetch하지 않는 이유는 AdminPage가
 * 이미 같은 값을 배너에도 쓰고 있어서, 여기서도 fetch하면 같은 화면에서 같은 API를 두 번
 * 부르게 되기 때문. onGoReports는 "보고서 확인" 클릭 시 동작 — AdminPage에서는 같은 화면
 * 안에서 섹션만 전환, ReportPage에서는 관리자 포털로 이동. */
export function AdminSyncBell({ sync, onGoReports }: { sync: SyncStatus | null; onGoReports: () => void }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="ad-icon-trigger" aria-label="알림" onClick={() => setOpen(true)}>
      <Bell size={17} />
      {sync?.drift && <span className="ad-notice-dot" />}
    </button>
    {open && <div className="ad-settings-overlay" onClick={() => setOpen(false)}>
      <aside className="ad-settings-panel ad-notifications-panel" onClick={(e) => e.stopPropagation()}>
        <div className="ad-settings-head"><h2>알림</h2><button onClick={() => setOpen(false)}><X size={18} /></button></div>
        {sync?.drift ? <div className="ad-notification-item">
          <AlertTriangle size={18} className="ad-notification-warn" />
          <div><strong>Power BI 동기화가 필요합니다</strong><p>새 보고서나 폴더 변경 사항이 있습니다. 보고서 메뉴에서 가져오기를 실행하세요.</p><button className="btn btn-sm btn-primary" onClick={() => { setOpen(false); onGoReports(); }}>보고서 확인</button></div>
        </div> : <div className="ad-notifications-empty"><Bell size={24} /><p>새 알림이 없습니다.</p></div>}
      </aside>
    </div>}
  </>;
}

function UsersSection({
  users,
  csrf,
  showToast,
  onAdd,
  onToggle,
  onToggleUpload,
  onEdited,
}: {
  users: AdminUser[];
  csrf: string;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
  onAdd: () => void;
  onToggle: (id: number) => void;
  onToggleUpload: (id: number) => void;
  onEdited: (user: AdminUser) => void;
}) {
  const [pageSize, tableRef] = useFitRows(42, 38);
  const { pageItems, page, totalPages, total, setPage } = usePaged(users, pageSize);
  const [reportsUser, setReportsUser] = useState<AdminUser | null>(null);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [showBulk, setShowBulk] = useState(false);
  return (
    <section>
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>사용자 관리</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => setShowBulk(true)}>
            <Upload size={15} className="icn" /> CSV로 일괄 등록
          </button>
          <button className="btn btn-primary" onClick={onAdd}>
            <Plus size={15} className="icn" /> 새 사용자 추가
          </button>
        </div>
      </div>
      {showBulk && (
        <BulkAddUsersModal
          csrf={csrf}
          onClose={() => setShowBulk(false)}
          onDone={() => {
            showToast("일괄 등록이 완료됐습니다. 목록을 새로고침합니다...", "ok");
            setTimeout(() => location.reload(), 1200);
          }}
        />
      )}
      <div className="card-table" ref={tableRef}>
        <table>
          <colgroup>
            <col style={{ width: "4%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "9%" }} />
            <col style={{ width: "17%" }} />
            <col style={{ width: "9%" }} />
            <col style={{ width: "27%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>아이디</th>
              <th>표시 이름</th>
              <th>보고서</th>
              <th title="클릭하면 업로드 허용/차단이 바뀝니다">업로드</th>
              <th>마지막 로그인</th>
              <th>상태</th>
              <th>액션</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td title={u.username}>
                  {u.username}
                  {u.is_admin && (
                    <span className="pill admin" style={{ marginLeft: 4 }}>
                      관리자
                    </span>
                  )}
                  {u.email && (
                    <span className="pill" title={`MS 계정 로그인 연동: ${u.email}`} style={{ marginLeft: 4 }}>
                      MS
                    </span>
                  )}
                </td>
                <td title={u.display_name}>{u.display_name}</td>
                <td>
                  {u.is_admin ? (
                    <span title="관리자는 권한과 무관하게 전체 열람">전체</span>
                  ) : (
                    <button
                      className="btn btn-sm"
                      title="클릭하면 열람 가능한 보고서 목록을 봅니다"
                      onClick={() => setReportsUser(u)}
                    >
                      {u.report_count}
                    </button>
                  )}
                </td>
                <td>
                  {u.is_admin ? (
                    <span title="관리자는 항상 업로드 가능">—</span>
                  ) : (
                    <button
                      className={`pill ${u.can_upload ? "active" : "inactive"}`}
                      style={{ border: "none", cursor: "pointer", font: "inherit" }}
                      title="클릭하여 업로드 허용/차단 전환"
                      onClick={() => onToggleUpload(u.id)}
                    >
                      {u.can_upload ? "허용" : "차단"}
                    </button>
                  )}
                </td>
                <td title={u.last_login_at || ""}>{u.last_login_at || "-"}</td>
                <td>
                  <span className={`pill ${u.is_active ? "active" : "inactive"}`}>
                    {u.is_active ? "활성" : "비활성"}
                  </span>
                </td>
                <td className="ad-actions-cell">
                  {u.username !== "admin" && (
                    <>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => setEditingUser(u)}
                      >
                        수정
                      </button>
                      <button
                        className="btn btn-warn btn-sm"
                        onClick={() => onToggle(u.id)}
                      >
                        활성/비활성
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} totalPages={totalPages} total={total} onPage={setPage} />
      {reportsUser && (
        <UserReportsModal user={reportsUser} onClose={() => setReportsUser(null)} />
      )}
      {editingUser && (
        <EditUserModal
          user={editingUser}
          csrf={csrf}
          departments={departmentOptions(users)}
          onClose={() => setEditingUser(null)}
          onSaved={(u) => {
            onEdited(u);
            setEditingUser(null);
            showToast("사용자 정보가 수정되었습니다.", "ok");
          }}
          onError={(m) => showToast("오류: " + m, "err")}
        />
      )}
    </section>
  );
}

/** 이미 쓰이고 있는 department 값 목록(오탈자 방지용 자동완성 제안일 뿐, 강제 아님 — docs/01 참고). */
function departmentOptions(users: AdminUser[]): string[] {
  return Array.from(new Set(users.map((u) => u.department).filter((d): d is string => !!d))).sort();
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="ad-field">
      <label>{label}</label>
      {children}
    </div>
  );
}

/** 모달 공통 뼈대: 오버레이(바깥 클릭 닫기) + 헤더(제목·× 버튼). 본문·푸터는 children으로 받는다. */
function Modal({
  title,
  wide,
  onClose,
  children,
}: {
  title: React.ReactNode;
  wide?: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="ad-modal-overlay" onClick={onClose}>
      <div
        className={`ad-modal${wide ? " ad-modal-wide" : ""}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ad-modal-header">
          <h3>{title}</h3>
          <button className="ad-modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function AddUserModal({
  csrf,
  departments,
  onClose,
  onAdded,
  onError,
}: {
  csrf: string;
  departments: string[];
  onClose: () => void;
  onAdded: () => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [groups, setGroups] = useState<AdminGroup[] | null>(null);
  const [selectedGroups, setSelectedGroups] = useState<number[]>([]);
  const [department, setDepartment] = useState("");

  useEffect(() => {
    adminGetGroups().then(setGroups).catch(() => setGroups([]));
  }, []);

  const toggleGroup = (id: number) => {
    setSelectedGroups((prev) =>
      prev.includes(id) ? prev.filter((g) => g !== id) : [...prev, id],
    );
  };

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    try {
      const fd = new FormData(e.currentTarget);
      fd.set("group_ids", selectedGroups.join(","));
      fd.set("department", department);
      await adminAddUser(fd);
      onAdded();
    } catch (err) {
      onError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal title="새 사용자 추가" wide onClose={onClose}>
      <form onSubmit={submit}>
          <div className="ad-modal-body">
            <input type="hidden" name="csrf" value={csrf} />
            <div className="ad-form-grid">
              <Field label="아이디 *">
                <input name="username" required placeholder="login_id" autoFocus />
              </Field>
              <Field label="비밀번호 * (8자 이상)">
                <input name="password" type="password" required placeholder="••••••••" />
              </Field>
              <Field label="표시 이름 *">
                <input name="display_name" required placeholder="홍길동" />
              </Field>
              <Field label="이메일 (MS 계정 로그인용, 선택)">
                <input name="email" type="email" placeholder="user@qualisoft.co.kr" />
              </Field>
              <Field label="관리자 권한">
                <select name="is_admin" defaultValue="false">
                  <option value="false">일반 사용자</option>
                  <option value="true">관리자</option>
                </select>
              </Field>
              <Field label="보고서 업로드">
                <select name="can_upload" defaultValue="true">
                  <option value="true">허용</option>
                  <option value="false">차단 (열람만 가능)</option>
                </select>
              </Field>
            </div>
            <Field label="그룹 (선택한 그룹의 보고서 열람 권한을 그대로 받습니다)">
              {!groups && <span className="muted">불러오는 중...</span>}
              {groups && groups.length === 0 && (
                <span className="muted">등록된 그룹이 없습니다.</span>
              )}
              {groups && groups.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 14px" }}>
                  {groups.map((g) => (
                    <label
                      key={g.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 5,
                        fontWeight: 400,
                        whiteSpace: "nowrap",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selectedGroups.includes(g.id)}
                        onChange={() => toggleGroup(g.id)}
                      />
                      {g.name}
                    </label>
                  ))}
                </div>
              )}
            </Field>
            <Field label="부서 (GET 필터)">
              <input value={department} onChange={e=>setDepartment(e.target.value)} list="department-options" placeholder="예: AMT" />
              <datalist id="department-options">{departments.map((d) => <option key={d} value={d} />)}</datalist>
            </Field>
          </div>
          <div className="ad-modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              취소
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "추가 중..." : "사용자 추가"}
            </button>
          </div>
        </form>
    </Modal>
  );
}

function EditUserModal({
  user,
  csrf,
  departments,
  onClose,
  onSaved,
  onError,
}: {
  user: AdminUser;
  csrf: string;
  departments: string[];
  onClose: () => void;
  onSaved: (user: AdminUser) => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [displayName, setDisplayName] = useState(user.display_name);
  const [department, setDepartment] = useState(user.department || "");
  const [email, setEmail] = useState(user.email || "");

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    try {
      await adminEditUser(
        user.id,
        {
          display_name: displayName, pbi_username: user.pbi_username, department, email,
        },
        csrf,
      );
      onSaved({
        ...user,
        display_name: displayName,
        department, email: email || null,
      });
    } catch (err) {
      onError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal title={<>사용자 수정 — {user.username}</>} wide onClose={onClose}>
      <form onSubmit={submit}>
        <div className="ad-modal-body">
          <div className="ad-form-grid">
            <Field label="표시 이름 *">
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                required
                autoFocus
              />
            </Field>
            <Field label="이메일 (MS 계정 로그인용, 선택)">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="user@qualisoft.co.kr"
              />
            </Field>
            <Field label="부서 (GET 필터)">
              <input value={department} onChange={e=>setDepartment(e.target.value)} list="department-options" placeholder="예: AMT" />
              <datalist id="department-options">{departments.map((d) => <option key={d} value={d} />)}</datalist>
            </Field>
          </div>
        </div>
        <div className="ad-modal-footer">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            취소
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "저장 중..." : "저장"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function BulkAddUsersModal({
  csrf,
  onClose,
  onDone,
}: {
  csrf: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BulkAddResult | null>(null);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const r = await adminBulkAddUsers(file, csrf);
      setResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="CSV로 사용자 일괄 등록" wide onClose={onClose}>
      <div className="ad-modal-body">
        {!result && (
          <>
            <p className="ad-bulk-lead">
              CSV 한 장으로 여러 계정을 한 번에 만듭니다. 행마다 독립 처리되므로
              일부가 실패해도 나머지는 그대로 등록됩니다.
            </p>

            <div className="ad-bulk-step">
              <span className="ad-bulk-num">1</span>
              <div className="ad-bulk-body">
                <div className="ad-bulk-title">템플릿을 받아 작성합니다</div>
                <div className="ad-bulk-code">
                  username,password,display_name,pbi_username,groups,is_admin,can_upload,department
                </div>
                <table className="ad-bulk-cols">
                  <tbody>
                    <tr><th>username</th><td className="req">필수</td><td>로그인 아이디</td></tr>
                    <tr><th>password</th><td className="req">필수</td><td>초기 비밀번호 (8자 이상)</td></tr>
                    <tr><th>display_name</th><td className="req">필수</td><td>화면에 표시할 이름</td></tr>
                    <tr><th>pbi_username</th><td>선택</td><td>RLS Effective Identity에 쓰이는 내부 키 — 비우면 username을 그대로 씀(대부분 이대로 두면 됨)</td></tr>
                    <tr><th>groups</th><td>선택</td><td>소속 그룹 — <b>미리 만들어져 있어야</b> 하며, 그 그룹의 보고서 열람 권한을 그대로 상속</td></tr>
                    <tr><th>is_admin</th><td>선택</td><td>관리자 여부 — 비우면 <code>false</code></td></tr>
                    <tr><th>can_upload</th><td>선택</td><td>업로드 허용 — 비우면 <code>true</code></td></tr>
                    <tr><th>department</th><td>선택</td><td>GET 필터 값 — 그 보고서의 필터 컬럼과 정확히 일치해야 함(예: <code>AMT</code>)</td></tr>
                  </tbody>
                </table>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    const csv =
                      "username,password,display_name,pbi_username,groups,is_admin,can_upload,department\n" +
                      "user01,TempPass123!,홍길동,user01@customer.com,영업팀,false,true,AMT\n";
                    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "사용자_일괄등록_템플릿.csv";
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    URL.revokeObjectURL(url);
                  }}
                >
                  <Download size={14} className="icn" /> 템플릿 다운로드
                </button>
              </div>
            </div>

            <div className="ad-bulk-step">
              <span className="ad-bulk-num">2</span>
              <div className="ad-bulk-body">
                <div className="ad-bulk-title">작성한 파일을 선택합니다</div>
                <label className="ad-bulk-drop">
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                  <Upload size={18} className="icn" />
                  <span className="ad-bulk-dropname">
                    {file ? file.name : "클릭해서 .csv 파일 선택"}
                  </span>
                  {file && (
                    <span className="ad-bulk-size">{Math.max(1, Math.round(file.size / 1024))} KB</span>
                  )}
                </label>
              </div>
            </div>

            {error && <p className="ad-bulk-error">오류: {error}</p>}
          </>
        )}
        {result && (
          <>
            <div className="ad-bulk-summary">
              <span className="ok">성공 {result.created}건</span>
              <span className={result.failed ? "fail" : "none"}>실패 {result.failed}건</span>
            </div>
            <div className="card-table" style={{ maxHeight: 320, overflow: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>행</th>
                    <th>아이디</th>
                    <th>결과</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((r) => (
                    <tr key={r.row}>
                      <td>{r.row}</td>
                      <td>{r.username}</td>
                      <td style={{ color: r.status === "ok" ? "inherit" : "var(--danger, #c0392b)" }}>
                        {r.status === "ok" ? "성공" : `실패 — ${r.message}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
      <div className="ad-modal-footer">
        {!result && (
          <>
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              취소
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!file || busy}
              onClick={submit}
            >
              {busy ? "등록 중..." : "업로드"}
            </button>
          </>
        )}
        {result && (
          <button type="button" className="btn btn-primary" onClick={onDone}>
            확인
          </button>
        )}
      </div>
    </Modal>
  );
}

function ReportsSection({
  reports,
  csrf,
  showToast,
  onDeleted,
  onManageAccess,
}: {
  reports: AdminReport[];
  csrf: string;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
  onDeleted: (id: number) => void;
  onManageAccess: (r: AdminReport) => void;
}) {
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState("");
  const [pageSize, tableRef] = useFitRows(44, 40);
  const { pageItems, page, totalPages, total, setPage } = usePaged(reports, pageSize);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const detail = reports.find((r) => r.id === selectedId) || pageItems[0] || null;

  const doImport = async () => {
    setImporting(true);
    setImportResult("");
    try {
      const j = await adminImportPbi(csrf);
      setImportResult(`신규 ${j.registered}개 등록 / 건너뜀 ${j.skipped}개`);
      showToast(
        j.registered > 0
          ? `${j.registered}개 보고서가 새로 등록됐습니다. 권한 설정 후 노출됩니다.`
          : "새로 등록된 보고서가 없습니다.",
        j.registered > 0 ? "ok" : "",
      );
      if (j.registered > 0) setTimeout(() => location.reload(), 2000);
    } catch (e) {
      setImportResult("오류: " + (e as Error).message);
      showToast("가져오기 실패: " + (e as Error).message, "err");
    } finally {
      setImporting(false);
    }
  };

  const doDelete = async (r: AdminReport) => {
    if (
      !confirm(
        `"${r.name}" 보고서를 삭제하시겠습니까?\nPower BI 워크스페이스에서도 완전히 삭제됩니다.`,
      )
    )
      return;
    try {
      const j = await adminDeleteReport(r.id, csrf);
      onDeleted(r.id);
      showToast(
        j.pbi_warning
          ? `"${r.name}" DB 삭제 완료. PBI 경고: ${j.pbi_warning}`
          : `"${r.name}" 보고서가 PBI와 DB에서 삭제됐습니다.`,
        j.pbi_warning ? "err" : "ok",
      );
    } catch (e) {
      showToast("오류: " + (e as Error).message, "err");
    }
  };

  return (
    <section>
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>보고서 관리</h2>
        <div className="ad-section-actions">
          <span className="ad-import-result">{importResult}</span>
          <button className="btn btn-primary" disabled={importing} onClick={doImport}>
            <Download size={15} className="icn" />{" "}
            {importing ? "가져오는 중..." : "PBI에서 가져오기"}
          </button>
        </div>
      </div>
      <div className="home-main">
        <div className="home-tablewrap card-table" ref={tableRef}>
          <table>
            <colgroup>
              <col style={{ width: "5%" }} />
              <col style={{ width: "30%" }} />
              <col style={{ width: "13%" }} />
              <col style={{ width: "16%" }} />
              <col style={{ width: "16%" }} />
              <col style={{ width: "20%" }} />
            </colgroup>
            <thead>
              <tr>
                <th>ID</th>
                <th>보고서명</th>
                <th>구분</th>
                <th>카테고리</th>
                <th>접근</th>
                <th>액션</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((r) => (
                <tr
                  key={r.id}
                  className={`home-table-row${detail?.id === r.id ? " sel" : ""}`}
                  onClick={() => setSelectedId(r.id)}
                >
                  <td>{r.id}</td>
                  <td title={r.name}>
                    <div className="home-table-name">
                      <span className="home-mini-ic" style={{ background: categoryColor(r.category) }}>
                        <BarChart3 size={12} />
                      </span>
                      {r.name}
                      {r.status === "deleted" && (
                        <span className="pill inactive" style={{ marginLeft: 6 }}>삭제됨</span>
                      )}
                      {r.status !== "active" && r.status !== "deleted" && (
                        <span className="pill pending" style={{ marginLeft: 6 }}>{r.status}</span>
                      )}
                    </div>
                  </td>
                  <td>
                    {r.visibility === "shared" ? (
                      <span className="pill active">공용</span>
                    ) : r.group_count > 0 ? (
                      <span className="pill pending">그룹 공유</span>
                    ) : (
                      <span className="pill inactive">비공개{r.owner_username ? ` · ${r.owner_username}` : ""}</span>
                    )}
                  </td>
                  <td>
                    <span
                      className="home-cat-pill"
                      style={{ background: withAlpha(categoryColor(r.category), "22"), color: categoryColor(r.category) }}
                    >
                      {r.category || "미분류"}
                    </span>
                  </td>
                  <td>
                    {r.viewer_count === 0 && r.group_count === 0 ? (
                      <span className="pill inactive">비공개</span>
                    ) : (
                      <span className="pill active">
                        공개
                        {r.viewer_count > 0 && ` · ${r.viewer_count}명`}
                        {r.group_count > 0 && ` · ${r.group_count}그룹`}
                      </span>
                    )}
                  </td>
                  <td className="ad-actions-cell">
                    <span
                      className="ad-aicon"
                      style={{ background: "#3452E8" }}
                      title="권한 관리"
                      onClick={(e) => { e.stopPropagation(); onManageAccess(r); }}
                    >
                      <UsersIcon size={12} />
                    </span>
                    {r.status !== "deleted" && (
                      <span
                        className="ad-aicon"
                        style={{ background: "#E25C4E" }}
                        title="삭제"
                        onClick={(e) => { e.stopPropagation(); doDelete(r); }}
                      >
                        <X size={13} />
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {detail && (
          <div className="home-detail">
            <span className="home-detail-icon" style={{ background: categoryColor(detail.category) }}>
              <BarChart3 size={20} />
            </span>
            <div className="home-detail-name">{detail.name}</div>
            <div className="home-detail-cat">
              {detail.category || "미분류"} ·{" "}
              {detail.report_type === "managed" ? "공용" : `개인 · ${detail.owner_username || "-"}`}
            </div>
            {detail.description && <div className="home-detail-desc">{detail.description}</div>}
            <div className="crm-detail-label">사용 현황</div>
            <div className="crm-detail-row">
              열람 인원<b>{detail.viewer_count}명</b>
            </div>
            <div className="crm-detail-row">
              권한 그룹<b>{detail.group_count}개</b>
            </div>
            <div className="crm-detail-row">
              등록일<b>{detail.created_at ? detail.created_at.slice(0, 10) : "-"}</b>
            </div>
            <div className="home-detail-actions">
              <button className="btn btn-ghost" onClick={() => onManageAccess(detail)}>
                권한 편집
              </button>
              {detail.status !== "deleted" && (
                <button className="btn btn-danger" onClick={() => doDelete(detail)}>
                  삭제
                </button>
              )}
            </div>
          </div>
        )}
      </div>
      <Pager page={page} totalPages={totalPages} total={total} onPage={setPage} />
    </section>
  );
}

export function ConfigSection({
  csrf,
  showToast,
}: {
  csrf: string;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
}) {
  const [rows, setRows] = useState<AppConfigRow[] | null>(null);
  const [edited, setEdited] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    adminGetConfig()
      .then(setRows)
      .catch(() => showToast("설정 조회 실패", "err"));
  }, [showToast]);

  const [showAdvanced, setShowAdvanced] = useState(false);

  // 기본 노출은 "실제 장애/민원 상황에서 즉시 만지는 값"만 남긴다 — 그 외 정책·구현
  // 세부값은 전부 "고급 설정"으로 뺐다. 예: 파일이 안 올라간다(용량 초과), 오늘 한도
  // 찼다, 로그인이 잠겼다 — 이 셋은 관리자가 바로 이 화면에서 풀어줘야 하는 실제 민원이다.
  const categories: { title: string; hint?: string; keys: string[] }[] = [
    {
      title: "업로드",
      keys: ["max_pbix_size_mb", "max_uploads_per_day", "max_personal_reports"],
    },
    {
      title: "로그인 · 보안",
      hint: "직원이 로그인이 안 된다고 하면 여기 두 값을 확인하세요.",
      keys: ["login_block_max_fail", "login_block_minutes"],
    },
  ];
  const advancedKeys = [
    "password_min_len", "pbi_sync_interval", "pbi_token_cache_margin_sec",
    "activity_log_retention_days", "error_log_retention_days", "refresh_auto_retry_max",
    "report_name_max_len", "import_poll_interval_sec", "import_poll_max",
    "embed_token_lifetime_min",
    "recents_limit", "activity_log_max_rows", "admin_upload_jobs_limit",
  ];
  const byKey = new Map((rows ?? []).map((r) => [r.key, r]));
  const dirtyKeys = Object.keys(edited).filter((key) => {
    const row = byKey.get(key);
    return row && edited[key].trim() && edited[key] !== row.value;
  });
  const saveAll = async () => {
    if (!dirtyKeys.length) return;
    setSaving(true);
    const saved: Record<string, string> = {};
    try {
      for (const key of dirtyKeys) {
        const value = edited[key].trim();
        await adminSetConfig(key, value, csrf);
        saved[key] = value;
      }
      setRows((prev) => prev?.map((r) => saved[r.key] !== undefined ? { ...r, value: saved[r.key] } : r) ?? prev);
      setEdited((prev) => {
        const next = { ...prev };
        Object.keys(saved).forEach((key) => delete next[key]);
        return next;
      });
      showToast(`${dirtyKeys.length}개 설정을 저장했습니다.`, "ok");
    } catch (e) {
      showToast("오류: " + (e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="cfg-page">
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>런타임 설정</h2>
      </div>
      {!rows && <div className="ad-modal-loading">불러오는 중...</div>}
      {rows &&
        categories.map((cat) => (
          <div key={cat.title} className="cfg-group">
            <h3 className="cfg-group-title">
              {cat.title}
              {cat.hint && <span className="cfg-group-hint">{cat.hint}</span>}
            </h3>
            <div className="cfg-grid">
              {cat.keys.map((key) => {
                const r = byKey.get(key);
                if (!r) return null;
                const dirty = edited[key] !== undefined && edited[key] !== r.value;
                return (
                  <div key={key} className={`cfg-card${dirty ? " dirty" : ""}`}>
                    <div className="cfg-key">{key}</div>
                    <div className="cfg-desc">{r.description || "-"}</div>
                    <div className="cfg-row">
                      <input
                        inputMode="numeric"
                        value={edited[key] ?? r.value}
                        onChange={(e) =>
                          setEdited((prev) => ({ ...prev, [key]: e.target.value }))
                        }
                        onKeyDown={(e) => e.key === "Enter" && dirty && saveAll()}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      {rows && (
        <div className="cfg-group">
          <button
            type="button"
            className="cfg-advanced-toggle"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "▾" : "▸"} 고급 설정 ({advancedKeys.length})
          </button>
          {showAdvanced && (
            <div className="cfg-grid">
              {advancedKeys.map((key) => {
                const r = byKey.get(key);
                if (!r) return null;
                const dirty = edited[key] !== undefined && edited[key] !== r.value;
                return (
                  <div key={key} className={`cfg-card${dirty ? " dirty" : ""}`}>
                    <div className="cfg-key">{key}</div>
                    <div className="cfg-desc">{r.description || "-"}</div>
                    <div className="cfg-row">
                      <input
                        inputMode="numeric"
                        value={edited[key] ?? r.value}
                        onChange={(e) =>
                          setEdited((prev) => ({ ...prev, [key]: e.target.value }))
                        }
                        onKeyDown={(e) => e.key === "Enter" && dirty && saveAll()}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
      <div className="cfg-savebar">
        <span>{dirtyKeys.length ? `${dirtyKeys.length}개 변경됨` : "변경 사항 없음"}</span>
        <button className="btn btn-primary" disabled={saving || !dirtyKeys.length} onClick={saveAll}>
          {saving ? "저장 중..." : "변경사항 저장"}
        </button>
      </div>
    </section>
  );
}

function AccessModal({
  report,
  csrf,
  onClose,
  showToast,
}: {
  report: AdminReport;
  csrf: string;
  onClose: () => void;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
}) {
  const [tab, setTab] = useState<"users" | "groups">("users");
  const [users, setUsers] = useState<AccessUser[] | null>(null);
  const [groups, setGroups] = useState<GroupAccess[] | null>(null);
  const [visibility, setVisibility] = useState<"personal" | "shared">(
    report.visibility === "shared" ? "shared" : "personal",
  );
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, g] = await Promise.all([
        adminGetAccess(report.id),
        adminGetGroupAccess(report.id),
      ]);
      setUsers(u);
      setGroups(g);
    } catch {
      setError("권한 목록 조회 실패");
    }
  }, [report.id]);

  useEffect(() => {
    load();
  }, [load]);

  const setAccess = async (userId: number, canView: boolean) => {
    try {
      await adminSetAccess(report.id, userId, canView, csrf);
      showToast(canView ? "열람 권한이 부여됐습니다." : "열람 권한이 해제됐습니다.", "ok");
      await load();
    } catch (err) {
      showToast((err as Error).message, "err");
    }
  };

  const setGroupAccess = async (groupId: number, canView: boolean) => {
    try {
      await adminSetGroupAccess(report.id, groupId, canView, csrf);
      showToast(canView ? "그룹에 열람 권한이 부여됐습니다." : "그룹 열람 권한이 해제됐습니다.", "ok");
      await load();
    } catch (err) {
      showToast((err as Error).message, "err");
    }
  };

  const changeVisibility = async (next: "personal" | "shared") => {
    try {
      await adminSetReportVisibility(report.id, next, csrf);
      setVisibility(next);
      showToast(next === "shared" ? "포털 공용으로 공개했습니다." : "공용 공개를 해제했습니다.", "ok");
    } catch (err) {
      showToast((err as Error).message, "err");
    }
  };

  return (
    <Modal title={<>열람 권한 — {report.name}</>} onClose={onClose}>
        <div className="ad-access-scope">
          <div><strong>포털 공용 공개</strong><span>켜면 로그인한 모든 사용자가 이 보고서를 열람할 수 있습니다.</span></div>
          <button className={`btn btn-sm ${visibility === "shared" ? "btn-danger" : "btn-primary"}`} onClick={() => changeVisibility(visibility === "shared" ? "personal" : "shared")}>
            {visibility === "shared" ? "공용 해제" : "공용으로 공개"}
          </button>
        </div>
        <div className="ad-modal-tabs">
          <button
            className={`btn btn-sm ${tab === "users" ? "btn-primary" : ""}`}
            onClick={() => setTab("users")}
          >
            사용자 {users ? `(${users.filter((u) => !u.is_admin && u.can_view).length})` : ""}
          </button>
          <button
            className={`btn btn-sm ${tab === "groups" ? "btn-primary" : ""}`}
            onClick={() => setTab("groups")}
            style={{ marginLeft: 6 }}
          >
            그룹 {groups ? `(${groups.filter((g) => g.can_view).length})` : ""}
          </button>
        </div>
        <div className="ad-modal-body">
          {error && <div className="ad-modal-err">{error}</div>}
          {!error && tab === "users" && !users && (
            <div className="ad-modal-loading">불러오는 중...</div>
          )}
          {tab === "users" &&
            users?.map((u) => (
              <div key={u.id} className="ad-access-row">
                <div className="ad-access-info">
                  <span className="ad-access-name">{u.display_name}</span>
                  <span className="ad-access-id">{u.username}</span>
                  {u.is_admin && <span className="pill admin">관리자</span>}
                  {!u.is_admin && u.via_group && (
                    <span
                      className={`pill ${u.direct === false ? "inactive" : "active"}`}
                      title="소속 그룹으로도 이 보고서 열람 권한이 있습니다"
                    >
                      그룹경유{u.direct === false ? " · 차단됨" : ""}
                    </span>
                  )}
                </div>
                {u.is_admin ? (
                  <span className="ad-access-always" title="관리자는 권한과 무관하게 모든 보고서를 봅니다">
                    전체 열람 (권한 불필요)
                  </span>
                ) : (
                  <button
                    className={`btn btn-sm ${u.can_view ? "btn-danger" : "btn-primary"}`}
                    title={
                      u.can_view && u.via_group
                        ? "그룹으로 부여된 권한이 있어도 이 사람만 예외로 차단합니다"
                        : undefined
                    }
                    onClick={() => setAccess(u.id, !u.can_view)}
                  >
                    {u.can_view ? "차단" : "허용"}
                  </button>
                )}
              </div>
            ))}
          {tab === "groups" && !error && !groups && (
            <div className="ad-modal-loading">불러오는 중...</div>
          )}
          {tab === "groups" && groups && groups.length === 0 && (
            <div className="ad-modal-loading">
              그룹이 없습니다. 관리자 포털 '그룹' 탭에서 먼저 만드세요.
            </div>
          )}
          {tab === "groups" &&
            groups?.map((g) => (
              <div key={g.id} className="ad-access-row">
                <div className="ad-access-info">
                  <span className="ad-access-name">{g.name}</span>
                  <span className="ad-access-id">멤버 {g.member_count}명</span>
                </div>
                <button
                  className={`btn btn-sm ${g.can_view ? "btn-danger" : "btn-primary"}`}
                  onClick={() => setGroupAccess(g.id, !g.can_view)}
                >
                  {g.can_view ? "해제" : "부여"}
                </button>
              </div>
            ))}
        </div>
    </Modal>
  );
}

function UserReportsModal({
  user,
  onClose,
}: {
  user: AdminUser;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<UserReportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminGetUserReports(user.id)
      .then(setRows)
      .catch(() => setError("열람 보고서 조회 실패"));
  }, [user.id]);

  return (
    <Modal
      title={
        <>
          열람 가능 보고서 — {user.display_name}{" "}
          <span className="ad-access-id">{user.username}</span>
        </>
      }
      onClose={onClose}
    >
        <div className="ad-modal-body">
          {error && <div className="ad-modal-err">{error}</div>}
          {!error && !rows && <div className="ad-modal-loading">불러오는 중...</div>}
          {rows && rows.length === 0 && (
            <div className="ad-modal-loading">열람 가능한 보고서가 없습니다.</div>
          )}
          {rows?.map((r) => (
            <div key={r.id} className="ad-access-row">
              <div className="ad-access-info">
                <span className="ad-access-name">{r.name}</span>
                {r.category && <span className="ad-access-id">{r.category}</span>}
              </div>
              <div>
                {r.direct && <span className="pill active">직접</span>}
                {r.via_groups.map((g) => (
                  <span key={g} className="pill admin" style={{ marginLeft: 4 }}>
                    {g}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
    </Modal>
  );
}

/* ── 그룹 관리 ─────────────────────────────────────────── */

function GroupsSection({
  csrf,
  showToast,
}: {
  csrf: string;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
}) {
  const [groups, setGroups] = useState<AdminGroup[] | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [memberGroup, setMemberGroup] = useState<AdminGroup | null>(null);

  const load = useCallback(async () => {
    try {
      setGroups(await adminGetGroups());
    } catch {
      showToast("그룹 목록 조회 실패", "err");
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    if (!name.trim()) return;
    try {
      await adminCreateGroup(name.trim(), desc.trim(), csrf);
      showToast(`'${name.trim()}' 그룹이 생성됐습니다.`, "ok");
      setName("");
      setDesc("");
      await load();
    } catch (e) {
      showToast("오류: " + (e as Error).message, "err");
    }
  };

  const remove = async (g: AdminGroup) => {
    if (!confirm(`'${g.name}' 그룹을 삭제할까요?\n멤버·보고서 부여도 함께 해제됩니다 (개별 부여는 유지).`)) return;
    try {
      await adminDeleteGroup(g.id, csrf);
      showToast(`'${g.name}' 그룹이 삭제됐습니다.`, "ok");
      await load();
    } catch (e) {
      showToast("오류: " + (e as Error).message, "err");
    }
  };

  return (
    <section>
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>그룹 관리</h2>
        <span className="ad-import-result">
          팀·부서 단위로 묶어 보고서 권한을 한 번에 부여합니다 (부여는 보고서 탭 → 권한 → 그룹).
        </span>
      </div>
      <div className="ad-section-head" style={{ gap: 8 }}>
        <input
          placeholder="그룹 이름 (예: 영업팀)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <input
          placeholder="설명 (선택)"
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
          style={{ flex: 1 }}
        />
        <button className="btn btn-primary" onClick={create} disabled={!name.trim()}>
          <Plus size={15} className="icn" /> 그룹 추가
        </button>
      </div>
      <div className="card-table">
        <table>
          <colgroup>
            <col style={{ width: "20%" }} />
            <col style={{ width: "34%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "22%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>그룹명</th>
              <th>설명</th>
              <th>멤버</th>
              <th title="이 그룹에 열람 권한이 부여된 보고서 수">보고서</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {!groups && (
              <tr>
                <td colSpan={5}>불러오는 중...</td>
              </tr>
            )}
            {groups && groups.length === 0 && (
              <tr>
                <td colSpan={5}>그룹이 없습니다. 위에서 첫 그룹을 만들어 보세요.</td>
              </tr>
            )}
            {groups?.map((g) => (
              <tr key={g.id}>
                <td title={g.name}>{g.name}</td>
                <td title={g.description || ""}>{g.description || "-"}</td>
                <td>{g.member_count}</td>
                <td>{g.report_count}</td>
                <td>
                  <button className="btn btn-sm" onClick={() => setMemberGroup(g)}>
                    멤버 관리
                  </button>
                  <button
                    className="btn btn-sm btn-danger"
                    style={{ marginLeft: 6 }}
                    onClick={() => remove(g)}
                  >
                    삭제
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {memberGroup && (
        <GroupMembersModal
          group={memberGroup}
          csrf={csrf}
          showToast={showToast}
          onClose={() => {
            setMemberGroup(null);
            load(); // 멤버 수 갱신
          }}
        />
      )}
    </section>
  );
}

function GroupMembersModal({
  group,
  csrf,
  onClose,
  showToast,
}: {
  group: AdminGroup;
  csrf: string;
  onClose: () => void;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
}) {
  const [members, setMembers] = useState<GroupMember[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMembers(await adminGetGroupMembers(group.id));
    } catch {
      setError("멤버 목록 조회 실패");
    }
  }, [group.id]);

  useEffect(() => {
    load();
  }, [load]);

  const setMember = async (userId: number, member: boolean) => {
    try {
      await adminSetGroupMember(group.id, userId, member, csrf);
      showToast(member ? "멤버로 추가됐습니다." : "멤버에서 제외됐습니다.", "ok");
      await load();
    } catch {
      showToast("오류가 발생했습니다.", "err");
    }
  };

  return (
    <Modal title={<>멤버 관리 — {group.name}</>} onClose={onClose}>
        <div className="ad-modal-body">
          {error && <div className="ad-modal-err">{error}</div>}
          {!error && !members && <div className="ad-modal-loading">불러오는 중...</div>}
          {members?.map((u) => (
            <div key={u.id} className="ad-access-row">
              <div className="ad-access-info">
                <span className="ad-access-name">{u.display_name}</span>
                <span className="ad-access-id">{u.username}</span>
                {u.is_admin && <span className="pill admin">관리자</span>}
              </div>
              <button
                className={`btn btn-sm ${u.is_member ? "btn-danger" : "btn-primary"}`}
                onClick={() => setMember(u.id, !u.is_member)}
              >
                {u.is_member ? "제외" : "추가"}
              </button>
            </div>
          ))}
        </div>
    </Modal>
  );
}

/* ── 권한 매트릭스 (그룹 × 보고서 한눈에 보기/토글) ────── */

const EVENT_LABELS: Record<string, string> = {
  report_view: "보고서 열람",
  report_upload: "보고서 업로드",
};

// event_log.details(JSONB)는 감사 이벤트 종류마다 키가 다르다(managed_report_imported는
// name/category/pbi_item_id, 다른 이벤트는 또 다른 키 조합) — 이벤트별로 따로 파싱하는
// 대신 뭐가 오든 "key: value · key: value"로 풀어 보여준다. 원본 JSON은 title(hover)로만.
function formatDetails(details: unknown): string {
  if (details == null) return "-";
  if (typeof details !== "object") return String(details);
  const entries = Array.isArray(details)
    ? details.map((v, i) => [String(i), v] as const)
    : Object.entries(details as Record<string, unknown>);
  if (entries.length === 0) return "-";
  return entries.map(([k, v]) => `${k}: ${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`).join(" · ");
}

function LogsSection() {
  const [tab, setTab] = useState<"activity" | "audit">("activity");
  const [username, setUsername] = useState("");
  const [event, setEvent] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [rows, setRows] = useState<LogRow[] | null>(null);
  const [rowLimit, setRowLimit] = useState(1000); // 서버 app_config.activity_log_max_rows로 즉시 갱신됨
  const [error, setError] = useState<string | null>(null);

  const [pageSize, tableRef] = useFitRows(40, 38);
  const { pageItems, page, totalPages, total, setPage } = usePaged(rows || [], pageSize);

  const filters = { username, event, date_from: dateFrom, date_to: dateTo };

  const load = useCallback(
    async (t: "activity" | "audit", f: typeof filters) => {
      setRows(null);
      setError(null);
      try {
        const { rows: loaded, limit } = await adminGetLogs(t, f);
        setRows(loaded);
        setRowLimit(limit);
      } catch {
        setError("로그 조회 실패");
      }
    },
    [],
  );

  useEffect(() => {
    load(tab, { username: "", event: "", date_from: "", date_to: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  return (
    <section>
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>로그</h2>
        <div className="ad-section-actions">
          <button
            className={`btn btn-sm ${tab === "activity" ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setTab("activity")}
          >
            사용자 활동
          </button>
          <button
            className={`btn btn-sm ${tab === "audit" ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setTab("audit")}
          >
            관리 감사
          </button>
        </div>
      </div>

      <div className="lg-filters">
        {tab === "activity" && (
          <>
            <input
              placeholder="사용자 검색"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <select value={event} onChange={(e) => setEvent(e.target.value)}>
              <option value="">전체 이벤트</option>
              <option value="report_view">보고서 열람</option>
              <option value="report_upload">보고서 업로드</option>
            </select>
          </>
        )}
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        <span className="lg-tilde">~</span>
        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        <button className="btn btn-primary btn-sm" onClick={() => load(tab, filters)}>
          조회
        </button>
        <a
          className="btn btn-ghost btn-sm"
          href={`/api/admin/logs/export?${logQueryString(tab, filters)}`}
          title="현재 필터 조건 그대로 CSV(Excel) 다운로드"
        >
          <Download size={13} className="icn" /> CSV
        </a>
      </div>

      {error && <div className="ad-modal-err">{error}</div>}
      {!error && !rows && <div className="ad-modal-loading">불러오는 중...</div>}
      {rows && rows.length >= rowLimit && (
        <div className="mx-hint" style={{ marginBottom: 8 }}>
          최근 {rowLimit.toLocaleString()}건까지만 표시합니다 — 전체가 필요하면 기간을 좁히거나 CSV로 받으세요.
        </div>
      )}
      {rows && (
        <div className="card-table" ref={tableRef}>
          <table>
            {tab === "activity" ? (
              <>
                <colgroup>
                  <col style={{ width: "20%" }} />
                  <col style={{ width: "14%" }} />
                  <col style={{ width: "16%" }} />
                  <col style={{ width: "34%" }} />
                  <col style={{ width: "16%" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>일시</th>
                    <th>사용자</th>
                    <th>이벤트</th>
                    <th>보고서</th>
                    <th>IP</th>
                  </tr>
                </thead>
                <tbody>
                  {pageItems.map((r) => (
                    <tr key={r.id}>
                      <td>{String(r.created_at).replace("T", " ").slice(0, 19)}</td>
                      <td>{r.username}</td>
                      <td>{EVENT_LABELS[r.event || ""] || r.event}</td>
                      <td title={r.report_name || ""}>{r.report_name || "-"}</td>
                      <td>{r.ip || "-"}</td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="rp-all-empty">
                        조건에 맞는 로그가 없습니다
                      </td>
                    </tr>
                  )}
                </tbody>
              </>
            ) : (
              <>
                <colgroup>
                  <col style={{ width: "20%" }} />
                  <col style={{ width: "14%" }} />
                  <col style={{ width: "18%" }} />
                  <col style={{ width: "22%" }} />
                  <col style={{ width: "26%" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>일시</th>
                    <th>행위자</th>
                    <th>행위</th>
                    <th>보고서</th>
                    <th>상세</th>
                  </tr>
                </thead>
                <tbody>
                  {pageItems.map((r) => (
                    <tr key={r.id}>
                      <td>{String(r.created_at).replace("T", " ").slice(0, 19)}</td>
                      <td>{r.actor || "시스템"}</td>
                      <td>{r.action}</td>
                      <td title={r.report_name || ""}>{r.report_name || "-"}</td>
                      <td className="ad-detail-cell">
                        <span className="ad-detail-text" title={JSON.stringify(r.details, null, 2)}>
                          {formatDetails(r.details)}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="rp-all-empty">
                        조건에 맞는 로그가 없습니다
                      </td>
                    </tr>
                  )}
                </tbody>
              </>
            )}
          </table>
        </div>
      )}
      {rows && <Pager page={page} totalPages={totalPages} total={total} onPage={setPage} />}
    </section>
  );
}
