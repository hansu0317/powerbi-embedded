import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  Download,
  History,
  LayoutDashboard,
  Layers,
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
  AdminJob,
} from "../bootstrap";
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
  adminSetGroupMember,
  adminSyncStatus,
  adminToggleUser,
  adminToggleUpload,
  adminGetLogs,
  adminGetSystemStatus,
  logQueryString,
  LogRow,
  SystemStatus,
  logout,
} from "../api";
import { Pager, usePaged, useFitRows } from "../Pager";

type SectionKey =
  | "overview" | "users" | "groups" | "reports" | "logs" | "config";
type Toast = { msg: string; tone: "ok" | "err" | "" } | null;

const SECTIONS: {
  key: SectionKey;
  Icon: typeof LayoutDashboard;
  label: string;
}[] = [
  { key: "overview", Icon: LayoutDashboard, label: "현황" },
  { key: "users", Icon: UsersIcon, label: "사용자" },
  { key: "groups", Icon: Layers, label: "그룹" },
  { key: "reports", Icon: BarChart3, label: "보고서" },
  { key: "logs", Icon: History, label: "로그" },
  { key: "config", Icon: SettingsIcon, label: "설정" },
];

function JobStatus({ status }: { status: string }) {
  if (status === "completed") return <span className="pill ok">완료</span>;
  if (["publishing", "accepted", "pbi_succeeded"].includes(status))
    return <span className="pill pending">진행 중</span>;
  return <span className="pill fail">{status}</span>;
}

export default function AdminPage({ data }: { data: AdminData }) {
  const { user, stats, csrf_token } = data;
  const [section, setSection] = useState<SectionKey>("overview");
  const [users, setUsers] = useState<AdminUser[]>(data.users);
  const [reports, setReports] = useState<AdminReport[]>(data.reports);
  const [toast, setToast] = useState<Toast>(null);
  const [accessReport, setAccessReport] = useState<AdminReport | null>(null);
  const [showAddUser, setShowAddUser] = useState(false);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [syncDismissed, setSyncDismissed] = useState(false);
  const [importing, setImporting] = useState(false);

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
    <div className="app-shell">
      <header className="topbar">
        <a href="/" className="topbar-brand" title="홈으로">
          <span className="brand">
            <span className="b-quali">quali</span>
            <span className="b-soft">soft</span>
          </span>
        </a>
        <span className="topbar-section">관리자 포털</span>
        <div className="topbar-spacer" />
        <div className="topbar-right">
          <span className="topbar-user">{user.display_name}</span>
          <a href="/" className="topbar-btn">
            보고서 뷰어
          </a>
          <button
            className="topbar-btn primary"
            onClick={async () => {
              await logout(csrf_token);
              window.location.href = "/login";
            }}
          >
            로그아웃
          </button>
        </div>
      </header>

      <div className="app-body">
        <nav className="app-sidebar">
          <div className="app-sidebar-title">관리 메뉴</div>
          <div className="app-sidebar-scroll">
            {SECTIONS.map((s) => (
              <div
                key={s.key}
                className={`app-nav-item${section === s.key ? " active" : ""}`}
                onClick={() => setSection(s.key)}
              >
                <s.Icon size={17} className="icn" />
                {s.label}
              </div>
            ))}
          </div>
        </nav>

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
              <OverviewSection stats={stats} jobs={data.jobs} />
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
            {section === "config" && (
              <ConfigSection csrf={csrf_token} showToast={showToast} />
            )}
          </div>
        </main>
      </div>

      {showAddUser && (
        <AddUserModal
          csrf={csrf_token}
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

function OverviewSection({
  stats,
  jobs,
}: {
  stats: AdminData["stats"];
  jobs: AdminJob[];
}) {
  const tableRef = useRef<HTMLDivElement>(null);
  const fit = useFitRows(tableRef, 40, 38);
  // 자가진단 — 페이지 로드 후 비동기 (실패해도 기존 현황은 그대로)
  const [sys, setSys] = useState<SystemStatus | null>(null);
  useEffect(() => {
    adminGetSystemStatus().then(setSys).catch(() => {});
  }, []);
  return (
    <section>
      <h2>현황</h2>
      <div className="ad-stat-grid">
        <StatCard label="활성 사용자" value={stats.active_users} sub="계정 비활성 제외" />
        <StatCard label="활성 보고서" value={stats.active_reports} sub="삭제·아카이브 제외" />
        <StatCard
          label="오늘 업로드"
          value={stats.today_uploads}
          sub={`성공 ${stats.today_success}건`}
        />
        {sys && (
          <>
            <StatCard
              label="DB 응답"
              value={sys.db_latency_ms}
              sub={`ms · 동기화 ${
                sys.loop_seconds_ago.pbi_sync != null
                  ? Math.round(sys.loop_seconds_ago.pbi_sync / 60) + "분 전"
                  : "대기 중"
              }`}
            />
            <StatCard
              label="실패 업로드 (7일)"
              value={sys.failed_jobs_7d}
              sub="failed·unknown·db_failed"
            />
          </>
        )}
      </div>

      <h2>최근 업로드</h2>
      <div className="card-table" ref={tableRef}>
        <table>
          <colgroup>
            <col style={{ width: "12%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "32%" }} />
            <col style={{ width: "16%" }} />
            <col style={{ width: "20%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>사용자</th>
              <th>보고서명</th>
              <th>상태</th>
              <th>일시</th>
            </tr>
          </thead>
          <tbody>
            {jobs.slice(0, fit).map((j) => (
              <tr key={j.id}>
                <td>{j.id}</td>
                <td>{j.username}</td>
                <td title={j.report_name}>{j.category ? `/${j.category}/${j.report_name}` : j.report_name}</td>
                <td>
                  <JobStatus status={j.status} />
                </td>
                <td>{j.created_at || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: number;
  sub: string;
}) {
  return (
    <div className="ad-stat-card">
      <div className="ad-stat-label">{label}</div>
      <div className="ad-stat-value">{value}</div>
      <div className="ad-stat-sub">{sub}</div>
    </div>
  );
}

function UsersSection({
  users,
  csrf,
  showToast,
  onAdd,
  onToggle,
  onToggleUpload,
}: {
  users: AdminUser[];
  csrf: string;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
  onAdd: () => void;
  onToggle: (id: number) => void;
  onToggleUpload: (id: number) => void;
}) {
  const tableRef = useRef<HTMLDivElement>(null);
  const pageSize = useFitRows(tableRef, 40, 38);
  const { pageItems, page, totalPages, total, setPage } = usePaged(users, pageSize);
  const [reportsUser, setReportsUser] = useState<AdminUser | null>(null);
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
            <col style={{ width: "5%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "10%" }} />
            <col style={{ width: "15%" }} />
            <col style={{ width: "7%" }} />
            <col style={{ width: "6%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "13%" }} />
            <col style={{ width: "9%" }} />
            <col style={{ width: "15%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>아이디</th>
              <th>표시 이름</th>
              <th>PBI 사용자명</th>
              <th>역할</th>
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
                </td>
                <td title={u.display_name}>{u.display_name}</td>
                <td title={u.pbi_username}>{u.pbi_username}</td>
                <td>{u.roles.join(", ")}</td>
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
                    <button
                      className="btn btn-warn btn-sm"
                      onClick={() => onToggle(u.id)}
                    >
                      활성/비활성
                    </button>
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
    </section>
  );
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
  onClose,
  onAdded,
  onError,
}: {
  csrf: string;
  onClose: () => void;
  onAdded: () => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [groups, setGroups] = useState<AdminGroup[] | null>(null);
  const [selectedGroups, setSelectedGroups] = useState<number[]>([]);

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
              <Field label="PBI 사용자명 (RLS 식별자)">
                <input name="pbi_username" placeholder="아이디와 동일하면 공란" />
              </Field>
              <Field label="역할 (RLS)">
                <input name="roles" defaultValue="도메인" />
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
                  username,password,display_name,pbi_username,roles,groups,is_admin,can_upload
                </div>
                <table className="ad-bulk-cols">
                  <tbody>
                    <tr><th>username</th><td className="req">필수</td><td>로그인 아이디</td></tr>
                    <tr><th>password</th><td className="req">필수</td><td>초기 비밀번호 (8자 이상)</td></tr>
                    <tr><th>display_name</th><td className="req">필수</td><td>화면에 표시할 이름</td></tr>
                    <tr><th>pbi_username</th><td>선택</td><td>RLS 식별자 — 비우면 아이디를 사용</td></tr>
                    <tr><th>roles</th><td>선택</td><td>RLS 역할 — 비우면 <code>도메인</code>. 여러 개는 <code>;</code>로 구분</td></tr>
                    <tr><th>groups</th><td>선택</td><td>소속 그룹 — <b>미리 만들어져 있어야</b> 하며, 그 그룹의 보고서 열람 권한을 그대로 상속</td></tr>
                    <tr><th>is_admin</th><td>선택</td><td>관리자 여부 — 비우면 <code>false</code></td></tr>
                    <tr><th>can_upload</th><td>선택</td><td>업로드 허용 — 비우면 <code>true</code></td></tr>
                  </tbody>
                </table>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    const csv =
                      "username,password,display_name,pbi_username,roles,groups,is_admin,can_upload\n" +
                      "user01,TempPass123!,홍길동,,도메인,영업팀,false,true\n";
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
  const tableRef = useRef<HTMLDivElement>(null);
  const pageSize = useFitRows(tableRef, 40, 38);
  const { pageItems, page, totalPages, total, setPage } = usePaged(reports, pageSize);

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
      <div className="card-table" ref={tableRef}>
        <table>
          <colgroup>
            <col style={{ width: "6%" }} />
            <col style={{ width: "24%" }} />
            <col style={{ width: "13%" }} />
            <col style={{ width: "13%" }} />
            <col style={{ width: "7%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "29%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>보고서명</th>
              <th>구분</th>
              <th>카테고리</th>
              <th title="열람권한이 명시적으로 부여된 사용자 수 (관리자 우회 접근은 제외)">
                열람권한
              </th>
              <th>상태</th>
              <th>액션</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td title={r.description || r.name}>
                  {r.name}
                  {r.description && (
                    <span className="ad-access-id" style={{ display: "block" }}>
                      {r.description.length > 40
                        ? r.description.slice(0, 40) + "…"
                        : r.description}
                    </span>
                  )}
                </td>
                <td>
                  {r.report_type === "managed" ? (
                    <span className="pill active">공용</span>
                  ) : (
                    <span className="pill pending">개인 · {r.owner_username || "-"}</span>
                  )}
                </td>
                <td>{r.category || "-"}</td>
                <td>
                  {r.viewer_count}
                  {r.group_count > 0 && (
                    <span className="ad-access-id"> +{r.group_count}그룹</span>
                  )}
                </td>
                <td>
                  {r.status === "active" ? (
                    <span className="pill active">활성</span>
                  ) : r.status === "deleted" ? (
                    <span className="pill inactive">삭제됨</span>
                  ) : (
                    <span className="pill pending">{r.status}</span>
                  )}
                </td>
                <td className="ad-actions-cell">
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => onManageAccess(r)}
                  >
                    권한
                  </button>
                  {r.status !== "deleted" && (
                    <button className="btn btn-danger btn-sm" onClick={() => doDelete(r)}>
                      삭제
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} totalPages={totalPages} total={total} onPage={setPage} />
    </section>
  );
}

function ConfigSection({
  csrf,
  showToast,
}: {
  csrf: string;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
}) {
  const [rows, setRows] = useState<AppConfigRow[] | null>(null);
  const [edited, setEdited] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    adminGetConfig()
      .then(setRows)
      .catch(() => showToast("설정 조회 실패", "err"));
  }, [showToast]);

  const save = async (key: string) => {
    const value = (edited[key] ?? "").trim();
    if (!value) return;
    setSaving(key);
    try {
      await adminSetConfig(key, value, csrf);
      setRows((prev) =>
        prev ? prev.map((r) => (r.key === key ? { ...r, value } : r)) : prev,
      );
      setEdited((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      showToast(`'${key}' 저장됨 — 재시작 없이 즉시 적용됩니다.`, "ok");
    } catch (e) {
      showToast("오류: " + (e as Error).message, "err");
    } finally {
      setSaving(null);
    }
  };

  const [showAdvanced, setShowAdvanced] = useState(false);

  // 실제로 관리자가 정책 판단으로 바꿀 만한 것들만 기본 노출 — 나머지(구현 세부값)는 "고급 설정"으로 뺐다
  const categories: { title: string; hint?: string; keys: string[] }[] = [
    {
      title: "업로드",
      keys: ["max_pbix_size_mb", "max_uploads_per_day", "max_personal_reports"],
    },
    {
      title: "로그인 · 보안",
      keys: ["password_min_len", "login_block_max_fail", "login_block_minutes"],
    },
    {
      title: "동기화 · 임베드",
      hint: "동기화 주기는 진행 중인 회차가 끝난 뒤부터 적용됩니다.",
      keys: ["pbi_sync_interval", "pbi_token_cache_margin_sec", "max_embed_rls_roles"],
    },
    {
      title: "로그 · 신선도",
      keys: ["activity_log_retention_days", "error_log_retention_days", "refresh_auto_retry_max"],
    },
  ];
  const advancedKeys = [
    "report_name_max_len", "import_poll_interval_sec", "import_poll_max",
    "embed_token_lifetime_min",
  ];
  const byKey = new Map((rows ?? []).map((r) => [r.key, r]));

  return (
    <section>
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>런타임 설정</h2>
        <span className="cfg-live-badge" title="저장 시 서버가 config를 다시 읽어 메모리 값을 즉시 갱신합니다">
          ● 저장 즉시 적용 — 재시작 불필요
        </span>
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
                        onKeyDown={(e) => e.key === "Enter" && dirty && save(key)}
                      />
                      <button
                        className="btn btn-sm btn-primary"
                        disabled={saving === key || !dirty}
                        onClick={() => save(key)}
                      >
                        {saving === key ? "저장 중..." : "저장"}
                      </button>
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
            {showAdvanced ? "▾" : "▸"} 고급 설정 ({advancedKeys.length}) — 구현 세부값, 평소엔 안 건드려도 됩니다
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
                        onKeyDown={(e) => e.key === "Enter" && dirty && save(key)}
                      />
                      <button
                        className="btn btn-sm btn-primary"
                        disabled={saving === key || !dirty}
                        onClick={() => save(key)}
                      >
                        {saving === key ? "저장 중..." : "저장"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
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
    } catch {
      showToast("오류가 발생했습니다.", "err");
    }
  };

  const setGroupAccess = async (groupId: number, canView: boolean) => {
    try {
      await adminSetGroupAccess(report.id, groupId, canView, csrf);
      showToast(canView ? "그룹에 열람 권한이 부여됐습니다." : "그룹 열람 권한이 해제됐습니다.", "ok");
      await load();
    } catch {
      showToast("오류가 발생했습니다.", "err");
    }
  };

  return (
    <Modal title={<>열람 권한 — {report.name}</>} onClose={onClose}>
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
                </div>
                {u.is_admin ? (
                  <span className="ad-access-always" title="관리자는 권한과 무관하게 모든 보고서를 봅니다">
                    전체 열람 (권한 불필요)
                  </span>
                ) : (
                  <button
                    className={`btn btn-sm ${u.can_view ? "btn-danger" : "btn-primary"}`}
                    onClick={() => setAccess(u.id, !u.can_view)}
                  >
                    {u.can_view ? "해제" : "부여"}
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

function LogsSection() {
  const [tab, setTab] = useState<"activity" | "audit">("activity");
  const [username, setUsername] = useState("");
  const [event, setEvent] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [rows, setRows] = useState<LogRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const tableRef = useRef<HTMLDivElement>(null);
  const pageSize = useFitRows(tableRef, 40, 38);
  const { pageItems, page, totalPages, total, setPage } = usePaged(rows || [], pageSize);

  const filters = { username, event, date_from: dateFrom, date_to: dateTo };

  const load = useCallback(
    async (t: "activity" | "audit", f: typeof filters) => {
      setRows(null);
      setError(null);
      try {
        setRows(await adminGetLogs(t, f));
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
      {rows && rows.length >= 1000 && (
        <div className="mx-hint" style={{ marginBottom: 8 }}>
          최근 1,000건까지만 표시합니다 — 전체가 필요하면 기간을 좁히거나 CSV로 받으세요.
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
                      <td className="ad-err-cell">
                        <span className="ad-err-text" style={{ color: "inherit" }}>
                          {JSON.stringify(r.details)}
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
