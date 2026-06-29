// 서버(Jinja 템플릿)가 window.__BOOTSTRAP__ 로 주입한 초기 데이터를 읽는다.
// 백엔드 라우트는 기존 컨텍스트(user/reports/stats/...)를 그대로 넘기고,
// 템플릿이 그 값을 JSON 으로 직렬화해 심어 둔다.

export interface SessionUser {
  display_name: string;
  is_admin: boolean;
}

export interface ReportItem {
  id: number;
  name: string;
  category: string | null;
  owner_username: string | null;
  report_type: string;
}

export interface LoginData {
  error: string | null;
  csrf_token: string;
}

export interface ReportData {
  user: SessionUser;
  reports: ReportItem[];
  favorites: number[];
  recents: number[];
  csrf_token: string;
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
  roles: string;
  is_admin: boolean;
  is_active: boolean;
  report_count: number;
  last_login_at: string | null;
}

export interface AdminReport {
  id: number;
  name: string;
  report_type: string;
  status: string;
  owner_username: string | null;
  category: string | null;
  viewer_count: number;
  pbi_dataset_id: string | null;
  created_at: string | null;
}

export interface AdminJob {
  id: number;
  username: string;
  report_name: string;
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
