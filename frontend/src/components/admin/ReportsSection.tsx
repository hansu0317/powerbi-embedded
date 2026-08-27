// 관리자 포털 "보고서" 탭 — 목록·PBI 가져오기·삭제·열람권한(개인/부서) 관리.
// pages/AdminPage.tsx에서 2026-08-27에 분리했다(분리 배경은 components/admin/shared.tsx
// 상단 주석 참고).
import { useCallback, useEffect, useState } from "react";
import { BarChart3, Download, Users as UsersIcon, X } from "lucide-react";

import type { AdminReport } from "../../lib/bootstrap";
import {
  AccessUser,
  DepartmentAccess,
  adminDeleteReport,
  adminGetAccess,
  adminGetDepartmentAccess,
  adminImportPbi,
  adminSetAccess,
  adminSetDepartmentAccess,
  adminSetReportVisibility,
} from "../../lib/api";
import { Pager, useFitRows, usePaged } from "../Pager";
import { categoryColor, withAlpha } from "../../utils/categoryColor";
import { Modal } from "./shared";

export function ReportsSection({
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
                    ) : r.dept_count > 0 ? (
                      <span className="pill pending">부서 공유</span>
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
                    {r.viewer_count === 0 && r.dept_count === 0 ? (
                      <span className="pill inactive">비공개</span>
                    ) : (
                      <span className="pill active">
                        공개
                        {r.viewer_count > 0 && ` · ${r.viewer_count}명`}
                        {r.dept_count > 0 && ` · ${r.dept_count}부서`}
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
              권한 부서<b>{detail.dept_count}개</b>
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

export function AccessModal({
  report,
  csrf,
  departments,
  onClose,
  showToast,
}: {
  report: AdminReport;
  csrf: string;
  departments: string[];
  onClose: () => void;
  showToast: (msg: string, tone?: "ok" | "err" | "") => void;
}) {
  const [tab, setTab] = useState<"users" | "departments">("users");
  const [users, setUsers] = useState<AccessUser[] | null>(null);
  const [deptAccess, setDeptAccess] = useState<DepartmentAccess[] | null>(null);
  const [newDept, setNewDept] = useState("");
  const [visibility, setVisibility] = useState<"personal" | "shared">(
    report.visibility === "shared" ? "shared" : "personal",
  );
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, d] = await Promise.all([
        adminGetAccess(report.id),
        adminGetDepartmentAccess(report.id),
      ]);
      setUsers(u);
      setDeptAccess(d);
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

  const setDeptAccessFor = async (department: string, canView: boolean) => {
    try {
      await adminSetDepartmentAccess(report.id, department, canView, csrf);
      showToast(canView ? "부서에 열람 권한이 부여됐습니다." : "부서 열람 권한이 해제됐습니다.", "ok");
      setNewDept("");
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
            className={`btn btn-sm ${tab === "departments" ? "btn-primary" : ""}`}
            onClick={() => setTab("departments")}
            style={{ marginLeft: 6 }}
          >
            부서 {deptAccess ? `(${deptAccess.length})` : ""}
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
                  {!u.is_admin && u.via_department && (
                    <span
                      className={`pill ${u.direct === false ? "inactive" : "active"}`}
                      title="소속 부서로도 이 보고서 열람 권한이 있습니다"
                    >
                      부서경유{u.direct === false ? " · 차단됨" : ""}
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
                      u.can_view && u.via_department
                        ? "부서로 부여된 권한이 있어도 이 사람만 예외로 차단합니다"
                        : undefined
                    }
                    onClick={() => setAccess(u.id, !u.can_view)}
                  >
                    {u.can_view ? "차단" : "허용"}
                  </button>
                )}
              </div>
            ))}
          {tab === "departments" && !error && !deptAccess && (
            <div className="ad-modal-loading">불러오는 중...</div>
          )}
          {tab === "departments" && (
            <div className="ad-access-row">
              <input
                value={newDept}
                onChange={(e) => setNewDept(e.target.value)}
                list="department-options"
                placeholder="부서 선택 또는 입력 (예: AMT)"
                style={{ flex: 1 }}
              />
              <datalist id="department-options">
                {departments.map((d) => <option key={d} value={d} />)}
              </datalist>
              <button
                className="btn btn-sm btn-primary"
                disabled={!newDept.trim()}
                onClick={() => setDeptAccessFor(newDept.trim(), true)}
              >
                부여
              </button>
            </div>
          )}
          {tab === "departments" && deptAccess && deptAccess.length === 0 && (
            <div className="ad-modal-loading">부여된 부서가 없습니다.</div>
          )}
          {tab === "departments" &&
            deptAccess?.map((d) => (
              <div key={d.department} className="ad-access-row">
                <div className="ad-access-info">
                  <span className="ad-access-name">{d.department}</span>
                  <span className="ad-access-id">인원 {d.member_count}명</span>
                </div>
                <button
                  className="btn btn-sm btn-danger"
                  onClick={() => setDeptAccessFor(d.department, false)}
                >
                  해제
                </button>
              </div>
            ))}
        </div>
    </Modal>
  );
}
