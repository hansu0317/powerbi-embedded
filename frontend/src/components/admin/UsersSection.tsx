// 관리자 포털 "사용자" 탭 — 목록·추가·수정·사용자별 열람 보고서 조회.
// pages/AdminPage.tsx에서 2026-08-27에 분리했다(분리 배경은 components/admin/shared.tsx
// 상단 주석 참고). CSV 일괄등록은 학습용 코드 축소 과정에서 제거했다(2026-08-27,
// git 이력의 v2 태그에 남아있음).
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";

import type { AdminUser } from "../../lib/bootstrap";
import {
  UserReportRow,
  adminAddUser,
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
  return (
    <section>
      <div className="ad-section-head">
        <h2 style={{ marginBottom: 0 }}>사용자 관리</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary" onClick={onAdd}>
            <Plus size={15} className="icn" /> 새 사용자 추가
          </button>
        </div>
      </div>
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
