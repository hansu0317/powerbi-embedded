// 서버(Jinja 템플릿)가 window.__BOOTSTRAP__ 로 주입한 초기 데이터를 읽는다.
// 백엔드 라우트는 기존 컨텍스트(user/reports/stats/...)를 그대로 넘기고,
// 템플릿이 그 값을 JSON 으로 직렬화해 심어 둔다.

export interface SessionUser {
  username: string;
  display_name: string;
  is_admin: boolean;
  can_upload?: boolean;
}

export interface ReportItem {
  id: number;
  name: string;
  category: string | null;
  owner_username: string | null;
  report_type: string;
  description?: string | null;
  portal_folder_id?: number | null;
  visibility?: "personal" | "group" | "shared";
  has_group_access?: boolean;
}

export interface PopularItem {
  report_id: number;
  views: number;
}

export interface LoginData {
  error: string | null;
  csrf_token: string;
  /** SSO_REDIRECT_URI가 설정된 경우에만 true — "Microsoft 계정으로 로그인" 버튼 노출 여부 */
  sso_enabled: boolean;
}

export interface ReportData {
  user: SessionUser;
  reports: ReportItem[];
  favorites: number[];
  recents: number[];
  popular: PopularItem[];
  csrf_token: string;
  /** 설정 시에만 상단바에 "마케팅 포털" 링크 노출 (MARKETING_PORTAL_URL, 선택) */
  marketing_portal_url: string | null;
  /** 관리자 설정 app_config.recents_limit — useRecents의 상한을 서버와 맞추는 데 쓴다. */
  recents_limit: number;
}

export interface AdminStats {
  active_users: number;
  active_reports: number;
  today_uploads: number;
  today_success: number;
}

export interface AdminUser {
  id: number;
  username: string;
  display_name: string;
  pbi_username: string;
  /** MS 계정 로그인(SSO) 매칭용 — 비어 있으면 그 계정은 SSO 로그인 대상이 아님 */
  email: string | null;
  is_admin: boolean;
  is_active: boolean;
  can_upload: boolean;
  report_count: number;
  last_login_at: string | null;
  department: string | null;
  data_scope: "self" | "department" | "all";
}

export interface AdminReport {
  id: number;
  name: string;
  report_type: string;
  status: string;
  owner_username: string | null;
  category: string | null;
  description?: string | null;
  visibility: "personal" | "group" | "shared";
  viewer_count: number;
  group_count: number;
  pbi_dataset_id: string | null;
  created_at: string | null;
}

export interface AdminJob {
  id: number;
  username: string;
  report_name: string;
  category?: string | null;
  status: string;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminData {
  user: SessionUser;
  stats: AdminStats;
  users: AdminUser[];
  reports: AdminReport[];
  jobs: AdminJob[];
  csrf_token: string;
}

export type Bootstrap =
  | { page: "login"; data: LoginData }
  | { page: "report"; data: ReportData }
  | { page: "admin"; data: AdminData };

declare global {
  interface Window {
    __BOOTSTRAP__?: Bootstrap;
  }
}

export function readBootstrap(): Bootstrap {
  const b = window.__BOOTSTRAP__;
  if (!b) throw new Error("__BOOTSTRAP__ 데이터가 없습니다.");
  return b;
}
