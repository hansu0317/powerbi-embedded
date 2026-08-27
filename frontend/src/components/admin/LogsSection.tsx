// 관리자 포털 "로그" 탭 — 사용자 활동 로그 / 관리 감사 로그 조회 + CSV 내보내기.
// pages/AdminPage.tsx에서 2026-08-27에 분리했다(분리 배경은 components/admin/shared.tsx
// 상단 주석 참고).
import { useCallback, useEffect, useState } from "react";
import { Download } from "lucide-react";

import { LogRow, adminGetLogs, logQueryString } from "../../lib/api";
import { Pager, useFitRows, usePaged } from "../Pager";

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

export function LogsSection() {
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
