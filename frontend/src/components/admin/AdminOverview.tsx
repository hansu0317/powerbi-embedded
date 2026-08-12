import { useEffect, useState } from "react";
import type { AdminData, AdminJob } from "../../lib/bootstrap";
import { adminGetSystemStatus, type SystemStatus } from "../../lib/api";
import { useFitRows } from "../Pager";

type Props = {
  stats: AdminData["stats"];
  jobs: AdminJob[];
  onGoSection: (section: "users" | "reports") => void;
};

export function AdminOverview({ stats, jobs, onGoSection }: Props) {
  const [fit, tableRef] = useFitRows(40, 38);
  const [system, setSystem] = useState<SystemStatus | null>(null);

  useEffect(() => {
    adminGetSystemStatus().then(setSystem).catch(() => undefined);
  }, []);

  return <section>
    <h2>현황</h2>
    <div className="ad-stat-grid">
      <StatCard label="활성 사용자" value={stats.active_users} sub="계정 비활성 제외 · 클릭하면 사용자 탭으로" onClick={() => onGoSection("users")}/>
      <StatCard label="활성 보고서" value={stats.active_reports} sub="삭제·아카이브 제외 · 클릭하면 전체 보고서로" onClick={() => onGoSection("reports")}/>
      <StatCard label="오늘 업로드" value={stats.today_uploads} sub={`성공 ${stats.today_success}건`}/>
      {system && <>
        <StatCard label="DB 응답" value={system.db_latency_ms} sub={`ms · 동기화 ${syncAge(system)}`}/>
        <StatCard label="실패 업로드 (7일)" value={system.failed_jobs_7d} sub="failed·unknown·db_failed"/>
      </>}
    </div>
    <h2>최근 업로드</h2>
    <div className="card-table" ref={tableRef}>
      <table>
        <colgroup><col style={{width:"12%"}}/><col style={{width:"20%"}}/><col style={{width:"32%"}}/><col style={{width:"16%"}}/><col style={{width:"20%"}}/></colgroup>
        <thead><tr><th>ID</th><th>사용자</th><th>보고서명</th><th>상태</th><th>일시</th></tr></thead>
        <tbody>{jobs.slice(0, fit).map((job) => <tr key={job.id}>
          <td>{job.id}</td><td>{job.username}</td>
          <td title={job.report_name}>{job.category ? `/${job.category}/${job.report_name}` : job.report_name}</td>
          <td><JobStatus status={job.status}/></td><td>{job.created_at || "-"}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}

function syncAge(system: SystemStatus) {
  const seconds = system.loop_seconds_ago.pbi_sync;
  return seconds == null ? "대기 중" : `${Math.round(seconds / 60)}분 전`;
}

function JobStatus({ status }: { status: string }) {
  if (status === "completed") return <span className="pill ok">완료</span>;
  if (["publishing", "accepted", "pbi_succeeded"].includes(status)) return <span className="pill pending">진행 중</span>;
  return <span className="pill fail">{status}</span>;
}

function StatCard({ label, value, sub, onClick }: { label:string; value:number; sub:string; onClick?:()=>void }) {
  return <div className={`ad-stat-card${onClick ? " clickable" : ""}`} onClick={onClick} role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined}>
    <div className="ad-stat-label">{label}</div><div className="ad-stat-value">{value}</div><div className="ad-stat-sub">{sub}</div>
  </div>;
}
