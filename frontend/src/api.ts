// 백엔드 API 호출 헬퍼.
// CSRF 토큰은 서버가 부트스트랩으로 내려준 값을 X-CSRF-Token 헤더로 전달한다.
import type { AdminReport } from "./bootstrap";

function extractDetail(j: any, fallback: string): string {
  const detail = j?.detail;
  if (typeof detail === "object" && detail) return detail.message || fallback;
  return detail || fallback;
}

export interface EmbedResponse {
  report_id: string;
  embed_url: string;
  embed_token: string;
  expires_at: number; // 토큰 만료 (Unix 초) — 프론트가 만료 전 재발급에 사용
  data_as_of?: string | null; // 데이터 기준 시각 (마지막 refresh 성공, ISO)
  refresh_status?: string | null; // Completed | Failed | NotRefreshable | ...

  settings?: {
    enable_page_nav?: boolean;
    enable_filter?: boolean;
    default_page?: string;
    tab_type?: string; // "dashboard" | "report" — v6 대시보드 임베드 분기용
  };
}

export async function fetchEmbed(reportId: number): Promise<EmbedResponse> {
  const res = await fetch(`/api/embed/${reportId}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(extractDetail(err, "알 수 없는 오류"));
  }
  return res.json();
}

export interface UploadAccepted {
  job_id: number;
  report_name: string;
  status: string;
}

export async function uploadPbix(
  file: File, csrf: string, reportName?: string, description?: string,
): Promise<UploadAccepted> {
  const fd = new FormData();
  fd.append("file", file);
  if (reportName) fd.append("report_name", reportName);
  if (description) fd.append("report_description", description);
  const res = await fetch("/api/upload", {
    method: "POST",
    body: fd,
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, res.statusText));
  return data;
}

export interface UploadStatus {
  job_id: number;
  status: string;
  report_name: string;
  report_id: number | null;
  error: string | null;
}

export async function fetchUploadStatus(jobId: number, csrf: string): Promise<UploadStatus> {
  const res = await fetch(`/api/upload/status/${jobId}`, {
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, "상태 조회 실패"));
  return data;
}

// ── 즐겨찾기 / 최근 본 보고서 (DB 영속) ──────────────────────────────────────

export async function setFavorite(reportId: number, favorite: boolean, csrf: string) {
  await fetch(`/api/favorites/${reportId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ favorite }),
  });
}

export async function recordRecent(reportId: number, csrf: string) {
  await fetch(`/api/recents/${reportId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
}

// ── 인증 ──────────────────────────────────────────────────────────────────────

export async function logout(csrf: string) {
  await fetch("/logout", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
}

// ── 사용자 편의 (v3) ─────────────────────────────────────────────────────────

export async function setDefaultReport(reportId: number | null, csrf: string) {
  const res = await fetch("/api/user/default-report", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ report_id: reportId }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "기본 보고서 설정 실패"));
  return j as { default_report_id: number | null };
}

// ── 관리자 API ────────────────────────────────────────────────────────────────

export interface SyncStatus {
  available: boolean;
  drift: boolean;
  new?: string[];
  moved?: { name: string; from: string | null; to: string | null }[];
  removed?: string[];
}

export async function adminSyncStatus(): Promise<SyncStatus> {
  const res = await fetch("/api/admin/sync-status");
  if (!res.ok) return { available: false, drift: false };
  return res.json();
}

export async function adminImportPbi(csrf: string) {
  const res = await fetch("/api/admin/import-pbi", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, res.statusText));
  return j as { registered: number; skipped: number; deleted: number; total: number };
}

export interface AppConfigRow {
  key: string;
  value: string;
  description: string | null;
  updated_at: string | null;
}

export async function adminGetConfig(): Promise<AppConfigRow[]> {
  const res = await fetch("/api/admin/config");
  if (!res.ok) throw new Error("설정 조회 실패");
  const j = await res.json();
  return j.config as AppConfigRow[];
}

export async function adminSetConfig(key: string, value: string, csrf: string) {
  const res = await fetch("/api/admin/config", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "설정 저장 실패"));
  return j as { key: string; value: string };
}

export async function adminFetchReports(): Promise<AdminReport[]> {
  const res = await fetch("/api/admin/reports");
  if (!res.ok) throw new Error("보고서 목록 조회 실패");
  const j = await res.json();
  return j.reports as AdminReport[];
}

export async function adminAddUser(form: FormData) {
  const res = await fetch("/api/admin/users/add", { method: "POST", body: form });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(extractDetail(j, res.statusText));
  }
  return res.json();
}

export async function adminToggleUser(userId: number, csrf: string) {
  const res = await fetch(`/api/admin/users/${userId}/toggle-active`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, res.statusText));
  return j as { is_active: boolean };
}

export async function adminDeleteReport(reportId: number, csrf: string) {
  const res = await fetch(`/api/admin/reports/${reportId}/delete`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, res.statusText));
  return j as { deleted: boolean; pbi_warning?: string };
}

export async function adminRefreshDataset(reportId: number, csrf: string) {
  const res = await fetch(`/api/admin/reports/${reportId}/refresh`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(extractDetail(j, "알 수 없는 오류"));
  }
  return res.json();
}

export interface AccessUser {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  can_view: boolean;
}

export interface UserReportRow {
  id: number;
  name: string;
  category: string | null;
  direct: boolean;        // 직접 부여 여부
  via_groups: string[];   // 경유 그룹명들
}

export async function adminGetUserReports(userId: number): Promise<UserReportRow[]> {
  const res = await fetch(`/api/admin/users/${userId}/reports`);
  if (!res.ok) throw new Error("열람 보고서 조회 실패");
  return (await res.json()).reports as UserReportRow[];
}

// ── 그룹 (팀/부서 단위 권한) ─────────────────────────────────────────────────

export interface AdminGroup {
  id: number;
  name: string;
  description: string | null;
  member_count: number;
  report_count: number;
  created_at: string | null;
}

export interface GroupMember {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  is_member: boolean;
}

export interface GroupAccess {
  id: number;
  name: string;
  member_count: number;
  can_view: boolean;
}

export async function adminGetGroups(): Promise<AdminGroup[]> {
  const res = await fetch("/api/admin/groups");
  if (!res.ok) throw new Error("그룹 목록 조회 실패");
  return (await res.json()).groups as AdminGroup[];
}

export async function adminCreateGroup(name: string, description: string, csrf: string) {
  const res = await fetch("/api/admin/groups", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ name, description }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "그룹 생성 실패"));
  return j as { id: number; name: string };
}

export async function adminDeleteGroup(groupId: number, csrf: string) {
  const res = await fetch(`/api/admin/groups/${groupId}/delete`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  if (!res.ok) throw new Error("그룹 삭제 실패");
  return res.json();
}

export async function adminGetGroupMembers(groupId: number): Promise<GroupMember[]> {
  const res = await fetch(`/api/admin/groups/${groupId}/members`);
  if (!res.ok) throw new Error("멤버 목록 조회 실패");
  return (await res.json()).members as GroupMember[];
}

export async function adminSetGroupMember(
  groupId: number, userId: number, member: boolean, csrf: string,
) {
  const res = await fetch(`/api/admin/groups/${groupId}/members/${userId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ member }),
  });
  if (!res.ok) throw new Error("멤버 변경 실패");
  return res.json();
}

export async function adminGetGroupAccess(reportId: number): Promise<GroupAccess[]> {
  const res = await fetch(`/api/admin/reports/${reportId}/group-access`);
  if (!res.ok) throw new Error("그룹 권한 조회 실패");
  return (await res.json()).groups as GroupAccess[];
}

export async function adminSetGroupAccess(
  reportId: number, groupId: number, canView: boolean, csrf: string,
) {
  const res = await fetch(`/api/admin/reports/${reportId}/group-access/${groupId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ can_view: canView }),
  });
  if (!res.ok) throw new Error("그룹 권한 변경 실패");
  return res.json();
}

export async function adminGetAccess(reportId: number): Promise<AccessUser[]> {
  const res = await fetch(`/api/admin/reports/${reportId}/access`);
  if (!res.ok) throw new Error("권한 목록 조회 실패");
  const j = await res.json();
  return j.users as AccessUser[];
}

export async function adminSetAccess(
  reportId: number,
  userId: number,
  canView: boolean,
  csrf: string,
) {
  const res = await fetch(`/api/admin/reports/${reportId}/access/${userId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ can_view: canView }),
  });
  if (!res.ok) throw new Error("권한 변경 실패");
  return res.json();
}

// ── 권한 매트릭스 / 로그 / 편의 (v3) ─────────────────────────────────────────

export interface MatrixGroup {
  id: number;
  name: string;
  member_count: number;
}

export interface MatrixReport {
  id: number;
  name: string;
  category: string | null;
  group_ids: number[];
}

export async function adminGetAccessMatrix(): Promise<{ groups: MatrixGroup[]; reports: MatrixReport[] }> {
  const res = await fetch("/api/admin/access-matrix");
  if (!res.ok) throw new Error("권한 매트릭스 조회 실패");
  return res.json();
}

export async function adminToggleUpload(userId: number, csrf: string) {
  const res = await fetch(`/api/admin/users/${userId}/toggle-upload`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "업로드 권한 변경 실패"));
  return j as { can_upload: boolean };
}

export async function adminSetDescription(reportId: number, description: string, csrf: string) {
  const res = await fetch(`/api/admin/reports/${reportId}/description`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ description }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "설명 저장 실패"));
  return j as { report_id: number; description: string | null };
}

export interface LogRow {
  id: number;
  username?: string;
  event?: string;
  actor?: string | null;
  action?: string;
  details?: unknown;
  report_name: string | null;
  ip?: string | null;
  created_at: string;
}

export function logQueryString(
  type: "activity" | "audit",
  filters: { username?: string; event?: string; date_from?: string; date_to?: string },
): string {
  const params = new URLSearchParams({ type });
  if (filters.username) params.set("username", filters.username);
  if (filters.event) params.set("event", filters.event);
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  return params.toString();
}

export async function adminGetLogs(
  type: "activity" | "audit",
  filters: { username?: string; event?: string; date_from?: string; date_to?: string },
): Promise<LogRow[]> {
  const res = await fetch(`/api/admin/logs?${logQueryString(type, filters)}`);
  if (!res.ok) throw new Error("로그 조회 실패");
  return (await res.json()).rows as LogRow[];
}

/* ── v4: RLS 설정 ─────────────────────────────────────── */

export interface RlsConfig {
  enabled: boolean;
  role_names: string[];
}

export async function adminGetRls(reportId: number): Promise<RlsConfig> {
  const res = await fetch(`/api/admin/reports/${reportId}/rls`);
  if (!res.ok) throw new Error("RLS 조회 실패");
  return res.json();
}

export async function adminSetRls(
  reportId: number, enabled: boolean, roleNames: string[], csrf: string,
): Promise<RlsConfig> {
  const res = await fetch(`/api/admin/reports/${reportId}/rls`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ enabled, role_names: roleNames }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, "RLS 저장 실패"));
  return data;
}

export async function adminSetUserRls(
  userId: number, pbiUsername: string, roles: string[], csrf: string,
): Promise<{ pbi_username: string; roles: string[] }> {
  const res = await fetch(`/api/admin/users/${userId}/rls`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ pbi_username: pbiUsername, roles }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, "저장 실패"));
  return data;
}

/* ── v4: 신선도·자가진단 ──────────────────────────────── */

export interface FreshnessRow {
  pbi_dataset_id: string;
  last_status: string | null;
  last_success_at: string | null;
  last_attempt_at: string | null;
  failure_reason: string | null;
  consecutive_failures: number;
  auto_retries_today: number;
  report_names: string[];
}

export async function adminGetFreshness(): Promise<FreshnessRow[]> {
  const res = await fetch("/api/admin/freshness");
  if (!res.ok) throw new Error("신선도 조회 실패");
  return (await res.json()).datasets as FreshnessRow[];
}

export interface SystemStatus {
  db_latency_ms: number;
  loop_seconds_ago: Record<string, number>;
  sync_interval_sec: number;
  failed_jobs_7d: number;
  failing_datasets: number;
  activity_rows: number;
  active_reports: number;
}

export async function adminGetSystemStatus(): Promise<SystemStatus> {
  const res = await fetch("/api/admin/system-status");
  if (!res.ok) throw new Error("시스템 상태 조회 실패");
  return res.json();
}

/* ── v5: 서버 오류 추적 ───────────────────────────────── */

export interface ErrorLogRow {
  id: number;
  error_code: string;
  http_status: number;
  message: string | null;
  username: string | null;
  path: string | null;
  detail: string | null;
  created_at: string;
}

export async function adminGetRecentErrors(): Promise<ErrorLogRow[]> {
  const res = await fetch("/api/admin/errors");
  if (!res.ok) throw new Error("오류 로그 조회 실패");
  return (await res.json()).errors as ErrorLogRow[];
}

/* ── v6: 뷰어 다운로드/내보내기 ───────────────────────── */

export async function downloadReportPbix(reportId: number): Promise<Blob> {
  const res = await fetch(`/api/reports/${reportId}/download/pbix`);
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(extractDetail(j, "PBIX 다운로드 실패"));
  }
  return res.blob();
}

export interface PptxExportJob {
  export_id: string;
}

export async function startPptxExport(reportId: number, csrf: string): Promise<PptxExportJob> {
  const res = await fetch(`/api/reports/${reportId}/export/pptx`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "내보내기 시작 실패"));
  return j;
}

export interface PptxExportStatus {
  status: string; // Running | Succeeded | Failed | NotStarted
}

export async function pollPptxExport(reportId: number, exportId: string): Promise<PptxExportStatus> {
  const res = await fetch(`/api/reports/${reportId}/export/pptx/${exportId}/status`);
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "상태 조회 실패"));
  return j;
}

export async function downloadPptxExport(reportId: number, exportId: string): Promise<Blob> {
  const res = await fetch(`/api/reports/${reportId}/export/pptx/${exportId}/file`);
  if (!res.ok) throw new Error("파일 다운로드 실패");
  return res.blob();
}

/* ── v6: 개인 활동 로그 ───────────────────────────────── */

export interface MyActivityRow {
  id: number;
  event: string;
  report_name: string | null;
  created_at: string;
}

export async function fetchMyActivity(): Promise<MyActivityRow[]> {
  const res = await fetch("/api/user/activity");
  if (!res.ok) throw new Error("활동 로그 조회 실패");
  return (await res.json()).activity as MyActivityRow[];
}

/* ── v7: 보고서 콘텐츠 업데이트 (데이터셋 유지) ───────── */

export async function startReportUpdate(
  reportId: number, file: File, csrf: string,
): Promise<UploadAccepted> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`/api/reports/${reportId}/update-content`, {
    method: "POST",
    body: fd,
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, res.statusText));
  return data;
}
