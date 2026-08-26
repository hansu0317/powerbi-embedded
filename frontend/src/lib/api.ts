// 백엔드 API 호출 헬퍼.
// CSRF 토큰은 서버가 부트스트랩으로 내려준 값을 X-CSRF-Token 헤더로 전달한다.
import type { AdminReport } from "./bootstrap";

// ── 탭 전용 로그인 토큰 ────────────────────────────────────────────────────
// 세션 쿠키는 브라우저 전체가 공유해서, 같은 브라우저의 다른 탭에서 다른 계정으로
// 로그인하면 이 탭까지 그 계정으로 덮어써진다. sessionStorage는 탭마다 독립이라
// 여기 저장한 토큰을 매 요청 Authorization 헤더로 보내면 탭별로 로그인이 분리된다.
const AUTH_TOKEN_KEY = "auth-token";
const AUTH_USER_KEY  = "auth-user";

export function getAuthToken(): string | null {
  return sessionStorage.getItem(AUTH_TOKEN_KEY);
}

export function getAuthUser(): string | null {
  return sessionStorage.getItem(AUTH_USER_KEY);
}

export function setAuthToken(token: string, username: string) {
  sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  sessionStorage.setItem(AUTH_USER_KEY, username);
}

export function clearAuthToken() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  sessionStorage.removeItem(AUTH_USER_KEY);
}

function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = getAuthToken();
  if (!token) return fetch(input, init);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}

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

  // GET 필터(부서, 2026-08-24) — reports.filter_table/filter_column이 설정된
  // 보고서 + 로그인 사용자의 users.department가 있을 때만 내려온다. 진짜 보안 경계가
  // 아니라, 필터 창에서 사용자가 지울 수 있는 표시 편의 기능이다.
  get_filter?: {
    table: string;
    column: string;
    value: string;
  };

  settings?: {
    tab_type?: string; // "dashboard" | "report" — 대시보드 임베드 분기용
  };
}

export async function fetchEmbed(reportId: number): Promise<EmbedResponse> {
  const res = await authFetch(`/api/embed/${reportId}`);
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
  file: File, csrf: string, description?: string, folderId?: number | null,
  visibility: "personal" | "shared" = "personal",
): Promise<UploadAccepted> {
  const fd = new FormData();
  fd.append("file", file);
  if (description) fd.append("report_description", description);
  if (folderId) fd.append("folder_id", String(folderId));
  fd.append("visibility", visibility);
  const res = await authFetch("/api/upload", {
    method: "POST",
    body: fd,
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, res.statusText));
  return data;
}

export interface ReportFolder { id:number; name:string; parent_id:number|null; owner_id:number|null; visibility:"personal"|"shared"; owner_username:string|null; report_count:number }
export async function fetchReportFolders():Promise<ReportFolder[]> { const r=await authFetch("/api/report-folders"); const j=await r.json(); if(!r.ok) throw new Error(extractDetail(j,"폴더 조회 실패")); return j.folders; }
// 업로드 대상 폴더 선택기 전용 — 위 fetchReportFolders(탐색 트리, 열람 가능 기준)와
// 의도적으로 다른 엔드포인트다. 아직 보고서가 없는 빈 공용 폴더도 업로드 대상으로는
// 보여야 해서 "쓸 수 있는가" 기준을 쓴다(2026-08-20, database/folders.py 참고).
export async function fetchWritableFolders():Promise<ReportFolder[]> { const r=await authFetch("/api/report-folders/writable"); const j=await r.json(); if(!r.ok) throw new Error(extractDetail(j,"폴더 조회 실패")); return j.folders; }
export async function moveReportToFolder(reportId:number,folderId:number,csrf:string){ const r=await authFetch(`/api/reports/${reportId}/folder`,{method:"POST",headers:{"X-CSRF-Token":csrf,"Content-Type":"application/json"},body:JSON.stringify({folder_id:folderId})}); const j=await r.json(); if(!r.ok) throw new Error(extractDetail(j,"보고서 이동 실패")); return j; }

export interface UploadStatus {
  job_id: number;
  status: string;
  report_name: string;
  report_id: number | null;
  error: string | null;
}

export async function fetchUploadStatus(jobId: number, csrf: string): Promise<UploadStatus> {
  const res = await authFetch(`/api/upload/status/${jobId}`, {
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, "상태 조회 실패"));
  return data;
}

// ── 즐겨찾기 / 최근 본 보고서 (DB 영속) ──────────────────────────────────────

export async function setFavorite(reportId: number, favorite: boolean, csrf: string) {
  await authFetch(`/api/favorites/${reportId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ favorite }),
  });
}

export async function recordRecent(reportId: number, csrf: string) {
  await authFetch(`/api/recents/${reportId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
}

// ── 인증 ──────────────────────────────────────────────────────────────────────

export async function logout(csrf: string) {
  await authFetch("/logout", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
}

// ── 사용자 편의 (v3) ─────────────────────────────────────────────────────────

// ── 관리자 API ────────────────────────────────────────────────────────────────

export interface SyncStatus {
  available: boolean;
  drift: boolean;
  new?: string[];
  moved?: { name: string; from: string | null; to: string | null }[];
  removed?: string[];
}

export async function adminSyncStatus(): Promise<SyncStatus> {
  const res = await authFetch("/api/admin/sync-status");
  if (!res.ok) return { available: false, drift: false };
  return res.json();
}

export async function adminImportPbi(csrf: string) {
  const res = await authFetch("/api/admin/import-pbi", {
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
  const res = await authFetch("/api/admin/config");
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "설정 조회 실패"));
  return j.config as AppConfigRow[];
}

export async function adminSetConfig(key: string, value: string, csrf: string) {
  const res = await authFetch("/api/admin/config", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "설정 저장 실패"));
  return j as { key: string; value: string };
}

export async function adminFetchReports(): Promise<AdminReport[]> {
  const res = await authFetch("/api/admin/reports");
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "보고서 목록 조회 실패"));
  return j.reports as AdminReport[];
}

export async function adminAddUser(form: FormData) {
  const res = await authFetch("/api/admin/users/add", { method: "POST", body: form });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(extractDetail(j, res.statusText));
  }
  return res.json();
}

export interface BulkAddRowResult {
  row: number;
  username: string;
  status: "ok" | "error";
  message: string | null;
}

export interface BulkAddResult {
  created: number;
  failed: number;
  results: BulkAddRowResult[];
}

export async function adminBulkAddUsers(file: File, csrf: string): Promise<BulkAddResult> {
  const form = new FormData();
  form.set("file", file);
  form.set("csrf", csrf);
  const res = await authFetch("/api/admin/users/bulk-import", { method: "POST", body: form });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, res.statusText));
  return j as BulkAddResult;
}

export interface EditUserPayload {
  display_name: string;
  pbi_username: string;
  department?: string;
  email?: string;
}

export async function adminEditUser(userId: number, payload: EditUserPayload, csrf: string) {
  const res = await authFetch(`/api/admin/users/${userId}/edit`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "사용자 정보 수정 실패"));
  return j;
}

export async function adminToggleUser(userId: number, csrf: string) {
  const res = await authFetch(`/api/admin/users/${userId}/toggle-active`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, res.statusText));
  return j as { is_active: boolean };
}

export async function adminDeleteReport(reportId: number, csrf: string) {
  const res = await authFetch(`/api/admin/reports/${reportId}/delete`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, res.statusText));
  return j as { deleted: boolean; pbi_warning?: string };
}

export interface AccessUser {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  direct: boolean | null;    // true=직접 허용, false=명시적 차단, null=개별 설정 없음
  via_department: boolean;   // 소속 부서로 부여된 권한이 있는지
  can_view: boolean;         // 최종 열람 가능 여부 (차단이 부서 권한보다 우선)
}

export interface UserReportRow {
  id: number;
  name: string;
  category: string | null;
  direct: boolean;          // 직접 부여 여부
  via_department: boolean;  // 소속 부서 경유 부여 여부
}

export async function adminGetUserReports(userId: number): Promise<UserReportRow[]> {
  const res = await authFetch(`/api/admin/users/${userId}/reports`);
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "열람 보고서 조회 실패"));
  return j.reports as UserReportRow[];
}

// ── 부서 단위 보고서 접근(1층 열람권한) ──────────────────────────────────────
// 그룹처럼 별도로 만들거나 삭제하지 않는다 — users.department에 이미 쓰이고 있는
// 값이 곧 부서 목록이다(2026-08-26, 옛 그룹 기능 대체).

export interface DepartmentAccess {
  department: string;
  member_count: number;
}

export async function adminListDepartments(): Promise<string[]> {
  const res = await authFetch("/api/admin/departments");
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "부서 목록 조회 실패"));
  return j.departments as string[];
}

export async function adminGetDepartmentAccess(reportId: number): Promise<DepartmentAccess[]> {
  const res = await authFetch(`/api/admin/reports/${reportId}/department-access`);
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "부서 권한 조회 실패"));
  return j.departments as DepartmentAccess[];
}

export async function adminSetDepartmentAccess(
  reportId: number, department: string, canView: boolean, csrf: string,
) {
  const res = await authFetch(`/api/admin/reports/${reportId}/department-access`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ department, can_view: canView }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "부서 권한 변경 실패"));
  return j;
}

export async function adminSetReportVisibility(
  reportId: number, visibility: "personal" | "shared", csrf: string,
) {
  const res = await authFetch(`/api/admin/reports/${reportId}/visibility`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ visibility }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "공개 범위 변경 실패"));
  return j;
}

export async function adminGetAccess(reportId: number): Promise<AccessUser[]> {
  const res = await authFetch(`/api/admin/reports/${reportId}/access`);
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "권한 목록 조회 실패"));
  return j.users as AccessUser[];
}

export async function adminSetAccess(
  reportId: number,
  userId: number,
  canView: boolean,
  csrf: string,
) {
  const res = await authFetch(`/api/admin/reports/${reportId}/access/${userId}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify({ can_view: canView }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "권한 변경 실패"));
  return j;
}

// ── 권한 매트릭스 / 로그 / 편의 (v3) ─────────────────────────────────────────

export async function adminToggleUpload(userId: number, csrf: string) {
  const res = await authFetch(`/api/admin/users/${userId}/toggle-upload`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "업로드 권한 변경 실패"));
  return j as { can_upload: boolean };
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
): Promise<{ rows: LogRow[]; limit: number }> {
  const res = await authFetch(`/api/admin/logs?${logQueryString(type, filters)}`);
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "로그 조회 실패"));
  // limit은 서버 app_config(activity_log_max_rows)에서 온 실제 조회 상한 — 화면의
  // "최근 N건까지만 표시" 안내가 관리자가 바꾼 값과 항상 맞도록 같이 받는다.
  return { rows: j.rows as LogRow[], limit: typeof j.limit === "number" ? j.limit : 1000 };
}

/* ── v4: 자가진단 ─────────────────────────────────────── */

export interface SystemStatus {
  db_latency_ms: number;
  loop_seconds_ago: Record<string, number>;
  sync_interval_sec: number;
  failed_jobs_7d: number;
  activity_rows: number;
  active_reports: number;
}

export async function adminGetSystemStatus(): Promise<SystemStatus> {
  const res = await authFetch("/api/admin/system-status");
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "시스템 상태 조회 실패"));
  return j;
}

/* ── v5: 서버 오류 추적 ───────────────────────────────── */

export interface MyActivityRow {
  id: number;
  event: string;
  report_name: string | null;
  created_at: string;
}

export async function fetchMyActivity(): Promise<MyActivityRow[]> {
  const res = await authFetch("/api/user/activity");
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(j, "활동 로그 조회 실패"));
  return j.activity as MyActivityRow[];
}

/* ── v7: 보고서 콘텐츠 업데이트 (데이터셋 유지) ───────── */

export async function startReportUpdate(
  reportId: number, file: File, csrf: string,
): Promise<UploadAccepted> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await authFetch(`/api/reports/${reportId}/update-content`, {
    method: "POST",
    body: fd,
    headers: { "X-CSRF-Token": csrf },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractDetail(data, res.statusText));
  return data;
}
