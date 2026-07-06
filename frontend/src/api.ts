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

  settings?: {
    enable_page_nav?: boolean;
    enable_filter?: boolean;
    default_page?: string;
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

export async function uploadPbix(file: File, csrf: string): Promise<UploadAccepted> {
  const fd = new FormData();
  fd.append("file", file);
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
