// 관리자 포털 "사용자" 탭 — 목록·추가·수정·CSV 일괄등록·사용자별 열람 보고서 조회.
// pages/AdminPage.tsx에서 2026-08-27에 분리했다(분리 배경은 components/admin/shared.tsx
// 상단 주석 참고).
import { useEffect, useState } from "react";
import { Download, Plus, Upload } from "lucide-react";

import type { AdminUser } from "../../lib/bootstrap";
import {
  BulkAddResult,
  UserReportRow,
  adminAddUser,
  adminBulkAddUsers,
  adminEditUser,
  adminGetUserReports,
} from "../../lib/api";
import { Pager, useFitRows, usePaged } from "../Pager";
import { Field, Modal, departmentOptions } from "./shared";

export function UsersSection({
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

export function AddUserModal({
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
  const [department, setDepartment] = useState("");

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    try {
      const fd = new FormData(e.currentTarget);
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
            <Field label="부서 (GET 필터 + 보고서 열람권한)">
              <input value={department} onChange={e=>setDepartment(e.target.value)} list="department-options" placeholder="예: AMT" />
              <datalist id="department-options">{departments.map((d) => <option key={d} value={d} />)}</datalist>
              <span className="muted" style={{ display: "block", marginTop: 4 }}>
                이 부서에 이미 부여된 보고서 열람권한을 그대로 받습니다(보고서 탭 → 권한 → 부서에서 부여).
              </span>
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
                  username,password,display_name,pbi_username,is_admin,can_upload,department
                </div>
                <table className="ad-bulk-cols">
                  <tbody>
                    <tr><th>username</th><td className="req">필수</td><td>로그인 아이디</td></tr>
                    <tr><th>password</th><td className="req">필수</td><td>초기 비밀번호 (8자 이상)</td></tr>
                    <tr><th>display_name</th><td className="req">필수</td><td>화면에 표시할 이름</td></tr>
                    <tr><th>pbi_username</th><td>선택</td><td>RLS Effective Identity에 쓰이는 내부 키 — 비우면 username을 그대로 씀(대부분 이대로 두면 됨)</td></tr>
                    <tr><th>is_admin</th><td>선택</td><td>관리자 여부 — 비우면 <code>false</code></td></tr>
                    <tr><th>can_upload</th><td>선택</td><td>업로드 허용 — 비우면 <code>true</code></td></tr>
                    <tr><th>department</th><td>선택</td><td>GET 필터 값이자 보고서 열람권한 축 — 그 부서에 이미 부여된 보고서 열람권한을 그대로 받음(예: <code>AMT</code>)</td></tr>
                  </tbody>
                </table>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    const csv =
                      "username,password,display_name,pbi_username,is_admin,can_upload,department\n" +
                      "user01,TempPass123!,홍길동,user01@customer.com,false,true,AMT\n";
                    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
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
                {r.via_department && (
                  <span className="pill admin" style={{ marginLeft: 4 }}>
                    부서
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
    </Modal>
  );
}
