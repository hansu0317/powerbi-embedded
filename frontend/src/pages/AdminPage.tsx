import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  ClipboardList,
  Download,
  LayoutDashboard,
  Plus,
  Users as UsersIcon,
} from "lucide-react";
import type {
  AdminData,
  AdminReport,
  AdminUser,
  AdminJob,
} from "../bootstrap";
import {
  AccessUser,
  adminAddUser,
  adminDeleteReport,
  adminGetAccess,
  adminImportPbi,
  adminRefreshDataset,
  adminSetAccess,
  adminToggleUser,
} from "../api";

type SectionKey = "overview" | "users" | "reports" | "jobs";
type Toast = { msg: string; tone: "ok" | "err" | "" } | null;

const SECTIONS: {
  key: SectionKey;
  Icon: typeof LayoutDashboard;
  label: string;
}[] = [
  { key: "overview", Icon: LayoutDashboard, label: "현황" },
  { key: "users", Icon: UsersIcon, label: "사용자" },
  { key: "reports", Icon: BarChart3, label: "보고서" },
  { key: "jobs", Icon: ClipboardList, label: "업로드 이력" },
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

  const showToast = useCallback((msg: string, tone: "ok" | "err" | "" = "") => {
    setToast({ msg, tone });
    setTimeout(() => setToast(null), 3000);
  }, []);

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
          <a href="/logout" className="topbar-btn primary">
            로그아웃
          </a>
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
          <div className="ad-content">
            {section === "overview" && (
              <OverviewSection stats={stats} jobs={data.jobs} />
            )}
            {section === "users" && (
              <UsersSection
                users={users}
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
            {section === "jobs" && <JobsSection jobs={data.jobs} />}
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
          onClose={() => setAccessReport(null)}
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
      </div>

      <h2>최근 업로드</h2>
      <div className="card-table">
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
            {jobs.slice(0, 10).map((j) => (
              <tr key={j.id}>
                <td>#{j.id}</td>
                <td>{j.username}</td>
                <td title={j.report_name}>{j.report_name}</td>
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
  onAdd,
  onToggle,
}: {
  users: AdminUser[];
  onAdd: () => void;
  onToggle: (id: number) => void;
}) {
  return (
    <section>
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>사용자 관리</h2>
        <button className="btn btn-primary" onClick={onAdd}>
          <Plus size={15} className="icn" /> 새 사용자 추가
        </button>
      </div>
      <div className="card-table">
        <table>
          <colgroup>
            <col style={{ width: "6%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: "16%" }} />
            <col style={{ width: "7%" }} />
            <col style={{ width: "6%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: "17%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>아이디</th>
              <th>표시 이름</th>
              <th>PBI 사용자명</th>
              <th>역할</th>
              <th>보고서</th>
              <th>마지막 로그인</th>
              <th>상태</th>
              <th>액션</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
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
                <td>{u.roles}</td>
                <td>{u.report_count}</td>
                <td title={u.last_login_at || ""}>{u.last_login_at || "-"}</td>
                <td>
                  <span className={`pill ${u.is_active ? "active" : "inactive"}`}>
                    {u.is_active ? "활성" : "비활성"}
                  </span>
                </td>
                <td>
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
  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    try {
      await adminAddUser(new FormData(e.currentTarget));
      onAdded();
    } catch (err) {
      onError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="ad-modal-overlay" onClick={onClose}>
      <div
        className="ad-modal ad-modal-wide"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ad-modal-header">
          <h3>새 사용자 추가</h3>
          <button className="ad-modal-close" onClick={onClose}>
            ×
          </button>
        </div>
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
            </div>
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
      </div>
    </div>
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

  const doRefresh = async (r: AdminReport) => {
    if (!confirm(`'${r.name}' 데이터셋을 새로고침 하시겠습니까?`)) return;
    try {
      await adminRefreshDataset(r.id, csrf);
      showToast(`'${r.name}' 새로고침 요청 완료 (PBI가 백그라운드 처리)`, "ok");
    } catch (e) {
      showToast("새로고침 실패: " + (e as Error).message, "err");
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
      <div className="card-table">
        <table>
          <colgroup>
            <col style={{ width: "7%" }} />
            <col style={{ width: "18%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "13%" }} />
            <col style={{ width: "7%" }} />
            <col style={{ width: "9%" }} />
            <col style={{ width: "26%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>보고서명</th>
              <th>유형</th>
              <th>소유자</th>
              <th>카테고리</th>
              <th>열람</th>
              <th>상태</th>
              <th>액션</th>
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td title={r.name}>{r.name}</td>
                <td>{r.report_type === "managed" ? "공용" : "개인"}</td>
                <td title={r.owner_username || "-"}>{r.owner_username || "-"}</td>
                <td>{r.category || "-"}</td>
                <td>{r.viewer_count}</td>
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
                  {r.pbi_dataset_id && (
                    <button className="btn btn-primary btn-sm" onClick={() => doRefresh(r)}>
                      새로고침
                    </button>
                  )}
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
    </section>
  );
}

function JobsSection({ jobs }: { jobs: AdminJob[] }) {
  return (
    <section>
      <h2>업로드 이력 (최근 30건)</h2>
      <div className="card-table">
        <table>
          <colgroup>
            <col style={{ width: "6%" }} />
            <col style={{ width: "10%" }} />
            <col style={{ width: "16%" }} />
            <col style={{ width: "9%" }} />
            <col style={{ width: "21%" }} />
            <col style={{ width: "19%" }} />
            <col style={{ width: "19%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>사용자</th>
              <th>보고서명</th>
              <th>상태</th>
              <th>오류</th>
              <th>시작</th>
              <th>갱신</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>#{j.id}</td>
                <td>{j.username}</td>
                <td title={j.report_name}>{j.report_name}</td>
                <td>
                  <JobStatus status={j.status} />
                </td>
                <td className="ad-err-cell">
                  {j.status !== "completed" && j.error_message ? (
                    <span className="ad-err-text" title={j.error_message}>
                      {j.error_message}
                    </span>
                  ) : (
                    "-"
                  )}
                </td>
                <td className="ad-nowrap">{j.created_at || "-"}</td>
                <td className="ad-nowrap">{j.updated_at || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
  const [users, setUsers] = useState<AccessUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await adminGetAccess(report.id));
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

  return (
    <div className="ad-modal-overlay" onClick={onClose}>
      <div className="ad-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ad-modal-header">
          <h3>열람 권한 — {report.name}</h3>
          <button className="ad-modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="ad-modal-body">
          {error && <div className="ad-modal-err">{error}</div>}
          {!error && !users && <div className="ad-modal-loading">불러오는 중...</div>}
          {users && users.length === 0 && (
            <div className="ad-modal-loading">사용자가 없습니다.</div>
          )}
          {users?.map((u) => (
            <div key={u.id} className="ad-access-row">
              <div className="ad-access-info">
                <span className="ad-access-name">{u.display_name}</span>
                <span className="ad-access-id">{u.username}</span>
                {u.is_admin && <span className="pill admin">관리자</span>}
              </div>
              <button
                className={`btn btn-sm ${u.can_view ? "btn-danger" : "btn-primary"}`}
                onClick={() => setAccess(u.id, !u.can_view)}
              >
                {u.can_view ? "해제" : "부여"}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
